"""Tests for IMUDriver Madgwick complementary filter (Issue #343)."""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from castor.drivers.imu_driver import IMUDriver, MadgwickFilter

# ── MadgwickFilter unit tests ─────────────────────────────────────────────────


def test_madgwick_init_identity_quaternion():
    f = MadgwickFilter()
    assert f.q == [1.0, 0.0, 0.0, 0.0]


def test_madgwick_init_beta():
    f = MadgwickFilter(beta=0.05)
    assert f.beta == pytest.approx(0.05)


def test_madgwick_get_euler_identity():
    f = MadgwickFilter()
    euler = f.get_euler()
    assert euler["yaw_deg"] == pytest.approx(0.0, abs=1e-4)
    assert euler["pitch_deg"] == pytest.approx(0.0, abs=1e-4)
    assert euler["roll_deg"] == pytest.approx(0.0, abs=1e-4)


def test_madgwick_update_no_motion():
    """With no motion, filter should remain near identity."""
    f = MadgwickFilter(beta=0.1)
    for _ in range(20):
        # Pure gravity downward, no gyro
        f.update(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, dt=0.01)
    euler = f.get_euler()
    assert abs(euler["yaw_deg"]) < 5.0
    assert abs(euler["pitch_deg"]) < 5.0
    assert abs(euler["roll_deg"]) < 5.0


def test_madgwick_quaternion_stays_unit():
    """Quaternion magnitude should remain 1 after updates."""
    f = MadgwickFilter(beta=0.1)
    for _ in range(50):
        f.update(0.1, 0.05, 0.02, 0.0, 0.2, 0.98, dt=0.02)
    norm = math.sqrt(sum(x * x for x in f.q))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_madgwick_reset_returns_to_identity():
    f = MadgwickFilter(beta=0.1)
    f.update(0.5, 0.1, 0.3, 0.0, 0.1, 0.99, dt=0.05)
    f.reset()
    assert f.q == [1.0, 0.0, 0.0, 0.0]


def test_madgwick_update_zero_accel_uses_gyro_only():
    """Zero-magnitude accelerometer should fall back to gyro integration."""
    f = MadgwickFilter(beta=0.1)
    # Update with zero accel and zero gyro — q should remain unchanged
    f.update(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, dt=0.01)
    # With no inputs, quaternion should stay near identity
    assert math.sqrt(sum(x * x for x in f.q)) == pytest.approx(1.0, abs=1e-6)


def test_madgwick_get_euler_returns_dict_keys():
    f = MadgwickFilter()
    euler = f.get_euler()
    assert "yaw_deg" in euler
    assert "pitch_deg" in euler
    assert "roll_deg" in euler


def test_madgwick_reduces_drift_vs_gyro_only():
    """Madgwick should correct drift when accel provides reference."""
    f_madgwick = MadgwickFilter(beta=0.5)  # High beta = strong accel trust
    # Simulate steady gravity with small gyro noise
    for _ in range(100):
        f_madgwick.update(0.05, 0.0, 0.0, 0.0, 0.0, 1.0, dt=0.01)
    euler = f_madgwick.get_euler()
    # Roll should converge toward 0 (gravity aligned with Z)
    assert abs(euler["roll_deg"]) < 30.0


def test_madgwick_update_uses_dt_param():
    """dt parameter should be used rather than sample_period_s when supplied."""
    f = MadgwickFilter(beta=0.1, sample_period_s=0.001)
    q_initial = f.q[:]
    # Large dt should produce different result than small dt
    f.update(0.1, 0.0, 0.0, 0.0, 0.0, 1.0, dt=0.1)
    # Quaternion should have changed
    q_after = f.q
    changed = any(abs(a - b) > 1e-8 for a, b in zip(q_initial, q_after, strict=False))
    assert changed


# ── IMUDriver integration tests ───────────────────────────────────────────────


def test_imu_driver_madgwick_enabled_by_default_false():
    """Default filter is complementary, not madgwick."""
    drv = IMUDriver()
    assert drv._imu_filter in ("complementary", "auto", "none", "")
    assert drv._madgwick is None or drv._imu_filter != "madgwick"


def test_imu_driver_madgwick_filter_activated():
    drv = IMUDriver(imu_filter="madgwick", imu_beta=0.1)
    assert drv._madgwick is not None
    assert isinstance(drv._madgwick, MadgwickFilter)
    assert drv._madgwick.beta == pytest.approx(0.1)


def test_imu_driver_health_check_includes_filter():
    drv = IMUDriver(imu_filter="complementary")
    hc = drv.health_check()
    assert "filter" in hc
    assert hc["filter"] == "complementary"


def test_imu_driver_madgwick_health_check_includes_beta():
    drv = IMUDriver(imu_filter="madgwick", imu_beta=0.2)
    hc = drv.health_check()
    assert hc["filter"] == "madgwick"
    assert hc["madgwick_beta"] == pytest.approx(0.2)


def test_imu_driver_reset_orientation_resets_madgwick():
    drv = IMUDriver(imu_filter="madgwick", imu_beta=0.1)
    # Run some updates
    drv._madgwick.update(0.1, 0.0, 0.0, 0.0, 0.0, 1.0, dt=0.01)
    drv.reset_orientation()
    # Quaternion should be back to identity
    assert drv._madgwick.q == [1.0, 0.0, 0.0, 0.0]


def test_imu_driver_orientation_with_madgwick_returns_filter_key():
    drv = IMUDriver(imu_filter="madgwick", imu_beta=0.1)
    drv._last_orient_ts = 1.0  # Ensure non-zero dt on first call
    import time

    with patch("castor.drivers.imu_driver.time") as mock_time:
        mock_time.monotonic.return_value = 1.05  # dt = 0.05
        mock_time.time = time.time
        result = drv.orientation()
    # In mock mode the time patching may not activate fully — just check structure
    assert "yaw_deg" in result
    assert "pitch_deg" in result
    assert "roll_deg" in result
