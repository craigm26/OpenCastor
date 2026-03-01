"""Tests for castor.mission.MissionRunner and /api/nav/mission* endpoints."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest
from starlette.testclient import TestClient


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_driver():
    d = MagicMock()
    d.move = MagicMock()
    d.stop = MagicMock()
    return d


def _make_config():
    return {
        "physics": {
            "wheel_circumference_m": 0.21,
            "turn_time_per_deg_s": 0.011,
            "min_drive_s": 0.0,  # no minimum so tests run instantly
        }
    }


# ── MissionRunner unit tests ──────────────────────────────────────────────────


class TestMissionRunner:
    def _runner(self):
        from castor.mission import MissionRunner

        return MissionRunner(_make_driver(), _make_config())

    def test_start_returns_job_id(self):
        r = self._runner()
        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.1}
            job_id = r.start([{"distance_m": 0.1, "heading_deg": 0}])
        assert isinstance(job_id, str) and len(job_id) == 36  # UUID4

    def test_status_running_initially(self):
        r = self._runner()
        barrier = threading.Barrier(2)

        def slow_execute(*args, **kwargs):
            barrier.wait(timeout=2)
            return {"ok": True, "duration_s": 0.1}

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.side_effect = slow_execute
            r.start([{"distance_m": 0.5, "heading_deg": 0}])
            barrier.wait(timeout=2)
            st = r.status()
            r.stop()

        assert st["total"] == 1

    def test_status_not_running_after_completion(self):
        r = self._runner()
        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.05}
            r.start([{"distance_m": 0.1, "heading_deg": 0}])
            # Wait for completion
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not r.status()["running"]:
                    break
                time.sleep(0.01)
        assert r.status()["running"] is False

    def test_all_waypoints_executed(self):
        r = self._runner()
        calls = []

        def recording_execute(dist, heading, speed):
            calls.append((dist, heading, speed))
            return {"ok": True, "duration_s": 0.01}

        waypoints = [
            {"distance_m": 0.5, "heading_deg": 0.0, "speed": 0.6},
            {"distance_m": 0.3, "heading_deg": 90.0, "speed": 0.4},
            {"distance_m": 0.2, "heading_deg": -45.0, "speed": 0.5},
        ]

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.side_effect = recording_execute
            r.start(waypoints)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not r.status()["running"]:
                    break
                time.sleep(0.01)

        assert len(calls) == 3
        assert calls[0] == (0.5, 0.0, 0.6)
        assert calls[1] == (0.3, 90.0, 0.4)
        assert calls[2] == (0.2, -45.0, 0.5)

    def test_results_recorded(self):
        r = self._runner()
        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.05}
            r.start([{"distance_m": 0.1}, {"distance_m": 0.2}])
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not r.status()["running"]:
                    break
                time.sleep(0.01)

        st = r.status()
        assert len(st["results"]) == 2
        assert st["results"][0]["ok"] is True

    def test_stop_cancels_mission(self):
        r = self._runner()
        executing_event = threading.Event()
        release_event = threading.Event()

        def slow_execute(*args, **kwargs):
            executing_event.set()       # signal: we are inside execute
            release_event.wait(timeout=3)  # wait until test says go
            return {"ok": True, "duration_s": 0.05}

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.side_effect = slow_execute
            r.start(
                [{"distance_m": 0.5}, {"distance_m": 0.5}, {"distance_m": 0.5}]
            )
            executing_event.wait(timeout=3)  # wait until first step is running
            r.stop()
            release_event.set()          # unblock the slow execute

        assert r.status()["running"] is False

    def test_stop_calls_driver_stop(self):
        driver = _make_driver()
        from castor.mission import MissionRunner

        r = MissionRunner(driver, _make_config())
        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.01}
            r.start([{"distance_m": 0.1}])
            r.stop()
        driver.stop.assert_called()

    def test_empty_waypoints_raises(self):
        r = self._runner()
        with pytest.raises(ValueError, match="at least one waypoint"):
            r.start([])

    def test_loop_mode_executes_multiple_times(self):
        r = self._runner()
        execute_count = [0]
        stop_after = 3
        event = threading.Event()

        def counting_execute(*args, **kwargs):
            execute_count[0] += 1
            if execute_count[0] >= stop_after:
                event.set()
            return {"ok": True, "duration_s": 0.01}

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.side_effect = counting_execute
            r.start([{"distance_m": 0.1}], loop=True)
            event.wait(timeout=5.0)
            r.stop()

        assert execute_count[0] >= stop_after

    def test_waypoint_step_failure_continues(self):
        """A failing waypoint records error but execution continues."""
        r = self._runner()
        results = []

        def fail_first(dist, heading, speed):
            if len(results) == 0:
                results.append("fail")
                raise RuntimeError("motor overload")
            results.append("ok")
            return {"ok": True, "duration_s": 0.01}

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.side_effect = fail_first
            r.start([{"distance_m": 0.1}, {"distance_m": 0.2}])
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not r.status()["running"]:
                    break
                time.sleep(0.01)

        st = r.status()
        assert len(st["results"]) == 2
        assert st["results"][0]["ok"] is False
        assert st["results"][1]["ok"] is True

    def test_dwell_respected(self):
        r = self._runner()
        timestamps = []

        def recording_execute(*args, **kwargs):
            timestamps.append(time.monotonic())
            return {"ok": True, "duration_s": 0.0}

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.side_effect = recording_execute
            r.start(
                [{"distance_m": 0.1, "dwell_s": 0.2}, {"distance_m": 0.1}]
            )
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not r.status()["running"]:
                    break
                time.sleep(0.01)

        assert len(timestamps) == 2
        gap = timestamps[1] - timestamps[0]
        assert gap >= 0.15  # dwell_s=0.2 with tolerance for CI


# ── API endpoint tests ────────────────────────────────────────────────────────


@pytest.fixture()
def mission_client():
    """TestClient with driver loaded and mission_runner cleared.

    Suppresses FastAPI startup/shutdown lifecycle events to avoid config
    loading, hardware init, and channel startup.
    """
    import collections
    import castor.api as api_mod
    from castor.api import app

    # Suppress lifecycle events (same pattern as test_api_endpoints.py client fixture)
    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    api_mod.state.config = {
        "physics": {"wheel_circumference_m": 0.21, "turn_time_per_deg_s": 0.011}
    }
    api_mod.state.driver = _make_driver()
    api_mod.state.brain = None
    api_mod.state.mission_runner = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.state.nav_job = None
    api_mod.API_TOKEN = None

    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.router.on_startup[:] = original_startup
        app.router.on_shutdown[:] = original_shutdown
        api_mod.state.driver = None
        api_mod.state.config = None
        api_mod.state.mission_runner = None


class TestMissionAPI:
    def test_start_mission_returns_job_id(self, mission_client):
        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.05}
            resp = mission_client.post(
                "/api/nav/mission",
                json={
                    "waypoints": [
                        {"distance_m": 0.5, "heading_deg": 0.0},
                        {"distance_m": 0.3, "heading_deg": 90.0},
                    ]
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is True
        assert "job_id" in body
        assert body["total"] == 2

    def test_start_mission_no_driver_returns_503(self, mission_client):
        import castor.api as api_mod

        api_mod.state.driver = None
        resp = mission_client.post(
            "/api/nav/mission",
            json={"waypoints": [{"distance_m": 0.5}]},
        )
        assert resp.status_code == 503

    def test_start_mission_empty_waypoints_returns_400(self, mission_client):
        resp = mission_client.post(
            "/api/nav/mission",
            json={"waypoints": []},
        )
        assert resp.status_code == 400

    def test_get_mission_status_no_runner(self, mission_client):
        import castor.api as api_mod

        api_mod.state.mission_runner = None
        resp = mission_client.get("/api/nav/mission")
        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is False

    def test_get_mission_status_after_start(self, mission_client):
        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.05}
            mission_client.post(
                "/api/nav/mission",
                json={"waypoints": [{"distance_m": 0.5}]},
            )
            resp = mission_client.get("/api/nav/mission")
        assert resp.status_code == 200
        body = resp.json()
        assert "job_id" in body
        assert body["total"] == 1

    def test_stop_mission_no_runner(self, mission_client):
        import castor.api as api_mod

        api_mod.state.mission_runner = None
        resp = mission_client.post("/api/nav/mission/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["was_running"] is False

    def test_stop_mission_running(self, mission_client):
        import castor.api as api_mod
        from castor.mission import MissionRunner

        mock_runner = MagicMock(spec=MissionRunner)
        mock_runner.status.return_value = {"running": True, "job_id": "abc"}
        api_mod.state.mission_runner = mock_runner

        resp = mission_client.post("/api/nav/mission/stop")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["was_running"] is True
        mock_runner.stop.assert_called_once()

    def test_mission_loop_flag_passed(self, mission_client):
        """loop=True is forwarded to MissionRunner.start()."""
        import castor.api as api_mod
        from castor.mission import MissionRunner

        mock_runner = MagicMock(spec=MissionRunner)
        mock_runner.start.return_value = "test-job-id"
        api_mod.state.mission_runner = mock_runner

        mission_client.post(
            "/api/nav/mission",
            json={"waypoints": [{"distance_m": 0.5}], "loop": True},
        )
        call_kwargs = mock_runner.start.call_args
        assert call_kwargs.kwargs.get("loop") is True or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] is True
        )
