"""Tests for LidarDriver.sector_history() — Issue #409."""

from __future__ import annotations

import os
import time

import pytest

from castor.drivers.lidar_driver import LidarDriver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(tmp_path) -> LidarDriver:
    """Return a mock-mode LidarDriver backed by a temp SQLite history DB."""
    db_path = str(tmp_path / "lidar_test.db")
    os.environ["LIDAR_HISTORY_DB"] = db_path
    driver = LidarDriver()
    return driver


def _seed_history(driver: LidarDriver, count: int = 3) -> None:
    """Insert *count* fake scan rows into the history DB via scan()."""
    for _ in range(count):
        driver.scan()
        time.sleep(0.01)  # tiny gap so timestamps differ


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_returns_dict(tmp_path):
    """sector_history() returns a dict."""
    driver = _make_driver(tmp_path)
    _seed_history(driver, 2)
    result = driver.sector_history()
    assert isinstance(result, dict)


def test_has_required_keys(tmp_path):
    """Return value contains 'sectors', 'window_s', and 'mode' keys."""
    driver = _make_driver(tmp_path)
    _seed_history(driver, 2)
    result = driver.sector_history()
    assert "sectors" in result
    assert "window_s" in result
    assert "mode" in result


def test_sectors_is_dict(tmp_path):
    """'sectors' value is a dict."""
    driver = _make_driver(tmp_path)
    _seed_history(driver, 2)
    result = driver.sector_history()
    assert isinstance(result["sectors"], dict)


def test_window_s_matches_param(tmp_path):
    """'window_s' reflects the value passed to sector_history()."""
    driver = _make_driver(tmp_path)
    result = driver.sector_history(window_s=15.0)
    assert result["window_s"] == 15.0


def test_mode_is_str(tmp_path):
    """'mode' is a string."""
    driver = _make_driver(tmp_path)
    result = driver.sector_history()
    assert isinstance(result["mode"], str)


def test_sector_values_are_lists(tmp_path):
    """Each value in 'sectors' is a list."""
    driver = _make_driver(tmp_path)
    _seed_history(driver, 3)
    result = driver.sector_history()
    for sector_name, entries in result["sectors"].items():
        assert isinstance(entries, list), f"sector {sector_name!r} is not a list"


def test_list_items_have_ts_and_dist_mm(tmp_path):
    """Each item in a sector list has 'ts' and 'dist_mm' keys."""
    driver = _make_driver(tmp_path)
    _seed_history(driver, 3)
    result = driver.sector_history()
    for sector_name, entries in result["sectors"].items():
        for item in entries:
            assert "ts" in item, f"sector {sector_name!r} item missing 'ts': {item}"
            assert "dist_mm" in item, f"sector {sector_name!r} item missing 'dist_mm': {item}"


def test_never_raises(tmp_path):
    """sector_history() should never raise even with a broken DB path."""
    os.environ["LIDAR_HISTORY_DB"] = "/nonexistent_dir_xyz/bad.db"
    driver = LidarDriver()
    result = driver.sector_history()  # must not raise
    assert isinstance(result, dict)


def test_custom_window_s_accepted(tmp_path):
    """A non-default window_s value is accepted and reflected in the result."""
    driver = _make_driver(tmp_path)
    result = driver.sector_history(window_s=120.0)
    assert result["window_s"] == 120.0


def test_empty_history_returns_empty_sectors(tmp_path):
    """With no scan history, 'sectors' should be an empty dict."""
    driver = _make_driver(tmp_path)
    # Do NOT seed any history
    result = driver.sector_history()
    assert result["sectors"] == {}


def test_ts_values_monotonically_increase(tmp_path):
    """Within each sector, ts values should be non-decreasing."""
    driver = _make_driver(tmp_path)
    _seed_history(driver, 5)
    result = driver.sector_history(window_s=60.0)
    for sector_name, entries in result["sectors"].items():
        if len(entries) < 2:
            continue
        ts_list = [e["ts"] for e in entries]
        assert ts_list == sorted(ts_list), (
            f"sector {sector_name!r} ts values not monotonically increasing: {ts_list}"
        )


def test_dist_mm_values_are_numeric(tmp_path):
    """dist_mm values in each sector entry are numeric (int or float)."""
    driver = _make_driver(tmp_path)
    _seed_history(driver, 3)
    result = driver.sector_history()
    for sector_name, entries in result["sectors"].items():
        for item in entries:
            assert isinstance(item["dist_mm"], (int, float)), (
                f"sector {sector_name!r} dist_mm is not numeric: {item['dist_mm']}"
            )


def test_sector_names_are_known(tmp_path):
    """Sector names returned are a subset of the known four cardinal sectors."""
    known = {"front", "left", "right", "rear"}
    driver = _make_driver(tmp_path)
    _seed_history(driver, 3)
    result = driver.sector_history()
    for sector_name in result["sectors"]:
        assert sector_name in known, f"Unexpected sector name: {sector_name!r}"
