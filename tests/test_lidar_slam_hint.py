"""Tests for LidarDriver.slam_hint() — Issue #332."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from castor.drivers.lidar_driver import LidarDriver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_points(angles_dists: list) -> list:
    """Build a scan-point list from (angle_deg_0_360, distance_mm) pairs."""
    return [
        {"angle_deg": float(a), "distance_mm": float(d), "quality": 15} for a, d in angles_dists
    ]


def _driver_with_mock_scan(points: list) -> LidarDriver:
    """Return a LidarDriver whose scan() always returns *points*."""
    drv = LidarDriver.__new__(LidarDriver)
    drv._mode = "mock"
    drv._lidar = None
    drv._lock = __import__("threading").Lock()
    drv._last_scan = []
    drv._scan_count = 0
    drv._history_db_path = None
    drv._history_con = None
    drv._history_insert_count = 0
    drv.scan = MagicMock(return_value=points)
    return drv


# ---------------------------------------------------------------------------
# Test 1 — slam_hint() returns a dict
# ---------------------------------------------------------------------------


def test_slam_hint_returns_dict():
    drv = LidarDriver.__new__(LidarDriver)
    drv._mode = "mock"
    drv._lidar = None
    drv._lock = __import__("threading").Lock()
    drv._last_scan = []
    drv._scan_count = 0
    drv._history_db_path = None
    drv._history_con = None
    drv._history_insert_count = 0
    result = drv.slam_hint()
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Test 2 — result has "available" and "walls" keys
# ---------------------------------------------------------------------------


def test_slam_hint_has_required_keys():
    drv = _driver_with_mock_scan([])
    result = drv.slam_hint()
    assert "available" in result
    assert "walls" in result


# ---------------------------------------------------------------------------
# Test 3 — available=False when scan raises
# ---------------------------------------------------------------------------


def test_slam_hint_available_false_on_scan_exception():
    drv = LidarDriver.__new__(LidarDriver)
    drv._mode = "mock"
    drv._lidar = None
    drv._lock = __import__("threading").Lock()
    drv._last_scan = []
    drv._scan_count = 0
    drv._history_db_path = None
    drv._history_con = None
    drv._history_insert_count = 0
    drv.scan = MagicMock(side_effect=RuntimeError("device error"))
    result = drv.slam_hint()
    assert result["available"] is False


# ---------------------------------------------------------------------------
# Test 4 — walls is a list
# ---------------------------------------------------------------------------


def test_slam_hint_walls_is_list():
    drv = _driver_with_mock_scan([])
    result = drv.slam_hint()
    assert isinstance(result["walls"], list)


# ---------------------------------------------------------------------------
# Test 5 — each wall entry has sector, distance_m, angle_deg, confidence
# ---------------------------------------------------------------------------


def test_slam_hint_wall_entry_keys():
    # Provide ≥3 front points (angles near 0°)
    points = _make_points([(5, 1000), (355, 1100), (10, 900), (0, 950)])
    drv = _driver_with_mock_scan(points)
    result = drv.slam_hint()
    assert result["available"] is True
    assert len(result["walls"]) >= 1
    wall = result["walls"][0]
    for key in ("sector", "distance_m", "angle_deg", "confidence"):
        assert key in wall, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Test 6 — confidence is between 0.0 and 1.0
# ---------------------------------------------------------------------------


def test_slam_hint_confidence_range():
    # 10 front points → confidence = 1.0; 3 left points → 0.3
    front_pts = _make_points([(a, 1000) for a in range(0, 10)])
    left_pts = _make_points([(a, 2000) for a in [45, 90, 135]])
    drv = _driver_with_mock_scan(front_pts + left_pts)
    result = drv.slam_hint()
    for wall in result["walls"]:
        assert 0.0 <= wall["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Test 7 — distance_m is in metres (not mm)
# ---------------------------------------------------------------------------


def test_slam_hint_distance_in_metres():
    # 5 front points all at 2000 mm = 2.0 m
    points = _make_points([(a, 2000) for a in [-10, -5, 0, 5, 10]])
    drv = _driver_with_mock_scan(points)
    result = drv.slam_hint()
    front_walls = [w for w in result["walls"] if w["sector"] == "front"]
    assert len(front_walls) == 1
    # Should be ~2.0 m, definitely not ~2000
    assert 0.0 < front_walls[0]["distance_m"] < 100.0
    assert abs(front_walls[0]["distance_m"] - 2.0) < 0.01


# ---------------------------------------------------------------------------
# Test 8 — sector values are one of front/left/right
# ---------------------------------------------------------------------------


def test_slam_hint_sector_values():
    # Points covering all three sectors
    pts = (
        _make_points([(a, 1000) for a in [-10, 0, 10, 20, -20]])  # front
        + _make_points([(a, 1500) for a in [45, 90, 135, 60, 120]])  # left
        + _make_points([(a, 2000) for a in [200, 210, 220, 230, 240]])  # right (signed -160..-120)
    )
    drv = _driver_with_mock_scan(pts)
    result = drv.slam_hint()
    valid_sectors = {"front", "left", "right"}
    for wall in result["walls"]:
        assert wall["sector"] in valid_sectors


# ---------------------------------------------------------------------------
# Test 9 — sector with fewer than 3 points is excluded
# ---------------------------------------------------------------------------


def test_slam_hint_excludes_sector_with_fewer_than_3_points():
    # Only 2 front points — should be excluded
    points = _make_points([(0, 1000), (5, 1100)])
    drv = _driver_with_mock_scan(points)
    result = drv.slam_hint()
    front_walls = [w for w in result["walls"] if w["sector"] == "front"]
    assert len(front_walls) == 0


# ---------------------------------------------------------------------------
# Test 10 — points only in front → only front wall detected
# ---------------------------------------------------------------------------


def test_slam_hint_only_front_sector():
    # 5 front points, nothing in left/right
    points = _make_points([(a, 1500) for a in [-20, -10, 0, 10, 20]])
    drv = _driver_with_mock_scan(points)
    result = drv.slam_hint()
    assert result["available"] is True
    sectors_found = {w["sector"] for w in result["walls"]}
    assert sectors_found == {"front"}


# ---------------------------------------------------------------------------
# Test 11 — empty scan → walls=[]
# ---------------------------------------------------------------------------


def test_slam_hint_empty_scan_gives_empty_walls():
    drv = _driver_with_mock_scan([])
    result = drv.slam_hint()
    assert result["available"] is False
    assert result["walls"] == []


# ---------------------------------------------------------------------------
# Test 12 — available=True when scan succeeds with points
# ---------------------------------------------------------------------------


def test_slam_hint_available_true_with_points():
    points = _make_points([(a, 1000) for a in [-15, -5, 0, 5, 15]])
    drv = _driver_with_mock_scan(points)
    result = drv.slam_hint()
    assert result["available"] is True


# ---------------------------------------------------------------------------
# Test 13 — confidence capped at 1.0 for many points
# ---------------------------------------------------------------------------


def test_slam_hint_confidence_capped_at_one():
    # 20 front points → confidence = min(1.0, 20/10) = 1.0
    points = _make_points([(a % 61 - 30, 1000) for a in range(20)])
    drv = _driver_with_mock_scan(points)
    result = drv.slam_hint()
    front = [w for w in result["walls"] if w["sector"] == "front"]
    assert len(front) == 1
    assert front[0]["confidence"] == 1.0


# ---------------------------------------------------------------------------
# Test 14 — distance_m uses median (not mean) — outlier robustness
# ---------------------------------------------------------------------------


def test_slam_hint_uses_median_distance():
    # 5 front points: 1000, 1000, 1000, 5000, 5000 mm
    # median = 1000 mm = 1.0 m; mean would be 2600 mm
    points = _make_points([(-10, 1000), (-5, 1000), (0, 1000), (5, 5000), (10, 5000)])
    drv = _driver_with_mock_scan(points)
    result = drv.slam_hint()
    front = [w for w in result["walls"] if w["sector"] == "front"]
    assert len(front) == 1
    # Median of [1000, 1000, 1000, 5000, 5000] = 1000 mm = 1.0 m
    assert abs(front[0]["distance_m"] - 1.0) < 0.01


# ---------------------------------------------------------------------------
# Test 15 — exactly 3 points in left sector → included
# ---------------------------------------------------------------------------


def test_slam_hint_exactly_3_points_included():
    points = _make_points([(45, 1000), (90, 1000), (135, 1000)])
    drv = _driver_with_mock_scan(points)
    result = drv.slam_hint()
    left = [w for w in result["walls"] if w["sector"] == "left"]
    assert len(left) == 1
