"""Tests for IMUDriver.pose() and IMUDriver.reset_pose() — Issue #336."""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from castor.drivers.imu_driver import IMUDriver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_driver() -> IMUDriver:
    """Return an IMUDriver instance in mock mode with pose state initialised."""
    drv = IMUDriver.__new__(IMUDriver)
    drv._mode = "mock"
    drv._bus = None
    drv._detected_model = "mock"
    drv._lock = threading.Lock()
    drv._orientation = {"yaw_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0}
    drv._last_orient_ts = 0.0
    drv._step_count = 0
    drv._step_last_mag = 0.0
    drv._step_threshold = 1.2
    drv._step_in_peak = False
    drv._pose_x_m = 0.0
    drv._pose_y_m = 0.0
    drv._pose_heading_deg = 0.0
    drv._pose_last_ts = None
    return drv


# ---------------------------------------------------------------------------
# Test 1 — pose() returns a dict
# ---------------------------------------------------------------------------


def test_pose_returns_dict():
    drv = _mock_driver()
    result = drv.pose()
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Test 2 — pose() has required keys
# ---------------------------------------------------------------------------


def test_pose_has_required_keys():
    drv = _mock_driver()
    result = drv.pose()
    for key in ("x_m", "y_m", "heading_deg", "confidence", "mode"):
        assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Test 3 — reset_pose() zeros x_m, y_m, heading_deg
# ---------------------------------------------------------------------------


def test_reset_pose_zeros_state():
    drv = _mock_driver()
    # Manually set non-zero pose
    drv._pose_x_m = 5.0
    drv._pose_y_m = -3.0
    drv._pose_heading_deg = 45.0
    drv._pose_last_ts = time.time()

    drv.reset_pose()

    assert drv._pose_x_m == 0.0
    assert drv._pose_y_m == 0.0
    assert drv._pose_heading_deg == 0.0
    assert drv._pose_last_ts is None


# ---------------------------------------------------------------------------
# Test 4 — reset_pose() callable without crash
# ---------------------------------------------------------------------------


def test_reset_pose_does_not_raise():
    drv = _mock_driver()
    # Should not raise under any condition
    drv.reset_pose()
    drv.reset_pose()  # Calling twice also fine


# ---------------------------------------------------------------------------
# Test 5 — first pose() call returns zeros (no dt yet)
# ---------------------------------------------------------------------------


def test_first_pose_call_returns_zeros():
    drv = _mock_driver()
    result = drv.pose()
    assert result["x_m"] == 0.0
    assert result["y_m"] == 0.0
    assert result["heading_deg"] == 0.0


# ---------------------------------------------------------------------------
# Test 6 — second pose() call returns numeric values
# ---------------------------------------------------------------------------


def test_second_pose_call_returns_numeric():
    drv = _mock_driver()
    drv.pose()  # first call — seeds timestamp
    time.sleep(0.02)  # ensure dt > 0
    result = drv.pose()
    assert isinstance(result["x_m"], float)
    assert isinstance(result["y_m"], float)
    assert isinstance(result["heading_deg"], float)


# ---------------------------------------------------------------------------
# Test 7 — confidence is between 0.0 and 1.0
# ---------------------------------------------------------------------------


def test_pose_confidence_range():
    drv = _mock_driver()
    drv.pose()  # seed
    time.sleep(0.01)
    result = drv.pose()
    assert 0.0 <= result["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Test 8 — mode is a string
# ---------------------------------------------------------------------------


def test_pose_mode_is_string():
    drv = _mock_driver()
    result = drv.pose()
    assert isinstance(result["mode"], str)


# ---------------------------------------------------------------------------
# Test 9 — reset_pose() then pose() returns zeros again
# ---------------------------------------------------------------------------


def test_reset_then_pose_returns_zeros():
    drv = _mock_driver()
    drv.pose()  # seed
    time.sleep(0.02)
    drv.pose()  # integrate something

    drv.reset_pose()
    result = drv.pose()  # first call after reset → zeros

    assert result["x_m"] == 0.0
    assert result["y_m"] == 0.0
    assert result["heading_deg"] == 0.0


# ---------------------------------------------------------------------------
# Test 10 — x_m is float
# ---------------------------------------------------------------------------


def test_pose_x_m_is_float():
    drv = _mock_driver()
    drv.pose()  # seed
    time.sleep(0.01)
    result = drv.pose()
    assert isinstance(result["x_m"], float)


# ---------------------------------------------------------------------------
# Test 11 — y_m is float
# ---------------------------------------------------------------------------


def test_pose_y_m_is_float():
    drv = _mock_driver()
    drv.pose()  # seed
    time.sleep(0.01)
    result = drv.pose()
    assert isinstance(result["y_m"], float)


# ---------------------------------------------------------------------------
# Test 12 — heading_deg is float
# ---------------------------------------------------------------------------


def test_pose_heading_deg_is_float():
    drv = _mock_driver()
    drv.pose()  # seed
    time.sleep(0.01)
    result = drv.pose()
    assert isinstance(result["heading_deg"], float)


# ---------------------------------------------------------------------------
# Test 13 — error path returns error dict (no raises)
# ---------------------------------------------------------------------------


def test_pose_error_returns_safe_dict():
    drv = _mock_driver()
    # Cause read() to raise
    drv.read = MagicMock(side_effect=RuntimeError("sensor failure"))
    drv._pose_last_ts = time.time() - 0.1  # ensure we reach the integration branch

    result = drv.pose()
    # Must have the zero-pose keys and an "error" key, no exception raised
    assert isinstance(result, dict)
    assert "error" in result
    assert result["x_m"] == 0.0
    assert result["y_m"] == 0.0
    assert result["heading_deg"] == 0.0
    assert result["confidence"] == 0.0


# ---------------------------------------------------------------------------
# Test 14 — heading integrates gyro_z over time
# ---------------------------------------------------------------------------


def test_pose_heading_integrates_gyro():
    drv = _mock_driver()

    # Mock read() to return a fixed gyro_z of 90 dps
    mock_data = {
        "accel_g": {"x": 0.0, "y": 0.0, "z": 1.0},
        "gyro_dps": {"x": 0.0, "y": 0.0, "z": 90.0},
        "mag_uT": None,
        "temp_c": 25.0,
        "mode": "mock",
        "model": "mock",
    }
    drv.read = MagicMock(return_value=mock_data)

    # Manually set last_ts to 1 second ago
    drv._pose_last_ts = time.time() - 1.0  # dt ≈ 1.0 s

    result = drv.pose()
    # heading should be approximately 90° * 1 s = 90°
    assert abs(result["heading_deg"] - 90.0) < 2.0  # allow small timing jitter


# ---------------------------------------------------------------------------
# Test 15 — heading wraps correctly beyond 180°
# ---------------------------------------------------------------------------


def test_pose_heading_wraps():
    drv = _mock_driver()
    drv._pose_heading_deg = 170.0  # near wrap boundary
    drv._pose_last_ts = time.time() - 1.0

    mock_data = {
        "accel_g": {"x": 0.0, "y": 0.0, "z": 1.0},
        "gyro_dps": {"x": 0.0, "y": 0.0, "z": 30.0},  # 30 dps × 1 s = +30°
        "mag_uT": None,
        "temp_c": 25.0,
        "mode": "mock",
        "model": "mock",
    }
    drv.read = MagicMock(return_value=mock_data)

    result = drv.pose()
    # 170 + 30 = 200 → wraps to 200 - 360 = -160
    assert -180.0 <= result["heading_deg"] <= 180.0
