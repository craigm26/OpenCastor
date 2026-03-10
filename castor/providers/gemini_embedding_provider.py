"""
Gemini Embedding 2 provider — premium multimodal semantic embeddings.

Maps text, images, and audio into a unified 1536-dim embedding space using
Google's ``gemini-embedding-2-preview`` model (Matryoshka Representation
Learning, default 1536 dimensions).

Requires a **paid** ``GOOGLE_API_KEY`` (not ADC).  Falls back to a zero-vector
mock when the key is absent or the ``google-genai`` package is not installed —
the runtime never crashes, it just logs a warning and returns zeros.

Install:  pip install google-genai>=1.0.0
API key:  https://aistudio.google.com/apikey
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np

from .embedding_backend import EmbeddingBackend

logger = logging.getLogger("OpenCastor.GeminiEmbedding")


def _mime_from_bytes(data: bytes, default_mime: str) -> str:
    """Detect MIME type from magic bytes; fall back to *default_mime*."""
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    return default_mime


try:
    from google import genai as _genai
    from google.genai import types as _gtypes

    HAS_GENAI = True
except ImportError:  # pragma: no cover
    HAS_GENAI = False

_DEFAULT_MODEL = "gemini-embedding-2-preview"
_DEFAULT_DIMENSIONS = 1536  # MRL options: 3072 (max quality), 1536, 768


class GeminiEmbeddingProvider(EmbeddingBackend):
    """Multimodal semantic embedding provider backed by Gemini Embedding 2.

    Args:
        config: Provider config dict.  Recognised keys:

            - ``model``      (str)  — embedding model name (default ``gemini-embedding-2-preview``)
            - ``dimensions`` (int)  — MRL output dimensions: 3072 / 1536 / 768 (default 1536)

        The provider reads ``GOOGLE_API_KEY`` from the environment.  If the key
        is missing **and** ``require_key=True`` (default False), a ``ValueError``
        is raised; otherwise mock mode is activated.
    """

    def __init__(self, config: dict, require_key: bool = False):
        self._model = config.get("model", _DEFAULT_MODEL)
        self._dims = int(config.get("dimensions", _DEFAULT_DIMENSIONS))
        self._client: Optional[object] = None
        self._available = False

        api_key = os.getenv("GOOGLE_API_KEY") or config.get("api_key", "")

        if not api_key:
            msg = (
                "GOOGLE_API_KEY not set — GeminiEmbeddingProvider running in mock mode "
                "(returns zero vectors). Set the key to enable semantic embeddings."
            )
            if require_key:
                raise ValueError(msg)
            logger.warning(msg)
            return

        if not HAS_GENAI:
            logger.warning(
                "google-genai package not installed — GeminiEmbeddingProvider in mock mode. "
                "Install with: pip install google-genai>=1.0.0"
            )
            return

        try:
            self._client = _genai.Client(api_key=api_key)
            self._available = True
            logger.info("GeminiEmbeddingProvider ready — model=%s dims=%d", self._model, self._dims)
        except Exception as exc:  # pragma: no cover
            logger.warning("GeminiEmbeddingProvider init failed (%s) — mock mode active", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True when the provider has a valid API key and genai is installed."""
        return self._available

    @property
    def dimensions(self) -> int:
        """Output embedding dimensions (MRL)."""
        return self._dims

    @property
    def backend_name(self) -> str:
        """Backend identifier."""
        return "gemini-embedding-2-preview" if self._available else "gemini-mock"

    def embed(
        self,
        text: str | None = None,
        image_bytes: bytes | None = None,
        audio_bytes: bytes | None = None,
    ) -> np.ndarray:
        """Embed text and/or image/audio into a unit-norm float32 vector.

        Conforms to the :class:`~castor.providers.embedding_backend.EmbeddingBackend` interface.

        Args:
            text:        Text string to embed (optional).
            image_bytes: Raw JPEG/PNG image bytes (optional).
            audio_bytes: Raw audio bytes (optional).

        Returns:
            float32 ndarray of shape ``(dimensions,)``.
        """
        if text is None and image_bytes is None and audio_bytes is None:
            return self._zeros()
        if image_bytes is not None:
            return self.embed_scene(image_bytes, text or "", audio_bytes)
        if audio_bytes is not None:
            return self.embed_scene(None, text or "", audio_bytes)
        return self.embed_text(text or "")

    def embed_text(self, text: str) -> np.ndarray:
        """Embed a plain-text string.

        Args:
            text: The text to embed (up to 8192 tokens).

        Returns:
            Float32 numpy array of shape ``(dimensions,)``.
        """
        if not self._available:
            return self._zeros()
        try:
            result = self._client.models.embed_content(  # type: ignore[union-attr]
                model=self._model,
                contents=text,
                config={"output_dimensionality": self._dims},
            )
            vec = np.array(result.embeddings[0].values, dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            if norm < 1e-9:
                return self._zeros()
            return (vec / norm).astype(np.float32)
        except Exception as exc:
            logger.warning("embed_text failed (%s) — returning zeros", exc)
            return self._zeros()

    def embed_scene(
        self,
        frame_bytes: bytes | None,
        text: str,
        audio_bytes: bytes | None = None,
    ) -> np.ndarray:
        """Embed a multimodal robot scene (image + text, optionally + audio).

        Passes all available modalities together so the model can capture
        cross-modal relationships (e.g. "obstacle ahead" spoken while a wall
        is visible in the frame).

        Args:
            frame_bytes: Raw JPEG/PNG camera frame, or None if unavailable.
            text:        Instruction, question, or scene description text.
            audio_bytes: Raw audio bytes (MP3/WAV), or None.

        Returns:
            Float32 numpy array of shape ``(dimensions,)``.
        """
        if not self._available:
            return self._zeros()

        contents: list = [text]

        if frame_bytes and len(frame_bytes) > 100:
            try:
                img_mime = _mime_from_bytes(frame_bytes, "image/jpeg")
                contents.append(_gtypes.Part.from_bytes(data=frame_bytes, mime_type=img_mime))
            except Exception as exc:
                logger.debug("Could not attach frame to embedding request: %s", exc)

        if audio_bytes and len(audio_bytes) > 100:
            try:
                audio_mime = _mime_from_bytes(audio_bytes, "audio/mpeg")
                contents.append(_gtypes.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            except Exception as exc:
                logger.debug("Could not attach audio to embedding request: %s", exc)

        try:
            result = self._client.models.embed_content(  # type: ignore[union-attr]
                model=self._model,
                contents=contents,
                config={"output_dimensionality": self._dims},
            )
            vec = np.array(result.embeddings[0].values, dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            if norm < 1e-9:
                return self._zeros()
            return (vec / norm).astype(np.float32)
        except Exception as exc:
            logger.warning("embed_scene failed (%s) — returning zeros", exc)
            return self._zeros()

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two embedding vectors.

        Args:
            a: First embedding vector.
            b: Second embedding vector.

        Returns:
            Similarity score in ``[-1.0, 1.0]``.  Returns 0.0 for zero vectors.
        """
        norm_a = float(np.linalg.norm(a))
        norm_b = float(np.linalg.norm(b))
        if norm_a < 1e-9 or norm_b < 1e-9:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _zeros(self) -> np.ndarray:
        return np.zeros(self._dims, dtype=np.float32)
