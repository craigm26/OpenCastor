"""Tests for IMUDriver step counter — Issue #314."""

from __future__ import annotations

import importlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Helper: always get a fresh IMUDriver in mock mode (no smbus2 required)
# ---------------------------------------------------------------------------


def _make_driver(**env):
    """Instantiate a fresh IMUDriver with the given env overrides."""
    # Remove any cached smbus2 so we always get mock mode in CI
    sys.modules.pop("smbus2", None)
    import castor.drivers.imu_driver as _mod

    # Reload to reset module-level state (singleton, HAS_SMBUS2)
    importlib.reload(_mod)

    from castor.drivers.imu_driver import IMUDriver

    return IMUDriver()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def driver(monkeypatch):
    """Fresh IMUDriver in mock mode, default threshold (1.2 g)."""
    monkeypatch.delenv("IMU_STEP_THRESHOLD", raising=False)
    sys.modules.pop("smbus2", None)
    import castor.drivers.imu_driver as _mod

    importlib.reload(_mod)
    from castor.drivers.imu_driver import IMUDriver

    return IMUDriver()


def _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.0):
    """Replace driver.read() with a function returning fixed accel values."""
    driver.read = lambda: {
        "accel_g": {"x": accel_x, "y": accel_y, "z": accel_z},
        "gyro_dps": {"x": 0.0, "y": 0.0, "z": 0.0},
        "mag_uT": None,
        "temp_c": 25.0,
        "mode": "mock",
        "model": "mock",
    }


def _patch_read_raising(driver):
    """Replace driver.read() with a function that raises RuntimeError."""

    def _bad():
        raise RuntimeError("simulated sensor failure")

    driver.read = _bad


# ---------------------------------------------------------------------------
# 1. step_count() returns an int
# ---------------------------------------------------------------------------


def test_step_count_returns_int(driver):
    result = driver.step_count()
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# 2. step_count() is 0 initially with mock values below threshold
# ---------------------------------------------------------------------------


def test_step_count_zero_initially(driver):
    # Mock read returns z≈1.0 g → magnitude ≈ 1.0, below default threshold 1.2
    assert driver.step_count() == 0


# ---------------------------------------------------------------------------
# 3. _step_count starts at 0
# ---------------------------------------------------------------------------


def test_step_count_attr_starts_at_zero(driver):
    assert driver._step_count == 0


# ---------------------------------------------------------------------------
# 4. _step_in_peak starts at False
# ---------------------------------------------------------------------------


def test_step_in_peak_starts_false(driver):
    assert driver._step_in_peak is False


# ---------------------------------------------------------------------------
# 5. Simulated peak: patch read() to return high accel → step counted
# ---------------------------------------------------------------------------


def test_step_counted_on_high_accel(driver):
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.5)  # mag = 1.5 > 1.2
    count = driver.step_count()
    assert count == 1


# ---------------------------------------------------------------------------
# 6. Simulated below-threshold: no step counted
# ---------------------------------------------------------------------------


def test_no_step_below_threshold(driver):
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.0)  # mag = 1.0 < 1.2
    assert driver.step_count() == 0


# ---------------------------------------------------------------------------
# 7. Peak detection with hysteresis: two consecutive high-mag readings = 1 step
# ---------------------------------------------------------------------------


def test_hysteresis_two_high_readings_one_step(driver):
    # First high-mag call → step counted, _step_in_peak = True
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.5)
    driver.step_count()
    # Second high-mag call → _step_in_peak still True → no additional step
    count = driver.step_count()
    assert count == 1


# ---------------------------------------------------------------------------
# 8. After peak, dropping below hysteresis threshold resets in-peak state
# ---------------------------------------------------------------------------


def test_hysteresis_reset_allows_next_step(driver):
    # First peak
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.5)
    driver.step_count()
    assert driver._step_count == 1

    # Drop below 0.8 * 1.2 = 0.96 g → resets _step_in_peak
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=0.5)  # mag = 0.5
    driver.step_count()
    assert driver._step_in_peak is False

    # Second peak → counted
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.5)
    count = driver.step_count()
    assert count == 2


# ---------------------------------------------------------------------------
# 9. reset=True returns count and resets to 0
# ---------------------------------------------------------------------------


def test_step_count_reset_returns_count_and_zeros(driver):
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.5)
    driver.step_count()  # count = 1

    # Drop below hysteresis
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=0.5)
    driver.step_count()

    # Second peak
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.5)
    driver.step_count()  # count = 2

    returned = driver.step_count(reset=True)
    assert returned == 2
    assert driver._step_count == 0


# ---------------------------------------------------------------------------
# 10. reset_steps() returns count and zeroes
# ---------------------------------------------------------------------------


def test_reset_steps_convenience(driver):
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.5)
    driver.step_count()  # count = 1

    returned = driver.reset_steps()
    assert returned == 1
    assert driver._step_count == 0


# ---------------------------------------------------------------------------
# 11. Double call without peak = still 0 additional steps (debounce)
# ---------------------------------------------------------------------------


def test_double_call_no_extra_step(driver):
    _patch_read(driver, accel_x=0.0, accel_y=0.0, accel_z=1.5)
    driver.step_count()
    driver.step_count()
    assert driver._step_count == 1


# ---------------------------------------------------------------------------
# 12. step_count() never raises even on read() exception
# ---------------------------------------------------------------------------


def test_step_count_never_raises_on_read_error(driver):
    _patch_read_raising(driver)
    # Should not raise; returns current count (0) gracefully
    result = driver.step_count()
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# 13. _step_threshold reads from IMU_STEP_THRESHOLD env var
# ---------------------------------------------------------------------------


def test_step_threshold_from_env(monkeypatch):
    monkeypatch.setenv("IMU_STEP_THRESHOLD", "2.5")
    sys.modules.pop("smbus2", None)
    import castor.drivers.imu_driver as _mod

    importlib.reload(_mod)
    from castor.drivers.imu_driver import IMUDriver

    d = IMUDriver()
    assert d._step_threshold == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# 14. Custom threshold: step detected only above custom value
# ---------------------------------------------------------------------------


def test_custom_threshold_step_only_above(monkeypatch):
    monkeypatch.setenv("IMU_STEP_THRESHOLD", "2.0")
    sys.modules.pop("smbus2", None)
    import castor.drivers.imu_driver as _mod

    importlib.reload(_mod)
    from castor.drivers.imu_driver import IMUDriver

    d = IMUDriver()

    # Magnitude 1.5 < 2.0 → no step
    _patch_read(d, accel_x=0.0, accel_y=0.0, accel_z=1.5)
    assert d.step_count() == 0

    # Magnitude 2.5 > 2.0 → step
    _patch_read(d, accel_x=0.0, accel_y=0.0, accel_z=2.5)
    assert d.step_count() == 1
