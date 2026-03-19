"""
castor/harness/dlq.py — Dead Letter Queue for failed/expired commands.

Failed commands land here instead of being silently dropped.  Operators can
review via ``GET /api/dlq`` and clear entries via ``POST /api/dlq/{id}/review``.

Storage: SQLite table ``dead_letters`` in the trajectories database.

RCAN config::

    dlq:
      enabled: true
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

__all__ = ["DeadLetterQueue"]

_DEFAULT_DB = Path.home() / ".config" / "opencastor" / "trajectories.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS dead_letters (
    id          TEXT PRIMARY KEY,
    command_id  TEXT NOT NULL,
    instruction TEXT NOT NULL,
    scope       TEXT NOT NULL,
    error       TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    reviewed    INTEGER NOT NULL DEFAULT 0,
    reviewed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_dlq_reviewed  ON dead_letters(reviewed);
CREATE INDEX IF NOT EXISTS idx_dlq_created   ON dead_letters(created_at);
"""


class DeadLetterQueue:
    """Holds failed/expired commands for operator review.

    Args:
        db_path: Path to the trajectories SQLite database.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Public API ────────────────────────────────────────────────────────────

    def push(
        self,
        command_id: str,
        instruction: str,
        scope: str,
        error: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Push a failed command into the DLQ."""
        dlq_id = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO dead_letters "
                "(id, command_id, instruction, scope, error, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    dlq_id,
                    command_id,
                    instruction,
                    scope,
                    error,
                    json.dumps(metadata or {}),
                    time.time(),
                ),
            )

    def list_pending(self, limit: int = 20) -> list[dict]:
        """Return unreviewed dead letters, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, command_id, instruction, scope, error, metadata, created_at "
                "FROM dead_letters WHERE reviewed = 0 "
                "ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "command_id": r[1],
                "instruction": r[2],
                "scope": r[3],
                "error": r[4],
                "metadata": json.loads(r[5]),
                "created_at": r[6],
            }
            for r in rows
        ]

    def mark_reviewed(self, dlq_id: str) -> None:
        """Mark a dead letter as reviewed (acknowledged by an operator)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE dead_letters SET reviewed = 1, reviewed_at = ? WHERE id = ?",
                (time.time(), dlq_id),
            )

    def count_pending(self) -> int:
        """Return the number of unreviewed dead letters."""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM dead_letters WHERE reviewed = 0").fetchone()
        return row[0] if row else 0

    def purge_old(self, older_than_days: int = 7) -> int:
        """Delete dead letters older than ``older_than_days`` days.

        Returns:
            Number of rows deleted.
        """
        cutoff = time.time() - older_than_days * 86400
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM dead_letters WHERE created_at < ?", (cutoff,))
        return cur.rowcount

    # ── Internal ──────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_CREATE_TABLE)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
