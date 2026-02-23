"""Tests for castor/slam.py (issue #136)."""

import math
import time
from unittest.mock import patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# OccupancyGrid
# ---------------------------------------------------------------------------


def _fresh_grid(rows=10, cols=10):
    from castor.slam import OccupancyGrid, _OCC_UNKNOWN

    return OccupancyGrid(rows=rows, cols=cols), _OCC_UNKNOWN


def test_grid_initialized_unknown():
    from castor.slam import _OCC_UNKNOWN

    grid, _ = _fresh_grid()
    assert (grid._grid == _OCC_UNKNOWN).all()


def test_mark_free_sets_value():
    from castor.slam import _OCC_FREE, _OCC_UNKNOWN

    grid, _ = _fresh_grid()
    grid.mark_free(3, 4)
    assert grid._grid[3, 4] == _OCC_FREE


def test_mark_free_does_not_overwrite_obstacle():
    from castor.slam import _OCC_OBSTACLE

    grid, _ = _fresh_grid()
    grid.mark_obstacle(3, 4)
    grid.mark_free(3, 4)  # should NOT overwrite obstacle
    assert grid._grid[3, 4] == _OCC_OBSTACLE


def test_mark_obstacle_sets_value():
    from castor.slam import _OCC_OBSTACLE

    grid, _ = _fresh_grid()
    grid.mark_obstacle(5, 5)
    assert grid._grid[5, 5] == _OCC_OBSTACLE


def test_out_of_bounds_mark_free_no_crash():
    grid, _ = _fresh_grid(rows=5, cols=5)
    grid.mark_free(-1, 0)  # negative row → ignored
    grid.mark_free(0, 10)  # col > cols → ignored


def test_out_of_bounds_mark_obstacle_no_crash():
    grid, _ = _fresh_grid(rows=5, cols=5)
    grid.mark_obstacle(99, 99)  # out of range → ignored


def test_to_png_returns_bytes():
    from castor.slam import OccupancyGrid  # use default 200x200 size

    grid = OccupancyGrid()
    png = grid.to_png()
    assert isinstance(png, bytes)
    assert len(png) > 0


def test_to_png_fallback_stdlib(monkeypatch):
    """to_png() via stdlib zlib when cv2 is absent."""
    from castor import slam

    monkeypatch.setattr(slam, "HAS_CV2", False)
    grid = slam.OccupancyGrid(rows=10, cols=10)
    png = grid.to_png()
    assert png[:4] == b"\x89PNG"


def test_reset_returns_to_unknown():
    from castor.slam import _OCC_UNKNOWN

    grid, _ = _fresh_grid()
    grid.mark_free(2, 2)
    grid.mark_obstacle(3, 3)
    grid.reset()
    assert (grid._grid == _OCC_UNKNOWN).all()


# ---------------------------------------------------------------------------
# SLAMMapper lifecycle
# ---------------------------------------------------------------------------


def _fresh_mapper():
    from castor.slam import SLAMMapper

    return SLAMMapper()


def test_mapper_start_stop_mock():
    """start_mapping() / stop_mapping() work in mock mode."""
    with patch("castor.slam.HAS_DEPTHAI", False):
        mapper = _fresh_mapper()
        mapper.start_mapping()
        assert mapper._mapping is True
        time.sleep(0.15)  # let mock loop run briefly
        path = mapper.stop_mapping()
        assert mapper._mapping is False


def test_mapper_start_idempotent():
    with patch("castor.slam.HAS_DEPTHAI", False):
        mapper = _fresh_mapper()
        mapper.start_mapping()
        t1 = mapper._thread
        mapper.start_mapping()  # second call should no-op
        assert mapper._thread is t1
        mapper.stop_mapping()


def test_get_map_png_returns_bytes():
    with patch("castor.slam.HAS_DEPTHAI", False):
        mapper = _fresh_mapper()
        png = mapper.get_map_png()
        assert isinstance(png, bytes)
        assert len(png) > 0


def test_get_pose_returns_dict():
    with patch("castor.slam.HAS_DEPTHAI", False):
        mapper = _fresh_mapper()
        pose = mapper.get_pose()
        assert "x" in pose
        assert "y" in pose
        assert "theta" in pose
        assert "confidence" in pose


def test_mock_loop_updates_pose():
    """After running for a short time, mock mode updates pose fields."""
    with patch("castor.slam.HAS_DEPTHAI", False):
        mapper = _fresh_mapper()
        mapper.start_mapping()
        time.sleep(0.3)
        pose = mapper.get_pose()
        mapper.stop_mapping()
        # Mock moves in a circle; confidence should be < 1.0
        assert pose["confidence"] == pytest.approx(0.7, abs=0.01)


# ---------------------------------------------------------------------------
# navigate_to
# ---------------------------------------------------------------------------


def test_navigate_to_origin():
    mapper = _fresh_mapper()
    result = mapper.navigate_to(0.0, 0.0)
    assert result["distance_m"] == pytest.approx(0.0, abs=0.01)
    assert result["feasible"] is True
    assert isinstance(result["path"], list)


def test_navigate_to_nonzero_distance():
    mapper = _fresh_mapper()
    result = mapper.navigate_to(1.0, 0.0)
    assert result["distance_m"] == pytest.approx(1.0, abs=0.1)
    assert result["waypoint_count"] > 0
    # First waypoint near origin, last near goal
    last = result["path"][-1]
    assert last["x"] == pytest.approx(1.0, abs=0.1)


def test_navigate_to_path_length():
    mapper = _fresh_mapper()
    result = mapper.navigate_to(0.5, 0.5)
    # distance ≈ 0.707 → steps = int(0.707/0.05) = 14 → 15 waypoints
    assert result["waypoint_count"] == len(result["path"])


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_mapper_singleton():
    import castor.slam as slam_mod

    slam_mod._mapper = None
    m1 = slam_mod.get_mapper()
    m2 = slam_mod.get_mapper()
    assert m1 is m2
    slam_mod._mapper = None
