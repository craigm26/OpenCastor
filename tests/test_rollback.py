"""Tests for castor.harness.rollback."""

import pytest

from castor.harness.rollback import RollbackManager


@pytest.fixture
def mgr(tmp_path):
    db = str(tmp_path / "test.db")
    return RollbackManager(db_path=db)


def test_capture_returns_id(mgr):
    snap_id = mgr.capture("run-1", {"joint_a": 45.0})
    assert isinstance(snap_id, str)
    assert len(snap_id) == 36  # UUID


def test_restore_roundtrip(mgr):
    state = {"joint_a": 45.0, "joint_b": 90.0}
    snap_id = mgr.capture("run-1", state)
    restored = mgr.restore(snap_id)
    assert restored == state


def test_restore_marks_used(mgr):
    snap_id = mgr.capture("run-1", {"x": 1})
    mgr.restore(snap_id)
    recent = mgr.list_recent(10)
    used = [r for r in recent if r["id"] == snap_id]
    assert used[0]["used_at"] is not None


def test_restore_missing_raises(mgr):
    with pytest.raises(KeyError):
        mgr.restore("nonexistent-uuid")


def test_latest_returns_most_recent(mgr):
    mgr.capture("run-1", {"a": 1})
    mgr.capture("run-1", {"a": 2})
    latest = mgr.latest("run-1")
    assert latest is not None
    assert latest["snapshot"]["a"] == 2


def test_latest_none_for_unknown_run(mgr):
    assert mgr.latest("unknown-run") is None


def test_list_recent(mgr):
    for i in range(5):
        mgr.capture(f"run-{i}", {"i": i})
    items = mgr.list_recent(limit=3)
    assert len(items) == 3


def test_is_physical_tool(mgr):
    assert mgr.is_physical_tool("move")
    assert mgr.is_physical_tool("grip")
    assert mgr.is_physical_tool("rotate")
    assert not mgr.is_physical_tool("get_telemetry")
    assert not mgr.is_physical_tool("web_search")


def test_multiple_runs_isolated(mgr):
    mgr.capture("run-A", {"x": 1})
    mgr.capture("run-B", {"x": 99})
    a = mgr.latest("run-A")
    b = mgr.latest("run-B")
    assert a["snapshot"]["x"] == 1
    assert b["snapshot"]["x"] == 99
