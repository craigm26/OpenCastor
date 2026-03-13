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

_VLA_DEVICE = os.getenv("VLA_DEVICE", "cpu")
_UNNORM_KEY = os.getenv("VLA_UNNORM_KEY", "bridge_orig")

try:
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
                raw = f"VLA action ({latency_ms} ms): " + ", ".join(
                    f"{k}={v:.3f}" for k, v in action.items() if k != "type"
                )
                return Thought(raw_text=raw, action=action)
            except Exception as exc:
                logger.error("VLA think error: %s", exc)

        # Mock: return a simple forward-stop action
        action = {"type": "move", "linear": 0.3, "angular": 0.0}
        raw = f"VLA mock action: linear=0.3 angular=0.0 (model={self._model_id})"
        return Thought(raw_text=raw, action=action)

    def think_stream(
        self, image_bytes: bytes, instruction: str, surface: str = "whatsapp"
    ) -> Iterator[str]:
        """VLA models don't stream — delegate to think() and yield the result."""
        thought = self.think(image_bytes, instruction, surface)
        yield thought.raw_text

    # ── Internal ──────────────────────────────────────────────────────

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
