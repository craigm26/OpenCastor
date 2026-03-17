"""Tests for castor/trajectory.py — TrajectoryLogger."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from castor.harness import HarnessContext, HarnessResult
from castor.providers.base import Thought
from castor.trajectory import TrajectoryLogger, TrajectoryRecord


@pytest.fixture(autouse=True)
def temp_db(tmp_path):
    """Use a temporary DB for every test."""
    db = tmp_path / "test_trajectories.db"
    TrajectoryLogger.set_db_path(db)
    yield db
    TrajectoryLogger._conn = None


def _ctx(**kwargs):
    return HarnessContext(instruction="test", **kwargs)


def _result(text="hello", **kwargs):
    return HarnessResult(thought=Thought(raw_text=text), **kwargs)


class TestTrajectoryLogger:
    @pytest.mark.asyncio
    async def test_log_and_retrieve(self):
        ctx = _ctx(session_id="s1", scope="chat")
        result = _result("response text", run_id="r1")
        await TrajectoryLogger.log_async(ctx, result)
        record = TrajectoryLogger.get_record("r1")
        assert record is not None
        assert record["session_id"] == "s1"
        assert record["scope"] == "chat"
        assert "response text" in record["final_response"]

    @pytest.mark.asyncio
    async def test_list_recent(self):
        for i in range(5):
            ctx = _ctx(session_id=f"s{i}")
            result = _result(f"response {i}", run_id=f"run{i}")
            await TrajectoryLogger.log_async(ctx, result)
        records = TrajectoryLogger.list_recent(limit=10)
        assert len(records) == 5

    @pytest.mark.asyncio
    async def test_p66_fields_stored(self):
        ctx = _ctx(scope="control")
        result = _result(
            "blocked",
            run_id="p66test",
            p66_consent_required=True,
            p66_blocked=True,
        )
        await TrajectoryLogger.log_async(ctx, result)
        record = TrajectoryLogger.get_record("p66test")
        assert record["p66_consent_req"] == 1
        assert record["p66_blocked"] == 1

    @pytest.mark.asyncio
    async def test_estop_stored(self):
        ctx = _ctx(scope="safety")
        result = _result("halted", run_id="estop1", p66_estop_bypassed=True)
        await TrajectoryLogger.log_async(ctx, result)
        record = TrajectoryLogger.get_record("estop1")
        assert record["p66_estop"] == 1

    @pytest.mark.asyncio
    async def test_export_jsonl(self):
        for i in range(3):
            await TrajectoryLogger.log_async(_ctx(), _result(f"r{i}", run_id=f"e{i}"))
        export = TrajectoryLogger.export_jsonl()
        lines = [l for l in export.strip().split("\n") if l]
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "instruction" in parsed

    @pytest.mark.asyncio
    async def test_stats(self):
        await TrajectoryLogger.log_async(_ctx(), _result(run_id="stat1"))
        await TrajectoryLogger.log_async(_ctx(), _result(run_id="stat2", p66_blocked=True))
        stats = TrajectoryLogger.stats()
        assert stats["total_runs"] == 2
        assert stats["p66_events"] >= 1

    @pytest.mark.asyncio
    async def test_idempotent_write(self):
        """Writing same run_id twice should not error (UPSERT)."""
        ctx = _ctx(session_id="dup")
        result = _result("first", run_id="dup1")
        await TrajectoryLogger.log_async(ctx, result)
        await TrajectoryLogger.log_async(ctx, result)
        assert TrajectoryLogger.stats()["total_runs"] == 1

    def test_record_missing_returns_none(self):
        assert TrajectoryLogger.get_record("does-not-exist") is None
