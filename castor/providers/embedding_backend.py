"""Abstract base class for all multimodal embedding backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class EmbeddingBackend(ABC):
    """Abstract base class for all multimodal embedding backends."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Output embedding dimension count."""
        ...

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable backend identifier."""
        ...

    @abstractmethod
    def embed(
        self,
        text: str | None = None,
        image_bytes: bytes | None = None,
        audio_bytes: bytes | None = None,
    ) -> np.ndarray:
        """Return unit-norm float32 vector of shape (dimensions,)."""
        ...

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two unit-norm vectors. Returns 0.0 for zero vectors."""
        norm_a = float(np.linalg.norm(a))
        norm_b = float(np.linalg.norm(b))
        if norm_a < 1e-9 or norm_b < 1e-9:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
