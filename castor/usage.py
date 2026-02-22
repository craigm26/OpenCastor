"""
castor/usage.py — Provider token usage and cost tracking.

Logs every think() call's token counts and estimated cost to SQLite.
Provides session and daily aggregates for the dashboard and /api/usage endpoint.

Usage::
    from castor.usage import UsageTracker
    tracker = UsageTracker()
    tracker.log_usage("google", "gemini-2.0-flash", prompt_tokens=150, completion_tokens=80)
    print(tracker.get_session_totals())
    print(tracker.get_daily_totals())
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("OpenCastor.Usage")

# ── Default paths ──────────────────────────────────────────────────────────────

_DEFAULT_DB_DIR = Path.home() / ".castor"
_DEFAULT_DB_NAME = "usage.db"

# ── Session ID (resets on each process restart) ────────────────────────────────

_SESSION_ID: str = str(uuid.uuid4())

# ── Cost table (USD per 1k tokens, input/output) ──────────────────────────────
# Per-1M token prices from provider pricing pages; divided by 1000 → per-1k.
# Free-tier / local models carry zero cost.

_COST_TABLE: Dict[str, Dict[str, tuple]] = {
    "google": {
        "gemini-2.0-flash": (0.0, 0.0),
        "gemini-2.5-flash-lite": (0.0, 0.0),
        "default": (0.075, 0.30),  # gemini-1.5-pro-level fallback / 1M → /1k
    },
    "openai": {
        "gpt-4.1-mini": (0.0004, 0.0016),
        "gpt-4.1": (0.002, 0.008),
        "default": (0.002, 0.008),
    },
    "anthropic": {
        "claude-haiku-4-5": (0.0008, 0.004),
        "claude-sonnet-4-6": (0.003, 0.015),
        "claude-opus-4-6": (0.015, 0.075),
        "default": (0.003, 0.015),
    },
    "vertex_ai": {
        "default": (0.075, 0.30),
    },
    "huggingface": {
        "default": (0.0, 0.0),
    },
    "ollama": {
        "default": (0.0, 0.0),
    },
    "llamacpp": {
        "default": (0.0, 0.0),
    },
    "mlx": {
        "default": (0.0, 0.0),
    },
}

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS usage (
    id               TEXT    PRIMARY KEY,
    ts               REAL    NOT NULL,
    session_id       TEXT    NOT NULL,
    provider         TEXT    NOT NULL,
    model            TEXT    NOT NULL,
    prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL    NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts          ON usage(ts);
CREATE INDEX IF NOT EXISTS idx_usage_session     ON usage(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_provider    ON usage(provider);
"""


class UsageTracker:
    """SQLite-backed provider token usage and cost tracker.

    Each :py:meth:`log_usage` call inserts one row for a single LLM call.
    Aggregates are computed via SQL on query.

    Thread-safe: each public method opens and closes its own connection.

    Args:
        db_path: Path to the SQLite database.  Defaults to ``~/.castor/usage.db``
                 (overridden by the ``CASTOR_USAGE_DB`` env var or constructor arg).
    """

    def __init__(self, db_path: Optional[str] = None):
        env_path = os.getenv("CASTOR_USAGE_DB")
        if db_path is None and env_path:
            db_path = env_path
        elif db_path is None:
            _DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
            db_path = str(_DEFAULT_DB_DIR / _DEFAULT_DB_NAME)

        self.db_path = db_path
        self._session_id = _SESSION_ID
        self._init_db()

    # ── Internal ──────────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        """Yield a SQLite connection that is committed (or rolled-back) on exit."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create schema if it does not already exist."""
        try:
            with self._conn() as conn:
                conn.executescript(_DDL)
        except Exception as exc:
            logger.warning("UsageTracker: could not initialise DB at %s: %s", self.db_path, exc)

    # ── Cost estimation ────────────────────────────────────────────────────────

    def _estimate_cost(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Return estimated cost in USD based on the internal cost table.

        Falls back to the provider's ``"default"`` entry, then 0.0.
        """
        provider_lower = provider.lower()
        model_lower = model.lower()

        provider_table = _COST_TABLE.get(provider_lower, {})

        # Try exact model match first, then strip variant suffixes, then "default"
        price_in, price_out = (
            provider_table.get(model_lower)
            or provider_table.get(model_lower.split(":")[0])
            or provider_table.get("default")
            or (0.0, 0.0)
        )

        cost = (prompt_tokens / 1000.0) * price_in + (completion_tokens / 1000.0) * price_out
        return round(cost, 8)

    # ── Public API ─────────────────────────────────────────────────────────────

    def log_usage(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: Optional[float] = None,
    ) -> None:
        """Record one LLM call's token usage (and optional cost override).

        If ``cost_usd`` is ``None``, the cost is estimated from the internal
        cost table via :py:meth:`_estimate_cost`.

        Args:
            provider:          Provider name (e.g. ``"google"``, ``"openai"``).
            model:             Model name (e.g. ``"gemini-2.0-flash"``).
            prompt_tokens:     Number of input / prompt tokens consumed.
            completion_tokens: Number of output / completion tokens generated.
            cost_usd:          Optional explicit cost override in USD.
        """
        if cost_usd is None:
            cost_usd = self._estimate_cost(provider, model, prompt_tokens, completion_tokens)

        row_id = str(uuid.uuid4())
        ts = time.time()

        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO usage (id, ts, session_id, provider, model,
                                       prompt_tokens, completion_tokens, cost_usd)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        ts,
                        self._session_id,
                        provider.lower(),
                        model,
                        int(prompt_tokens),
                        int(completion_tokens),
                        float(cost_usd),
                    ),
                )
        except Exception as exc:
            logger.debug("UsageTracker.log_usage failed: %s", exc)

    def get_session_totals(self) -> Dict[str, Dict]:
        """Return per-provider token and cost totals for the current session.

        Returns:
            Dict mapping ``provider`` → ``{tokens_in, tokens_out, total_tokens, cost_usd, calls}``
        """
        result: Dict[str, Dict] = {}
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    """
                    SELECT provider,
                           SUM(prompt_tokens)    AS tokens_in,
                           SUM(completion_tokens) AS tokens_out,
                           SUM(prompt_tokens + completion_tokens) AS total_tokens,
                           SUM(cost_usd)          AS cost_usd,
                           COUNT(*)               AS calls
                    FROM   usage
                    WHERE  session_id = ?
                    GROUP  BY provider
                    """,
                    (self._session_id,),
                ).fetchall()
                for row in rows:
                    result[row["provider"]] = {
                        "tokens_in": row["tokens_in"] or 0,
                        "tokens_out": row["tokens_out"] or 0,
                        "total_tokens": row["total_tokens"] or 0,
                        "cost_usd": round(row["cost_usd"] or 0.0, 8),
                        "calls": row["calls"] or 0,
                    }
        except Exception as exc:
            logger.debug("UsageTracker.get_session_totals failed: %s", exc)
        return result

    def get_daily_totals(self, days: int = 7) -> List[Dict]:
        """Return per-day token and cost aggregates over the past N days.

        Args:
            days: How many days of history to include (default 7).

        Returns:
            List of dicts ordered ascending by date, each with keys:
            ``date``, ``tokens_in``, ``tokens_out``, ``total_tokens``,
            ``cost_usd``, ``calls``.
        """
        since = time.time() - days * 86400.0
        rows_out: List[Dict] = []
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    """
                    SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS date,
                           SUM(prompt_tokens)    AS tokens_in,
                           SUM(completion_tokens) AS tokens_out,
                           SUM(prompt_tokens + completion_tokens) AS total_tokens,
                           SUM(cost_usd)          AS cost_usd,
                           COUNT(*)               AS calls
                    FROM   usage
                    WHERE  ts >= ?
                    GROUP  BY date
                    ORDER  BY date ASC
                    """,
                    (since,),
                ).fetchall()
                for row in rows:
                    rows_out.append(
                        {
                            "date": row["date"],
                            "tokens_in": row["tokens_in"] or 0,
                            "tokens_out": row["tokens_out"] or 0,
                            "total_tokens": row["total_tokens"] or 0,
                            "cost_usd": round(row["cost_usd"] or 0.0, 8),
                            "calls": row["calls"] or 0,
                        }
                    )
        except Exception as exc:
            logger.debug("UsageTracker.get_daily_totals failed: %s", exc)
        return rows_out

    def get_all_time_totals(self) -> Dict:
        """Return lifetime aggregated totals across all sessions.

        Returns:
            Dict with keys: ``tokens_in``, ``tokens_out``, ``total_tokens``,
            ``cost_usd``, ``calls``.
        """
        result: Dict = {
            "tokens_in": 0,
            "tokens_out": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
        }
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT SUM(prompt_tokens)    AS tokens_in,
                           SUM(completion_tokens) AS tokens_out,
                           SUM(prompt_tokens + completion_tokens) AS total_tokens,
                           SUM(cost_usd)          AS cost_usd,
                           COUNT(*)               AS calls
                    FROM   usage
                    """
                ).fetchone()
                if row:
                    result = {
                        "tokens_in": row["tokens_in"] or 0,
                        "tokens_out": row["tokens_out"] or 0,
                        "total_tokens": row["total_tokens"] or 0,
                        "cost_usd": round(row["cost_usd"] or 0.0, 8),
                        "calls": row["calls"] or 0,
                    }
        except Exception as exc:
            logger.debug("UsageTracker.get_all_time_totals failed: %s", exc)
        return result

    def get_today_totals(self) -> Dict:
        """Return today's aggregated totals (all providers combined).

        Convenience method used by the dashboard.

        Returns:
            Dict with keys: ``tokens_in``, ``tokens_out``, ``total_tokens``,
            ``cost_usd``, ``calls``.
        """
        since_midnight = time.time() - (time.time() % 86400)
        result: Dict = {
            "tokens_in": 0,
            "tokens_out": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
        }
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT SUM(prompt_tokens)    AS tokens_in,
                           SUM(completion_tokens) AS tokens_out,
                           SUM(prompt_tokens + completion_tokens) AS total_tokens,
                           SUM(cost_usd)          AS cost_usd,
                           COUNT(*)               AS calls
                    FROM   usage
                    WHERE  ts >= ?
                    """,
                    (since_midnight,),
                ).fetchone()
                if row and row["calls"]:
                    result = {
                        "tokens_in": row["tokens_in"] or 0,
                        "tokens_out": row["tokens_out"] or 0,
                        "total_tokens": row["total_tokens"] or 0,
                        "cost_usd": round(row["cost_usd"] or 0.0, 8),
                        "calls": row["calls"] or 0,
                    }
        except Exception as exc:
            logger.debug("UsageTracker.get_today_totals failed: %s", exc)
        return result


# ── Module-level singleton ─────────────────────────────────────────────────────

_tracker: Optional[UsageTracker] = None


def get_tracker() -> UsageTracker:
    """Return the process-wide :class:`UsageTracker` singleton.

    Initialised lazily on first call; safe to call from any thread.
    """
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker
