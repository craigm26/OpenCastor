"""
castor/harness/rollback.py — Rollback point manager.

Snapshots robot state before any PHYSICAL_TOOL executes.  Stores snapshots in
the trajectories SQLite database (table ``rollback_snapshots``).  Exposes
``restore()`` to retrieve a snapshot so the calling bridge can re-apply it.

RCAN config::

    rollback:
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

__all__ = ["RollbackManager"]

_DEFAULT_DB = Path.home() / ".config" / "opencastor" / "trajectories.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS rollback_snapshots (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at  REAL NOT NULL,
    used_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_rb_run ON rollback_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_rb_ts  ON rollback_snapshots(created_at);
"""


class RollbackManager:
    """Manages pre-physical-action state snapshots.

    Args:
        db_path: Path to the trajectories SQLite database.
    """

    PHYSICAL_TOOLS: frozenset[str] = frozenset(
        {"move", "grip", "actuate", "rotate", "extend", "retract"}
    )

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Public API ────────────────────────────────────────────────────────────

    def capture(self, run_id: str, state: dict) -> str:
        """Snapshot current robot state before a physical command.

        Args:
            run_id: The current harness run ID.
            state: Arbitrary state dict (motor positions, joint angles, etc.).

        Returns:
            snapshot_id (UUID string).
        """
        snapshot_id = str(uuid.uuid4())
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO rollback_snapshots (id, run_id, snapshot_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (snapshot_id, run_id, json.dumps(state), now),
            )
        return snapshot_id

    def restore(self, snapshot_id: str) -> dict:
        """Retrieve a snapshot by ID.  Marks it as used.

        Returns:
            The snapshot dict.

        Raises:
            KeyError: If snapshot_id does not exist.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM rollback_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Snapshot {snapshot_id!r} not found")
            conn.execute(
                "UPDATE rollback_snapshots SET used_at = ? WHERE id = ?",
                (time.time(), snapshot_id),
            )
        return json.loads(row[0])

    def latest(self, run_id: str) -> Optional[dict]:
        """Return the most recent snapshot for a run, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, snapshot_json, created_at FROM rollback_snapshots "
                "WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "snapshot": json.loads(row[1]), "created_at": row[2]}

    def list_recent(self, limit: int = 10) -> list[dict]:
        """Return the most recent snapshots across all runs."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, run_id, created_at, used_at FROM rollback_snapshots "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"id": r[0], "run_id": r[1], "created_at": r[2], "used_at": r[3]} for r in rows]

    def is_physical_tool(self, tool_name: str) -> bool:
        """Return True if tool_name is in the PHYSICAL_TOOLS set."""
        return tool_name in self.PHYSICAL_TOOLS

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
