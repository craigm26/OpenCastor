"""Tests for castor.mission.MissionRunner and /api/nav/mission* endpoints."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

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
            executing_event.set()  # signal: we are inside execute
            release_event.wait(timeout=3)  # wait until test says go
            return {"ok": True, "duration_s": 0.05}

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.side_effect = slow_execute
            r.start([{"distance_m": 0.5}, {"distance_m": 0.5}, {"distance_m": 0.5}])
            executing_event.wait(timeout=3)  # wait until first step is running
            r.stop()
            release_event.set()  # unblock the slow execute

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
            r.start([{"distance_m": 0.1, "dwell_s": 0.2}, {"distance_m": 0.1}])
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
    import contextlib

    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    # Also replace the lifespan context manager with a no-op so that real
    # hardware/config initialisation is skipped during tests.
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan

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
        app.router.lifespan_context = original_lifespan
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


# ── /api/nav/mission/generate endpoint tests ─────────────────────────────────


class TestMissionGenerateAPI:
    def _waypoints_json(self):
        return (
            '[{"distance_m":0.5,"heading_deg":0,"speed":0.6,"dwell_s":0,"label":"forward"},'
            '{"distance_m":0.3,"heading_deg":90,"speed":0.5,"dwell_s":1.0,"label":"turn right"},'
            '{"distance_m":0.5,"heading_deg":-90,"speed":0.6,"dwell_s":0,"label":"return"}]'
        )

    def test_generate_returns_waypoints(self, mission_client):
        import castor.api as api_mod

        mock_brain = MagicMock()
        mock_brain.think.return_value = MagicMock(raw_text=self._waypoints_json())
        api_mod.state.brain = mock_brain

        resp = mission_client.post(
            "/api/nav/mission/generate",
            json={"description": "patrol the room", "steps_hint": 3},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["waypoints"], list)
        assert len(body["waypoints"]) == 3
        assert body["waypoints"][0]["distance_m"] == 0.5
        assert body["loop"] is False

    def test_generate_loop_flag_forwarded(self, mission_client):
        import castor.api as api_mod

        mock_brain = MagicMock()
        mock_brain.think.return_value = MagicMock(raw_text=self._waypoints_json())
        api_mod.state.brain = mock_brain

        resp = mission_client.post(
            "/api/nav/mission/generate",
            json={"description": "circle", "steps_hint": 3, "loop": True},
        )
        assert resp.status_code == 200
        assert resp.json()["loop"] is True

    def test_generate_strips_markdown_fences(self, mission_client):
        import castor.api as api_mod

        fenced = "```json\n" + self._waypoints_json() + "\n```"
        mock_brain = MagicMock()
        mock_brain.think.return_value = MagicMock(raw_text=fenced)
        api_mod.state.brain = mock_brain

        resp = mission_client.post(
            "/api/nav/mission/generate",
            json={"description": "patrol", "steps_hint": 3},
        )
        assert resp.status_code == 200
        assert len(resp.json()["waypoints"]) == 3

    def test_generate_no_brain_returns_503(self, mission_client):
        import castor.api as api_mod

        api_mod.state.brain = None
        resp = mission_client.post(
            "/api/nav/mission/generate",
            json={"description": "patrol", "steps_hint": 3},
        )
        assert resp.status_code == 503

    def test_generate_no_driver_returns_503(self, mission_client):
        import castor.api as api_mod

        api_mod.state.brain = MagicMock()
        api_mod.state.driver = None
        resp = mission_client.post(
            "/api/nav/mission/generate",
            json={"description": "patrol", "steps_hint": 3},
        )
        assert resp.status_code == 503

    def test_generate_bad_json_returns_422(self, mission_client):
        import castor.api as api_mod

        mock_brain = MagicMock()
        mock_brain.think.return_value = MagicMock(raw_text="not valid json at all")
        api_mod.state.brain = mock_brain

        resp = mission_client.post(
            "/api/nav/mission/generate",
            json={"description": "patrol", "steps_hint": 3},
        )
        assert resp.status_code == 422

    def test_generate_empty_array_returns_422(self, mission_client):
        import castor.api as api_mod

        mock_brain = MagicMock()
        mock_brain.think.return_value = MagicMock(raw_text="[]")
        api_mod.state.brain = mock_brain

        resp = mission_client.post(
            "/api/nav/mission/generate",
            json={"description": "patrol", "steps_hint": 3},
        )
        assert resp.status_code == 422

    def test_generate_normalises_missing_keys(self, mission_client):
        """Waypoints with missing optional keys get sensible defaults."""
        import castor.api as api_mod

        sparse = '[{"distance_m": 1.0}]'
        mock_brain = MagicMock()
        mock_brain.think.return_value = MagicMock(raw_text=sparse)
        api_mod.state.brain = mock_brain

        resp = mission_client.post(
            "/api/nav/mission/generate",
            json={"description": "straight", "steps_hint": 1},
        )
        assert resp.status_code == 200
        wp = resp.json()["waypoints"][0]
        assert wp["distance_m"] == 1.0
        assert wp["heading_deg"] == 0.0
        assert wp["speed"] == 0.6
        assert wp["dwell_s"] == 0.0
        assert wp["label"] == "step-1"


# ===========================================================================
# Issue #238 — mission/generate execute=true flag
# ===========================================================================


class TestMissionGenerateExecute:
    _wps_json = '[{"distance_m":0.5,"heading_deg":0,"speed":0.6,"dwell_s":0,"label":"fwd"}]'

    def test_execute_false_returns_null_job_id(self, mission_client):
        """execute=false (default) returns job_id=null and does not start a runner."""
        import castor.api as api_mod

        mock_brain = MagicMock()
        mock_brain.think.return_value = MagicMock(raw_text=self._wps_json)
        api_mod.state.brain = mock_brain

        resp = mission_client.post(
            "/api/nav/mission/generate",
            json={"description": "go forward", "steps_hint": 1, "execute": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] is None

    def test_execute_true_returns_job_id_and_starts_mission(self, mission_client):
        """execute=true starts the mission and returns a non-null job_id."""
        import castor.api as api_mod

        mock_brain = MagicMock()
        mock_brain.think.return_value = MagicMock(raw_text=self._wps_json)
        api_mod.state.brain = mock_brain

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.01}
            resp = mission_client.post(
                "/api/nav/mission/generate",
                json={"description": "go forward", "steps_hint": 1, "execute": True},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] is not None
        assert len(body["job_id"]) == 36  # UUID4


# ===========================================================================
# Issue #243 — mission replay by job_id
# ===========================================================================


class TestMissionReplay:
    def test_replay_known_job_starts_new_mission(self, mission_client):
        """Replaying a known job_id launches a new mission and returns new_job_id."""
        import castor.api as api_mod
        from castor.mission import MissionRunner

        driver = _make_driver()
        api_mod.state.driver = driver

        runner = MissionRunner(driver, _make_config())
        api_mod.state.mission_runner = runner

        waypoints = [
            {"distance_m": 0.3, "heading_deg": 0, "speed": 0.5, "dwell_s": 0, "label": "x"}
        ]

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.01}
            original_job = runner.start(waypoints)
            # Wait for completion
            for _ in range(50):
                if not runner.status()["running"]:
                    break
                time.sleep(0.05)

            resp = mission_client.post(f"/api/nav/mission/replay/{original_job}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["new_job_id"] != original_job
        assert body["waypoints"] == 1

    def test_replay_unknown_job_returns_404(self, mission_client):
        """Replaying an unknown job_id returns 404."""
        resp = mission_client.post("/api/nav/mission/replay/nonexistent-job-id")
        assert resp.status_code == 404

    def test_history_endpoint_returns_past_missions(self, mission_client):
        """GET /api/nav/mission/history lists completed missions."""
        import castor.api as api_mod
        from castor.mission import MissionRunner

        driver = _make_driver()
        api_mod.state.driver = driver
        runner = MissionRunner(driver, _make_config())
        api_mod.state.mission_runner = runner

        waypoints = [{"distance_m": 0.2, "heading_deg": 0}]
        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.01}
            job_id = runner.start(waypoints)
            for _ in range(50):
                if not runner.status()["running"]:
                    break
                time.sleep(0.05)

        resp = mission_client.get("/api/nav/mission/history")
        assert resp.status_code == 200
        history = resp.json()["history"]
        assert any(h["job_id"] == job_id for h in history)

    def test_history_empty_when_no_runner(self, mission_client):
        """GET /api/nav/mission/history returns empty list when no runner exists."""
        import castor.api as api_mod

        api_mod.state.mission_runner = None
        resp = mission_client.get("/api/nav/mission/history")
        assert resp.status_code == 200
        assert resp.json()["history"] == []


# ===========================================================================
# Issues #242 + #244 — Arduino sensor and servo endpoints
# ===========================================================================


class TestArduinoEndpoints:
    def _arduino_driver(self):
        """Mock driver that looks like ArduinoSerialDriver."""
        d = MagicMock()
        d.query_sensor = MagicMock(return_value={"distance_cm": 42.0})
        d.set_servo = MagicMock(return_value={"ok": True})
        return d

    def test_sensor_read_returns_data(self, mission_client):
        """GET /api/arduino/sensor/{id} returns data dict from driver."""
        import castor.api as api_mod

        api_mod.state.driver = self._arduino_driver()
        resp = mission_client.get("/api/arduino/sensor/hcsr04")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["sensor_id"] == "hcsr04"
        assert body["data"]["distance_cm"] == 42.0

    def test_sensor_read_none_returns_unavailable(self, mission_client):
        """When driver.query_sensor() returns None, endpoint returns available=false."""
        import castor.api as api_mod

        d = self._arduino_driver()
        d.query_sensor.return_value = None
        api_mod.state.driver = d

        resp = mission_client.get("/api/arduino/sensor/hcsr04")
        assert resp.status_code == 200
        assert resp.json()["available"] is False

    def test_sensor_no_driver_returns_503(self, mission_client):
        import castor.api as api_mod

        api_mod.state.driver = None
        resp = mission_client.get("/api/arduino/sensor/hcsr04")
        assert resp.status_code == 503

    def test_sensor_driver_without_method_returns_503(self, mission_client):
        import castor.api as api_mod

        d = MagicMock(spec=[])  # no methods
        api_mod.state.driver = d
        resp = mission_client.get("/api/arduino/sensor/hcsr04")
        assert resp.status_code == 503

    def test_servo_set_returns_ok(self, mission_client):
        """POST /api/arduino/servo sets the servo and returns ok."""
        import castor.api as api_mod

        api_mod.state.driver = self._arduino_driver()
        resp = mission_client.post("/api/arduino/servo", json={"pin": 9, "angle": 90})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["pin"] == 9
        assert body["angle"] == 90

    def test_servo_angle_out_of_range_returns_422(self, mission_client):
        import castor.api as api_mod

        api_mod.state.driver = self._arduino_driver()
        resp = mission_client.post("/api/arduino/servo", json={"pin": 9, "angle": 200})
        assert resp.status_code == 422

    def test_servo_no_driver_returns_503(self, mission_client):
        import castor.api as api_mod

        api_mod.state.driver = None
        resp = mission_client.post("/api/arduino/servo", json={"pin": 9, "angle": 45})
        assert resp.status_code == 503

    def test_servo_driver_without_method_returns_503(self, mission_client):
        import castor.api as api_mod

        d = MagicMock(spec=[])
        api_mod.state.driver = d
        resp = mission_client.post("/api/arduino/servo", json={"pin": 9, "angle": 45})
        assert resp.status_code == 503


# ===========================================================================
# Issue #249 — Mission geo-fence position tracking
# ===========================================================================


class TestMissionGeoFence:
    def test_position_starts_at_origin(self):
        """Position should be (0, 0, 0) when a new MissionRunner is created."""
        from castor.mission import MissionRunner

        runner = MissionRunner(_make_driver(), _make_config())
        pos = runner.position()
        assert pos["x_m"] == 0.0
        assert pos["y_m"] == 0.0
        assert pos["heading_deg"] == 0.0

    def test_position_updates_after_waypoint(self):
        """Dead-reckoning position updates after a completed waypoint."""
        from castor.mission import MissionRunner

        driver = _make_driver()
        runner = MissionRunner(driver, _make_config())

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.01}
            runner.start([{"distance_m": 1.0, "heading_deg": 0}])
            for _ in range(50):
                if not runner.status()["running"]:
                    break
                time.sleep(0.05)

        pos = runner.position()
        # Heading 0° → y_m increases by distance_m * cos(0) = 1.0
        assert abs(pos["y_m"] - 1.0) < 0.01
        assert abs(pos["x_m"]) < 0.01

    def test_geofence_breach_stops_mission(self):
        """Mission aborts when position leaves the geo-fence bounds."""
        from castor.mission import MissionRunner

        driver = _make_driver()
        runner = MissionRunner(driver, _make_config())
        # Tiny fence: only 0.1m in each direction — first 1m waypoint breaches it
        runner.set_geofence({"x_min": -0.1, "x_max": 0.1, "y_min": -0.1, "y_max": 0.1})

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.01}
            runner.start([{"distance_m": 1.0, "heading_deg": 0}])
            for _ in range(50):
                if not runner.status()["running"]:
                    break
                time.sleep(0.05)

        status = runner.status()
        assert not status["running"]
        assert "geofence_breach" in (status["error"] or "")

    def test_no_geofence_allows_any_position(self):
        """Without a geo-fence, mission completes normally regardless of position."""
        from castor.mission import MissionRunner

        driver = _make_driver()
        runner = MissionRunner(driver, _make_config())

        with patch("castor.mission.WaypointNav") as MockNav:
            MockNav.return_value.execute.return_value = {"ok": True, "duration_s": 0.01}
            runner.start([{"distance_m": 100.0, "heading_deg": 0}])
            for _ in range(50):
                if not runner.status()["running"]:
                    break
                time.sleep(0.05)

        status = runner.status()
        assert status["error"] is None or status["error"] == ""

    def test_geofence_api_set_returns_ok(self, mission_client):
        """POST /api/nav/mission/geofence sets a geo-fence and returns 200."""
        resp = mission_client.post(
            "/api/nav/mission/geofence",
            json={"x_min": -2.0, "x_max": 2.0, "y_min": -2.0, "y_max": 2.0},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_geofence_api_invalid_bounds_returns_422(self, mission_client):
        """Inverted bounds (x_min >= x_max) return 422."""
        resp = mission_client.post(
            "/api/nav/mission/geofence",
            json={"x_min": 2.0, "x_max": -2.0, "y_min": -2.0, "y_max": 2.0},
        )
        assert resp.status_code == 422

    def test_geofence_api_clear_returns_null(self, mission_client):
        """DELETE /api/nav/mission/geofence returns geofence: null."""
        import castor.api as api_mod
        from castor.mission import MissionRunner

        api_mod.state.mission_runner = MissionRunner(_make_driver(), _make_config())
        api_mod.state.mission_runner.set_geofence(
            {"x_min": -1.0, "x_max": 1.0, "y_min": -1.0, "y_max": 1.0}
        )

        resp = mission_client.delete("/api/nav/mission/geofence")
        assert resp.status_code == 200
        assert resp.json()["geofence"] is None

    def test_position_api_returns_origin_when_no_runner(self, mission_client):
        """GET /api/nav/mission/position returns (0,0,0) when no runner exists."""
        import castor.api as api_mod

        api_mod.state.mission_runner = None
        resp = mission_client.get("/api/nav/mission/position")
        assert resp.status_code == 200
        body = resp.json()
        assert body["x_m"] == 0.0
        assert body["y_m"] == 0.0


# ===========================================================================
# Issue #250 — Recording annotations
# ===========================================================================


class TestRecordingAnnotations:
    def _recorder_with_recording(self, tmp_path):
        """Return (recorder, rec_id) with an active recording in a temp dir."""
        from castor.recorder import VideoRecorder

        recorder = VideoRecorder(output_dir=tmp_path)
        rec_id = recorder.start("test-rec")
        recorder.stop()
        return recorder, rec_id

    def test_add_annotation_returns_id(self, tmp_path):
        recorder, rec_id = self._recorder_with_recording(tmp_path)
        ann_id = recorder.add_annotation(rec_id, 1.5, "moving forward", "move")
        assert ann_id is not None
        assert len(ann_id) == 36  # UUID4

    def test_get_annotations_returns_sorted_list(self, tmp_path):
        recorder, rec_id = self._recorder_with_recording(tmp_path)
        recorder.add_annotation(rec_id, 3.0, "third")
        recorder.add_annotation(rec_id, 1.0, "first")
        recorder.add_annotation(rec_id, 2.0, "second")

        annotations = recorder.get_annotations(rec_id)
        assert annotations is not None
        assert len(annotations) == 3
        timestamps = [a["timestamp_s"] for a in annotations]
        assert timestamps == sorted(timestamps)

    def test_delete_annotation_returns_true(self, tmp_path):
        recorder, rec_id = self._recorder_with_recording(tmp_path)
        ann_id = recorder.add_annotation(rec_id, 0.5, "test label")
        deleted = recorder.delete_annotation(rec_id, ann_id)
        assert deleted is True
        assert recorder.get_annotations(rec_id) == []

    def test_delete_nonexistent_returns_false(self, tmp_path):
        recorder, rec_id = self._recorder_with_recording(tmp_path)
        assert recorder.delete_annotation(rec_id, "nonexistent-id") is False

    def test_add_annotation_unknown_rec_returns_none(self, tmp_path):
        from castor.recorder import VideoRecorder

        recorder = VideoRecorder(output_dir=tmp_path)
        result = recorder.add_annotation("nonexistent-id", 1.0, "label")
        assert result is None

    def test_get_annotations_unknown_rec_returns_none(self, tmp_path):
        from castor.recorder import VideoRecorder

        recorder = VideoRecorder(output_dir=tmp_path)
        assert recorder.get_annotations("nonexistent-id") is None
