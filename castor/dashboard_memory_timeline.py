"""
castor/dashboard_memory_timeline.py — Memory timeline visualisation module (Issue #349).

Provides helper classes and functions for rendering the episode memory
timeline in the OpenCastor CastorDash dashboard.

The timeline displays:
    - Episode counts over time (bucketed by minute/hour/day)
    - Outcome distributions (ok / error / timeout / unknown)
    - Latency trends (mean latency per time bucket)
    - Action-type distribution (for action-type analysis)

Usage::

    from castor.dashboard_memory_timeline import MemoryTimeline

    timeline = MemoryTimeline(db_path="~/.castor/memory.db")
    data = timeline.get_timeline(window_h=24, bucket_minutes=60)
    # data: {"buckets": [...], "outcome_counts": {...}, "latency_trend": [...]}
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.MemoryTimeline")

_DEFAULT_DB_DIR = Path.home() / ".castor"
_DEFAULT_DB_NAME = "memory.db"
_DEFAULT_BUCKET_MINUTES = 60  # 1-hour buckets by default
_DEFAULT_WINDOW_H = 24  # 24-hour window by default


class MemoryTimeline:
    """Memory timeline data provider for the CastorDash dashboard.

    Reads from the SQLite episode memory database and aggregates episodes
    into time-bucketed series suitable for charting.

    Args:
        db_path: Path to the SQLite memory database.
                 Defaults to ``~/.castor/memory.db`` (or
                 ``CASTOR_MEMORY_DB`` env var).
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        env_path = os.getenv("CASTOR_MEMORY_DB")
        if db_path is None and env_path:
            self._db_path = env_path
        elif db_path is None:
            self._db_path = str(_DEFAULT_DB_DIR / _DEFAULT_DB_NAME)
        else:
            self._db_path = db_path

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _connect(self) -> Optional[sqlite3.Connection]:
        """Open a read-only connection to the episode database."""
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.row_factory = sqlite3.Row
            return con
        except Exception as exc:
            logger.warning("MemoryTimeline: could not open DB %s: %s", self._db_path, exc)
            return None

    # ── Timeline bucketing ────────────────────────────────────────────────────

    @staticmethod
    def _bucket_ts(ts: float, bucket_seconds: float) -> float:
        """Floor-align *ts* to the nearest *bucket_seconds* boundary."""
        return math.floor(ts / bucket_seconds) * bucket_seconds

    def get_timeline(
        self,
        window_h: float = _DEFAULT_WINDOW_H,
        bucket_minutes: int = _DEFAULT_BUCKET_MINUTES,
    ) -> Dict[str, Any]:
        """Return bucketed episode counts, latency trends, and outcome distributions.

        Args:
            window_h:       Look-back window in hours.
            bucket_minutes: Size of each time bucket in minutes.

        Returns:
            Dict with keys:

            * ``"buckets"`` — list of ``{"ts", "label", "count", "mean_latency_ms",
              "outcomes": {ok, error, timeout, unknown}}`` dicts, one per bucket.
            * ``"outcome_counts"`` — ``dict[outcome_str, int]`` totals.
            * ``"latency_trend"`` — ``list[{"ts", "mean_ms"}]``.
            * ``"action_type_counts"`` — ``dict[action_type, int]`` totals.
            * ``"total_episodes"`` — ``int``.
            * ``"window_h"`` — requested window.
            * ``"bucket_minutes"`` — requested bucket size.
        """
        bucket_seconds = bucket_minutes * 60.0
        now = time.time()
        since = now - (window_h * 3600.0)

        # Aggregate: bucket_ts → {count, latency_sum, outcomes, action_types}
        buckets: Dict[float, Dict[str, Any]] = {}
        outcome_totals: Dict[str, int] = defaultdict(int)
        action_totals: Dict[str, int] = defaultdict(int)
        total = 0

        con = self._connect()
        if con is not None:
            try:
                rows = con.execute(
                    """
                    SELECT ts, latency_ms, outcome, action_json
                    FROM episodes
                    WHERE ts >= ?
                    ORDER BY ts ASC
                    """,
                    (since,),
                ).fetchall()
                for row in rows:
                    ts = float(row["ts"])
                    latency = float(row["latency_ms"] or 0.0)
                    outcome = str(row["outcome"] or "unknown")
                    action_json = row["action_json"] or "{}"
                    try:
                        import json as _json

                        action = _json.loads(action_json) if action_json else {}
                    except Exception:
                        action = {}
                    action_type = (
                        action.get("type", "unknown") if isinstance(action, dict) else "unknown"
                    )

                    bk = self._bucket_ts(ts, bucket_seconds)
                    if bk not in buckets:
                        buckets[bk] = {
                            "ts": bk,
                            "count": 0,
                            "latency_sum": 0.0,
                            "outcomes": defaultdict(int),
                        }
                    b = buckets[bk]
                    b["count"] += 1
                    b["latency_sum"] += latency
                    b["outcomes"][outcome] += 1
                    outcome_totals[outcome] += 1
                    action_totals[action_type] += 1
                    total += 1
            except Exception as exc:
                logger.warning("MemoryTimeline.get_timeline: query error: %s", exc)
            finally:
                con.close()

        # Build ordered bucket list (fill gaps with zero-count buckets)
        first_bk = self._bucket_ts(since, bucket_seconds)
        last_bk = self._bucket_ts(now, bucket_seconds)
        ordered_buckets: List[Dict[str, Any]] = []
        current_bk = first_bk
        latency_trend: List[Dict[str, Any]] = []

        while current_bk <= last_bk:
            import datetime

            label = datetime.datetime.fromtimestamp(current_bk).strftime("%Y-%m-%d %H:%M")
            if current_bk in buckets:
                b = buckets[current_bk]
                mean_ms = (b["latency_sum"] / b["count"]) if b["count"] > 0 else 0.0
                outcomes = dict(b["outcomes"])
            else:
                mean_ms = 0.0
                outcomes = {}

            count = buckets[current_bk]["count"] if current_bk in buckets else 0
            ordered_buckets.append(
                {
                    "ts": current_bk,
                    "label": label,
                    "count": count,
                    "mean_latency_ms": round(mean_ms, 2),
                    "outcomes": outcomes,
                }
            )
            latency_trend.append({"ts": current_bk, "mean_ms": round(mean_ms, 2)})
            current_bk += bucket_seconds

        return {
            "buckets": ordered_buckets,
            "outcome_counts": dict(outcome_totals),
            "latency_trend": latency_trend,
            "action_type_counts": dict(action_totals),
            "total_episodes": total,
            "window_h": window_h,
            "bucket_minutes": bucket_minutes,
        }

    def get_outcome_summary(self, window_h: float = 24.0) -> Dict[str, Any]:
        """Return a summary of episode outcomes over the look-back window.

        Args:
            window_h: Look-back window in hours.

        Returns:
            Dict with ``"total"``, ``"outcomes"`` (dict), and ``"ok_rate"`` (float 0–1).
        """
        since = time.time() - (window_h * 3600.0)
        outcomes: Dict[str, int] = defaultdict(int)
        total = 0

        con = self._connect()
        if con is not None:
            try:
                rows = con.execute(
                    "SELECT outcome FROM episodes WHERE ts >= ?",
                    (since,),
                ).fetchall()
                for row in rows:
                    outcome = str(row["outcome"] or "unknown")
                    outcomes[outcome] += 1
                    total += 1
            except Exception as exc:
                logger.warning("MemoryTimeline.get_outcome_summary: %s", exc)
            finally:
                con.close()

        ok_count = outcomes.get("ok", 0)
        ok_rate = (ok_count / total) if total > 0 else 0.0
        return {"total": total, "outcomes": dict(outcomes), "ok_rate": round(ok_rate, 4)}

    def get_latency_percentiles(
        self,
        window_h: float = 24.0,
    ) -> Dict[str, Optional[float]]:
        """Compute p50, p95, p99 latency from episodes in the window.

        Args:
            window_h: Look-back window in hours.

        Returns:
            Dict with ``"p50_ms"``, ``"p95_ms"``, ``"p99_ms"``, and
            ``"count"`` (number of episodes used).
        """
        since = time.time() - (window_h * 3600.0)
        samples: List[float] = []

        con = self._connect()
        if con is not None:
            try:
                rows = con.execute(
                    "SELECT latency_ms FROM episodes WHERE ts >= ? AND latency_ms IS NOT NULL",
                    (since,),
                ).fetchall()
                samples = sorted(
                    float(r["latency_ms"]) for r in rows if r["latency_ms"] is not None
                )
            except Exception as exc:
                logger.warning("MemoryTimeline.get_latency_percentiles: %s", exc)
            finally:
                con.close()

        def _pct(pct: float) -> Optional[float]:
            if not samples:
                return None
            n = len(samples)
            idx = (pct / 100.0) * (n - 1)
            lo = int(idx)
            hi = lo + 1
            frac = idx - lo
            if hi >= n:
                return round(samples[-1], 2)
            return round(samples[lo] * (1.0 - frac) + samples[hi] * frac, 2)

        return {
            "p50_ms": _pct(50),
            "p95_ms": _pct(95),
            "p99_ms": _pct(99),
            "count": len(samples),
        }
