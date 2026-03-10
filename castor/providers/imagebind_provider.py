"""
ImageBind experimental embedding provider — Tier 1 (local 6-modality).

LICENSE WARNING: ImageBind is released under CC BY-NC 4.0 by Meta.
This is NOT a commercial-use license. Do not use in commercial products
or redistribute commercially. Suitable for research and internal deployments.

Install: see docs/setup/imagebind-setup.md
"""

from __future__ import annotations

import logging

import numpy as np

from .embedding_backend import EmbeddingBackend

logger = logging.getLogger("OpenCastor.ImageBindEmbedding")

HAS_IMAGEBIND = False

try:
    import imagebind.models.imagebind_model as _ib_model  # noqa: F401

    HAS_IMAGEBIND = True
except ImportError:
    pass

_DEFAULT_DIMS = 1024


class ImageBindProvider(EmbeddingBackend):
    """ImageBind multimodal embedding provider — Tier 1.

    Supports image, text, and audio modalities (IMU/depth as future extension).
    Falls back to 1024-dim zeros when ImageBind is not installed.

    NOTE: ImageBind is licensed under CC BY-NC 4.0 by Meta.
    Do NOT use in commercial products. For research/internal use only.

    Args:
        config: Optional config dict. Keys:
            - ``model_name`` (str): ImageBind model variant (default: ``"imagebind_huge"``)
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._model_name = cfg.get("model_name", "imagebind_huge")
        self._model = None
        self._mock = not HAS_IMAGEBIND

        if not self._mock:
            self._load_model()

    def _load_model(self) -> None:
        """Load ImageBind model weights."""
        try:
            import imagebind.models.imagebind_model as ib_model

            self._model = ib_model.imagebind_huge(pretrained=True)
            self._model.eval()
            logger.info("ImageBindProvider loaded (CPU mode)")
        except Exception as exc:
            logger.warning("ImageBindProvider: model load failed (%s) — mock mode", exc)
            self._mock = True

    @property
    def dimensions(self) -> int:
        """Output embedding dimensions (1024)."""
        return _DEFAULT_DIMS

    @property
    def backend_name(self) -> str:
        """Backend identifier."""
        return "imagebind-mock" if self._mock else "imagebind"

    @property
    def available(self) -> bool:
        """True when ImageBind is loaded and ready."""
        return not self._mock

    def embed(
        self,
        text: str | None = None,
        image_bytes: bytes | None = None,
        audio_bytes: bytes | None = None,
    ) -> np.ndarray:
        """Embed one or more modalities into a unit-norm 1024-dim float32 vector.

        Args:
            text:        Text string (optional).
            image_bytes: Raw JPEG/PNG bytes (optional).
            audio_bytes: Raw WAV/MP3 bytes (optional).

        Returns:
            float32 ndarray of shape ``(1024,)``. Returns zeros in mock mode.
        """
        if self._mock:
            return np.zeros(_DEFAULT_DIMS, dtype=np.float32)

        try:
            import io

            import imagebind.data as ib_data
            import torch
            from imagebind.models.imagebind_model import ModalityType

            inputs = {}
            if text is not None:
                inputs[ModalityType.TEXT] = ib_data.load_and_transform_text([text], device="cpu")
            if image_bytes is not None:
                from PIL import Image

                img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                inputs[ModalityType.VISION] = ib_data.load_and_transform_vision_data(
                    [img], device="cpu"
                )
            if audio_bytes is not None:
                import os
                import tempfile

                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        f.write(audio_bytes)
                        tmp_path = f.name
                    inputs[ModalityType.AUDIO] = ib_data.load_and_transform_audio_data(
                        [tmp_path], device="cpu"
                    )
                finally:
                    if tmp_path is not None:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

            if not inputs:
                return np.zeros(_DEFAULT_DIMS, dtype=np.float32)

            with torch.no_grad():
                embeddings = self._model(inputs)

            vecs = [v[0].numpy().astype(np.float32) for v in embeddings.values()]
            if len(vecs) == 1:
                combined = vecs[0]
            else:
                combined = sum(vecs) / len(vecs)

            norm = float(np.linalg.norm(combined))
            if norm < 1e-9:
                return np.zeros(_DEFAULT_DIMS, dtype=np.float32)
            return (combined / norm).astype(np.float32)

        except Exception as exc:
            logger.warning("ImageBindProvider.embed error: %s", exc)
            return np.zeros(_DEFAULT_DIMS, dtype=np.float32)
