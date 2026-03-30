"""
Vision-Language-Action (VLA) model provider.

Supports:
  - OpenVLA (openvla/openvla-7b on HuggingFace) — 7B parameter VLA
  - π0 (Physical Intelligence) — when local weights are available
  - Mock fallback for development/testing

VLA models take an RGB image + language instruction and directly output
a robot action vector (end-effector deltas or joint velocities).

Env:
  OPENVLA_MODEL_PATH   — local path or HF model ID (default: openvla/openvla-7b)
  VLA_UNNORM_KEY       — dataset normalization key for OpenVLA (default: bridge_orig)
  VLA_DEVICE           — "cpu" | "cuda" | "mps" (default: cpu)

Install:  pip install transformers accelerate torch pillow
          (OpenVLA requires ~28 GB disk, ~14 GB VRAM for GPU inference)

RCAN config:
  agent:
    provider: vla
    model: openvla/openvla-7b   # or local path
"""

import io
import logging
import os
import time
from collections.abc import Iterator

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.VLA")

# Joints written via write_arm_command at inference time (indices 2–6 of the 7-DoF vector).
# Index 0 (linear) and 1 (angular) drive the mobile base, not the arm.
_ARM_JOINT_KEYS = ["grip_x", "grip_y", "grip_z", "wrist", "gripper"]

_VLA_DEVICE = os.getenv("VLA_DEVICE", "cpu")
_UNNORM_KEY = os.getenv("VLA_UNNORM_KEY", "bridge_orig")


def _patch_transformers_imports() -> None:
    """Back-fill symbols that openvla's processing_prismatic.py imports from
    ``transformers.tokenization_utils``.

    In transformers >= 4.38 (and especially 5.x dev), ``PaddingStrategy``,
    ``PreTokenizedInput``, ``TextInput``, and ``TruncationStrategy`` were
    relocated to ``transformers.tokenization_utils_base``.  The openvla
    remote-code module still uses the old import path, so we patch the module
    object in-place before AutoProcessor loads the remote code.
    """
    try:
        import transformers.tokenization_utils as _tu
        import transformers.tokenization_utils_base as _tub

        for _name in ("PaddingStrategy", "PreTokenizedInput", "TextInput", "TruncationStrategy"):
            if not hasattr(_tu, _name) and hasattr(_tub, _name):
                setattr(_tu, _name, getattr(_tub, _name))
    except Exception as exc:  # pragma: no cover
        logger.debug("_patch_transformers_imports skipped: %s", exc)


try:
    _patch_transformers_imports()
    from PIL import Image as _PILImage
    from transformers import AutoModelForVision2Seq, AutoProcessor

    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False


class VLAProvider(BaseProvider):
    """Vision-Language-Action model provider for direct robot control.

    Outputs action vectors rather than free text. The action is embedded in
    the Thought.action dict as {"type": "vla_action", "vector": [...]}.
    """

    _ACTION_KEYS = ["linear", "angular", "grip_x", "grip_y", "grip_z", "wrist", "gripper"]

    def __init__(self, config: dict):
        super().__init__(config)
        model_id = config.get("model", os.getenv("OPENVLA_MODEL_PATH", "openvla/openvla-7b"))
        self._model_id = model_id
        self._processor = None
        self._model = None
        self._mode = "mock"
        # Safety layer for arm command gating (injected from CastorFS at runtime).
        self._safety_layer = config.get("safety_layer")
        self._principal: str = config.get("principal", "vla_provider")

        if HAS_TRANSFORMERS:
            try:
                logger.info(
                    "Loading VLA model %s on %s (may take a minute)…", model_id, _VLA_DEVICE
                )
                self._processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
                self._model = AutoModelForVision2Seq.from_pretrained(
                    model_id,
                    attn_implementation="eager",
                    torch_dtype="auto",
                    low_cpu_mem_usage=True,
                    trust_remote_code=True,
                )
                self._model.to(_VLA_DEVICE)
                self._mode = "openvla"
                logger.info("VLA model ready: %s", model_id)
            except Exception as exc:
                logger.warning("VLA model load failed: %s — mock mode", exc)
        else:
            logger.info(
                "VLA provider in mock mode "
                "(install: pip install transformers accelerate torch pillow)"
            )

    def health_check(self) -> dict:
        return {
            "ok": True,
            "mode": self._mode,
            "model": self._model_id,
            "device": _VLA_DEVICE,
            "error": None,
        }

    def think(self, image_bytes: bytes, instruction: str, surface: str = "whatsapp") -> Thought:
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            return safety_block

        t0 = time.monotonic()

        if self._mode == "openvla" and self._model is not None:
            try:
                action_vec = self._run_openvla(image_bytes, instruction)
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                action = self._vec_to_action(action_vec)
                # ── Safety gate: pass every arm joint through write_arm_command ──
                if not self._write_arm_joints(action, action_vec):
                    raw = "VLA arm command blocked by SafetyLayer"
                    logger.warning(raw)
                    return Thought(raw_text=raw, action={"type": "blocked"})
                # Real inference: high confidence (model output is deterministic)
                action["confidence"] = 0.85
                action["brain"] = "vla_openvla"
                raw = f"VLA action ({latency_ms} ms, conf=0.85): " + ", ".join(
                    f"{k}={v:.3f}" for k, v in action.items() if k not in ("type", "brain")
                )
                return Thought(raw_text=raw, action=action)
            except Exception as exc:
                logger.error("VLA think error: %s", exc)

        # CPU fallback / mock: return a simple forward-stop action with confidence
        # confidence < 0.60 will trigger escalation to the planning brain (Claude)
        action = {
            "type": "move",
            "linear": 0.3,
            "angular": 0.0,
            "confidence": 0.55,  # below gate → escalates to planning brain in dual-brain mode
            "brain": "vla_mock",
        }
        raw = f"VLA mock action: linear=0.3 angular=0.0 confidence=0.55 (model={self._model_id})"
        return Thought(raw_text=raw, action=action)

    def think_stream(
        self, image_bytes: bytes, instruction: str, surface: str = "whatsapp"
    ) -> Iterator[str]:
        """VLA models don't stream — delegate to think() and yield the result."""
        thought = self.think(image_bytes, instruction, surface)
        yield thought.raw_text

    # ── Internal ──────────────────────────────────────────────────────

    def _write_arm_joints(self, action: dict, vec: list[float]) -> bool:
        """Gate each arm joint through write_arm_command before committing the action.

        Returns True if all commands are accepted (or no safety layer is attached).
        Returns False if any arm joint command is blocked by the SafetyLayer.
        """
        from castor.hardware.so_arm101.safety_bridge import write_arm_command

        safety_layer = self._safety_layer
        principal = self._principal

        # Map action keys to (position_rad, velocity_rad_s) from the raw vector.
        # vec[2..6] correspond to _ARM_JOINT_KEYS in order.
        for idx, joint_name in enumerate(_ARM_JOINT_KEYS):
            position = (
                float(vec[idx + 2]) if len(vec) > idx + 2 else float(action.get(joint_name, 0.0))
            )
            velocity = 0.0  # VLA outputs position deltas; velocity unknown, default safe
            if safety_layer:
                allowed = write_arm_command(
                    safety_layer=safety_layer,
                    joint=joint_name,
                    position=position,
                    velocity=velocity,
                    principal=principal,
                )
                if not allowed:
                    logger.warning(
                        "arm command blocked by SafetyLayer: %s=%.4f", joint_name, position
                    )
                    return False
        return True

    def _run_openvla(self, jpeg_bytes: bytes, instruction: str) -> list[float]:
        """Run OpenVLA inference and return the 7-DoF action vector."""
        import torch

        if jpeg_bytes and len(jpeg_bytes) > 4:
            pil_img = _PILImage.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        else:
            pil_img = _PILImage.new("RGB", (224, 224), color=(128, 128, 128))

        prompt = f"In: What action should the robot take to {instruction}?\nOut:"
        inputs = self._processor(prompt, pil_img).to(_VLA_DEVICE, dtype=torch.bfloat16)

        with torch.no_grad():
            action = self._model.predict_action(
                **inputs,
                unnorm_key=_UNNORM_KEY,
                do_sample=False,
            )

        return action.squeeze().tolist()

    def _vec_to_action(self, vec: list[float]) -> dict:
        """Map a 7-DoF action vector to an OpenCastor action dict."""
        # OpenVLA output: [delta_x, delta_y, delta_z, delta_rx, delta_ry, delta_rz, gripper]
        if len(vec) >= 2:
            linear = float(vec[0])  # forward/back
            angular = float(vec[1])  # turn
        else:
            linear, angular = 0.0, 0.0

        action: dict = {
            "type": "vla_action",
            "linear": round(max(-1.0, min(1.0, linear)), 3),
            "angular": round(max(-1.0, min(1.0, angular)), 3),
            "vector": [round(v, 4) for v in vec],
        }
        # Map gripper DoF if present (index 6)
        if len(vec) >= 7:
            action["gripper"] = round(float(vec[6]), 3)
        return action
