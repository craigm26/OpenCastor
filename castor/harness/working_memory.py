"""
castor/harness/working_memory.py — Ephemeral per-run scratchpad.

Lets multi-step skills share state within a single AgentHarness.run() call
without polluting the trajectory log or the agent context window.

Cleared at the start of every run.  Optionally snapshotted into the
trajectory log at the end of the run.

RCAN config::

    working_memory:
      enabled: true
      max_keys: 50               # prevent unbounded growth
      log_to_trajectory: true    # snapshot at end of run
"""

from __future__ import annotations

import copy
import json
from typing import Any

__all__ = ["WorkingMemory"]


class WorkingMemory:
    """Ephemeral key/value store for a single harness run.

    Args:
        max_keys: Maximum number of keys before ``set`` raises ``MemoryError``.
    """

    def __init__(self, max_keys: int = 50) -> None:
        self._max_keys = max_keys
        self._store: dict[str, Any] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``.

        Raises:
            MemoryError: If ``max_keys`` would be exceeded.
        """
        if key not in self._store and len(self._store) >= self._max_keys:
            raise MemoryError(
                f"WorkingMemory: max_keys ({self._max_keys}) reached. "
                "Delete a key before adding a new one."
            )
        self._store[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Return value for ``key``, or ``default`` if not found."""
        return self._store.get(key, default)

    def delete(self, key: str) -> None:
        """Remove ``key`` from the store.  No-op if not present."""
        self._store.pop(key, None)

    def all(self) -> dict[str, Any]:
        """Return a shallow copy of the entire store."""
        return dict(self._store)

    def clear(self) -> None:
        """Clear all keys (called at the start of each run)."""
        self._store.clear()

    def snapshot(self) -> dict:
        """Return a deep-copy snapshot for trajectory logging."""
        return copy.deepcopy(self._store)

    # ── Convenience ───────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        return key in self._store

    def to_json(self) -> str:
        """Serialise the store to a JSON string (best-effort)."""
        try:
            return json.dumps(self._store)
        except (TypeError, ValueError):
            # Non-serialisable values → stringify
            return json.dumps({k: str(v) for k, v in self._store.items()})
