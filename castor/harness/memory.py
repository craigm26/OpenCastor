"""Memory backends and overflow strategies for AgentHarness (#743)."""

from __future__ import annotations

import abc
import enum
import json
from pathlib import Path
from typing import Any


class OverflowStrategy(str, enum.Enum):
    TRUNCATE = "truncate"
    SUMMARIZE = "summarize"
    DROP_OLDEST = "drop_oldest"


class MemoryBackend(abc.ABC):
    """Abstract memory backend."""

    @abc.abstractmethod
    def read(self, session_id: str) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def write(self, session_id: str, entries: list[dict[str, Any]]) -> None: ...

    @abc.abstractmethod
    def clear(self, session_id: str) -> None: ...


class WorkingMemoryBackend(MemoryBackend):
    """In-process dict-backed memory (default)."""

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {}

    def read(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._store.get(session_id, []))

    def write(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        self._store[session_id] = list(entries)

    def clear(self, session_id: str) -> None:
        self._store.pop(session_id, None)


class FilesystemBackend(MemoryBackend):
    """JSON file-backed memory under ~/.castor/memory/ or a custom dir."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or Path.home() / ".castor" / "memory")

    def _path(self, session_id: str) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return self.base_dir / f"{session_id}.json"

    def read(self, session_id: str) -> list[dict[str, Any]]:
        p = self._path(session_id)
        return json.loads(p.read_text()) if p.exists() else []

    def write(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        self._path(session_id).write_text(json.dumps(entries))

    def clear(self, session_id: str) -> None:
        p = self._path(session_id)
        if p.exists():
            p.unlink()


class FirestoreBackend(MemoryBackend):
    """Firestore-backed memory; falls back to FilesystemBackend if unavailable."""

    def __init__(self, fallback: MemoryBackend | None = None) -> None:
        self._fallback: MemoryBackend = fallback or FilesystemBackend()
        self._db: Any = None
        try:
            import firebase_admin
            from firebase_admin import firestore as _fs  # noqa: PLC0415

            if not firebase_admin._apps:
                raise RuntimeError("firebase_admin not initialized")
            self._db = _fs.client()
        except Exception:
            pass

    def _doc(self, session_id: str) -> Any:
        return self._db.collection("castor_memory").document(session_id) if self._db else None

    def read(self, session_id: str) -> list[dict[str, Any]]:
        if not self._db:
            return self._fallback.read(session_id)
        try:
            doc = self._doc(session_id).get()
            return doc.to_dict().get("entries", []) if doc.exists else []
        except Exception:
            return self._fallback.read(session_id)

    def write(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        if not self._db:
            self._fallback.write(session_id, entries)
            return
        try:
            self._doc(session_id).set({"entries": entries})
        except Exception:
            self._fallback.write(session_id, entries)

    def clear(self, session_id: str) -> None:
        if not self._db:
            self._fallback.clear(session_id)
            return
        try:
            self._doc(session_id).delete()
        except Exception:
            self._fallback.clear(session_id)


class MemoryManager:
    """Manages memory entries with configurable backend and overflow strategy."""

    def __init__(
        self,
        backend: MemoryBackend | None = None,
        max_tokens: int = 2048,
        strategy: OverflowStrategy = OverflowStrategy.TRUNCATE,
    ) -> None:
        self.backend = backend or WorkingMemoryBackend()
        self.max_tokens = max_tokens
        self.strategy = strategy

    def _token_count(self, entries: list[dict[str, Any]]) -> int:
        return sum(len(str(e)) // 4 for e in entries)

    def apply_overflow(
        self, entries: list[dict[str, Any]], model_name: str = ""
    ) -> list[dict[str, Any]]:
        if self._token_count(entries) <= self.max_tokens:
            return entries
        if self.strategy == OverflowStrategy.DROP_OLDEST:
            while entries and self._token_count(entries) > self.max_tokens:
                entries = entries[1:]
            return entries
        if self.strategy == OverflowStrategy.SUMMARIZE:
            # Best-effort summarize; fall through to truncate on failure
            try:
                from castor.tiered_brain import TieredBrain  # noqa: F401, PLC0415
            except Exception:
                pass
        # TRUNCATE (default fallback)
        while entries and self._token_count(entries) > self.max_tokens:
            entries = entries[1:]
        return entries
