"""
tests/test_safety_telemetry.py — Unit + API tests for castor/safety_telemetry.py.

Covers:
  - SafetyEventLogger.log(), recent(), stats()
  - Event type canonicalization
  - API: GET /api/safety/events, /stats, POST /api/safety/test-bounds
"""

from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def tel():
    from castor.safety_telemetry import SafetyEventLogger

    logger = SafetyEventLogger()
    yield logger
    logger.clear()


def test_log_returns_event_dict(tel):
    ev = tel.log("estop", detail="emergency stop triggered")
    assert ev["event_type"] == "estop"
    assert ev["detail"] == "emergency stop triggered"
    assert "timestamp" in ev
    assert "id" in ev


def test_unknown_type_canonicalized(tel):
    ev = tel.log("something_weird", detail="test")
    assert ev["event_type"] == "other"


def test_recent_newest_first(tel):
    tel.log("estop", detail="first")
    time.sleep(0.01)
    tel.log("bounds_violation", detail="second")
    recent = tel.recent(limit=10)
    assert recent[0]["detail"] == "second"
    assert recent[1]["detail"] == "first"


def test_recent_limit(tel):
    for i in range(10):
        tel.log("estop", detail=str(i))
    assert len(tel.recent(limit=3)) == 3


def test_recent_filter_by_type(tel):
    tel.log("estop")
    tel.log("bounds_violation")
    tel.log("estop")
    estops = tel.recent(event_type="estop")
    assert all(e["event_type"] == "estop" for e in estops)
    assert len(estops) == 2


def test_stats_totals(tel):
    tel.log("estop")
    tel.log("bounds_violation")
    tel.log("estop")
    stats = tel.stats()
    assert stats["total_events"] == 3
    assert stats["by_type"]["estop"] == 2
    assert stats["by_type"]["bounds_violation"] == 1


def test_stats_last_24h(tel):
    tel.log("injection_block", detail="injection attempt")
    stats = tel.stats()
    assert stats["last_24h"]["injection_block"] >= 1
    assert stats["last_event"] is not None


def test_stats_empty(tel):
    stats = tel.stats()
    assert stats["total_events"] == 0
    assert stats["last_event"] is None


def test_clear(tel):
    tel.log("estop")
    tel.clear()
    assert tel.stats()["total_events"] == 0


def test_with_action(tel):
    action = {"action": "forward", "speed": 1.5}
    ev = tel.log("bounds_violation", action=action)
    assert ev["action"] == action


def test_singleton():
    import castor.safety_telemetry as m

    m._telemetry = None
    t1 = m.get_telemetry()
    t2 = m.get_telemetry()
    assert t1 is t2
    m._telemetry = None


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    import castor.safety_telemetry as m

    m._telemetry = None

    from fastapi.testclient import TestClient

    from castor.api import app

    return TestClient(app)


def test_api_safety_events_empty(api_client):
    resp = api_client.get("/api/safety/events")
    assert resp.status_code == 200
    assert "events" in resp.json()


def test_api_safety_events_with_data(api_client):
    from castor.safety_telemetry import get_telemetry

    get_telemetry().log("estop", detail="test e-stop")
    resp = api_client.get("/api/safety/events?limit=10")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert any(e["event_type"] == "estop" for e in events)


def test_api_safety_events_type_filter(api_client):
    from castor.safety_telemetry import get_telemetry

    get_telemetry().log("bounds_violation", detail="v1")
    get_telemetry().log("estop", detail="v2")
    resp = api_client.get("/api/safety/events?event_type=bounds_violation")
    assert all(e["event_type"] == "bounds_violation" for e in resp.json()["events"])


def test_api_safety_stats(api_client):
    resp = api_client.get("/api/safety/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_events" in data
    assert "by_type" in data
    assert "last_24h" in data


def test_api_safety_test_bounds(api_client):
    resp = api_client.post(
        "/api/safety/test-bounds",
        json={"action": {"action": "forward", "speed": 0.5}},
    )
    assert resp.status_code in (200, 500)  # 500 if BoundsChecker unavailable
    if resp.status_code == 200:
        data = resp.json()
        assert "within_bounds" in data
