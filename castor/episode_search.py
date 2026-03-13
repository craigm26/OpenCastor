"""Episode similarity search for OpenCastor (issue #144).

TF-IDF cosine similarity search over episode instruction text.
No external dependencies — pure stdlib.

Usage::

    from castor.episode_search import EpisodeSimilaritySearch, get_searcher

    searcher = get_searcher()
    results = searcher.search("go forward and turn left", limit=5)

REST API:
    GET /api/memory/search?q=<query>&limit=10
"""

import logging
import math
import re
from collections import Counter
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.EpisodeSearch")


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace."""
    return re.findall(r"[a-z0-9]+", text.lower())


class EpisodeSimilaritySearch:
    """TF-IDF cosine similarity search over EpisodeMemory records.

    Args:
        memory: An EpisodeMemory instance (or None to create one lazily).
        max_index_size: Maximum number of episodes to keep in the index.
    """

    def __init__(self, memory: Any = None, max_index_size: int = 10_000):
        if memory is None:
            from castor.memory import EpisodeMemory

            self._mem = EpisodeMemory()
        else:
            self._mem = memory
        self._max_index_size = max_index_size
        self._index: list[dict[str, Any]] = []
        self._idf: dict[str, float] = {}
        self._built: bool = False

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _build(self) -> None:
        """Build (or rebuild) the TF-IDF index from episode memory."""
        episodes = self._mem.query_recent(limit=self._max_index_size)
        if not episodes:
            self._index = []
            self._idf = {}
            self._built = True
            return

        docs: list[list[str]] = []
        for ep in episodes:
            docs.append(_tokenize(ep.get("instruction", "") or ""))

        # IDF: log((N+1) / (df+1)) + 1  (smooth)
        n = len(docs)
        df: Counter = Counter()
        for tokens in docs:
            df.update(set(tokens))
        self._idf = {term: math.log((n + 1) / (df[term] + 1)) + 1.0 for term in df}

        self._index = []
        for ep, tokens in zip(episodes, docs, strict=False):
            tf = Counter(tokens)
            vec: dict[str, float] = {}
            for term, count in tf.items():
                tfidf = (count / max(len(tokens), 1)) * self._idf.get(term, 0.0)
                if tfidf > 0:
                    vec[term] = tfidf
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self._index.append(
                {
                    "id": ep.get("id"),
                    "instruction": ep.get("instruction", ""),
                    "vec": {t: v / norm for t, v in vec.items()},
                    "episode": ep,
                }
            )

        self._built = True
        logger.debug("Episode search index built: %d episodes", len(self._index))

    def invalidate(self) -> None:
        """Force index rebuild on next search call."""
        self._built = False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.01,
    ) -> list[dict[str, Any]]:
        """Search episodes by instruction similarity.

        Args:
            query: Free-text search query.
            limit: Maximum results to return.
            min_score: Minimum cosine similarity score (0–1).

        Returns:
            List of episode dicts with an added ``score`` field,
            sorted by descending relevance.
        """
        if not self._built:
            self._build()

        if not self._index:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        qtf = Counter(query_tokens)
        qvec: dict[str, float] = {}
        for term, count in qtf.items():
            tfidf = (count / len(query_tokens)) * self._idf.get(term, 0.0)
            if tfidf > 0:
                qvec[term] = tfidf
        if not qvec:
            return self._keyword_fallback(query_tokens, limit)

        qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1.0
        qvec = {t: v / qnorm for t, v in qvec.items()}

        scored: list[tuple] = []
        for entry in self._index:
            score = sum(qvec[t] * entry["vec"].get(t, 0.0) for t in qvec if t in entry["vec"])
            if score >= min_score:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, entry in scored[:limit]:
            ep = dict(entry["episode"])
            ep["score"] = round(score, 4)
            results.append(ep)

        return results

    def _keyword_fallback(self, tokens: list[str], limit: int) -> list[dict[str, Any]]:
        """Simple keyword matching when all query terms are out-of-vocabulary."""
        query_set = set(tokens)
        scored = []
        for entry in self._index:
            doc_tokens = set(_tokenize(entry["instruction"]))
            overlap = len(query_set & doc_tokens)
            if overlap:
                ep = dict(entry["episode"])
                ep["score"] = round(overlap / max(len(query_set), 1), 4)
                scored.append(ep)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def stats(self) -> dict[str, Any]:
        """Return index statistics."""
        if not self._built:
            self._build()
        return {
            "indexed_episodes": len(self._index),
            "vocabulary_size": len(self._idf),
            "built": self._built,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_searcher: Optional[EpisodeSimilaritySearch] = None


def get_searcher(memory: Any = None) -> EpisodeSimilaritySearch:
    """Return the process-wide EpisodeSimilaritySearch singleton."""
    global _searcher
    if _searcher is None:
        _searcher = EpisodeSimilaritySearch(memory=memory)
    return _searcher
