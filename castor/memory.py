"""
castor/memory.py — Persistent episode memory store (SQLite).

Logs every brain decision (thought, action, latency, image hash, timestamp)
to a local SQLite database so operators can inspect history and the learner
can query past context.

Usage::

    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()  # defaults to ~/.castor/memory.db
    mem.log_episode(
        instruction="move forward",
        raw_thought='{"type":"move","linear":0.5}',
        action={"type": "move", "linear": 0.5},
        latency_ms=320.5,
        image_hash="abc123",
        outcome="ok",
        source="api",
    )
    episodes = mem.query_recent(limit=10)
    mem.export_jsonl("/tmp/episodes.jsonl")
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Memory")

_DEFAULT_DB_DIR = Path.home() / ".castor"
_DEFAULT_DB_NAME = "memory.db"


class EpisodeMemory:
    """SQLite-backed episode memory store.

    Each *episode* is a single brain decision:
      instruction → thought → action → outcome.

    Thread-safe via ``check_same_thread=False`` and per-call connections.

    Args:
        db_path:      Full path to the SQLite database file.  Defaults to
                      ``~/.castor/memory.db`` (overridden by
                      ``CASTOR_MEMORY_DB`` env var or constructor argument).
        max_episodes: Automatically evict oldest episodes when the store
                      exceeds this count (FIFO).  ``0`` means unlimited.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        max_episodes: int = 10_000,
    ):
        env_path = os.getenv("CASTOR_MEMORY_DB")
        if db_path is None and env_path:
            db_path = env_path
        elif db_path is None:
            _DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
            db_path = str(_DEFAULT_DB_DIR / _DEFAULT_DB_NAME)

        self.db_path = db_path
        self.max_episodes = max_episodes
        self._init_db()

    # ── Internal ──────────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        """Yield a connected, auto-committing SQLite connection."""
        con = sqlite3.connect(self.db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        """Create the episodes table if it does not exist."""
        ddl = """
        CREATE TABLE IF NOT EXISTS episodes (
            id           TEXT PRIMARY KEY,
            ts           REAL NOT NULL,
            instruction  TEXT,
            raw_thought  TEXT,
            action_json  TEXT,
            latency_ms   REAL,
            image_hash   TEXT,
            outcome      TEXT,
            source       TEXT DEFAULT 'loop'
        );
        CREATE INDEX IF NOT EXISTS idx_ts ON episodes (ts DESC);
        """
        with self._conn() as con:
            con.executescript(ddl)

    # ── Write ─────────────────────────────────────────────────────────────────

    def log_episode(
        self,
        instruction: str = "",
        raw_thought: str = "",
        action: Optional[Dict] = None,
        latency_ms: float = 0.0,
        image_hash: str = "",
        outcome: str = "ok",
        source: str = "loop",
    ) -> str:
        """Insert a new episode.  Returns the generated episode UUID."""
        ep_id = str(uuid.uuid4())
        action_json = json.dumps(action) if action else None
        ts = time.time()
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO episodes
                    (id, ts, instruction, raw_thought, action_json,
                     latency_ms, image_hash, outcome, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ep_id, ts, instruction[:512], raw_thought[:2048],
                 action_json, latency_ms, image_hash, outcome, source),
            )
        self._evict_if_needed()
        return ep_id

    def _evict_if_needed(self) -> None:
        """Delete oldest episodes when the store exceeds max_episodes."""
        if self.max_episodes <= 0:
            return
        with self._conn() as con:
            count = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            if count > self.max_episodes:
                excess = count - self.max_episodes
                con.execute(
                    """
                    DELETE FROM episodes WHERE id IN (
                        SELECT id FROM episodes ORDER BY ts ASC LIMIT ?
                    )
                    """,
                    (excess,),
                )

    # ── Read ──────────────────────────────────────────────────────────────────

    def query_recent(
        self,
        limit: int = 20,
        action_type: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict]:
        """Return the most recent episodes as a list of dicts.

        Args:
            limit:       Max number of records to return (capped at 500).
            action_type: If set, filter by ``action.type`` (e.g. ``"move"``).
            source:      If set, filter by origin (``"loop"``, ``"api"``,
                         ``"whatsapp"``, etc.).
        """
        limit = min(max(1, limit), 500)
        where_clauses = []
        params: list = []

        if action_type:
            where_clauses.append("action_json LIKE ?")
            params.append(f'%"type": "{action_type}"%')

        if source:
            where_clauses.append("source = ?")
            params.append(source)

        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        params.append(limit)

        with self._conn() as con:
            rows = con.execute(
                f"SELECT * FROM episodes {where} ORDER BY ts DESC LIMIT ?",
                params,
            ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def get_episode(self, ep_id: str) -> Optional[Dict]:
        """Return a single episode by ID, or None if not found."""
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM episodes WHERE id = ?", (ep_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def count(self) -> int:
        """Return the total number of stored episodes."""
        with self._conn() as con:
            return con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    # ── Export ────────────────────────────────────────────────────────────────

    def export_jsonl(self, path: str, limit: int = 0) -> int:
        """Write episodes to a JSON-Lines file.

        Args:
            path:  Output file path.
            limit: Max episodes to export; 0 means all.

        Returns:
            Number of episodes written.
        """
        sql = "SELECT * FROM episodes ORDER BY ts ASC"
        params: tuple = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)

        written = 0
        with self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(self._row_to_dict(row)) + "\n")
                written += 1
        return written

    # ── Admin ─────────────────────────────────────────────────────────────────

    def clear(self) -> int:
        """Delete ALL episodes.  Returns count deleted."""
        with self._conn() as con:
            n = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            con.execute("DELETE FROM episodes")
        return n

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        if d.get("action_json"):
            try:
                d["action"] = json.loads(d["action_json"])
            except Exception:
                d["action"] = None
        else:
            d["action"] = None
        del d["action_json"]
        return d

    @staticmethod
    def hash_image(image_bytes: bytes) -> str:
        """Return a short SHA-256 hex digest for a camera frame."""
        if not image_bytes:
            return ""
        return hashlib.sha256(image_bytes).hexdigest()[:16]
