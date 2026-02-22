"""
Tests for POST /api/memory/replay/{episode_id}  (issue #105).

Covers:
  - 200 success with replayed=True when episode exists and has an action
  - 404 when episode ID does not exist
  - 422 when episode exists but has no action
  - 503 when driver is not initialised
"""

import collections
import time
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures (mirror the style used in test_api_endpoints.py)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset API state and env vars before every test."""
    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)
    monkeypatch.delenv("OPENCASTOR_CONFIG", raising=False)

    import castor.api as api_mod

    api_mod.state.config = None
    api_mod.state.brain = None
    api_mod.state.driver = None
    api_mod.state.channels = {}
    api_mod.state.last_thought = None
    api_mod.state.boot_time = time.time()
    api_mod.state.fs = None
    api_mod.state.ruri = None
    api_mod.state.mdns_broadcaster = None
    api_mod.state.mdns_browser = None
    api_mod.state.rcan_router = None
    api_mod.state.capability_registry = None
    api_mod.state.offline_fallback = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.state.learner = None
    api_mod.API_TOKEN = None
    api_mod._command_history.clear()
    api_mod._webhook_history.clear()

    yield


@pytest.fixture()
def client():
    """TestClient with lifecycle hooks disabled."""
    from castor.api import app

    orig_startup = app.router.on_startup[:]
    orig_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.router.on_startup[:] = orig_startup
        app.router.on_shutdown[:] = orig_shutdown


@pytest.fixture()
def mock_driver():
    driver = MagicMock()
    driver.move = MagicMock()
    driver.stop = MagicMock()
    driver.close = MagicMock()
    return driver


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_VALID_EPISODE = {
    "id": "test-episode-uuid-1234",
    "ts": 1700000000.0,
    "instruction": "move forward",
    "raw_thought": '{"type":"move","linear":0.5}',
    "action": {"type": "move", "linear": 0.5},
    "latency_ms": 120.0,
    "image_hash": "abc123",
    "outcome": "ok",
    "source": "loop",
}

_EPISODE_NO_ACTION = {
    "id": "test-episode-no-action",
    "ts": 1700000001.0,
    "instruction": "think",
    "raw_thought": "some text",
    "action": None,
    "latency_ms": 50.0,
    "image_hash": "",
    "outcome": "ok",
    "source": "api",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReplayEpisodeSuccess:
    def test_replay_returns_200_and_replayed_true(self, client, mock_driver):
        """When episode exists with an action and driver is ready, returns 200."""
        import castor.api as api_mod

        api_mod.state.driver = mock_driver

        with patch("castor.memory.EpisodeMemory.get_episode", return_value=_VALID_EPISODE):
            resp = client.post(f"/api/memory/replay/{_VALID_EPISODE['id']}")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["replayed"] is True
        assert body["episode_id"] == _VALID_EPISODE["id"]
        assert body["action"] == _VALID_EPISODE["action"]

    def test_replay_calls_driver_move(self, client, mock_driver):
        """Replaying a move episode calls driver.move()."""
        import castor.api as api_mod

        api_mod.state.driver = mock_driver

        with patch("castor.memory.EpisodeMemory.get_episode", return_value=_VALID_EPISODE):
            client.post(f"/api/memory/replay/{_VALID_EPISODE['id']}")

        mock_driver.move.assert_called_once_with(0.5, 0.0)


class TestReplayEpisodeNotFound:
    def test_missing_episode_returns_404(self, client, mock_driver):
        """When get_episode() returns None the endpoint must return 404."""
        import castor.api as api_mod

        api_mod.state.driver = mock_driver

        with patch("castor.memory.EpisodeMemory.get_episode", return_value=None):
            resp = client.post("/api/memory/replay/nonexistent-uuid")

        assert resp.status_code == 404
        assert "not found" in resp.json()["error"].lower()


class TestReplayEpisodeNoAction:
    def test_episode_without_action_returns_422(self, client, mock_driver):
        """When an episode has action=None the endpoint must return 422."""
        import castor.api as api_mod

        api_mod.state.driver = mock_driver

        with patch("castor.memory.EpisodeMemory.get_episode", return_value=_EPISODE_NO_ACTION):
            resp = client.post(f"/api/memory/replay/{_EPISODE_NO_ACTION['id']}")

        assert resp.status_code == 422
        assert "no action" in resp.json()["error"].lower()


class TestReplayEpisodeNoDriver:
    def test_no_driver_returns_503(self, client):
        """When state.driver is None the endpoint must return 503."""
        import castor.api as api_mod

        api_mod.state.driver = None  # explicit — no driver

        with patch("castor.memory.EpisodeMemory.get_episode", return_value=_VALID_EPISODE):
            resp = client.post(f"/api/memory/replay/{_VALID_EPISODE['id']}")

        assert resp.status_code == 503
        assert "driver" in resp.json()["error"].lower()
