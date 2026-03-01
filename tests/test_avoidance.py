"""Tests for castor.avoidance (ReactiveAvoider)."""

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_avoider_singleton():
    import castor.avoidance as mod

    mod._singleton = None
    yield
    mod._singleton = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_avoider(driver=None, estop_cb=None, estop_mm=200, slow_mm=500, slow_factor=0.4):
    """Return a ReactiveAvoider with no real hardware sensors.

    The lidar is blocked by making health_check return ok=False.
    castor.depth is importable in the test environment, so the avoider may
    enter 'depth' mode, but _sample_sensors will still return inf (no camera).
    All obstacle-zone assertions are made via _sample_sensors patching.
    """
    import castor.drivers.lidar_driver as lidar_mod

    orig_singleton = lidar_mod._singleton
    lidar_mod._singleton = None

    bad_lidar = MagicMock()
    bad_lidar.health_check.return_value = {"ok": False}

    try:
        with patch("castor.drivers.lidar_driver.get_lidar", return_value=bad_lidar):
            from castor.avoidance import ReactiveAvoider

            avoider = ReactiveAvoider(
                driver=driver,
                estop_callback=estop_cb,
                estop_mm=estop_mm,
                slow_mm=slow_mm,
                slow_factor=slow_factor,
            )
    finally:
        lidar_mod._singleton = orig_singleton

    return avoider


# ---------------------------------------------------------------------------
# Init / enable-disable
# ---------------------------------------------------------------------------


class TestReactiveAvoiderInit:
    def test_starts_enabled(self):
        av = _fresh_avoider()
        assert av.enabled is True

    def test_no_lidar_sensor(self):
        """When no hardware lidar is present, mode is not 'lidar'."""
        av = _fresh_avoider()
        assert av.mode != "lidar"

    def test_disable_sets_enabled_false(self):
        av = _fresh_avoider()
        av.disable()
        assert av.enabled is False

    def test_enable_restores_enabled(self):
        av = _fresh_avoider()
        av.disable()
        av.enable()
        assert av.enabled is True

    def test_initial_zone_is_clear(self):
        av = _fresh_avoider()
        assert av.zone == "clear"


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestReactiveAvoiderStatus:
    def test_status_has_required_keys(self):
        av = _fresh_avoider()
        s = av.status()
        for key in ("enabled", "mode", "nearest_mm", "zone", "estop_mm", "slow_mm", "slow_factor"):
            assert key in s, f"missing key: {key}"

    def test_status_reflects_enable_state(self):
        av = _fresh_avoider()
        av.disable()
        assert av.status()["enabled"] is False

    def test_status_returns_configured_thresholds(self):
        av = _fresh_avoider(estop_mm=150, slow_mm=400, slow_factor=0.3)
        s = av.status()
        assert s["estop_mm"] == 150
        assert s["slow_mm"] == 400
        assert s["slow_factor"] == pytest.approx(0.3, abs=0.001)


# ---------------------------------------------------------------------------
# check_obstacles()
# ---------------------------------------------------------------------------


class TestCheckObstacles:
    def test_clear_zone_when_no_sensor_data(self):
        """With no real sensor, _sample_sensors returns inf → clear."""
        av = _fresh_avoider()
        with patch.object(av, "_sample_sensors", return_value=(float("inf"), {})):
            result = av.check_obstacles()
        assert result["zone"] == "clear"

    def test_estop_zone_when_very_close(self):
        av = _fresh_avoider(estop_mm=200, slow_mm=500)
        with patch.object(av, "_sample_sensors", return_value=(100.0, {})):
            result = av.check_obstacles()
        assert result["zone"] == "estop"

    def test_slow_zone_between_thresholds(self):
        av = _fresh_avoider(estop_mm=200, slow_mm=500)
        with patch.object(av, "_sample_sensors", return_value=(350.0, {})):
            result = av.check_obstacles()
        assert result["zone"] == "slow"

    def test_clear_zone_beyond_slow_threshold(self):
        av = _fresh_avoider(estop_mm=200, slow_mm=500)
        with patch.object(av, "_sample_sensors", return_value=(600.0, {})):
            result = av.check_obstacles()
        assert result["zone"] == "clear"

    def test_nearest_mm_updates(self):
        av = _fresh_avoider()
        with patch.object(av, "_sample_sensors", return_value=(123.0, {})):
            av.check_obstacles()
        assert av.nearest_mm == 123.0


# ---------------------------------------------------------------------------
# move()
# ---------------------------------------------------------------------------


class TestReactiveAvoiderMove:
    def test_estop_zone_sets_linear_zero(self):
        mock_driver = MagicMock()
        av = _fresh_avoider(driver=mock_driver, estop_mm=200)
        with patch.object(av, "_sample_sensors", return_value=(50.0, {})):
            result = av.move({"linear": 0.5, "angular": 0.0})
        assert result["linear"] == 0.0
        assert result.get("_avoidance_zone") == "estop"
        mock_driver.stop.assert_called_once()

    def test_estop_fires_callback(self):
        cb = MagicMock()
        av = _fresh_avoider(estop_cb=cb, estop_mm=200)
        with patch.object(av, "_sample_sensors", return_value=(50.0, {})):
            av.move({"linear": 0.5})
        cb.assert_called_once()

    def test_slow_zone_reduces_linear(self):
        av = _fresh_avoider(estop_mm=200, slow_mm=500, slow_factor=0.4)
        with patch.object(av, "_sample_sensors", return_value=(300.0, {})):
            result = av.move({"linear": 1.0, "angular": 0.0})
        assert result["linear"] == pytest.approx(0.4, abs=0.001)
        assert result.get("_avoidance_zone") == "slow"

    def test_slow_zone_does_not_reduce_reverse(self):
        """Negative linear (reverse) should pass through unmodified in slow zone."""
        av = _fresh_avoider(estop_mm=200, slow_mm=500, slow_factor=0.4)
        with patch.object(av, "_sample_sensors", return_value=(300.0, {})):
            result = av.move({"linear": -0.5, "angular": 0.0})
        assert result["linear"] == pytest.approx(-0.5, abs=0.001)

    def test_disabled_passes_through(self):
        mock_driver = MagicMock()
        av = _fresh_avoider(driver=mock_driver, estop_mm=200)
        av.disable()
        result = av.move({"linear": 0.8, "angular": 0.1})
        assert result["linear"] == 0.8
        mock_driver.move.assert_called_once()

    def test_clear_zone_passes_through_unchanged(self):
        av = _fresh_avoider(estop_mm=200, slow_mm=500)
        with patch.object(av, "_sample_sensors", return_value=(800.0, {})):
            result = av.move({"linear": 0.5, "angular": 0.0})
        assert result["linear"] == pytest.approx(0.5, abs=0.001)
        assert "_avoidance_zone" not in result


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


def test_get_avoider_singleton():
    import castor.drivers.lidar_driver as lidar_mod

    orig = lidar_mod._singleton
    lidar_mod._singleton = None

    bad_lidar = MagicMock()
    bad_lidar.health_check.return_value = {"ok": False}
    try:
        with patch("castor.drivers.lidar_driver.get_lidar", return_value=bad_lidar):
            from castor.avoidance import get_avoider

            a1 = get_avoider()
            a2 = get_avoider()
    finally:
        lidar_mod._singleton = orig
    assert a1 is a2
