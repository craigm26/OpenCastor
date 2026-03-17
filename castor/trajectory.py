"""
castor/trajectory.py — Trajectory logger.

Records every AgentHarness run as a structured trajectory — the complete
log of instruction → context → tool calls → result.  This is the training
dataset for future model fine-tuning and the P66 audit trail.

"The Harness is the Dataset." — Phil Schmid

Storage:
  Primary:  SQLite at ~/.config/opencastor/trajectories.db
  Optional: Firestore sync (when bridge is active)

CLI (via castor.cli integration)::

    castor trajectory list           # recent 20 runs
    castor trajectory show <id>      # full record
    castor trajectory export         # JSONL for fine-tuning
    castor trajectory stats          # summary statistics

Usage::

    from castor.trajectory import TrajectoryLogger
    await TrajectoryLogger.log_async(ctx, result)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from castor.harness import HarnessContext, HarnessResult

logger = logging.getLogger("OpenCastor.Trajectory")

__all__ = ["TrajectoryLogger", "TrajectoryRecord"]

_DEFAULT_DB_PATH = Path.home() / ".config" / "opencastor" / "trajectories.db"
_SCHEMA_VERSION = 1

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trajectories (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    session_id      TEXT,
    robot_rrn       TEXT,
    instruction     TEXT,
    scope           TEXT,
    surface         TEXT,
    skill_triggered TEXT,
    context_tokens  INTEGER,
    was_compacted   INTEGER,
    tool_calls_json TEXT,
    final_response  TEXT,
    total_latency_ms REAL,
    primary_model   TEXT,
    secondary_model TEXT,
    secondary_verdict_json TEXT,
    drift_score     REAL,
    iterations      INTEGER,
    p66_consent_req INTEGER,
    p66_consent_ok  INTEGER,
    p66_blocked     INTEGER,
    p66_estop       INTEGER,
    error           TEXT,
    schema_version  INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_session ON trajectories(session_id);
CREATE INDEX IF NOT EXISTS idx_rrn     ON trajectories(robot_rrn);
CREATE INDEX IF NOT EXISTS idx_ts      ON trajectories(timestamp);
"""


@dataclass
class TrajectoryRecord:
    """Full record of a single harness run."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: _now_iso())
    session_id: str = ""
    robot_rrn: str = ""
    instruction: str = ""
    scope: str = "chat"
    surface: str = ""
    skill_triggered: Optional[str] = None
    context_tokens: int = 0
    was_compacted: bool = False
    tool_calls: list[dict] = field(default_factory=list)
    final_response: str = ""
    total_latency_ms: float = 0.0
    primary_model: str = ""
    secondary_model: Optional[str] = None
    secondary_verdict: Optional[dict] = None
    drift_score: Optional[float] = None
    iterations: int = 0
    p66_consent_required: bool = False
    p66_consent_granted: bool = False
    p66_blocked: bool = False
    p66_estop_bypassed: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), default=str)


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


class TrajectoryLogger:
    """Singleton-ish trajectory logger backed by SQLite.

    All public methods are class methods so callers don't need an instance.
    """

    _db_path: Path = _DEFAULT_DB_PATH
    _conn: Optional[sqlite3.Connection] = None
    _lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    @classmethod
    async def log_async(
        cls,
        ctx: HarnessContext,
        result: HarnessResult,
        robot_rrn: str = "",
        primary_model: str = "",
    ) -> None:
        """Fire-and-forget async logging. Never raises."""
        try:
            record = cls._build_record(ctx, result, robot_rrn, primary_model)
            await asyncio.to_thread(cls._write_record, record)
        except Exception as exc:
            logger.debug("Trajectory log error (non-fatal): %s", exc)

    @classmethod
    def log_sync(
        cls,
        ctx: HarnessContext,
        result: HarnessResult,
        robot_rrn: str = "",
        primary_model: str = "",
    ) -> None:
        """Synchronous logging fallback."""
        try:
            record = cls._build_record(ctx, result, robot_rrn, primary_model)
            cls._write_record(record)
        except Exception as exc:
            logger.debug("Trajectory log error (non-fatal): %s", exc)

    @classmethod
    def list_recent(cls, limit: int = 20) -> list[dict]:
        """Return the most recent N trajectory records as dicts."""
        try:
            conn = cls._get_conn()
            rows = conn.execute(
                "SELECT * FROM trajectories ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM trajectories LIMIT 0").description]
            return [dict(zip(cols, row, strict=False)) for row in rows]
        except Exception as exc:
            logger.warning("Trajectory list failed: %s", exc)
            return []

    @classmethod
    def get_record(cls, record_id: str) -> Optional[dict]:
        """Return a single trajectory record by ID."""
        try:
            conn = cls._get_conn()
            row = conn.execute(
                "SELECT * FROM trajectories WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                return None
            cols = [d[0] for d in conn.execute("SELECT * FROM trajectories LIMIT 0").description]
            return dict(zip(cols, row, strict=False))
        except Exception:
            return None

    @classmethod
    def export_jsonl(cls, limit: int = 10_000) -> str:
        """Export trajectory records as JSONL string for fine-tuning."""
        try:
            conn = cls._get_conn()
            rows = conn.execute(
                "SELECT * FROM trajectories ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM trajectories LIMIT 0").description]
            lines = []
            for row in rows:
                record = dict(zip(cols, row, strict=False))
                # Parse nested JSON fields back
                for json_field in ("tool_calls_json", "secondary_verdict_json"):
                    raw = record.pop(json_field, None)
                    key = json_field.replace("_json", "")
                    try:
                        record[key] = json.loads(raw) if raw else []
                    except Exception:
                        record[key] = []
                lines.append(json.dumps(record))
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("Export failed: %s", exc)
            return ""

    @classmethod
    def stats(cls) -> dict:
        """Return summary statistics about logged trajectories."""
        try:
            conn = cls._get_conn()
            total = conn.execute("SELECT COUNT(*) FROM trajectories").fetchone()[0]
            avg_lat = conn.execute(
                "SELECT AVG(total_latency_ms) FROM trajectories"
            ).fetchone()[0] or 0
            p66_events = conn.execute(
                "SELECT COUNT(*) FROM trajectories WHERE p66_consent_req=1 OR p66_blocked=1 OR p66_estop=1"
            ).fetchone()[0]
            errors = conn.execute(
                "SELECT COUNT(*) FROM trajectories WHERE error IS NOT NULL"
            ).fetchone()[0]
            return {
                "total_runs": total,
                "avg_latency_ms": round(avg_lat, 1),
                "p66_events": p66_events,
                "errors": errors,
            }
        except Exception:
            return {}

    # ── Internals ─────────────────────────────────────────────────────────────

    @classmethod
    def _build_record(
        cls,
        ctx: HarnessContext,
        result: HarnessResult,
        robot_rrn: str,
        primary_model: str,
    ) -> TrajectoryRecord:
        """Build a TrajectoryRecord from HarnessContext + HarnessResult."""
        # Try to get robot RRN from shared state if not provided
        if not robot_rrn:
            try:
                from castor.main import get_shared_fs
                fs = get_shared_fs()
                if fs:
                    robot_rrn = getattr(fs, "rrn", "") or ""
            except Exception:
                pass

        # Try to get model name
        if not primary_model:
            try:
                from castor.main import get_shared_brain
                brain = get_shared_brain()
                if brain:
                    primary_model = getattr(brain, "model_name", "") or ""
            except Exception:
                pass

        tool_calls_serialized = [
            {
                "tool": tc.tool_name,
                "args": tc.args,
                "result": _safe_str(tc.result),
                "latency_ms": tc.latency_ms,
                "p66_consent_required": tc.p66_consent_required,
                "p66_consent_granted": tc.p66_consent_granted,
                "p66_blocked": tc.p66_blocked,
                "error": tc.error,
            }
            for tc in result.tools_called
        ]

        return TrajectoryRecord(
            id=result.run_id,
            session_id=ctx.session_id,
            robot_rrn=robot_rrn,
            instruction=ctx.instruction[:1000],          # cap at 1KB
            scope=ctx.scope,
            surface=ctx.surface,
            skill_triggered=result.skill_triggered,
            context_tokens=result.context_tokens,
            was_compacted=result.was_compacted,
            tool_calls=tool_calls_serialized,
            final_response=result.thought.raw_text[:2000],  # cap at 2KB
            total_latency_ms=result.total_latency_ms,
            primary_model=primary_model,
            iterations=result.iterations,
            p66_consent_required=result.p66_consent_required,
            p66_consent_granted=result.p66_consent_granted,
            p66_blocked=result.p66_blocked,
            p66_estop_bypassed=result.p66_estop_bypassed,
            error=result.error,
        )

    @classmethod
    def _write_record(cls, record: TrajectoryRecord) -> None:
        """Write a record to SQLite (synchronous)."""
        conn = cls._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO trajectories (
                id, timestamp, session_id, robot_rrn, instruction, scope, surface,
                skill_triggered, context_tokens, was_compacted, tool_calls_json,
                final_response, total_latency_ms, primary_model, secondary_model,
                secondary_verdict_json, drift_score, iterations,
                p66_consent_req, p66_consent_ok, p66_blocked, p66_estop, error,
                schema_version
            ) VALUES (
                :id, :timestamp, :session_id, :robot_rrn, :instruction, :scope, :surface,
                :skill_triggered, :context_tokens, :was_compacted, :tool_calls_json,
                :final_response, :total_latency_ms, :primary_model, :secondary_model,
                :secondary_verdict_json, :drift_score, :iterations,
                :p66_consent_req, :p66_consent_ok, :p66_blocked, :p66_estop, :error,
                :schema_version
            )
            """,
            {
                "id": record.id,
                "timestamp": record.timestamp,
                "session_id": record.session_id,
                "robot_rrn": record.robot_rrn,
                "instruction": record.instruction,
                "scope": record.scope,
                "surface": record.surface,
                "skill_triggered": record.skill_triggered,
                "context_tokens": record.context_tokens,
                "was_compacted": int(record.was_compacted),
                "tool_calls_json": json.dumps(record.tool_calls),
                "final_response": record.final_response,
                "total_latency_ms": record.total_latency_ms,
                "primary_model": record.primary_model,
                "secondary_model": record.secondary_model,
                "secondary_verdict_json": json.dumps(record.secondary_verdict) if record.secondary_verdict else None,
                "drift_score": record.drift_score,
                "iterations": record.iterations,
                "p66_consent_req": int(record.p66_consent_required),
                "p66_consent_ok": int(record.p66_consent_granted),
                "p66_blocked": int(record.p66_blocked),
                "p66_estop": int(record.p66_estop_bypassed),
                "error": record.error,
                "schema_version": _SCHEMA_VERSION,
            },
        )
        conn.commit()

    @classmethod
    def _get_conn(cls) -> sqlite3.Connection:
        """Return (or create) the SQLite connection."""
        if cls._conn is None:
            cls._db_path.parent.mkdir(parents=True, exist_ok=True)
            cls._conn = sqlite3.connect(str(cls._db_path), check_same_thread=False)
            cls._conn.executescript(_CREATE_TABLE_SQL)
            cls._conn.commit()
            logger.debug("Trajectory DB opened: %s", cls._db_path)
        return cls._conn

    @classmethod
    def set_db_path(cls, path: Path) -> None:
        """Override default DB path (useful for testing)."""
        cls._db_path = path
        cls._conn = None   # force reconnect


def _safe_str(val: Any) -> str:
    """Convert any value to a safe string representation."""
    if val is None:
        return ""
    try:
        return str(val)[:500]
    except Exception:
        return "<unserializable>"
