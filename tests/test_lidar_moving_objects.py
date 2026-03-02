"""Tests for LidarDriver.moving_objects() — issue #358."""

from __future__ import annotations

import importlib
import time

import pytest


@pytest.fixture(autouse=True)
def _reset_lidar_singleton():
    import castor.drivers.lidar_driver as mod

    mod._singleton = None
    yield
    mod._singleton = None


@pytest.fixture()
def driver(tmp_path, monkeypatch):
    import castor.drivers.lidar_driver as _mod

    db = str(tmp_path / "lidar.db")
    monkeypatch.setenv("LIDAR_HISTORY_DB", db)
    importlib.reload(_mod)
    from castor.drivers.lidar_driver import LidarDriver

    d = LidarDriver({})
    yield d
    d.close()


@pytest.fixture()
def driver_no_history(tmp_path, monkeypatch):
    import castor.drivers.lidar_driver as _mod

    monkeypatch.setenv("LIDAR_HISTORY_DB", "none")
    importlib.reload(_mod)
    from castor.drivers.lidar_driver import LidarDriver

    d = LidarDriver({})
    yield d
    d.close()


def _pts(angle_dist_pairs):
    return [{"angle_deg": float(a), "distance_mm": float(d), "quality": 15} for a, d in angle_dist_pairs]


def _inject_two_scans(driver):
    """Ensure at least 2 history rows."""
    driver.scan()
    time.sleep(0.02)
    driver.scan()


# ── Returns [] when no history ────────────────────────────────────────────────


def test_moving_objects_empty_when_no_history(driver):
    assert driver.moving_objects() == []


def test_moving_objects_empty_after_single_scan(driver):
    driver.scan()
    assert driver.moving_objects() == []


# ── Structure after two scans ────────────────────────────────────────────────


def test_moving_objects_returns_list(driver):
    _inject_two_scans(driver)
    assert isinstance(driver.moving_objects(), list)


def test_moving_objects_result_keys(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(45, 2000)])
    driver._last_scan = _pts([(45, 1500)])
    result = driver.moving_objects(min_delta_m=0.05)
    for entry in result:
        assert "angle_deg" in entry
        assert "delta_m" in entry
        assert "direction" in entry


def test_moving_objects_angle_deg_is_int(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(45.7, 2000)])
    driver._last_scan = _pts([(45.7, 1500)])
    for entry in driver.moving_objects(min_delta_m=0.05):
        assert isinstance(entry["angle_deg"], int)
        assert 0 <= entry["angle_deg"] <= 359


# ── Approaching detection ─────────────────────────────────────────────────────


def test_moving_objects_detects_approaching(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(45, 2000)])
    driver._last_scan = _pts([(45, 1800)])  # 200 mm closer
    result = driver.moving_objects(min_delta_m=0.05)
    assert any(r["angle_deg"] == 45 and r["direction"] == "approaching" for r in result)


def test_moving_objects_approaching_negative_delta(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(120, 2000)])
    driver._last_scan = _pts([(120, 1500)])  # closer = negative delta
    result = driver.moving_objects(min_delta_m=0.05)
    matching = [r for r in result if r["angle_deg"] == 120]
    assert matching and matching[0]["delta_m"] < 0.0


# ── Receding detection ────────────────────────────────────────────────────────


def test_moving_objects_detects_receding(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(90, 500)])
    driver._last_scan = _pts([(90, 800)])  # 300 mm farther
    result = driver.moving_objects(min_delta_m=0.05)
    assert any(r["angle_deg"] == 90 and r["direction"] == "receding" for r in result)


def test_moving_objects_receding_positive_delta(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(200, 1000)])
    driver._last_scan = _pts([(200, 1600)])  # farther = positive delta
    result = driver.moving_objects(min_delta_m=0.05)
    matching = [r for r in result if r["angle_deg"] == 200]
    assert matching and matching[0]["delta_m"] > 0.0


# ── Threshold filtering ───────────────────────────────────────────────────────


def test_moving_objects_filters_below_threshold(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(10, 1000)])
    driver._last_scan = _pts([(10, 1010)])  # 10 mm = 0.01 m < 0.05 default
    assert all(r["angle_deg"] != 10 for r in driver.moving_objects(min_delta_m=0.05))


def test_moving_objects_custom_threshold(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(30, 1000)])
    driver._last_scan = _pts([(30, 1020)])  # 0.02 m — above 0.01 threshold
    assert any(r["angle_deg"] == 30 for r in driver.moving_objects(min_delta_m=0.01))


# ── Edge cases ────────────────────────────────────────────────────────────────


def test_moving_objects_empty_prev(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = []
    driver._last_scan = _pts([(90, 500)])
    assert driver.moving_objects() == []


def test_moving_objects_empty_curr(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(90, 500)])
    driver._last_scan = []
    assert driver.moving_objects() == []


def test_moving_objects_history_disabled(driver_no_history):
    driver_no_history.scan()
    time.sleep(0.02)
    driver_no_history.scan()
    assert driver_no_history.moving_objects() == []


def test_moving_objects_never_raises(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = [None, "bad", 42]  # type: ignore[assignment]
    driver._last_scan = [None, "bad", 42]  # type: ignore[assignment]
    result = driver.moving_objects()
    assert isinstance(result, list)


def test_moving_objects_direction_values(driver):
    _inject_two_scans(driver)
    driver._prev_scan_points = _pts([(0, 1000), (180, 1000)])
    driver._last_scan = _pts([(0, 800), (180, 1300)])
    for entry in driver.moving_objects(min_delta_m=0.05):
        assert entry["direction"] in ("approaching", "receding")
