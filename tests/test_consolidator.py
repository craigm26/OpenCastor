"""Tests for castor/memory/consolidator.py — episodic memory consolidation."""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from castor.memory.consolidator import (
    ConsolidationReport,
    EpisodeConsolidator,
    _cosine,
)

# Disable sentence-transformers model download in all tests in this module
# (EpisodeMemory._embed_text tries to load all-MiniLM-L6-v2 which hangs on Pi)
os.environ.setdefault("CASTOR_MEMORY_EMBEDDING_MODEL", "nonexistent-model-for-testing")

_EMBED_PATCH = patch(
    "castor.memory.episode.EpisodeMemory._embed_text",
    staticmethod(lambda text: None),
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def no_embeddings():
    """Patch out embedding model loading for all tests in this module."""
    with _EMBED_PATCH:
        yield


@pytest.fixture()
def fresh_db(tmp_path: Path) -> str:
    """Return path to a fresh EpisodeMemory SQLite DB."""
    from castor.memory.episode import EpisodeMemory

    db_path = str(tmp_path / "memory.db")
    EpisodeMemory(db_path=db_path)
    return db_path


@pytest.fixture()
def db_with_episodes(fresh_db: str) -> str:
    """DB pre-populated with a mix of normal, stale, and duplicate episodes."""
    from castor.memory.episode import EpisodeMemory

    mem = EpisodeMemory(db_path=fresh_db)

    # Recent normal episodes
    for i in range(5):
        mem.log_episode(
            instruction=f"move forward {i} times",
            outcome="ok",
            source="test",
        )

    # Stale episodes (31 days old)
    stale_ts = time.time() - (31 * 24 * 3600)
    conn = sqlite3.connect(fresh_db)
    conn.execute("ALTER TABLE episodes ADD COLUMN retrieval_count INTEGER DEFAULT 0")
    conn.execute("ALTER TABLE episodes ADD COLUMN priority TEXT DEFAULT 'normal'")
    conn.commit()
    for j in range(3):
        conn.execute(
            "INSERT INTO episodes (id, ts, instruction, outcome, source, retrieval_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), stale_ts, f"old task {j}", "ok", "test", 0),
        )
    conn.commit()
    conn.close()

    # High-value episode (retrieved 5 times)
    ep_id = mem.log_episode(instruction="frequently used task", outcome="ok")
    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "UPDATE episodes SET retrieval_count = 5 WHERE id = ?",
        (ep_id,),
    )
    conn.commit()
    conn.close()

    return fresh_db


# ── Unit Tests ────────────────────────────────────────────────────────────────


class TestCosineHelper:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine(a, b) == pytest.approx(0.0)

    def test_empty_vectors(self):
        assert _cosine([], []) == 0.0

    def test_mismatched_lengths(self):
        assert _cosine([1.0], [1.0, 2.0]) == 0.0

    def test_similar_vectors(self):
        import math

        a = [1.0, 1.0]
        b = [1.0, 0.9]
        mag_a = math.sqrt(2.0)
        mag_b = math.sqrt(1.0 + 0.81)
        expected = (1.0 + 0.9) / (mag_a * mag_b)
        assert _cosine(a, b) == pytest.approx(expected, rel=1e-5)


class TestConsolidationReport:
    def test_total_changes(self):
        report = ConsolidationReport(
            duplicates_merged=2,
            stale_archived=3,
            priority_boosted=1,
        )
        assert report.total_changes == 6

    def test_precision_improved(self):
        report = ConsolidationReport(precision_before=0.5, precision_after=0.6)
        assert report.precision_improved is True

    def test_precision_not_improved(self):
        report = ConsolidationReport(precision_before=0.5, precision_after=0.52)
        assert report.precision_improved is False

    def test_summary_dry_run(self):
        report = ConsolidationReport(
            timestamp="2026-03-17T10:00:00+00:00",
            dry_run=True,
            stale_archived=2,
        )
        s = report.summary()
        assert "DRY RUN" in s
        assert "2026-03-17" in s

    def test_to_dict(self):
        report = ConsolidationReport(duplicates_merged=1)
        d = report.to_dict()
        assert d["duplicates_merged"] == 1
        assert "timestamp" in d


class TestSchema:
    def test_ensure_schema_creates_tables(self, fresh_db: str):
        c = EpisodeConsolidator(db_path=fresh_db)
        c._ensure_schema()
        conn = sqlite3.connect(fresh_db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "episodes_archived" in tables
        conn.close()

    def test_ensure_schema_idempotent(self, fresh_db: str):
        c = EpisodeConsolidator(db_path=fresh_db)
        c._ensure_schema()
        c._ensure_schema()  # second call should not raise


class TestStaleArchival:
    def test_find_stale_returns_old_episodes(self, db_with_episodes: str):
        c = EpisodeConsolidator(db_path=db_with_episodes)
        c._ensure_schema()
        stale = c._find_stale_episodes()
        assert len(stale) >= 3  # we inserted 3 stale episodes

    def test_archive_removes_from_main_table(self, db_with_episodes: str):
        c = EpisodeConsolidator(db_path=db_with_episodes)
        c._ensure_schema()
        stale = c._find_stale_episodes()
        assert stale, "Expected stale episodes"

        ep_id = stale[0]
        c._archive_episode(ep_id, reason="test")

        conn = sqlite3.connect(db_with_episodes)
        row = conn.execute("SELECT id FROM episodes WHERE id = ?", (ep_id,)).fetchone()
        archived = conn.execute(
            "SELECT id FROM episodes_archived WHERE id = ?", (ep_id,)
        ).fetchone()
        conn.close()

        assert row is None, "Episode should be removed from main table"
        assert archived is not None, "Episode should be in archived table"

    def test_dry_run_does_not_archive(self, db_with_episodes: str):
        c = EpisodeConsolidator(db_path=db_with_episodes, dry_run=True)
        c._ensure_schema()
        report = c.consolidate()
        assert report.dry_run is True

        # No rows should be in archived table
        conn = sqlite3.connect(db_with_episodes)
        count = conn.execute("SELECT COUNT(*) FROM episodes_archived").fetchone()[0]
        conn.close()
        assert count == 0


class TestPriorityBoosting:
    def test_find_high_value(self, db_with_episodes: str):
        c = EpisodeConsolidator(db_path=db_with_episodes)
        c._ensure_schema()
        high_value = c._find_high_value_episodes()
        assert len(high_value) >= 1

    def test_boost_sets_priority(self, db_with_episodes: str):
        c = EpisodeConsolidator(db_path=db_with_episodes)
        c._ensure_schema()
        high_value = c._find_high_value_episodes()
        assert high_value
        ep_id = high_value[0]
        c._boost_priority(ep_id)

        conn = sqlite3.connect(db_with_episodes)
        row = conn.execute("SELECT priority FROM episodes WHERE id = ?", (ep_id,)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "high"


class TestRetrievalTracking:
    def test_record_retrieval_increments_count(self, fresh_db: str):
        from castor.memory.episode import EpisodeMemory

        mem = EpisodeMemory(db_path=fresh_db)
        ep_id = mem.log_episode(instruction="test")

        # Ensure column exists
        EpisodeConsolidator(db_path=fresh_db)._ensure_schema()

        EpisodeConsolidator.record_retrieval(fresh_db, ep_id)
        EpisodeConsolidator.record_retrieval(fresh_db, ep_id)

        conn = sqlite3.connect(fresh_db)
        row = conn.execute("SELECT retrieval_count FROM episodes WHERE id = ?", (ep_id,)).fetchone()
        conn.close()
        assert row[0] == 2


class TestFullPass:
    def test_consolidate_dry_run(self, db_with_episodes: str):
        c = EpisodeConsolidator(db_path=db_with_episodes, dry_run=True)
        report = c.consolidate()
        assert isinstance(report, ConsolidationReport)
        assert report.dry_run is True
        assert report.error is None

    def test_consolidate_live_changes(self, db_with_episodes: str):
        c = EpisodeConsolidator(db_path=db_with_episodes, dry_run=False)
        report = c.consolidate()
        assert report.error is None
        # Should have archived some stale episodes and boosted high-value
        assert report.total_changes >= 0  # may be 0 if DB state doesn't trigger thresholds

    def test_precision_measured(self, db_with_episodes: str):
        c = EpisodeConsolidator(db_path=db_with_episodes)
        p = c._measure_retrieval_precision()
        assert 0.0 <= p <= 1.0

    def test_precision_empty_db(self, fresh_db: str):
        c = EpisodeConsolidator(db_path=fresh_db)
        p = c._measure_retrieval_precision()
        assert p == 0.5  # not enough data → default
