"""Tests for IMUDriver.step_counter() and reset_step_counter() (#381)."""

import pytest

from castor.drivers.imu_driver import IMUDriver


@pytest.fixture
def imu():
    return IMUDriver()


# ── basic return shape ────────────────────────────────────────────────────────

def test_step_counter_returns_dict(imu):
    result = imu.step_counter()
    assert isinstance(result, dict)


def test_step_counter_has_required_keys(imu):
    result = imu.step_counter()
    for key in ("steps", "threshold_g", "min_interval_s", "mode"):
        assert key in result, f"missing key: {key}"


def test_step_counter_steps_is_int(imu):
    result = imu.step_counter()
    assert isinstance(result["steps"], int)


def test_step_counter_threshold_g_is_float(imu):
    result = imu.step_counter()
    assert isinstance(result["threshold_g"], float)


def test_step_counter_min_interval_s_is_float(imu):
    result = imu.step_counter()
    assert isinstance(result["min_interval_s"], float)


def test_step_counter_mode_is_string(imu):
    result = imu.step_counter()
    assert isinstance(result["mode"], str)
    assert result["mode"] in ("mock", "hardware")


def test_step_counter_steps_non_negative(imu):
    result = imu.step_counter()
    assert result["steps"] >= 0


# ── parameter passing ─────────────────────────────────────────────────────────

def test_step_counter_custom_threshold(imu):
    result = imu.step_counter(threshold_g=1.5)
    assert abs(result["threshold_g"] - 1.5) < 1e-6


def test_step_counter_custom_min_interval(imu):
    result = imu.step_counter(min_interval_s=0.5)
    assert abs(result["min_interval_s"] - 0.5) < 1e-6


def test_step_counter_default_threshold_from_driver(imu):
    default = imu._step_threshold
    result = imu.step_counter()
    assert abs(result["threshold_g"] - default) < 1e-6


# ── mock mode always 0 steps ──────────────────────────────────────────────────

def test_step_counter_mock_mode_returns_zero(imu):
    if imu._mode == "mock":
        result = imu.step_counter()
        assert result["steps"] == 0


def test_step_counter_multiple_calls_stable_in_mock(imu):
    if imu._mode == "mock":
        for _ in range(5):
            result = imu.step_counter()
            assert result["steps"] == 0


# ── reset_step_counter ────────────────────────────────────────────────────────

def test_reset_step_counter_returns_none(imu):
    assert imu.reset_step_counter() is None


def test_reset_step_counter_zeros_count(imu):
    imu._step_count = 42
    imu.reset_step_counter()
    assert imu._step_count == 0


def test_reset_step_counter_clears_peak_flag(imu):
    imu._step_in_peak = True
    imu.reset_step_counter()
    assert imu._step_in_peak is False


def test_reset_then_step_counter_returns_zero(imu):
    imu._step_count = 10
    imu.reset_step_counter()
    result = imu.step_counter()
    assert result["steps"] == 0


# ── never raises ─────────────────────────────────────────────────────────────

def test_step_counter_never_raises(imu):
    try:
        imu.step_counter()
    except Exception as exc:
        pytest.fail(f"step_counter raised: {exc}")


def test_reset_step_counter_never_raises(imu):
    try:
        imu.reset_step_counter()
    except Exception as exc:
        pytest.fail(f"reset_step_counter raised: {exc}")
