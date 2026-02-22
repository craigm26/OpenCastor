"""Tests for WaypointNav (issue #120) and /api/nav/* endpoints."""

from __future__ import annotations

import collections
import time
from unittest.mock import MagicMock, call, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_driver():
    d = MagicMock()
    d.move = MagicMock()
    d.stop = MagicMock()
    d.close = MagicMock()
    return d


def _make_config(**physics_kwargs):
    physics = {"wheel_circumference_m": 0.21, "turn_time_per_deg_s": 0.011}
    physics.update(physics_kwargs)
    return {"physics": physics}


# ---------------------------------------------------------------------------
# WaypointNav unit tests
# ---------------------------------------------------------------------------


class TestWaypointNav:
    def test_straight_drive(self):
        """distance_m=0.5, heading_deg=0 -> move called, stop called."""
        from castor.nav import WaypointNav

        driver = _make_driver()
        nav = WaypointNav(driver, _make_config())

        with patch("castor.nav.time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic.side_effect = [0.0, 2.381]  # start, end
            result = nav.execute(distance_m=0.5, heading_deg=0)

        driver.move.assert_called_once()
        _, kwargs = driver.move.call_args
        # linear should be positive
        linear_val = driver.move.call_args[1].get("linear") or driver.move.call_args[0][0]
        assert linear_val > 0
        driver.stop.assert_called()
        assert result["ok"] is True
        assert result["distance_m"] == 0.5

    def test_turn_before_drive(self):
        """heading_deg=90 -> angular move first, then forward move."""
        from castor.nav import WaypointNav

        driver = _make_driver()
        nav = WaypointNav(driver, _make_config())

        call_sequence = []

        def record_move(**kwargs):
            call_sequence.append(("move", kwargs))

        def record_stop():
            call_sequence.append(("stop",))

        driver.move.side_effect = lambda **kw: call_sequence.append(("move", kw))
        driver.stop.side_effect = lambda: call_sequence.append(("stop",))

        with patch("castor.nav.time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic.side_effect = [0.0, 3.0]
            nav.execute(distance_m=0.5, heading_deg=90)

        moves = [c for c in call_sequence if c[0] == "move"]
        assert len(moves) == 2, f"Expected 2 move calls, got {len(moves)}: {call_sequence}"
        # First move should be angular (turn)
        first_move_kwargs = moves[0][1]
        assert first_move_kwargs.get("linear", 0) == 0.0
        assert first_move_kwargs.get("angular", 0) != 0.0
        # Second move should be forward (drive)
        second_move_kwargs = moves[1][1]
        assert second_move_kwargs.get("linear", 0) > 0.0
        assert second_move_kwargs.get("angular", 0) == 0.0

    def test_zero_distance(self):
        """distance_m=0.0 -> no forward move call, stop still called."""
        from castor.nav import WaypointNav

        driver = _make_driver()
        nav = WaypointNav(driver, _make_config())

        with patch("castor.nav.time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic.side_effect = [0.0, 0.1]
            result = nav.execute(distance_m=0.0, heading_deg=0)

        # No moves at all (no turn, no drive)
        driver.move.assert_not_called()
        driver.stop.assert_called()
        assert result["ok"] is True

    def test_negative_distance_reverse(self):
        """distance_m=-0.3 -> move called with negative linear."""
        from castor.nav import WaypointNav

        driver = _make_driver()
        nav = WaypointNav(driver, _make_config())

        with patch("castor.nav.time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic.side_effect = [0.0, 2.0]
            nav.execute(distance_m=-0.3, heading_deg=0)

        driver.move.assert_called_once()
        # Extract the linear argument
        args, kwargs = driver.move.call_args
        linear = kwargs.get("linear", args[0] if args else None)
        assert linear < 0, f"Expected negative linear, got {linear}"

    def test_speed_clamped(self):
        """speed>1.0 -> clamped to 1.0."""
        from castor.nav import WaypointNav

        driver = _make_driver()
        nav = WaypointNav(driver, _make_config())

        with patch("castor.nav.time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic.side_effect = [0.0, 1.0]
            result = nav.execute(distance_m=0.5, heading_deg=0, speed=5.0)

        # Move should be called with at most 1.0 linear
        args, kwargs = driver.move.call_args
        linear = kwargs.get("linear", args[0] if args else None)
        assert abs(linear) <= 1.0

    def test_safety_stop_on_exception(self):
        """driver.move raises -> stop() still called in finally block."""
        from castor.nav import WaypointNav

        driver = _make_driver()
        driver.move.side_effect = RuntimeError("motor fault")
        nav = WaypointNav(driver, _make_config())

        with patch("castor.nav.time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic.side_effect = [0.0, 0.1]
            with pytest.raises(RuntimeError):
                nav.execute(distance_m=0.5, heading_deg=0)

        driver.stop.assert_called()

    def test_return_dict_fields(self):
        """execute() returns dict with ok, duration_s, distance_m, heading_deg."""
        from castor.nav import WaypointNav

        driver = _make_driver()
        nav = WaypointNav(driver, _make_config())

        with patch("castor.nav.time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic.side_effect = [0.0, 1.5]
            result = nav.execute(distance_m=0.5, heading_deg=30, speed=0.6)

        assert "ok" in result
        assert "duration_s" in result
        assert "distance_m" in result
        assert "heading_deg" in result
        assert result["distance_m"] == 0.5
        assert result["heading_deg"] == 30

    def test_custom_physics_config(self):
        """Custom wheel_circumference_m and turn_time_per_deg_s are respected."""
        from castor.nav import WaypointNav

        driver = _make_driver()
        nav = WaypointNav(driver, _make_config(wheel_circumference_m=0.30, turn_time_per_deg_s=0.02))

        assert nav.wheel_circumference_m == 0.30
        assert nav.turn_time_per_deg_s == 0.02


# ---------------------------------------------------------------------------
# API endpoint tests  /api/nav/*
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)
    monkeypatch.delenv("OPENCASTOR_CONFIG", raising=False)

    import castor.api as api_mod

    api_mod.state.config = None
    api_mod.state.brain = None
    api_mod.state.driver = None
    api_mod.state.channels = {}
    api_mod.state.last_thought = None
    api_mod.state.boot_time = time.time()
    api_mod.state.fs = None
    api_mod.state.ruri = None
    api_mod.state.mdns_broadcaster = None
    api_mod.state.mdns_browser = None
    api_mod.state.rcan_router = None
    api_mod.state.capability_registry = None
    api_mod.state.offline_fallback = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.state.learner = None
    api_mod.state.listener = None
    api_mod.state.nav_job = None
    api_mod.API_TOKEN = None
    api_mod._command_history.clear()
    api_mod._webhook_history.clear()
    yield


@pytest.fixture()
def client():
    from castor.api import app

    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.router.on_startup[:] = original_startup
        app.router.on_shutdown[:] = original_shutdown


@pytest.fixture()
def api_mod():
    import castor.api as mod
    return mod


class TestNavEndpoints:
    def test_api_waypoint_no_driver(self, client):
        """state.driver=None -> 503."""
        resp = client.post(
            "/api/nav/waypoint",
            json={"distance_m": 0.5, "heading_deg": 0, "speed": 0.6},
        )
        assert resp.status_code == 503

    def test_api_waypoint_returns_job_id(self, client, api_mod):
        """Mock driver loaded -> 200 with job_id in response."""
        api_mod.state.driver = _make_driver()

        resp = client.post(
            "/api/nav/waypoint",
            json={"distance_m": 0.5, "heading_deg": 0, "speed": 0.6},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "job_id" in body
        assert body["job_id"] is not None
        assert body["running"] is True

    def test_api_nav_status_no_job(self, client):
        """No job started -> status returns running=False."""
        resp = client.get("/api/nav/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is False
        assert body["job_id"] is None

    def test_api_nav_status_running(self, client, api_mod):
        """nav_job set to running -> status shows running=True."""
        api_mod.state.nav_job = {
            "job_id": "abc-123",
            "running": True,
            "result": None,
        }
        resp = client.get("/api/nav/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is True
        assert body["job_id"] == "abc-123"

    def test_api_nav_status_completed(self, client, api_mod):
        """nav_job set to completed -> status shows running=False with result."""
        api_mod.state.nav_job = {
            "job_id": "xyz-999",
            "running": False,
            "result": {"ok": True, "duration_s": 1.5, "distance_m": 0.5, "heading_deg": 0},
        }
        resp = client.get("/api/nav/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is False
        assert body["result"]["ok"] is True

    def test_api_waypoint_default_speed(self, client, api_mod):
        """speed is optional and defaults to 0.6."""
        api_mod.state.driver = _make_driver()

        resp = client.post(
            "/api/nav/waypoint",
            json={"distance_m": 0.3, "heading_deg": 45},
        )
        assert resp.status_code == 200

    def test_api_waypoint_stores_job_in_state(self, client, api_mod):
        """After request, state.nav_job is set with correct job_id."""
        api_mod.state.driver = _make_driver()

        resp = client.post(
            "/api/nav/waypoint",
            json={"distance_m": 0.5, "heading_deg": 0, "speed": 0.6},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        assert api_mod.state.nav_job is not None
        assert api_mod.state.nav_job["job_id"] == job_id

    def test_api_waypoint_different_job_ids(self, client, api_mod):
        """Each waypoint call produces a unique job_id."""
        api_mod.state.driver = _make_driver()

        resp1 = client.post(
            "/api/nav/waypoint",
            json={"distance_m": 0.5, "heading_deg": 0},
        )
        job_id_1 = resp1.json()["job_id"]

        resp2 = client.post(
            "/api/nav/waypoint",
            json={"distance_m": 0.3, "heading_deg": 45},
        )
        job_id_2 = resp2.json()["job_id"]

        assert job_id_1 != job_id_2
