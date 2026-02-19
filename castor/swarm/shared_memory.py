"""SharedMemory — cross-robot shared knowledge store with JSON persistence."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MemoryEntry:
    """A single entry in the shared memory store."""

    key: str
    value: Any
    robot_id: str  # which robot wrote it
    timestamp: float
    ttl_s: float | None  # None = permanent

    def is_expired(self) -> bool:
        if self.ttl_s is None:
            return False
        return (time.time() - self.timestamp) > self.ttl_s

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "robot_id": self.robot_id,
            "timestamp": self.timestamp,
            "ttl_s": self.ttl_s,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MemoryEntry:
        return cls(
            key=d["key"],
            value=d["value"],
            robot_id=d["robot_id"],
            timestamp=float(d["timestamp"]),
            ttl_s=d.get("ttl_s"),
        )


class SharedMemory:
    """Cross-robot shared knowledge store.

    Persists to a JSON file and can merge remote snapshots via simple rules.
    """

    def __init__(self, robot_id: str, persist_path: str | None = None) -> None:
        self.robot_id = robot_id
        if persist_path is None:
            persist_path = str(Path.home() / ".opencastor" / "swarm_memory.json")
        self._path = persist_path
        self._store: dict[str, MemoryEntry] = {}

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def put(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        """Store a value under key, associated with this robot."""
        self._store[key] = MemoryEntry(
            key=key,
            value=value,
            robot_id=self.robot_id,
            timestamp=time.time(),
            ttl_s=ttl_s,
        )

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for key, or default if missing/expired."""
        entry = self._store.get(key)
        if entry is None:
            return default
        if entry.is_expired():
            del self._store[key]
            return default
        return entry.value

    def delete(self, key: str) -> bool:
        """Remove key. Returns True if key existed."""
        if key in self._store:
            del self._store[key]
            return True
        return False

    def keys(self) -> list[str]:
        """Return all live (non-expired) keys."""
        self.expire_stale()
        return list(self._store.keys())

    def expire_stale(self) -> int:
        """Remove expired entries. Returns count removed."""
        expired = [k for k, v in self._store.items() if v.is_expired()]
        for k in expired:
            del self._store[k]
        return len(expired)

    def snapshot(self) -> dict:
        """Return all live entries as {key: MemoryEntry}."""
        self.expire_stale()
        return dict(self._store)

    def merge(self, remote_snapshot: dict) -> int:
        """Merge remote entries into local store.

        remote_snapshot may contain MemoryEntry objects or dicts.
        Merge rule: keep entry with the latest timestamp.
        Returns count of entries merged (updated or added).
        """
        merged = 0
        for key, entry in remote_snapshot.items():
            if isinstance(entry, dict):
                entry = MemoryEntry.from_dict(entry)
            if entry.is_expired():
                continue
            local = self._store.get(key)
            if local is None or entry.timestamp > local.timestamp:
                self._store[key] = entry
                merged += 1
        return merged

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist current store to JSON file."""
        path = Path(self._path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in self._store.items()}
        path.write_text(json.dumps(data, indent=2))

    def load(self) -> None:
        """Load store from JSON file (if it exists)."""
        path = Path(self._path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._store = {k: MemoryEntry.from_dict(v) for k, v in data.items()}
        except (json.JSONDecodeError, KeyError):
            # Corrupt file — start fresh
            self._store = {}
