"""Tests for LidarDriver.point_cloud_2d() — Issue #418."""

from __future__ import annotations

import math
import os

import pytest

from castor.drivers.lidar_driver import LidarDriver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(tmp_path) -> LidarDriver:
    """Return a mock-mode LidarDriver backed by a temp SQLite history DB."""
    db_path = str(tmp_path / "lidar_test.db")
    os.environ["LIDAR_HISTORY_DB"] = db_path
    return LidarDriver()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_returns_dict(tmp_path):
    """point_cloud_2d() returns a dict."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    assert isinstance(result, dict)


def test_has_required_keys(tmp_path):
    """Return value contains 'points', 'count', and 'mode' keys."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    assert "points" in result
    assert "count" in result
    assert "mode" in result


def test_points_is_list(tmp_path):
    """'points' value is a list."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    assert isinstance(result["points"], list)


def test_count_equals_len_points(tmp_path):
    """'count' equals len('points')."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    assert result["count"] == len(result["points"])


def test_mode_is_str(tmp_path):
    """'mode' is a string."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    assert isinstance(result["mode"], str)


def test_each_point_has_required_keys(tmp_path):
    """Each point dict has x_m, y_m, dist_mm, angle_deg keys."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    for i, pt in enumerate(result["points"]):
        assert "x_m" in pt, f"point[{i}] missing 'x_m'"
        assert "y_m" in pt, f"point[{i}] missing 'y_m'"
        assert "dist_mm" in pt, f"point[{i}] missing 'dist_mm'"
        assert "angle_deg" in pt, f"point[{i}] missing 'angle_deg'"


def test_x_m_and_y_m_are_floats(tmp_path):
    """x_m and y_m are float values."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    for i, pt in enumerate(result["points"]):
        assert isinstance(pt["x_m"], float), f"point[{i}] x_m is not float: {pt['x_m']}"
        assert isinstance(pt["y_m"], float), f"point[{i}] y_m is not float: {pt['y_m']}"


def test_never_raises(tmp_path):
    """point_cloud_2d() never raises even with a broken DB path."""
    os.environ["LIDAR_HISTORY_DB"] = "/nonexistent_xyz/bad.db"
    driver = LidarDriver()
    result = driver.point_cloud_2d()  # must not raise
    assert isinstance(result, dict)


def test_count_is_non_negative(tmp_path):
    """'count' is always >= 0."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    assert result["count"] >= 0


def test_all_returned_dist_mm_positive(tmp_path):
    """All returned points have dist_mm > 0 (zero/invalid filtered out)."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    for i, pt in enumerate(result["points"]):
        assert pt["dist_mm"] > 0, f"point[{i}] has non-positive dist_mm: {pt['dist_mm']}"


def test_cartesian_distance_matches_dist_mm(tmp_path):
    """For every point: x_m^2 + y_m^2 ≈ (dist_mm/1000)^2."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    assert len(result["points"]) > 0, "No points returned — cannot verify Cartesian math"
    for i, pt in enumerate(result["points"]):
        expected_r2 = (pt["dist_mm"] / 1000.0) ** 2
        actual_r2 = pt["x_m"] ** 2 + pt["y_m"] ** 2
        assert math.isclose(actual_r2, expected_r2, rel_tol=1e-4), (
            f"point[{i}] Cartesian distance mismatch: "
            f"x_m={pt['x_m']}, y_m={pt['y_m']}, dist_mm={pt['dist_mm']}, "
            f"expected r^2={expected_r2:.6f}, actual r^2={actual_r2:.6f}"
        )


def test_mock_mode_returns_points(tmp_path):
    """In mock mode, the driver returns a non-empty list of points."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    assert result["count"] > 0, "Mock mode should produce scan points"


def test_angle_deg_values_in_range(tmp_path):
    """angle_deg values are in [0, 360) for all returned points."""
    driver = _make_driver(tmp_path)
    result = driver.point_cloud_2d()
    for i, pt in enumerate(result["points"]):
        assert 0.0 <= pt["angle_deg"] < 360.0, (
            f"point[{i}] angle_deg out of range: {pt['angle_deg']}"
        )
