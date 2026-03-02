"""Tests for IMUDriver.tap_detection() and reset_taps() — issue #357."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_singleton():
    import castor.drivers.imu_driver as mod

    mod._singleton = None
    yield
    mod._singleton = None


def _mock_driver():
    with patch("castor.drivers.imu_driver.HAS_SMBUS2", False):
        from castor.drivers.imu_driver import IMUDriver

        return IMUDriver(bus=1, model="auto")


def _hw_driver():
    """IMUDriver forced into hardware-mode for testing (read() patched per-test)."""
    with patch("castor.drivers.imu_driver.HAS_SMBUS2", False):
        from castor.drivers.imu_driver import IMUDriver

        drv = IMUDriver(bus=1, model="auto")
    drv._mode = "hardware"
    drv._bus = object()  # truthy sentinel
    return drv


def _patch_accel(drv, x=0.0, y=0.0, z=1.0):
    drv.read = lambda: {
        "accel_g": {"x": x, "y": y, "z": z},
        "gyro_dps": {"x": 0.0, "y": 0.0, "z": 0.0},
        "mag_uT": None,
        "temp_c": 25.0,
        "mode": "hardware",
    }


# ── Return shape ──────────────────────────────────────────────────────────────


def test_tap_detection_returns_dict():
    drv = _mock_driver()
    assert isinstance(drv.tap_detection(), dict)


def test_tap_detection_required_keys():
    drv = _mock_driver()
    r = drv.tap_detection()
    assert "single_tap" in r
    assert "double_tap" in r
    assert "axis" in r
    assert "timestamp" in r


# ── Mock mode ─────────────────────────────────────────────────────────────────


def test_tap_detection_mock_single_tap_false():
    assert _mock_driver().tap_detection()["single_tap"] is False


def test_tap_detection_mock_double_tap_false():
    assert _mock_driver().tap_detection()["double_tap"] is False


def test_tap_detection_mock_axis_none():
    assert _mock_driver().tap_detection()["axis"] is None


def test_tap_detection_mock_timestamp_none():
    assert _mock_driver().tap_detection()["timestamp"] is None


# ── Instance state ────────────────────────────────────────────────────────────


def test_imu_has_tap_state_attrs():
    drv = _mock_driver()
    assert hasattr(drv, "_last_tap_time")
    assert hasattr(drv, "_tap_count")
    assert drv._last_tap_time is None
    assert drv._tap_count == 0


def test_imu_has_tap_threshold_attr():
    drv = _mock_driver()
    assert hasattr(drv, "_tap_accel_threshold_g")
    assert drv._tap_accel_threshold_g > 0.0


# ── reset_taps ────────────────────────────────────────────────────────────────


def test_reset_taps_zeros_state():
    drv = _mock_driver()
    drv._last_tap_time = 123.456
    drv._tap_count = 3
    drv.reset_taps()
    assert drv._last_tap_time is None
    assert drv._tap_count == 0


def test_reset_taps_on_fresh_driver_no_error():
    drv = _mock_driver()
    drv.reset_taps()  # must not raise
    assert drv._last_tap_time is None
    assert drv._tap_count == 0


# ── Hardware: no tap below threshold ─────────────────────────────────────────


def test_hw_no_tap_below_threshold():
    drv = _hw_driver()
    _patch_accel(drv, x=0.0, y=0.0, z=1.0)  # 1 g < 2 g threshold
    r = drv.tap_detection(accel_threshold_g=2.0)
    assert r["single_tap"] is False
    assert r["double_tap"] is False
    assert r["axis"] is None
    assert r["timestamp"] is None


# ── Hardware: single tap ──────────────────────────────────────────────────────


def test_hw_single_tap_detected():
    drv = _hw_driver()
    _patch_accel(drv, x=0.0, y=0.0, z=3.0)  # z=3 g > threshold
    drv.reset_taps()
    r = drv.tap_detection(accel_threshold_g=2.0)
    assert r["single_tap"] is True
    assert r["double_tap"] is False
    assert r["axis"] == "z"
    assert r["timestamp"] is not None


def test_hw_single_tap_axis_x():
    drv = _hw_driver()
    _patch_accel(drv, x=2.5, y=0.1, z=0.1)
    drv.reset_taps()
    r = drv.tap_detection(accel_threshold_g=2.0)
    assert r["axis"] == "x"


def test_hw_single_tap_axis_y():
    drv = _hw_driver()
    _patch_accel(drv, x=0.1, y=3.0, z=0.1)
    drv.reset_taps()
    r = drv.tap_detection(accel_threshold_g=2.0)
    assert r["axis"] == "y"


# ── Hardware: double tap ──────────────────────────────────────────────────────


def test_hw_double_tap_within_window():
    drv = _hw_driver()
    _patch_accel(drv, x=0.0, y=0.0, z=3.0)
    drv.reset_taps()
    r1 = drv.tap_detection(accel_threshold_g=2.0, double_tap_window_s=0.5)
    assert r1["single_tap"] is True
    r2 = drv.tap_detection(accel_threshold_g=2.0, double_tap_window_s=0.5)
    assert r2["double_tap"] is True
    assert r2["single_tap"] is False


def test_hw_double_tap_resets_state():
    drv = _hw_driver()
    _patch_accel(drv, x=0.0, y=0.0, z=3.0)
    drv.reset_taps()
    drv.tap_detection(accel_threshold_g=2.0, double_tap_window_s=0.5)
    drv.tap_detection(accel_threshold_g=2.0, double_tap_window_s=0.5)  # double
    assert drv._last_tap_time is None
    assert drv._tap_count == 0


def test_hw_slow_second_tap_is_new_single(tmp_path):
    drv = _hw_driver()
    _patch_accel(drv, x=0.0, y=0.0, z=3.0)
    drv.reset_taps()
    drv.tap_detection(accel_threshold_g=2.0, double_tap_window_s=0.05)
    drv._last_tap_time = time.time() - 10.0  # simulate slow second tap
    r = drv.tap_detection(accel_threshold_g=2.0, double_tap_window_s=0.05)
    assert r["single_tap"] is True
    assert r["double_tap"] is False


# ── Hardware: error fallback ──────────────────────────────────────────────────


def test_hw_fallback_on_read_error():
    drv = _hw_driver()
    drv.read = lambda: (_ for _ in ()).throw(RuntimeError("sensor fail"))
    r = drv.tap_detection(accel_threshold_g=2.0)
    assert r["single_tap"] is False
    assert r["double_tap"] is False
    assert r["axis"] is None
    assert r["timestamp"] is None
