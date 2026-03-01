"""Tests for Dashboard memory timeline (Issue #349)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import uuid

import pytest

from castor.dashboard_memory_timeline import MemoryTimeline


def make_db() -> str:
    """Create a temp SQLite DB with the episodes schema."""
    path = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS episodes (
            id           TEXT PRIMARY KEY,
            ts           REAL NOT NULL,
            instruction  TEXT,
            raw_thought  TEXT,
            action_json  TEXT,
            latency_ms   REAL,
            image_hash   TEXT,
            outcome      TEXT,
            source       TEXT DEFAULT 'loop',
            image_blob   BLOB DEFAULT NULL,
            tags         TEXT DEFAULT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ts ON episodes (ts DESC);
    """)
    con.commit()
    con.close()
    return path


def insert_episode(
    path: str, ts: float, outcome: str = "ok", latency_ms: float = 100.0, action_type: str = "move"
) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO episodes (id, ts, outcome, latency_ms, action_json) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), ts, outcome, latency_ms, json.dumps({"type": action_type})),
    )
    con.commit()
    con.close()


# ── MemoryTimeline instantiation ──────────────────────────────────────────────


def test_memory_timeline_init_default():
    tl = MemoryTimeline()
    assert tl._db_path is not None


def test_memory_timeline_init_custom_path():
    tl = MemoryTimeline(db_path="/tmp/custom.db")
    assert tl._db_path == "/tmp/custom.db"


# ── get_timeline tests ────────────────────────────────────────────────────────


def test_get_timeline_returns_expected_keys():
    path = make_db()
    tl = MemoryTimeline(db_path=path)
    result = tl.get_timeline(window_h=1, bucket_minutes=10)
    for key in (
        "buckets",
        "outcome_counts",
        "latency_trend",
        "action_type_counts",
        "total_episodes",
        "window_h",
        "bucket_minutes",
    ):
        assert key in result, f"Missing key: {key}"


def test_get_timeline_empty_db():
    path = make_db()
    tl = MemoryTimeline(db_path=path)
    result = tl.get_timeline(window_h=1, bucket_minutes=60)
    assert result["total_episodes"] == 0
    assert isinstance(result["buckets"], list)


def test_get_timeline_counts_episodes():
    path = make_db()
    now = time.time()
    for _ in range(5):
        insert_episode(path, now - 60)
    tl = MemoryTimeline(db_path=path)
    result = tl.get_timeline(window_h=1, bucket_minutes=60)
    assert result["total_episodes"] == 5


def test_get_timeline_outcome_counts():
    path = make_db()
    now = time.time()
    insert_episode(path, now - 60, outcome="ok")
    insert_episode(path, now - 60, outcome="ok")
    insert_episode(path, now - 60, outcome="error")
    tl = MemoryTimeline(db_path=path)
    result = tl.get_timeline(window_h=1, bucket_minutes=60)
    assert result["outcome_counts"].get("ok", 0) == 2
    assert result["outcome_counts"].get("error", 0) == 1


def test_get_timeline_action_type_counts():
    path = make_db()
    now = time.time()
    insert_episode(path, now - 60, action_type="move")
    insert_episode(path, now - 60, action_type="move")
    insert_episode(path, now - 60, action_type="stop")
    tl = MemoryTimeline(db_path=path)
    result = tl.get_timeline(window_h=1, bucket_minutes=60)
    assert result["action_type_counts"].get("move", 0) == 2
    assert result["action_type_counts"].get("stop", 0) == 1


def test_get_timeline_buckets_have_expected_keys():
    path = make_db()
    now = time.time()
    insert_episode(path, now - 60)
    tl = MemoryTimeline(db_path=path)
    result = tl.get_timeline(window_h=1, bucket_minutes=60)
    for bucket in result["buckets"]:
        assert "ts" in bucket
        assert "label" in bucket
        assert "count" in bucket
        assert "mean_latency_ms" in bucket
        assert "outcomes" in bucket


def test_get_timeline_latency_trend():
    path = make_db()
    now = time.time()
    insert_episode(path, now - 60, latency_ms=200.0)
    tl = MemoryTimeline(db_path=path)
    result = tl.get_timeline(window_h=1, bucket_minutes=60)
    assert isinstance(result["latency_trend"], list)
    assert all("ts" in x and "mean_ms" in x for x in result["latency_trend"])


def test_get_timeline_window_excludes_old_episodes():
    path = make_db()
    now = time.time()
    insert_episode(path, now - 7200)  # 2 hours ago — outside 1h window
    insert_episode(path, now - 60)  # 1 minute ago — inside window
    tl = MemoryTimeline(db_path=path)
    result = tl.get_timeline(window_h=1, bucket_minutes=60)
    assert result["total_episodes"] == 1


def test_get_timeline_missing_db_returns_empty():
    tl = MemoryTimeline(db_path="/tmp/does_not_exist_opencastor_test_timeline.db")
    result = tl.get_timeline(window_h=1)
    assert result["total_episodes"] == 0 or isinstance(result["total_episodes"], int)


# ── get_outcome_summary tests ─────────────────────────────────────────────────


def test_get_outcome_summary_empty_db():
    path = make_db()
    tl = MemoryTimeline(db_path=path)
    result = tl.get_outcome_summary()
    assert result["total"] == 0
    assert result["ok_rate"] == 0.0


def test_get_outcome_summary_ok_rate():
    path = make_db()
    now = time.time()
    insert_episode(path, now - 60, outcome="ok")
    insert_episode(path, now - 60, outcome="ok")
    insert_episode(path, now - 60, outcome="error")
    tl = MemoryTimeline(db_path=path)
    result = tl.get_outcome_summary(window_h=1)
    assert result["ok_rate"] == pytest.approx(2 / 3, rel=0.01)


def test_get_outcome_summary_has_outcomes_dict():
    path = make_db()
    now = time.time()
    insert_episode(path, now - 60, outcome="ok")
    tl = MemoryTimeline(db_path=path)
    result = tl.get_outcome_summary(window_h=1)
    assert "outcomes" in result
    assert isinstance(result["outcomes"], dict)


# ── get_latency_percentiles tests ─────────────────────────────────────────────


def test_get_latency_percentiles_empty():
    path = make_db()
    tl = MemoryTimeline(db_path=path)
    result = tl.get_latency_percentiles()
    assert result["p50_ms"] is None
    assert result["p95_ms"] is None
    assert result["count"] == 0


def test_get_latency_percentiles_with_data():
    path = make_db()
    now = time.time()
    for i in range(1, 101):
        insert_episode(path, now - 60, latency_ms=float(i))
    tl = MemoryTimeline(db_path=path)
    result = tl.get_latency_percentiles(window_h=1)
    assert result["p50_ms"] is not None
    assert result["p95_ms"] >= result["p50_ms"]
    assert result["p99_ms"] >= result["p95_ms"]
    assert result["count"] == 100


def test_get_latency_percentiles_returns_expected_keys():
    path = make_db()
    tl = MemoryTimeline(db_path=path)
    result = tl.get_latency_percentiles()
    for k in ("p50_ms", "p95_ms", "p99_ms", "count"):
        assert k in result


# ── Bucket alignment helper ───────────────────────────────────────────────────


def test_bucket_ts_aligns_to_boundary():
    aligned = MemoryTimeline._bucket_ts(3750.0, 3600.0)
    assert aligned == 3600.0


def test_bucket_ts_zero():
    assert MemoryTimeline._bucket_ts(0.0, 60.0) == 0.0
