"""Tests for castor.drivers.imu_driver."""

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_imu_singleton():
    import castor.drivers.imu_driver as mod

    mod._singleton = None
    yield
    mod._singleton = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_driver(model="auto"):
    """Return an IMUDriver forced into mock mode (no smbus2)."""
    with patch("castor.drivers.imu_driver.HAS_SMBUS2", False):
        from castor.drivers.imu_driver import IMUDriver

        return IMUDriver(bus=1, model=model)


# ---------------------------------------------------------------------------
# Init / mock mode
# ---------------------------------------------------------------------------


class TestIMUDriverInit:
    def test_mock_mode_without_smbus2(self):
        drv = _mock_driver()
        assert drv._mode == "mock"

    def test_detected_model_is_mock_when_no_hw(self):
        drv = _mock_driver()
        assert drv._detected_model == "mock"

    def test_health_check_ok_in_mock_mode(self):
        drv = _mock_driver()
        h = drv.health_check()
        assert h["ok"] is True
        assert h["mode"] == "mock"

    def test_health_check_contains_bus(self):
        drv = _mock_driver()
        h = drv.health_check()
        assert "bus" in h
        assert h["bus"] == 1


# ---------------------------------------------------------------------------
# read() — mock mode output keys
# ---------------------------------------------------------------------------


class TestIMUDriverRead:
    def test_read_returns_required_keys(self):
        drv = _mock_driver()
        data = drv.read()
        for key in ("accel_g", "gyro_dps", "mag_uT", "temp_c", "mode", "model"):
            assert key in data, f"missing key: {key}"

    def test_accel_has_xyz(self):
        drv = _mock_driver()
        accel = drv.read()["accel_g"]
        for axis in ("x", "y", "z"):
            assert axis in accel

    def test_gyro_has_xyz(self):
        drv = _mock_driver()
        gyro = drv.read()["gyro_dps"]
        for axis in ("x", "y", "z"):
            assert axis in gyro

    def test_mock_temp_is_float(self):
        drv = _mock_driver()
        assert isinstance(drv.read()["temp_c"], float)

    def test_mock_mode_field(self):
        drv = _mock_driver()
        assert drv.read()["mode"] == "mock"

    def test_accel_z_near_1g_in_mock(self):
        """Mock should simulate resting z-axis ~ 1 g."""
        drv = _mock_driver()
        az = drv.read()["accel_g"]["z"]
        assert 0.95 <= az <= 1.05


# ---------------------------------------------------------------------------
# calibrate() — mock/non-bno055
# ---------------------------------------------------------------------------


class TestIMUDriverCalibrate:
    def test_calibrate_returns_ok(self):
        drv = _mock_driver()
        result = drv.calibrate()
        assert result["ok"] is True

    def test_calibrate_includes_note_for_unsupported(self):
        drv = _mock_driver()
        result = drv.calibrate()
        assert "note" in result


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


def test_close_sets_mode_to_mock():
    drv = _mock_driver()
    drv._mode = "hardware"
    drv.close()
    assert drv._mode == "mock"


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


def test_get_imu_singleton():
    with patch("castor.drivers.imu_driver.HAS_SMBUS2", False):
        from castor.drivers.imu_driver import get_imu

        i1 = get_imu()
        i2 = get_imu()
    assert i1 is i2


# ---------------------------------------------------------------------------
# _s16 helper
# ---------------------------------------------------------------------------


def test_s16_positive():
    from castor.drivers.imu_driver import _s16

    assert _s16(0x00, 0x64) == 100


def test_s16_negative():
    from castor.drivers.imu_driver import _s16

    # 0xFF80 = -128 in signed 16-bit
    assert _s16(0xFF, 0x80) == -128
