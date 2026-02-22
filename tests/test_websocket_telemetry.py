"""
Tests for the /ws/telemetry WebSocket endpoint (Issue #118).

Uses Starlette's WebSocketTestSession (via TestClient) for synchronous
WebSocket testing without a real async runtime.
"""

import collections
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared setup helpers
# ---------------------------------------------------------------------------

def _reset_state(monkeypatch, api_mod):
    """Reset AppState to clean defaults."""
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
    api_mod._command_history.clear()
    api_mod._webhook_history.clear()


@pytest.fixture()
def clean_client(monkeypatch):
    """TestClient with no auth, no hardware, lifecycle events cleared."""
    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)

    import castor.api as api_mod
    _reset_state(monkeypatch, api_mod)

    from castor.api import app
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, api_mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWebSocketTelemetry:
    def test_telemetry_connects_and_receives(self, clean_client):
        """Client can connect and receive at least one JSON frame."""
        client, _ = clean_client
        with client.websocket_connect("/ws/telemetry") as ws:
            frame = ws.receive_json()
        assert isinstance(frame, dict)

    def test_telemetry_has_required_fields(self, clean_client):
        """Every telemetry frame must contain the required fields."""
        client, _ = clean_client
        required = {"ts", "robot", "loop_count", "avg_latency_ms", "camera", "driver",
                    "depth", "provider", "using_fallback"}
        with client.websocket_connect("/ws/telemetry") as ws:
            frame = ws.receive_json()
        missing = required - set(frame.keys())
        assert not missing, f"Missing fields: {missing}"

    def test_telemetry_ts_is_recent(self, clean_client):
        """The ts field must be a Unix timestamp within the last 10 seconds."""
        client, _ = clean_client
        with client.websocket_connect("/ws/telemetry") as ws:
            frame = ws.receive_json()
        ts = frame.get("ts", 0)
        assert abs(time.time() - ts) < 10

    def test_telemetry_stop_command(self, clean_client):
        """Sending {'cmd': 'stop'} should call driver.stop() if driver is set."""
        client, api_mod = clean_client
        mock_driver = MagicMock()
        api_mod.state.driver = mock_driver

        with client.websocket_connect("/ws/telemetry") as ws:
            # Receive one push frame to confirm connection is alive
            ws.receive_json()
            # Send stop command
            ws.send_json({"cmd": "stop"})
            # Give the server a moment to process — receive next push frame
            ws.receive_json()

        mock_driver.stop.assert_called()

    def test_telemetry_invalid_token(self, monkeypatch):
        """With API_TOKEN set, wrong token must close the connection (code 1008)."""
        monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
        monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)

        import castor.api as api_mod
        _reset_state(monkeypatch, api_mod)
        api_mod.API_TOKEN = "correct-secret-token"

        from castor.api import app
        app.router.on_startup.clear()
        app.router.on_shutdown.clear()

        with TestClient(app, raise_server_exceptions=False) as client:
            with pytest.raises(Exception):
                # Wrong token — server must reject
                with client.websocket_connect("/ws/telemetry?token=wrong") as ws:
                    ws.receive_json()  # Should raise (connection closed / rejected)

    def test_telemetry_valid_token(self, monkeypatch):
        """With API_TOKEN set, correct token allows the connection."""
        monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
        monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)

        import castor.api as api_mod
        _reset_state(monkeypatch, api_mod)
        api_mod.API_TOKEN = "my-secret"

        from castor.api import app
        app.router.on_startup.clear()
        app.router.on_shutdown.clear()

        with TestClient(app, raise_server_exceptions=False) as client:
            with client.websocket_connect("/ws/telemetry?token=my-secret") as ws:
                frame = ws.receive_json()
        assert "ts" in frame

    def test_telemetry_disconnect_no_crash(self, clean_client):
        """Client disconnecting mid-stream must not raise an unhandled exception."""
        client, _ = clean_client
        # Connect, receive one frame, then disconnect abruptly.
        # TestClient should complete without raising.
        with client.websocket_connect("/ws/telemetry") as ws:
            ws.receive_json()
        # If we reach here the server handled the disconnect gracefully

    def test_telemetry_depth_included(self, clean_client):
        """When depth data is available the 'depth' field must contain sector info."""
        import numpy as np
        client, api_mod = clean_client

        arr = np.full((30, 90), 800, dtype=np.uint16)  # 80 cm everywhere
        mock_camera = MagicMock()
        mock_camera.last_depth = arr
        mock_camera.is_available.return_value = True

        with patch("castor.main.get_shared_camera", return_value=mock_camera):
            with client.websocket_connect("/ws/telemetry") as ws:
                frame = ws.receive_json()

        depth = frame.get("depth", {})
        assert depth.get("available") is True
        assert "left_cm" in depth
        assert "center_cm" in depth
        assert "right_cm" in depth

    def test_telemetry_no_depth_available(self, clean_client):
        """When no depth camera is present, depth field must show available=false."""
        client, _ = clean_client

        with patch("castor.main.get_shared_camera", return_value=None):
            with client.websocket_connect("/ws/telemetry") as ws:
                frame = ws.receive_json()

        depth = frame.get("depth", {})
        assert depth.get("available") is False
