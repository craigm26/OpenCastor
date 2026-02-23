"""
tests/test_pointcloud.py — Unit tests for castor/pointcloud.py

Covers:
  - PointCloudCapture: mock mode (HAS_DEPTHAI=False)
  - capture() returns Nx3 numpy array
  - to_json_dict() structure and content
  - to_ply_bytes() produces valid binary PLY
  - stats() returns expected keys
  - Singleton factory (get_capture)
  - Edge: empty point cloud stats
"""

from __future__ import annotations

import struct
from unittest.mock import patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_capture():
    """Return a PointCloudCapture forced into mock mode."""
    with patch("castor.pointcloud.HAS_DEPTHAI", False):
        from castor.pointcloud import PointCloudCapture

        return PointCloudCapture()


# ---------------------------------------------------------------------------
# capture()
# ---------------------------------------------------------------------------


def test_capture_returns_ndarray_mock():
    """capture() in mock mode returns a numpy ndarray."""
    cap = _fresh_capture()
    pts = cap.capture()
    assert isinstance(pts, np.ndarray)


def test_capture_shape_nx3():
    """capture() result must be shape (N, 3)."""
    cap = _fresh_capture()
    pts = cap.capture()
    assert pts.ndim == 2
    assert pts.shape[1] == 3


def test_capture_mock_500_points():
    """Mock mode returns exactly 500 points (min(500, _MAX_POINTS))."""
    with patch("castor.pointcloud._MAX_POINTS", 500):
        cap = _fresh_capture()
        pts = cap.capture()
    assert pts.shape[0] == 500


def test_capture_mode_is_mock():
    """PointCloudCapture._mode should be 'mock' when HAS_DEPTHAI=False."""
    cap = _fresh_capture()
    assert cap._mode == "mock"


# ---------------------------------------------------------------------------
# to_json_dict()
# ---------------------------------------------------------------------------


def test_to_json_dict_keys():
    """to_json_dict() must contain required keys."""
    cap = _fresh_capture()
    d = cap.to_json_dict()
    assert "point_count" in d
    assert "points" in d
    assert "bounds" in d
    assert "mode" in d


def test_to_json_dict_point_count_matches_points():
    """to_json_dict() point_count must equal len(points)."""
    cap = _fresh_capture()
    d = cap.to_json_dict()
    assert d["point_count"] == len(d["points"])


def test_to_json_dict_bounds_structure():
    """bounds must have x, y, z keys each with 2-element lists."""
    cap = _fresh_capture()
    d = cap.to_json_dict()
    bounds = d["bounds"]
    for axis in ("x", "y", "z"):
        assert axis in bounds
        assert len(bounds[axis]) == 2
        assert bounds[axis][0] <= bounds[axis][1]


def test_to_json_dict_mode_mock():
    """to_json_dict() mode should reflect mock."""
    cap = _fresh_capture()
    d = cap.to_json_dict()
    assert d["mode"] == "mock"


# ---------------------------------------------------------------------------
# to_ply_bytes()
# ---------------------------------------------------------------------------


def test_to_ply_bytes_returns_bytes():
    """to_ply_bytes() must return bytes."""
    cap = _fresh_capture()
    raw = cap.to_ply_bytes()
    assert isinstance(raw, bytes)
    assert len(raw) > 0


def test_to_ply_bytes_header_starts_with_ply():
    """PLY files must start with the 'ply' magic string."""
    cap = _fresh_capture()
    raw = cap.to_ply_bytes()
    assert raw.startswith(b"ply\n")


def test_to_ply_bytes_body_length():
    """Binary PLY body should be N * 3 * 4 bytes (float32 x,y,z)."""
    cap = _fresh_capture()
    pts = cap.capture()
    raw = cap.to_ply_bytes()
    header_end = raw.find(b"end_header\n") + len(b"end_header\n")
    body = raw[header_end:]
    expected_bytes = pts.shape[0] * 3 * 4  # float32
    assert len(body) == expected_bytes


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------


def test_stats_keys():
    """stats() must contain point_count, mode, bounds_m, density_pts_per_m3."""
    cap = _fresh_capture()
    s = cap.stats()
    assert "point_count" in s
    assert "mode" in s
    assert "bounds_m" in s
    assert "density_pts_per_m3" in s


def test_stats_point_count_positive():
    """stats() point_count must be > 0 in mock mode."""
    cap = _fresh_capture()
    s = cap.stats()
    assert s["point_count"] > 0


def test_stats_density_positive():
    """density_pts_per_m3 must be a positive float."""
    cap = _fresh_capture()
    s = cap.stats()
    assert isinstance(s["density_pts_per_m3"], float)
    assert s["density_pts_per_m3"] > 0.0


def test_stats_bounds_m_axis_keys():
    """bounds_m must have x, y, z sub-keys."""
    cap = _fresh_capture()
    s = cap.stats()
    for axis in ("x", "y", "z"):
        assert axis in s["bounds_m"]


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


def test_get_capture_singleton():
    """get_capture() must return the same instance on repeated calls."""
    import castor.pointcloud as pc_mod

    pc_mod._singleton = None  # reset
    with patch("castor.pointcloud.HAS_DEPTHAI", False):
        c1 = pc_mod.get_capture()
        c2 = pc_mod.get_capture()
    assert c1 is c2
    pc_mod._singleton = None  # clean up
