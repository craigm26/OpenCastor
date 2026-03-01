"""Tests for LidarDriver scan history (issue #307)."""

from __future__ import annotations

import os
import time

import pytest

import castor.drivers.lidar_driver as _mod
from castor.drivers.lidar_driver import LidarDriver


def _reset_singleton():
    _mod._singleton = None


@pytest.fixture()
def driver(tmp_path, monkeypatch):
    """LidarDriver in mock mode with an isolated history DB."""
    _reset_singleton()
    db = str(tmp_path / "lidar_hist.db")
    monkeypatch.setenv("LIDAR_HISTORY_DB", db)
    # rplidar is not installed in CI so the driver defaults to mock mode
    d = LidarDriver({})
    yield d
    d.close()
    _reset_singleton()


@pytest.fixture()
def driver_no_history(tmp_path, monkeypatch):
    """LidarDriver with history disabled via LIDAR_HISTORY_DB=none."""
    _reset_singleton()
    monkeypatch.setenv("LIDAR_HISTORY_DB", "none")
    d = LidarDriver({})
    yield d
    d.close()
    _reset_singleton()


# ── History logging ───────────────────────────────────────────────────────────


def test_scan_logs_to_history(driver):
    """A single scan() call should produce one history row."""
    driver.scan()
    hist = driver.get_scan_history()
    assert len(hist) == 1


def test_multiple_scans_logged(driver):
    """Each scan() call should produce a distinct history row."""
    for _ in range(5):
        driver.scan()
    hist = driver.get_scan_history()
    assert len(hist) == 5


def test_history_row_has_required_keys(driver):
    """Every history row must contain all expected keys."""
    driver.scan()
    hist = driver.get_scan_history()
    assert len(hist) == 1
    row = hist[0]
    for key in (
        "ts",
        "min_distance_mm",
        "front_mm",
        "left_mm",
        "right_mm",
        "rear_mm",
        "point_count",
    ):
        assert key in row, f"missing key: {key}"


def test_history_newest_first(driver):
    """Rows must be returned newest-first (descending ts)."""
    driver.scan()
    time.sleep(0.02)
    driver.scan()
    hist = driver.get_scan_history()
    assert len(hist) == 2
    assert hist[0]["ts"] >= hist[1]["ts"]


def test_history_limit_respected(driver):
    """get_scan_history(limit=N) must return at most N rows."""
    for _ in range(20):
        driver.scan()
    hist = driver.get_scan_history(limit=5)
    assert len(hist) <= 5


def test_history_window_filters_old_rows(driver):
    """Rows older than window_s should not be included."""
    driver.scan()
    time.sleep(0.05)
    hist = driver.get_scan_history(window_s=0.001)
    assert len(hist) == 0


def test_history_disabled_get_returns_empty(driver_no_history):
    """With history disabled, get_scan_history() must return []."""
    driver_no_history.scan()
    hist = driver_no_history.get_scan_history()
    assert hist == []


def test_scan_succeeds_when_history_disabled(driver_no_history):
    """scan() must return valid data even when history is disabled."""
    result = driver_no_history.scan()
    assert isinstance(result, list)
    assert len(result) > 0


def test_scan_succeeds_even_when_history_db_is_invalid(monkeypatch, tmp_path):
    """A DB error during history logging must not propagate from scan()."""
    _reset_singleton()
    bad_dir = str(tmp_path / "not_a_file")
    os.makedirs(bad_dir, exist_ok=True)
    monkeypatch.setenv("LIDAR_HISTORY_DB", bad_dir)
    d = LidarDriver({})
    # scan() must not raise even when history DB path is a directory
    result = d.scan()
    assert isinstance(result, list)
    assert len(result) > 0
    d.close()
    _reset_singleton()


def test_default_window_returns_data_from_last_60s(driver):
    """Default window_s=60 should include rows just logged."""
    driver.scan()
    hist = driver.get_scan_history()  # default window_s=60
    assert len(hist) == 1


def test_point_count_matches_scan_result_length(driver):
    """point_count in history row must equal the number of points returned by scan()."""
    result = driver.scan()
    hist = driver.get_scan_history()
    assert len(hist) == 1
    assert hist[0]["point_count"] == len(result)


def test_min_distance_mm_is_populated(driver):
    """min_distance_mm must be a positive number for a mock scan."""
    driver.scan()
    hist = driver.get_scan_history()
    assert hist[0]["min_distance_mm"] is not None
    assert hist[0]["min_distance_mm"] > 0


def test_sector_fields_are_populated(driver):
    """Sector fields (front_mm, left_mm, right_mm, rear_mm) must be non-None for mock."""
    driver.scan()
    hist = driver.get_scan_history()
    row = hist[0]
    for field in ("front_mm", "left_mm", "right_mm", "rear_mm"):
        assert row[field] is not None, f"{field} should not be None for a mock scan"


def test_get_scan_history_returns_empty_list_on_no_scans(driver):
    """With no scans yet, get_scan_history() should return []."""
    hist = driver.get_scan_history()
    assert hist == []
