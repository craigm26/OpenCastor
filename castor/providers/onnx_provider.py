"""ONNX Runtime provider for OpenCastor.

Run local AI vision models on-device using the ONNX Runtime inference engine.
This provider is ideal for edge deployment (Raspberry Pi, Jetson) where cloud
API latency or connectivity is a concern.

Supported model types:
- Vision-language models exported to ONNX (e.g., CLIP, MobileVLM variants)
- Text-only instruction following models (ONNX NLP models)
- Custom ONNX models with text/vision inputs

Install::

    pip install onnxruntime       # CPU
    pip install onnxruntime-gpu   # NVIDIA GPU (CUDA)

Environment variables:
    ONNX_MODEL_PATH  — Path to the ONNX model file (required)
    ONNX_PROVIDERS   — Comma-separated execution providers (default: CPUExecutionProvider)

RCAN config example::

    agent:
      provider: onnx
      model: /models/my_robot_brain.onnx
      onnx_providers: [CPUExecutionProvider]
      max_tokens: 256
"""

import json
import logging
import os
import time
from typing import Any, Dict, Iterator, List, Optional

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.ONNX")

try:
    import onnxruntime as ort  # type: ignore

    HAS_ONNX = True
    logger.debug("onnxruntime %s available", ort.__version__)
except ImportError:
    HAS_ONNX = False
    logger.info("onnxruntime not installed. ONNX provider will run in mock mode.")

try:
    import numpy as np  # type: ignore

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Attempt to import a tokenizer (transformers optional)
try:
    from transformers import AutoTokenizer  # type: ignore

    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False

DEFAULT_MAX_TOKENS = 256
DEFAULT_PROVIDERS = ["CPUExecutionProvider"]


def _resolve_providers(config: Dict[str, Any]) -> List[str]:
    env_prov = os.getenv("ONNX_PROVIDERS", "")
    if env_prov:
        return [p.strip() for p in env_prov.split(",") if p.strip()]
    cfg = config.get("onnx_providers", config.get("providers", DEFAULT_PROVIDERS))
    if isinstance(cfg, str):
        return [p.strip() for p in cfg.split(",") if p.strip()]
    return list(cfg)


def _resolve_model_path(config: Dict[str, Any]) -> Optional[str]:
    return (
        os.getenv("ONNX_MODEL_PATH")
        or config.get("onnx_model_path")
        or config.get("model_path")
        or config.get("model")
    )


class ONNXProvider(BaseProvider):
    """ONNX Runtime inference provider.

    Loads an ONNX model file and runs inference locally.  When ``onnxruntime``
    is not installed the provider degrades to mock mode and returns placeholder
    responses so the rest of the runtime continues to function.

    Config options:
        - ``model`` / ``onnx_model_path``: Path to the ``.onnx`` file
        - ``onnx_providers``: List of execution providers (CPUExecutionProvider,
          CUDAExecutionProvider, CoreMLExecutionProvider, etc.)
        - ``tokenizer_path``: Path or HF repo for the tokenizer (optional)
        - ``max_tokens``: Max generated tokens (default: 256)
        - ``system_prompt``: Custom system prompt override
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self._model_path = _resolve_model_path(config)
        self._providers = _resolve_providers(config)
        self._max_tokens = int(config.get("max_tokens", DEFAULT_MAX_TOKENS))
        self._tokenizer_path = config.get("tokenizer_path")

        self._session: Optional[Any] = None
        self._tokenizer: Optional[Any] = None
        self._input_names: List[str] = []
        self._output_names: List[str] = []
        self._mode = "mock"

        if not HAS_ONNX:
            logger.warning(
                "ONNXProvider: onnxruntime not installed. Running in mock mode. "
                "Install with: pip install onnxruntime"
            )
            return

        if not self._model_path:
            logger.warning(
                "ONNXProvider: no model path provided (set ONNX_MODEL_PATH or "
                "agent.model in RCAN config). Running in mock mode."
            )
            return

        if not os.path.exists(str(self._model_path)):
            logger.warning(
                "ONNXProvider: model file not found at '%s'. Running in mock mode.",
                self._model_path,
            )
            return

        try:
            self._session = ort.InferenceSession(  # type: ignore[attr-defined]
                str(self._model_path),
                providers=self._providers,
            )
            self._input_names = [inp.name for inp in self._session.get_inputs()]
            self._output_names = [out.name for out in self._session.get_outputs()]
            self._mode = "hardware"
            logger.info(
                "ONNXProvider ready: model=%s providers=%s inputs=%s",
                self._model_path,
                self._providers,
                self._input_names,
            )
        except Exception as exc:
            logger.error("ONNXProvider: failed to load model: %s", exc)
            self._mode = "mock"

        # Optional tokenizer
        if HAS_TRANSFORMERS and self._tokenizer_path:
            try:
                self._tokenizer = AutoTokenizer.from_pretrained(self._tokenizer_path)
                logger.info("ONNXProvider: tokenizer loaded from %s", self._tokenizer_path)
            except Exception as exc:
                logger.warning("ONNXProvider: failed to load tokenizer: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> "np.ndarray":  # type: ignore[name-defined]
        """Convert text to token IDs via transformers tokenizer."""
        if self._tokenizer is None or not HAS_NUMPY:
            raise RuntimeError("Tokenizer not available")
        enc = self._tokenizer(text, return_tensors="np", truncation=True, max_length=512)
        return enc["input_ids"]

    def _decode_tokens(self, token_ids: "np.ndarray") -> str:  # type: ignore[name-defined]
        """Decode token IDs back to text."""
        if self._tokenizer is None:
            return str(token_ids.tolist())
        return self._tokenizer.decode(token_ids[0], skip_special_tokens=True)

    def _run_inference(self, instruction: str, image_bytes: Optional[bytes] = None) -> str:
        """Run the ONNX session and return decoded text output."""
        if self._session is None or not HAS_NUMPY:
            return json.dumps({"action": "none", "reason": "ONNX mock mode"})

        feeds: Dict[str, Any] = {}

        # Build input feeds based on available input names
        prompt = f"{self.system_prompt}\n\nUser: {instruction}\nAssistant:"

        if "input_ids" in self._input_names and self._tokenizer is not None:
            feeds["input_ids"] = self._tokenize(prompt)
            if "attention_mask" in self._input_names:
                feeds["attention_mask"] = np.ones_like(feeds["input_ids"])

        if "pixel_values" in self._input_names and image_bytes:
            try:
                import cv2  # type: ignore

                img_array = np.frombuffer(image_bytes, dtype=np.uint8)
                img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                img = cv2.resize(img, (224, 224))
                img = img.astype(np.float32) / 255.0
                # NCHW format
                img = img.transpose(2, 0, 1)[np.newaxis, ...]
                feeds["pixel_values"] = img
            except Exception as exc:
                logger.warning("ONNXProvider: could not preprocess image: %s", exc)

        if not feeds:
            # Fallback: pass raw text as a simple string input if the model expects it
            for name in self._input_names[:1]:
                feeds[name] = np.array([[ord(c) for c in prompt[:512]]], dtype=np.int64)

        try:
            outputs = self._session.run(self._output_names, feeds)
        except Exception as exc:
            logger.error("ONNXProvider: inference error: %s", exc)
            return json.dumps({"action": "none", "reason": str(exc)})

        raw = outputs[0] if outputs else np.array([])

        # Attempt to decode as token IDs
        if self._tokenizer is not None and hasattr(raw, "shape") and raw.ndim >= 2:
            try:
                return self._decode_tokens(raw)
            except Exception:
                pass

        return str(raw.tolist() if hasattr(raw, "tolist") else raw)

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "api",
    ) -> Thought:
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            return safety_block

        if self._mode == "mock":
            logger.debug("ONNXProvider mock think: %s", instruction[:60])
            mock_text = json.dumps({"action": "none", "reason": "ONNX mock mode — no model loaded"})
            return Thought(mock_text, {"action": "none"})

        t0 = time.time()
        raw_text = self._run_inference(instruction, image_bytes if image_bytes else None)
        action = self._clean_json(raw_text)
        latency_ms = round((time.time() - t0) * 1000, 1)
        logger.debug("ONNXProvider think: %.1f ms", latency_ms)
        return Thought(raw_text, action)

    def think_stream(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "api",
    ) -> Iterator[str]:
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            yield safety_block.raw_text
            return

        # ONNX does not natively stream; yield full response as a single chunk
        thought = self.think(image_bytes, instruction, surface)
        yield thought.raw_text

    def health_check(self) -> Dict[str, Any]:
        t0 = time.time()
        if self._mode == "mock":
            return {
                "ok": False,
                "mode": "mock",
                "latency_ms": 0.0,
                "error": "onnxruntime not installed or model not found",
                "model_path": self._model_path,
                "providers": self._providers,
            }

        # Verify session is alive with a minimal probe
        try:
            inputs_info = [
                {"name": inp.name, "shape": inp.shape, "type": inp.type}
                for inp in self._session.get_inputs()
            ]
            return {
                "ok": True,
                "mode": "hardware",
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": None,
                "model_path": self._model_path,
                "providers": self._session.get_providers(),
                "inputs": inputs_info,
            }
        except Exception as exc:
            return {
                "ok": False,
                "mode": "error",
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": str(exc),
                "model_path": self._model_path,
                "providers": self._providers,
            }
