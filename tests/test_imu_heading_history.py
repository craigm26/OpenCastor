"""Tests for IMUDriver.heading_history() — Issue #413."""

import time

import pytest

from castor.drivers.imu_driver import IMUDriver


@pytest.fixture()
def imu():
    """Return a fresh IMUDriver instance (mock mode, no hardware required)."""
    return IMUDriver()


# ── Basic return-type tests ────────────────────────────────────────────────────


def test_returns_dict(imu):
    """heading_history() returns a dict."""
    result = imu.heading_history()
    assert isinstance(result, dict)


def test_has_required_keys(imu):
    """Result contains the expected keys: readings, window_s, count, mode."""
    result = imu.heading_history()
    for key in ("readings", "window_s", "count", "mode"):
        assert key in result, f"Missing key: {key}"


def test_readings_is_list(imu):
    """'readings' value is a list."""
    result = imu.heading_history()
    assert isinstance(result["readings"], list)


def test_count_equals_len_readings(imu):
    """'count' always equals len(readings)."""
    result = imu.heading_history()
    assert result["count"] == len(result["readings"])


def test_window_s_matches_param(imu):
    """'window_s' reflects the argument passed to heading_history()."""
    for w in (10.0, 30.0, 120.0):
        result = imu.heading_history(window_s=w)
        assert result["window_s"] == w


def test_mode_is_str(imu):
    """'mode' value is a string."""
    result = imu.heading_history()
    assert isinstance(result["mode"], str)


# ── Per-reading field tests ────────────────────────────────────────────────────


def test_each_reading_has_ts_and_yaw_deg(imu):
    """Every entry in 'readings' has 'ts' and 'yaw_deg' keys."""
    # Call twice to ensure at least one reading is present
    imu.heading_history()
    result = imu.heading_history()
    for entry in result["readings"]:
        assert "ts" in entry, f"Missing 'ts' in entry: {entry}"
        assert "yaw_deg" in entry, f"Missing 'yaw_deg' in entry: {entry}"


def test_ts_is_numeric(imu):
    """'ts' in each reading is a numeric type (float or int)."""
    imu.heading_history()
    result = imu.heading_history()
    for entry in result["readings"]:
        assert isinstance(entry["ts"], (float, int)), f"ts is not numeric: {entry['ts']}"


def test_yaw_deg_is_float(imu):
    """'yaw_deg' in each reading is a float."""
    imu.heading_history()
    result = imu.heading_history()
    for entry in result["readings"]:
        assert isinstance(entry["yaw_deg"], float), f"yaw_deg is not float: {entry['yaw_deg']}"


# ── Boundary / robustness tests ────────────────────────────────────────────────


def test_count_non_negative(imu):
    """'count' is never negative."""
    result = imu.heading_history()
    assert result["count"] >= 0


def test_never_raises(imu):
    """heading_history() never raises regardless of driver state."""
    try:
        imu.heading_history()
        imu.heading_history(window_s=0.001)
        imu.heading_history(window_s=3600.0)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"heading_history() raised unexpectedly: {exc}")


def test_custom_window_s_accepted(imu):
    """A non-default window_s is accepted and reflected in the result."""
    result = imu.heading_history(window_s=45.0)
    assert result["window_s"] == 45.0


def test_calling_twice_increases_count_by_one(imu):
    """Calling heading_history() twice adds exactly one reading each time."""
    # Ensure a known baseline by clearing the internal history
    imu._heading_history = []
    first = imu.heading_history()
    count_after_first = first["count"]
    second = imu.heading_history()
    count_after_second = second["count"]
    assert count_after_second == count_after_first + 1


def test_readings_within_window(imu):
    """All returned readings have ts >= now - window_s."""
    window_s = 60.0
    before = time.time()
    result = imu.heading_history(window_s=window_s)
    after = time.time()
    cutoff = before - window_s
    for entry in result["readings"]:
        assert entry["ts"] >= cutoff, (
            f"ts {entry['ts']} is older than cutoff {cutoff}"
        )
        assert entry["ts"] <= after + 1, (
            f"ts {entry['ts']} is in the future relative to call time {after}"
        )
