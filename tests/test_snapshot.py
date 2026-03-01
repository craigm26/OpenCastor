"""
tests/test_snapshot.py — Unit + API tests for castor/snapshot.py.

Covers:
  - SnapshotManager.take() structure
  - history ring buffer
  - latest() returns most recent
  - background thread lifecycle
  - system_metrics helper
  - API: GET /api/snapshot/latest, /history, POST /api/snapshot/take
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def mgr():
    from castor.snapshot import SnapshotManager

    return SnapshotManager(max_history=10)


def test_take_returns_dict(mgr):
    snap = mgr.take()
    assert isinstance(snap, dict)
    assert "timestamp" in snap
    assert "timestamp_iso" in snap
    assert "system" in snap


def test_take_stores_in_history(mgr):
    mgr.take()
    assert len(mgr.history()) == 1


def test_history_newest_first(mgr):
    mgr.take()
    time.sleep(0.01)
    mgr.take()
    history = mgr.history()
    assert history[0]["timestamp"] >= history[1]["timestamp"]


def test_latest_returns_most_recent(mgr):
    mgr.take()
    time.sleep(0.01)
    second = mgr.take()
    assert mgr.latest()["timestamp"] == second["timestamp"]


def test_latest_returns_none_when_empty(mgr):
    assert mgr.latest() is None


def test_history_limit(mgr):
    for _ in range(15):
        mgr.take()
    assert len(mgr.history(limit=5)) == 5


def test_max_history_ring_buffer():
    from castor.snapshot import SnapshotManager

    mgr = SnapshotManager(max_history=3)
    for _ in range(10):
        mgr.take()
    assert len(mgr.history()) == 3


def test_take_with_state_captures_fields(mgr):
    mock_state = MagicMock()
    mock_state.brain = MagicMock()
    mock_state.brain.health_check.return_value = {"ok": True}
    mock_state.driver = None
    mock_state.channels = {}
    mock_state.last_thought = {"raw_text": "hello"}
    mock_state.paused = False
    mock_state.fs = None

    snap = mgr.take(state=mock_state)
    assert snap["last_thought"] == {"raw_text": "hello"}
    assert snap["provider"] == {"ok": True}
    assert snap["driver"] is None


def test_clear(mgr):
    mgr.take()
    mgr.clear()
    assert mgr.latest() is None


def test_background_thread_start_stop():
    from castor.snapshot import SnapshotManager

    mgr = SnapshotManager()
    mgr.start(interval_s=0.05)
    time.sleep(0.15)
    mgr.stop()
    # Should have taken at least one snapshot
    assert mgr.latest() is not None


def test_get_manager_singleton():
    import castor.snapshot as m

    m._manager = None
    s1 = m.get_manager()
    s2 = m.get_manager()
    assert s1 is s2
    m._manager = None


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    from fastapi.testclient import TestClient

    import castor.snapshot as snap_mod
    from castor.api import app

    snap_mod._manager = None  # reset singleton
    return TestClient(app)


def test_api_snapshot_take(api_client):
    resp = api_client.post("/api/snapshot/take")
    assert resp.status_code == 200
    data = resp.json()
    assert "timestamp" in data
    assert "system" in data


def test_api_snapshot_latest(api_client):
    api_client.post("/api/snapshot/take")
    resp = api_client.get("/api/snapshot/latest")
    assert resp.status_code == 200
    assert "timestamp" in resp.json()


def test_api_snapshot_latest_404_when_empty(api_client):
    import castor.snapshot as m

    m._manager = None  # fresh manager, no snapshots
    resp = api_client.get("/api/snapshot/latest")
    assert resp.status_code == 404


def test_api_snapshot_history(api_client):
    api_client.post("/api/snapshot/take")
    resp = api_client.get("/api/snapshot/history?limit=5")
    assert resp.status_code == 200
    assert "snapshots" in resp.json()
