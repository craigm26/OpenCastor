"""Tests for CameraManager.model, .composite_mode, and OAK camera type routing."""

from unittest.mock import MagicMock
import pytest
from castor.camera import CameraManager, _OakCameraSource


def test_camera_manager_model_usb():
    mgr = CameraManager([{"id": "front", "type": "usb", "index": 0, "role": "primary"}])
    assert mgr.model == "USB 0"


def test_camera_manager_composite_mode():
    mgr = CameraManager([], composite_mode="tile")
    assert mgr.composite_mode == "tile"


def test_camera_manager_oak_source_created():
    mgr = CameraManager([{"id": "depth", "type": "oak_d", "role": "primary"}])
    src = mgr._sources.get("depth")
    assert isinstance(src, _OakCameraSource)


def test_camera_manager_model_oak():
    mgr = CameraManager([{"id": "depth", "type": "oak_d", "role": "primary"}])
    assert mgr.model == "OAK-4 Pro"


def test_oak_source_open_without_depthai(monkeypatch):
    import castor.camera as cam_mod

    monkeypatch.setattr(cam_mod, "HAS_DEPTHAI", False)
    src = _OakCameraSource("test_cam")
    result = src.open()
    assert result is False


def test_camera_manager_is_available_false_when_not_open():
    mgr = CameraManager([{"id": "front", "type": "usb", "index": 0, "role": "primary"}])
    assert mgr.is_available() is False


def test_api_status_includes_camera_fields(monkeypatch):
    """GET /api/status must include camera_model and camera_mode."""
    from fastapi.testclient import TestClient
    from castor.api import app, state

    mock_cam = MagicMock()
    mock_cam.model = "USB 0"
    mock_cam.composite_mode = "primary_only"
    monkeypatch.setattr(state, "camera", mock_cam, raising=False)
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "camera_model" in data
    assert "camera_mode" in data
