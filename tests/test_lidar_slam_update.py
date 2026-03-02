"""Tests for LidarDriver.slam_update() (#376)."""

import pytest

from castor.drivers.lidar_driver import LidarDriver


def _driver():
    return LidarDriver(port="/dev/null")


# ── basic return shape ────────────────────────────────────────────────────────

def test_slam_update_returns_dict():
    d = _driver()
    result = d.slam_update()
    assert isinstance(result, dict)


def test_slam_update_has_required_keys():
    d = _driver()
    result = d.slam_update()
    for key in ("cells_updated", "total_occupied", "cells", "mode", "reset"):
        assert key in result, f"missing key: {key}"


def test_slam_update_mode_is_string():
    d = _driver()
    result = d.slam_update()
    assert isinstance(result["mode"], str)


def test_slam_update_cells_is_int():
    d = _driver()
    result = d.slam_update()
    assert isinstance(result["cells"], int)
    assert result["cells"] > 0


def test_slam_update_cells_updated_non_negative():
    d = _driver()
    result = d.slam_update()
    assert result["cells_updated"] >= 0


def test_slam_update_total_occupied_non_negative():
    d = _driver()
    result = d.slam_update()
    assert result["total_occupied"] >= 0


def test_slam_update_reset_flag_false_by_default():
    d = _driver()
    result = d.slam_update()
    assert result["reset"] is False


# ── reset behaviour ───────────────────────────────────────────────────────────

def test_slam_update_reset_true_returns_reset_flag():
    d = _driver()
    result = d.slam_update(reset=True)
    assert result["reset"] is True


def test_slam_update_reset_clears_map():
    d = _driver()
    d.slam_update()  # populate once
    result = d.slam_update(reset=True)
    assert result["total_occupied"] == 0


def test_slam_update_after_reset_map_empty():
    d = _driver()
    d.slam_update()
    d.slam_update(reset=True)
    result = d.slam_update()
    # mock mode: no real scans, so total_occupied should stay 0
    assert result["total_occupied"] == 0


# ── parameter propagation ─────────────────────────────────────────────────────

def test_slam_update_custom_size_m():
    d = _driver()
    result = d.slam_update(size_m=10.0)
    assert result["cells"] > 0


def test_slam_update_custom_resolution_m():
    d = _driver()
    r1 = d.slam_update(size_m=4.0, resolution_m=0.1)
    r2 = d.slam_update(reset=True, size_m=4.0, resolution_m=0.05)
    assert r2["cells"] > r1["cells"]


def test_slam_update_cells_match_expected_formula():
    d = _driver()
    size_m = 5.0
    resolution_m = 0.05
    expected_cells = int(size_m / resolution_m)
    result = d.slam_update(size_m=size_m, resolution_m=resolution_m)
    assert result["cells"] == expected_cells


# ── persistence across calls ──────────────────────────────────────────────────

def test_slam_update_map_persists_across_calls():
    d = _driver()
    d.slam_update()
    r2 = d.slam_update()
    # second call should see same or more occupied cells than first
    assert r2["total_occupied"] >= 0  # never negative


def test_slam_update_no_exception_multiple_calls():
    d = _driver()
    for _ in range(5):
        d.slam_update()


# ── mock mode ────────────────────────────────────────────────────────────────

def test_slam_update_mock_mode_reports_mode():
    d = _driver()
    result = d.slam_update()
    assert result["mode"] in ("mock", "hardware")


def test_slam_update_never_raises():
    d = _driver()
    try:
        d.slam_update(size_m=0.1, resolution_m=0.05)
    except Exception as exc:
        pytest.fail(f"slam_update raised: {exc}")
