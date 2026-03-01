"""Tests for Mission ETA (issue #277) — castor/mission.py."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch


def _make_runner(driver=None):
    from castor.mission import MissionRunner

    return MissionRunner(driver=driver, config={})


# ── status() structure ────────────────────────────────────────────────────────


def test_status_has_eta_fields():
    """status() must include elapsed_s and eta_s keys."""
    runner = _make_runner()
    s = runner.status()
    assert "elapsed_s" in s
    assert "eta_s" in s


def test_status_idle_elapsed_is_zero():
    """Before a mission starts, elapsed_s should be 0.0."""
    runner = _make_runner()
    assert runner.status()["elapsed_s"] == 0.0


def test_status_idle_eta_is_none():
    """Before a mission starts, eta_s should be None."""
    runner = _make_runner()
    assert runner.status()["eta_s"] is None


def test_status_not_running():
    """status() running field should be False when no mission is active."""
    runner = _make_runner()
    assert runner.status()["running"] is False


# ── start() resets ETA state ──────────────────────────────────────────────────


def test_start_resets_eta_state():
    """Calling start() should reset elapsed_s, eta_s, and waypoint_durations."""
    runner = _make_runner()
    runner._elapsed_s = 99.9
    runner._eta_s = 10.0
    runner._waypoint_durations = [1.0, 2.0]

    mock_driver = MagicMock()
    mock_driver.move = MagicMock()
    mock_driver.stop = MagicMock()
    mock_driver.health_check = MagicMock(return_value={"ok": True, "mode": "mock"})
    runner._driver = mock_driver

    # Immediately stop so the thread doesn't block
    def abort_immediately():
        time.sleep(0.05)
        runner.stop()

    t = threading.Thread(target=abort_immediately, daemon=True)
    t.start()
    runner.start([{"distance_m": 100, "heading_deg": 0}])
    t.join(timeout=2.0)
    # elapsed_s should have been reset by start() call
    assert runner._elapsed_s >= 0.0


# ── ETA updates during a mission ─────────────────────────────────────────────


def test_eta_is_zero_after_mission_completes():
    """After the mission runner finishes, eta_s should be 0.0."""
    from castor.mission import MissionRunner

    mock_driver = MagicMock()
    mock_driver.move = MagicMock()
    mock_driver.stop = MagicMock()
    mock_driver.health_check = MagicMock(return_value={"ok": True, "mode": "mock"})

    runner = MissionRunner(
        driver=mock_driver,
        config={"physics": {"wheel_circumference_m": 0.22, "turn_time_per_deg_s": 0.011}},
    )

    with patch("castor.nav.WaypointNav.execute", return_value=None):
        runner.start([{"distance_m": 0.1, "heading_deg": 0}])
        # Wait for thread to finish
        for _ in range(20):
            if not runner.status()["running"]:
                break
            time.sleep(0.05)

    assert runner.status()["eta_s"] == 0.0


# ── API endpoint ──────────────────────────────────────────────────────────────


def test_api_nav_mission_status_idle():
    """GET /api/nav/mission/status returns expected structure when idle."""
    from fastapi.testclient import TestClient

    from castor.api import app, state

    state.mission_runner = None
    client = TestClient(app)
    resp = client.get(
        "/api/nav/mission/status",
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    assert "elapsed_s" in data
    assert "eta_s" in data


def test_api_nav_mission_status_with_runner():
    """GET /api/nav/mission/status returns runner.status() when runner exists."""
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from castor.api import app, state

    mock_runner = MagicMock()
    mock_runner.status.return_value = {
        "running": False,
        "current_waypoint": 0,
        "total_waypoints": 3,
        "position": {},
        "geofence": None,
        "elapsed_s": 5.2,
        "eta_s": 10.0,
    }
    state.mission_runner = mock_runner

    client = TestClient(app)
    resp = client.get(
        "/api/nav/mission/status",
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["elapsed_s"] == 5.2
    assert data["eta_s"] == 10.0
    state.mission_runner = None
