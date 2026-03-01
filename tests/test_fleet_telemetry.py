"""Tests for Fleet dashboard with real-time telemetry charts.

Issue #200 — FleetAggregator, RobotSnapshot, real-time polling.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_METRICS = """\
# HELP opencastor_uptime_seconds Robot uptime
# TYPE opencastor_uptime_seconds gauge
opencastor_uptime_seconds{robot="alex"} 3600.0
opencastor_loops_total{robot="alex"} 1234.0
opencastor_brain_up{robot="alex"} 1.0
opencastor_driver_up{robot="alex"} 1.0
opencastor_safety_score{robot="alex"} 0.95
opencastor_active_channels{robot="alex"} 2.0
"""

_FLEET_CFG = {
    "fleet": {
        "peers": [
            {"name": "alex", "url": "http://alex.local:8000"},
            {"name": "bob", "url": "http://bob.local:8000"},
        ]
    }
}


def _make_agg(**kwargs):
    from castor.fleet_telemetry import FleetAggregator

    robots = [
        {"name": "alex", "url": "http://alex:8000", "token": ""},
        {"name": "bob", "url": "http://bob:8000", "token": ""},
    ]
    return FleetAggregator(robots, **kwargs)


# ---------------------------------------------------------------------------
# RobotSnapshot
# ---------------------------------------------------------------------------


class TestRobotSnapshot:
    def test_basic_attrs(self):
        from castor.fleet_telemetry import RobotSnapshot

        snap = RobotSnapshot(
            name="alex",
            base_url="http://alex:8000",
            ok=True,
            metrics_text=_SAMPLE_METRICS,
            latency_ms=12.5,
        )
        assert snap.name == "alex"
        assert snap.ok is True
        assert snap.latency_ms == 12.5

    def test_extract_uptime(self):
        from castor.fleet_telemetry import RobotSnapshot

        snap = RobotSnapshot("a", "http://a:8000", True, metrics_text=_SAMPLE_METRICS)
        assert snap.extract_metric("opencastor_uptime_seconds") == pytest.approx(3600.0)

    def test_extract_loops_total(self):
        from castor.fleet_telemetry import RobotSnapshot

        snap = RobotSnapshot("a", "http://a:8000", True, metrics_text=_SAMPLE_METRICS)
        assert snap.extract_metric("opencastor_loops_total") == pytest.approx(1234.0)

    def test_extract_missing_returns_none(self):
        from castor.fleet_telemetry import RobotSnapshot

        snap = RobotSnapshot("a", "http://a:8000", True, metrics_text=_SAMPLE_METRICS)
        assert snap.extract_metric("nonexistent_metric") is None

    def test_to_dict_structure(self):
        from castor.fleet_telemetry import RobotSnapshot

        snap = RobotSnapshot("alex", "http://alex:8000", True, metrics_text=_SAMPLE_METRICS)
        d = snap.to_dict()
        assert d["name"] == "alex"
        assert d["ok"] is True
        assert "metrics" in d
        assert "uptime_s" in d["metrics"]

    def test_to_dict_metrics_values(self):
        from castor.fleet_telemetry import RobotSnapshot

        snap = RobotSnapshot("a", "http://a:8000", True, metrics_text=_SAMPLE_METRICS)
        d = snap.to_dict()
        assert d["metrics"]["uptime_s"] == pytest.approx(3600.0)
        assert d["metrics"]["safety_score"] == pytest.approx(0.95)

    def test_error_snapshot(self):
        from castor.fleet_telemetry import RobotSnapshot

        snap = RobotSnapshot("a", "http://a:8000", False, error="Connection refused")
        d = snap.to_dict()
        assert d["ok"] is False
        assert "Connection refused" in d["error"]

    def test_timestamp_set(self):
        from castor.fleet_telemetry import RobotSnapshot

        before = time.time()
        snap = RobotSnapshot("a", "http://a:8000", True)
        after = time.time()
        assert before <= snap.timestamp <= after


# ---------------------------------------------------------------------------
# FleetAggregator init
# ---------------------------------------------------------------------------


class TestFleetAggregatorInit:
    def test_robot_count(self):
        agg = _make_agg()
        assert agg.robot_count == 2

    def test_robot_names(self):
        agg = _make_agg()
        assert set(agg.robot_names) == {"alex", "bob"}

    def test_from_config(self):
        from castor.fleet_telemetry import FleetAggregator

        agg = FleetAggregator.from_config(_FLEET_CFG)
        assert agg.robot_count == 2
        assert "alex" in agg.robot_names

    def test_from_config_empty(self):
        from castor.fleet_telemetry import FleetAggregator

        agg = FleetAggregator.from_config({})
        assert agg.robot_count == 0


# ---------------------------------------------------------------------------
# FleetAggregator.fetch_all — mocked HTTP
# ---------------------------------------------------------------------------


class TestFetchAll:
    def test_returns_snapshots(self):
        agg = _make_agg()
        with patch.object(agg, "_get", return_value=_SAMPLE_METRICS):
            snaps = agg.fetch_all(force=True)
        assert len(snaps) == 2

    def test_failed_robot_marked_not_ok(self):
        agg = _make_agg()

        def fake_get(url, **kwargs):
            if "alex" in url:
                raise ConnectionError("refused")
            return _SAMPLE_METRICS

        with patch.object(agg, "_get", side_effect=fake_get):
            snaps = agg.fetch_all(force=True)

        alex_snap = next(s for s in snaps if s.name == "alex")
        assert alex_snap.ok is False

    def test_cache_used_within_ttl(self):
        agg = _make_agg(ttl_s=60)
        with patch.object(agg, "_get", return_value=_SAMPLE_METRICS) as mock_get:
            agg.fetch_all(force=True)
            agg.fetch_all()  # should use cache
        # Each robot hits /api/metrics and /health = 2 endpoints × 2 robots = 4 calls
        assert mock_get.call_count == 4  # only first fetch hits the network


# ---------------------------------------------------------------------------
# FleetAggregator.to_dict
# ---------------------------------------------------------------------------


class TestToDict:
    def test_structure(self):
        agg = _make_agg()
        with patch.object(agg, "_get", return_value=_SAMPLE_METRICS):
            d = agg.to_dict(force=True)
        assert "robots" in d
        assert "count" in d
        assert "healthy" in d
        assert "timestamp" in d

    def test_count_and_healthy(self):
        agg = _make_agg()
        with patch.object(agg, "_get", return_value=_SAMPLE_METRICS):
            d = agg.to_dict(force=True)
        assert d["count"] == 2
        assert d["healthy"] == 2


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


class TestFleetSingleton:
    def test_get_fleet_aggregator(self):
        from castor.fleet_telemetry import get_fleet_aggregator, reset_fleet_aggregator

        reset_fleet_aggregator()
        agg = get_fleet_aggregator(_FLEET_CFG)
        assert agg is not None

    def test_returns_same_instance(self):
        from castor.fleet_telemetry import get_fleet_aggregator, reset_fleet_aggregator

        reset_fleet_aggregator()
        a1 = get_fleet_aggregator(_FLEET_CFG)
        a2 = get_fleet_aggregator()
        assert a1 is a2

    def test_reset_creates_new_instance(self):
        from castor.fleet_telemetry import get_fleet_aggregator, reset_fleet_aggregator

        reset_fleet_aggregator()
        a1 = get_fleet_aggregator(_FLEET_CFG)
        reset_fleet_aggregator()
        a2 = get_fleet_aggregator(_FLEET_CFG)
        assert a1 is not a2


# ---------------------------------------------------------------------------
# FLEET_DASHBOARD_HTML
# ---------------------------------------------------------------------------


class TestFleetDashboardHTML:
    def test_dashboard_html_exists(self):
        from castor.fleet_telemetry import FLEET_DASHBOARD_HTML

        assert len(FLEET_DASHBOARD_HTML) > 0

    def test_dashboard_has_chart_js(self):
        from castor.fleet_telemetry import FLEET_DASHBOARD_HTML

        assert "chart.js" in FLEET_DASHBOARD_HTML.lower()

    def test_dashboard_has_telemetry_fetch(self):
        from castor.fleet_telemetry import FLEET_DASHBOARD_HTML

        assert "/api/fleet/telemetry" in FLEET_DASHBOARD_HTML

    def test_dashboard_polls(self):
        from castor.fleet_telemetry import FLEET_DASHBOARD_HTML

        assert "setInterval" in FLEET_DASHBOARD_HTML
