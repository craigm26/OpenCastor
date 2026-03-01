"""Tests for LidarDriver.obstacle_velocity() — Issue #315."""

from __future__ import annotations

import importlib
import sqlite3
import time

import pytest

# ---------------------------------------------------------------------------
# Fixture: isolated LidarDriver with a temp SQLite history DB
# ---------------------------------------------------------------------------


@pytest.fixture()
def driver_with_history(tmp_path, monkeypatch):
    import castor.drivers.lidar_driver as _mod

    _mod._singleton = None
    db = str(tmp_path / "lidar_hist.db")
    monkeypatch.setenv("LIDAR_HISTORY_DB", db)
    # Reload so that _resolve_history_db_path() picks up the new env var
    importlib.reload(_mod)
    from castor.drivers.lidar_driver import LidarDriver

    d = LidarDriver({})
    yield d, db
    d.close()
    _mod._singleton = None


@pytest.fixture()
def driver_no_history(tmp_path, monkeypatch):
    """Driver with history explicitly disabled."""
    import castor.drivers.lidar_driver as _mod

    _mod._singleton = None
    monkeypatch.setenv("LIDAR_HISTORY_DB", "none")
    importlib.reload(_mod)
    from castor.drivers.lidar_driver import LidarDriver

    d = LidarDriver({})
    yield d
    d.close()
    _mod._singleton = None


# ---------------------------------------------------------------------------
# Helper to insert scan rows directly into the history DB
# ---------------------------------------------------------------------------


def _insert_rows(db_path: str, rows):
    """Insert rows as (ts, front_mm, left_mm, right_mm, rear_mm).

    The DB must already exist (driver must have connected to it).
    """
    con = sqlite3.connect(db_path)
    for ts, front_mm, left_mm, right_mm, rear_mm in rows:
        con.execute(
            "INSERT INTO scans (ts, min_distance_mm, front_mm, left_mm, right_mm, rear_mm, point_count) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                ts,
                min(front_mm, left_mm, right_mm, rear_mm),
                front_mm,
                left_mm,
                right_mm,
                rear_mm,
                10,
            ),
        )
    con.commit()
    con.close()


def _init_db(driver, db_path):
    """Force the driver to open its history DB (needed before _insert_rows)."""
    driver._ensure_history_db()


# ---------------------------------------------------------------------------
# 1. Returns dict with required keys
# ---------------------------------------------------------------------------


def test_velocity_returns_required_keys(driver_with_history):
    driver, db = driver_with_history
    result = driver.obstacle_velocity()
    for key in (
        "front_mm_per_s",
        "left_mm_per_s",
        "right_mm_per_s",
        "rear_mm_per_s",
        "window_s",
        "samples",
    ):
        assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# 2. Returns all zeros when no scan history exists
# ---------------------------------------------------------------------------


def test_velocity_zeros_no_history(driver_with_history):
    driver, db = driver_with_history
    result = driver.obstacle_velocity()
    assert result["front_mm_per_s"] == 0.0
    assert result["left_mm_per_s"] == 0.0
    assert result["right_mm_per_s"] == 0.0
    assert result["rear_mm_per_s"] == 0.0


# ---------------------------------------------------------------------------
# 3. Returns all zeros when only 1 scan
# ---------------------------------------------------------------------------


def test_velocity_zeros_single_scan(driver_with_history):
    driver, db = driver_with_history
    _init_db(driver, db)
    t = time.time()
    _insert_rows(db, [(t, 500.0, 1000.0, 1000.0, 1000.0)])
    result = driver.obstacle_velocity(window_s=10.0)
    assert result["front_mm_per_s"] == 0.0
    assert result["samples"] <= 1


# ---------------------------------------------------------------------------
# 4. samples field is correct count
# ---------------------------------------------------------------------------


def test_velocity_samples_count(driver_with_history):
    driver, db = driver_with_history
    _init_db(driver, db)
    t = time.time()
    rows = [(t + i * 0.5, 500.0 + i * 10, 1000.0, 1000.0, 1000.0) for i in range(5)]
    _insert_rows(db, rows)
    result = driver.obstacle_velocity(window_s=10.0)
    assert result["samples"] == 5


# ---------------------------------------------------------------------------
# 5. window_s field matches parameter
# ---------------------------------------------------------------------------


def test_velocity_window_s_field(driver_with_history):
    driver, db = driver_with_history
    result = driver.obstacle_velocity(window_s=3.7)
    assert result["window_s"] == pytest.approx(3.7)


# ---------------------------------------------------------------------------
# 6. Positive velocity when distance increasing (obstacle receding)
# ---------------------------------------------------------------------------


def test_velocity_positive_when_receding(driver_with_history):
    driver, db = driver_with_history
    _init_db(driver, db)
    t = time.time()
    # front_mm increases over time → positive slope
    rows = [(t + i * 0.5, 500.0 + i * 100.0, 1000.0, 1000.0, 1000.0) for i in range(5)]
    _insert_rows(db, rows)
    result = driver.obstacle_velocity(window_s=10.0)
    assert result["front_mm_per_s"] > 0.0, "Expected positive velocity for receding obstacle"


# ---------------------------------------------------------------------------
# 7. Negative velocity when distance decreasing (obstacle approaching)
# ---------------------------------------------------------------------------


def test_velocity_negative_when_approaching(driver_with_history):
    driver, db = driver_with_history
    _init_db(driver, db)
    t = time.time()
    # front_mm decreases over time → negative slope
    rows = [(t + i * 0.5, 800.0 - i * 100.0, 1000.0, 1000.0, 1000.0) for i in range(5)]
    _insert_rows(db, rows)
    result = driver.obstacle_velocity(window_s=10.0)
    assert result["front_mm_per_s"] < 0.0, "Expected negative velocity for approaching obstacle"


# ---------------------------------------------------------------------------
# 8. window_s parameter respected (filters old scans)
# ---------------------------------------------------------------------------


def test_velocity_window_filters_old_scans(driver_with_history):
    driver, db = driver_with_history
    _init_db(driver, db)
    t = time.time()
    # Old rows (100 s ago) — should be excluded by a 5-second window
    old_rows = [(t - 100.0 + i * 0.5, 200.0 + i * 50.0, 1000.0, 1000.0, 1000.0) for i in range(5)]
    # Recent rows (within 5 s) — constant distance → zero velocity
    recent_rows = [(t - 1.0 + i * 0.2, 600.0, 1000.0, 1000.0, 1000.0) for i in range(5)]
    _insert_rows(db, old_rows + recent_rows)

    result = driver.obstacle_velocity(window_s=5.0)
    # Only recent rows should be included; velocity should be near zero
    assert result["samples"] == 5
    assert abs(result["front_mm_per_s"]) < 1.0, "Old rows should be excluded from window"


# ---------------------------------------------------------------------------
# 9. Never raises on DB error
# ---------------------------------------------------------------------------


def test_velocity_never_raises_on_db_error(driver_with_history):
    driver, db = driver_with_history
    # Corrupt the connection so queries fail
    driver._history_con = None
    driver._history_db_path = "/nonexistent/path/lidar.db"
    result = driver.obstacle_velocity()
    # Must return the zero dict, not raise
    assert isinstance(result, dict)
    assert result["front_mm_per_s"] == 0.0


# ---------------------------------------------------------------------------
# 10. History disabled → returns zeros
# ---------------------------------------------------------------------------


def test_velocity_zeros_history_disabled(driver_no_history):
    driver = driver_no_history
    result = driver.obstacle_velocity()
    assert result["front_mm_per_s"] == 0.0
    assert result["samples"] == 0


# ---------------------------------------------------------------------------
# 11. Left sector velocity computed correctly
# ---------------------------------------------------------------------------


def test_velocity_left_sector(driver_with_history):
    driver, db = driver_with_history
    _init_db(driver, db)
    t = time.time()
    # left_mm increases (receding), others constant
    rows = [(t + i * 0.5, 1000.0, 400.0 + i * 80.0, 1000.0, 1000.0) for i in range(5)]
    _insert_rows(db, rows)
    result = driver.obstacle_velocity(window_s=10.0)
    assert result["left_mm_per_s"] > 0.0


# ---------------------------------------------------------------------------
# 12. Right sector velocity computed correctly
# ---------------------------------------------------------------------------


def test_velocity_right_sector(driver_with_history):
    driver, db = driver_with_history
    _init_db(driver, db)
    t = time.time()
    # right_mm decreases (approaching)
    rows = [(t + i * 0.5, 1000.0, 1000.0, 900.0 - i * 80.0, 1000.0) for i in range(5)]
    _insert_rows(db, rows)
    result = driver.obstacle_velocity(window_s=10.0)
    assert result["right_mm_per_s"] < 0.0


# ---------------------------------------------------------------------------
# 13. Rear sector velocity computed correctly
# ---------------------------------------------------------------------------


def test_velocity_rear_sector(driver_with_history):
    driver, db = driver_with_history
    _init_db(driver, db)
    t = time.time()
    # rear_mm increases (receding obstacle behind)
    rows = [(t + i * 0.5, 1000.0, 1000.0, 1000.0, 300.0 + i * 60.0) for i in range(5)]
    _insert_rows(db, rows)
    result = driver.obstacle_velocity(window_s=10.0)
    assert result["rear_mm_per_s"] > 0.0


# ---------------------------------------------------------------------------
# 14. Slope is approximately correct (regression sanity check)
# ---------------------------------------------------------------------------


def test_velocity_slope_approximate_value(driver_with_history):
    driver, db = driver_with_history
    _init_db(driver, db)
    t = time.time()
    # Insert 5 rows spaced 1 second apart, front_mm increasing by 200 mm/s
    rows = [(t + i * 1.0, 500.0 + i * 200.0, 1000.0, 1000.0, 1000.0) for i in range(5)]
    _insert_rows(db, rows)
    result = driver.obstacle_velocity(window_s=20.0)
    # Slope should be close to 200 mm/s
    assert result["front_mm_per_s"] == pytest.approx(200.0, rel=0.05)
