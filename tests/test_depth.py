"""
Tests for castor.depth — OAK-D depth overlay and obstacle zone detection.
"""

import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from castor.depth import get_depth_overlay, get_obstacle_zones


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_depth_array(h: int = 48, w: int = 64, fill: int = 1000) -> np.ndarray:
    """Create a simple uint16 depth array with a constant fill value (millimetres)."""
    return np.full((h, w), fill, dtype=np.uint16)


def _make_rgb_jpeg(h: int = 48, w: int = 64) -> bytes:
    """Return a minimal JPEG of the requested size using OpenCV."""
    import cv2
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (64, 128, 192)  # BGR fill so it's not all black
    _, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


# ---------------------------------------------------------------------------
# get_obstacle_zones
# ---------------------------------------------------------------------------

class TestObstacleZones:
    def test_no_depth_returns_unavailable(self):
        """None depth_frame must return {'available': False}."""
        result = get_obstacle_zones(None)
        assert result == {"available": False}

    def test_three_sectors_correct_values(self):
        """Known depth array: left third = 500 mm, center = 800 mm, right = 1200 mm."""
        h, w = 30, 90
        arr = np.zeros((h, w), dtype=np.uint16)
        arr[:, :30]  = 500   # left third  →  50.0 cm
        arr[:, 30:60] = 800  # center third →  80.0 cm
        arr[:, 60:]  = 1200  # right third  → 120.0 cm

        result = get_obstacle_zones(arr)

        assert result["available"] is True
        assert result["left_cm"]   == pytest.approx(50.0, abs=0.2)
        assert result["center_cm"] == pytest.approx(80.0, abs=0.2)
        assert result["right_cm"]  == pytest.approx(120.0, abs=0.2)

    def test_nearest_cm_is_minimum_across_sectors(self):
        """nearest_cm must equal the smallest of left/center/right."""
        h, w = 30, 90
        arr = np.zeros((h, w), dtype=np.uint16)
        arr[:, :30]  = 2000   # 200 cm
        arr[:, 30:60] = 300   # 30 cm   ← nearest
        arr[:, 60:]  = 1500   # 150 cm

        result = get_obstacle_zones(arr)

        assert result["available"] is True
        assert result["nearest_cm"] == pytest.approx(30.0, abs=0.2)
        # Nearest must be the overall min
        assert result["nearest_cm"] == min(
            result["left_cm"], result["center_cm"], result["right_cm"]
        )

    def test_sectors_with_zeros_excluded(self):
        """Zero-depth pixels (invalid readings) must be ignored."""
        h, w = 10, 30
        arr = np.zeros((h, w), dtype=np.uint16)
        # Left third: all zeros (invalid)
        arr[:, :10]  = 0
        # Center: mix of zeros and valid
        arr[:, 10:20] = 0
        arr[0, 15] = 700   # one valid pixel at 70 cm
        # Right: all valid
        arr[:, 20:]  = 1000  # 100 cm

        result = get_obstacle_zones(arr)

        assert result["available"] is True
        assert result["left_cm"]   == 0.0   # no valid pixels
        assert result["center_cm"] == pytest.approx(70.0, abs=0.2)
        assert result["right_cm"]  == pytest.approx(100.0, abs=0.2)

    def test_getframe_interface(self):
        """Objects with .getFrame() method should work like numpy arrays."""
        arr = np.full((30, 90), 600, dtype=np.uint16)  # 60 cm everywhere
        mock_frame = MagicMock()
        mock_frame.getFrame.return_value = arr

        result = get_obstacle_zones(mock_frame)

        assert result["available"] is True
        assert result["left_cm"]   == pytest.approx(60.0, abs=0.2)
        assert result["center_cm"] == pytest.approx(60.0, abs=0.2)
        assert result["right_cm"]  == pytest.approx(60.0, abs=0.2)
        assert result["nearest_cm"] == pytest.approx(60.0, abs=0.2)

    def test_all_zero_depth_frame(self):
        """A depth frame of all zeros is effectively unavailable — nearest_cm = 0."""
        arr = np.zeros((30, 90), dtype=np.uint16)
        result = get_obstacle_zones(arr)
        assert result["available"] is True
        assert result["nearest_cm"] == 0.0


# ---------------------------------------------------------------------------
# get_depth_overlay
# ---------------------------------------------------------------------------

class TestDepthOverlay:
    def test_returns_bytes(self):
        """get_depth_overlay must always return non-empty bytes (JPEG)."""
        rgb = _make_rgb_jpeg()
        depth = _make_depth_array()
        result = get_depth_overlay(rgb, depth)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_jpeg_magic_bytes(self):
        """Output must start with JPEG magic (0xFF 0xD8)."""
        rgb = _make_rgb_jpeg()
        depth = _make_depth_array()
        result = get_depth_overlay(rgb, depth)
        assert result[:2] == b"\xff\xd8"

    def test_no_depth_returns_jpeg(self):
        """None depth_frame: should still return a valid JPEG of the RGB."""
        rgb = _make_rgb_jpeg()
        result = get_depth_overlay(rgb, None)
        assert isinstance(result, bytes)
        assert result[:2] == b"\xff\xd8"

    def test_none_rgb_no_crash(self):
        """None RGB frame should not crash — returns a black frame JPEG."""
        depth = _make_depth_array()
        result = get_depth_overlay(None, depth)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_getframe_interface(self):
        """depth_frame with .getFrame() should produce a JPEG overlay."""
        rgb = _make_rgb_jpeg()
        arr = _make_depth_array()
        mock_frame = MagicMock()
        mock_frame.getFrame.return_value = arr

        result = get_depth_overlay(rgb, mock_frame)
        assert isinstance(result, bytes)
        assert result[:2] == b"\xff\xd8"


# ---------------------------------------------------------------------------
# API endpoint tests (using FastAPI TestClient)
# ---------------------------------------------------------------------------

def _make_client_and_reset(monkeypatch):
    """Create a TestClient with lifecycle events cleared."""
    import collections
    import time

    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)

    import castor.api as api_mod

    api_mod.state.config = None
    api_mod.state.brain = None
    api_mod.state.driver = None
    api_mod.state.channels = {}
    api_mod.state.last_thought = None
    api_mod.state.boot_time = time.time()
    api_mod.state.fs = None
    api_mod.state.ruri = None
    api_mod.state.offline_fallback = None
    api_mod.state.provider_fallback = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.API_TOKEN = None

    from castor.api import app
    from starlette.testclient import TestClient

    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    return TestClient(app, raise_server_exceptions=False)


class TestDepthEndpoints:
    def test_depth_endpoint_no_camera(self, monkeypatch):
        """GET /api/depth/frame returns 503 when no camera is available."""
        client = _make_client_and_reset(monkeypatch)
        # Patch the lazy import inside the endpoint (castor.main.get_shared_camera)
        with patch("castor.main.get_shared_camera", return_value=None):
            resp = client.get("/api/depth/frame")
        assert resp.status_code == 503

    def test_depth_endpoint_camera_unavailable(self, monkeypatch):
        """GET /api/depth/frame returns 503 when camera.is_available() is False."""
        client = _make_client_and_reset(monkeypatch)
        mock_camera = MagicMock()
        mock_camera.is_available.return_value = False
        mock_camera.last_depth = None

        with patch("castor.main.get_shared_camera", return_value=mock_camera):
            resp = client.get("/api/depth/frame")
        assert resp.status_code == 503

    def test_depth_endpoint_no_depth(self, monkeypatch):
        """GET /api/depth/frame with camera but no depth — returns JPEG of plain RGB."""
        client = _make_client_and_reset(monkeypatch)

        mock_camera = MagicMock()
        mock_camera.is_available.return_value = True
        mock_camera.last_depth = None
        mock_camera.capture_jpeg.return_value = _make_rgb_jpeg()

        with patch("castor.main.get_shared_camera", return_value=mock_camera):
            with patch("castor.api._capture_live_frame", return_value=_make_rgb_jpeg()):
                resp = client.get("/api/depth/frame")

        # Should succeed (200) with a JPEG
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.content[:2] == b"\xff\xd8"

    def test_obstacles_endpoint_returns_json(self, monkeypatch):
        """GET /api/depth/obstacles returns valid JSON with expected structure."""
        client = _make_client_and_reset(monkeypatch)

        arr = np.full((30, 90), 1000, dtype=np.uint16)  # 100 cm everywhere
        mock_camera = MagicMock()
        mock_camera.is_available.return_value = True
        mock_camera.last_depth = arr

        with patch("castor.main.get_shared_camera", return_value=mock_camera):
            resp = client.get("/api/depth/obstacles")

        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data

    def test_obstacles_endpoint_no_camera(self, monkeypatch):
        """GET /api/depth/obstacles returns available=false when no camera."""
        client = _make_client_and_reset(monkeypatch)

        with patch("castor.main.get_shared_camera", return_value=None):
            resp = client.get("/api/depth/obstacles")

        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
