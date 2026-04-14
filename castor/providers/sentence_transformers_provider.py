"""
Sentence-Transformers embedding provider — local semantic search for episode memory.

Encodes text to dense vector embeddings using the sentence-transformers library.
Designed for semantic similarity search over OpenCastor's EpisodeMemory store.
Falls back to zero-vector mock mode when the library is not installed.

Env:     ST_MODEL   (model name; default all-MiniLM-L6-v2)
         ST_DEVICE  (cpu / cuda; default cpu)
Install: pip install sentence-transformers

Default model: all-MiniLM-L6-v2
  - Size: ~80 MB
  - Speed: ~14k sentences/sec on CPU
  - Dimensions: 384
  - Quality: strong for semantic similarity tasks
"""

import logging
import math
import os
from typing import Optional

logger = logging.getLogger("OpenCastor.EmbeddingProvider")

try:
    from sentence_transformers import SentenceTransformer

    HAS_ST = True
except ImportError:  # pragma: no cover
    HAS_ST = False

_DEFAULT_MODEL = "all-MiniLM-L6-v2"

# Module-level singleton
_instance: Optional["EmbeddingProvider"] = None


class EmbeddingProvider:
    """Local semantic embedding provider backed by sentence-transformers.

    Provides text encoding, cosine similarity, and top-k semantic search.
    All methods degrade gracefully to zero-vector / 0.0 mock responses when
    sentence-transformers is not installed.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
    ):
        self.model_name = model_name or os.getenv("ST_MODEL", _DEFAULT_MODEL)
        self.device = device or os.getenv("ST_DEVICE", "cpu")
        self._model = None
        self._mode = "mock"

        if HAS_ST:
            try:
                try:
                    # Prefer locally cached model to avoid HuggingFace HEAD checks.
                    self._model = SentenceTransformer(
                        self.model_name, device=self.device, local_files_only=True
                    )
                except Exception:
                    # Not cached yet — download once.
                    self._model = SentenceTransformer(self.model_name, device=self.device)
                self._mode = "hardware"
                logger.info(
                    "EmbeddingProvider loaded model=%s device=%s",
                    self.model_name,
                    self.device,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "EmbeddingProvider: failed to load model %s (%s) — using mock",
                    self.model_name,
                    exc,
                )
        else:
            logger.warning(
                "sentence-transformers not installed — EmbeddingProvider in mock mode. "
                "Install with: pip install sentence-transformers"
            )

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode a list of texts into dense float vectors.

        Returns:
            List of embedding vectors (one per input text).
            Mock mode returns zero vectors of length 384.
        """
        if not texts:
            return []
        if self._model is None:
            return [[0.0] * 384 for _ in texts]
        try:
            embeddings = self._model.encode(texts, convert_to_numpy=True)
            return [vec.tolist() for vec in embeddings]
        except Exception as exc:
            logger.error("EmbeddingProvider encode error: %s", exc)
            return [[0.0] * 384 for _ in texts]

    def similarity(self, a: str, b: str) -> float:
        """Compute cosine similarity between two texts.

        Returns:
            Float in [-1.0, 1.0]. Returns 0.0 in mock mode or on error.
        """
        if self._model is None:
            return 0.0
        try:
            vecs = self.encode([a, b])
            return _cosine(vecs[0], vecs[1])
        except Exception as exc:
            logger.error("EmbeddingProvider similarity error: %s", exc)
            return 0.0

    def search(
        self,
        query: str,
        candidates: list[str],
        top_k: int = 5,
    ) -> list[tuple[int, float]]:
        """Semantic search: return top-k (index, score) pairs from candidates.

        Args:
            query:      The query text to search for.
            candidates: List of candidate strings to rank.
            top_k:      Number of top results to return.

        Returns:
            List of (candidate_index, similarity_score) sorted by score descending.
            Returns empty list when candidates is empty or in mock mode.
        """
        if not candidates:
            return []
        if self._model is None:
            return []
        try:
            all_texts = [query] + candidates
            vecs = self.encode(all_texts)
            q_vec = vecs[0]
            scores = [(idx, _cosine(q_vec, vecs[idx + 1])) for idx in range(len(candidates))]
            scores.sort(key=lambda x: x[1], reverse=True)
            return scores[:top_k]
        except Exception as exc:
            logger.error("EmbeddingProvider search error: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    # Health                                                               #
    # ------------------------------------------------------------------ #

    def health_check(self) -> dict:
        """Return health status dict compatible with OpenCastor driver/provider pattern."""
        return {
            "ok": self._mode == "hardware",
            "mode": self._mode,
            "model": self.model_name,
            "device": self.device,
        }


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two float vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ------------------------------------------------------------------ #
# Singleton factory                                                   #
# ------------------------------------------------------------------ #


def get_embedding_provider(
    model_name: Optional[str] = None,
    device: Optional[str] = None,
) -> EmbeddingProvider:
    """Return the module-level EmbeddingProvider singleton.

    On first call, initialises the provider with *model_name* and *device*
    (or their env-var defaults). Subsequent calls ignore those arguments and
    return the cached instance.
    """
    global _instance
    if _instance is None:
        _instance = EmbeddingProvider(model_name=model_name, device=device)
    return _instance
