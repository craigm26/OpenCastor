"""Tests for IMUDriver orientation tracking (issue #308)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Singleton reset (autouse)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_imu_singleton():
    import castor.drivers.imu_driver as mod

    mod._singleton = None
    yield
    mod._singleton = None


# ---------------------------------------------------------------------------
# Helper: build a driver in guaranteed mock mode (no smbus2)
# ---------------------------------------------------------------------------


def _mock_driver():
    """Return an IMUDriver forced into mock mode."""
    with patch("castor.drivers.imu_driver.HAS_SMBUS2", False):
        from castor.drivers.imu_driver import IMUDriver

        return IMUDriver(bus=1, model="auto")


# ---------------------------------------------------------------------------
# orientation() — return structure
# ---------------------------------------------------------------------------


class TestOrientationStructure:
    def test_orientation_returns_dict(self):
        drv = _mock_driver()
        result = drv.orientation()
        assert isinstance(result, dict)

    def test_orientation_has_yaw_deg(self):
        drv = _mock_driver()
        assert "yaw_deg" in drv.orientation()

    def test_orientation_has_pitch_deg(self):
        drv = _mock_driver()
        assert "pitch_deg" in drv.orientation()

    def test_orientation_has_roll_deg(self):
        drv = _mock_driver()
        assert "roll_deg" in drv.orientation()

    def test_orientation_has_confidence(self):
        drv = _mock_driver()
        assert "confidence" in drv.orientation()

    def test_orientation_has_mode(self):
        drv = _mock_driver()
        assert "mode" in drv.orientation()

    def test_orientation_has_all_required_keys(self):
        drv = _mock_driver()
        required = {"yaw_deg", "pitch_deg", "roll_deg", "confidence", "mode"}
        result = drv.orientation()
        assert required.issubset(result.keys())


# ---------------------------------------------------------------------------
# orientation() — mock-mode values
# ---------------------------------------------------------------------------


class TestOrientationMockValues:
    def test_mock_yaw_is_zero(self):
        drv = _mock_driver()
        assert drv.orientation()["yaw_deg"] == 0.0

    def test_mock_pitch_is_zero(self):
        drv = _mock_driver()
        assert drv.orientation()["pitch_deg"] == 0.0

    def test_mock_roll_is_zero(self):
        drv = _mock_driver()
        assert drv.orientation()["roll_deg"] == 0.0

    def test_mock_confidence_is_0_5(self):
        drv = _mock_driver()
        assert drv.orientation()["confidence"] == pytest.approx(0.5)

    def test_mock_mode_field_is_mock(self):
        drv = _mock_driver()
        assert drv.orientation()["mode"] == "mock"

    def test_confidence_is_float_in_range(self):
        drv = _mock_driver()
        conf = drv.orientation()["confidence"]
        assert isinstance(conf, float)
        assert 0.0 <= conf <= 1.0

    def test_orientation_called_twice_no_exception(self):
        drv = _mock_driver()
        drv.orientation()
        drv.orientation()  # Must not raise


# ---------------------------------------------------------------------------
# reset_orientation()
# ---------------------------------------------------------------------------


class TestResetOrientation:
    def test_reset_zeros_yaw(self):
        drv = _mock_driver()
        drv._orientation["yaw_deg"] = 42.0
        drv.reset_orientation()
        assert drv._orientation["yaw_deg"] == 0.0

    def test_reset_zeros_pitch(self):
        drv = _mock_driver()
        drv._orientation["pitch_deg"] = -15.0
        drv.reset_orientation()
        assert drv._orientation["pitch_deg"] == 0.0

    def test_reset_zeros_roll(self):
        drv = _mock_driver()
        drv._orientation["roll_deg"] = 90.0
        drv.reset_orientation()
        assert drv._orientation["roll_deg"] == 0.0

    def test_reset_clears_last_ts(self):
        drv = _mock_driver()
        drv._last_orient_ts = 123456.789
        drv.reset_orientation()
        assert drv._last_orient_ts == 0.0

    def test_orientation_after_reset_is_zero(self):
        drv = _mock_driver()
        drv._orientation = {"yaw_deg": 10.0, "pitch_deg": 20.0, "roll_deg": 30.0}
        drv.reset_orientation()
        result = drv.orientation()
        assert result["yaw_deg"] == 0.0
        assert result["pitch_deg"] == 0.0
        assert result["roll_deg"] == 0.0


# ---------------------------------------------------------------------------
# Instance variables
# ---------------------------------------------------------------------------


class TestOrientationInstanceVars:
    def test_orientation_dict_initialized(self):
        drv = _mock_driver()
        assert hasattr(drv, "_orientation")
        assert isinstance(drv._orientation, dict)

    def test_last_orient_ts_initialized_to_zero(self):
        drv = _mock_driver()
        assert hasattr(drv, "_last_orient_ts")
        assert drv._last_orient_ts == 0.0

    def test_initial_orientation_values_are_zero(self):
        drv = _mock_driver()
        assert drv._orientation["yaw_deg"] == 0.0
        assert drv._orientation["pitch_deg"] == 0.0
        assert drv._orientation["roll_deg"] == 0.0
