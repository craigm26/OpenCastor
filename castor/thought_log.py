"""
OpenCastor Thought Log — F4.

In-memory deque (capped at *max_memory* entries) with optional JSONL
file persistence.  Provides ``record()`` and ``get()`` for
thought-level observability.

The ``reasoning`` field is omitted by default; callers must explicitly
request it (e.g. via ``include_reasoning=True`` for config-scope JWT).
"""

import json
import logging
import time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.ThoughtLog")


class ThoughtLog:
    """Record and retrieve AI Thought objects for observability."""

    def __init__(self, max_memory: int = 1000, storage_path: Optional[str] = None):
        self._store: deque = deque(maxlen=max_memory)
        self._index: dict[str, int] = {}  # thought_id -> deque position (approximate)
        self._storage_path = storage_path

        if storage_path:
            try:
                import os

                os.makedirs(
                    os.path.dirname(storage_path) if os.path.dirname(storage_path) else ".",
                    exist_ok=True,
                )
            except Exception as exc:
                logger.debug("ThoughtLog storage dir error (non-fatal): %s", exc)

    def record(self, thought: Any, context_snapshot: Optional[dict] = None) -> None:
        """Persist a Thought to the in-memory store (and optionally JSONL file).

        Args:
            thought:          A :class:`~castor.providers.base.Thought` instance.
            context_snapshot: Optional extra context dict to store alongside.
        """
        entry = {
            "id": getattr(thought, "id", None),
            "timestamp_ms": int(time.time() * 1000),
            "provider": getattr(thought, "provider", ""),
            "model": getattr(thought, "model", ""),
            "model_version": getattr(thought, "model_version", None),
            "layer": getattr(thought, "layer", "fast"),
            "instruction": None,  # not stored by default (privacy)
            "action": getattr(thought, "action", None),
            "confidence": getattr(thought, "confidence", None),
            "escalated": getattr(thought, "escalated", False),
            "reasoning": getattr(thought, "raw_text", None),
        }
        if context_snapshot:
            entry["context"] = context_snapshot

        self._store.append(entry)

        if self._storage_path:
            try:
                with open(self._storage_path, "a") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
            except Exception as exc:
                logger.debug("ThoughtLog JSONL write failed (non-fatal): %s", exc)

    def get(self, thought_id: str, include_reasoning: bool = False) -> Optional[dict]:
        """Retrieve a thought by ID.

        Args:
            thought_id:        UUID of the thought.
            include_reasoning: If False (default), omit the ``reasoning`` field.

        Returns:
            A copy of the stored entry dict, or None if not found.
        """
        for entry in self._store:
            if entry.get("id") == thought_id:
                result = dict(entry)
                if not include_reasoning:
                    result.pop("reasoning", None)
                return result
        return None

    def list_recent(self, limit: int = 20) -> list:
        """Return the most recent *limit* thought entries (newest last)."""
        entries = list(self._store)
        return entries[-limit:]
