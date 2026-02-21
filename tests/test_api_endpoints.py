"""
Comprehensive tests for castor.api -- the FastAPI gateway endpoints.

Uses FastAPI's TestClient (starlette) for synchronous endpoint testing.
Mocks AppState internals (brain, driver, fs, channels) so tests run
without hardware, AI providers, or messaging SDKs.
"""

import base64
import collections
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from castor.providers.base import Thought

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SENTINEL = object()


def _make_mock_brain(raw_text="moving forward", action=_SENTINEL):
    """Create a mock brain whose think() returns a predictable Thought.

    Pass action=None explicitly to get a Thought with no action.
    Omitting action gives a default move action.
    """
    brain = MagicMock()
    if action is _SENTINEL:
        action = {"type": "move", "linear": 0.5}
    brain.think.return_value = Thought(raw_text, action)
    return brain


def _make_mock_driver():
    """Create a mock hardware driver with move/stop/close."""
    driver = MagicMock()
    driver.move = MagicMock()
    driver.stop = MagicMock()
    driver.close = MagicMock()
    return driver


def _make_mock_fs():
    """Create a mock CastorFS with all methods used by the API."""
    fs = MagicMock()
    fs.estop = MagicMock()
    fs.clear_estop = MagicMock(return_value=True)
    fs.read = MagicMock(return_value={"some": "data"})
    fs.write = MagicMock(return_value=True)
    fs.exists = MagicMock(return_value=True)
    fs.ls = MagicMock(return_value=["dev", "proc", "var"])
    fs.tree = MagicMock(return_value="/\n|-- dev/\n|-- proc/\n|-- var/")
    fs.proc = MagicMock()
    fs.proc.snapshot = MagicMock(return_value={"uptime_s": 42.0, "loops": 10})
    fs.memory = MagicMock()
    fs.memory.get_episodes = MagicMock(return_value=[])
    fs.memory.list_facts = MagicMock(return_value={})
    fs.memory.list_behaviors = MagicMock(return_value={})
    fs.perms = MagicMock()
    fs.perms.dump = MagicMock(return_value={"acls": {}, "capabilities": {}})
    fs.ns = MagicMock()
    fs.ns.read = MagicMock(return_value=None)
    return fs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_state_and_env(monkeypatch):
    """Reset application state and relevant env vars before every test.

    We import `state` and `app` fresh and patch the module-level API_TOKEN
    so each test starts with a clean slate.
    """
    # Remove auth-related env vars so tests start in open-access mode
    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)
    monkeypatch.delenv("OPENCASTOR_CONFIG", raising=False)

    import castor.api as api_mod

    # Reset all mutable state fields
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

    # Reset the module-level API_TOKEN
    api_mod.API_TOKEN = None

    # Clear rate-limiter history so tests don't trip each other's per-IP limits
    api_mod._command_history.clear()
    api_mod._webhook_history.clear()

    yield


@pytest.fixture()
def client():
    """Return a TestClient wired to the FastAPI app.

    We replace the real startup/shutdown lifecycle events to avoid loading
    configs, hardware, channels, or other real infrastructure.
    """
    from castor.api import app

    # Save original event handlers and replace with no-ops
    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        # Restore original handlers
        app.router.on_startup[:] = original_startup
        app.router.on_shutdown[:] = original_shutdown


@pytest.fixture()
def api_mod():
    """Return the castor.api module for direct state manipulation."""
    import castor.api as mod

    return mod


# =====================================================================
# /health -- public, no auth required
# =====================================================================
class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_health_contains_uptime(self, client):
        resp = client.get("/health")
        body = resp.json()
        assert "uptime_s" in body
        assert isinstance(body["uptime_s"], (int, float))
        assert body["uptime_s"] >= 0

    def test_health_brain_false_when_not_loaded(self, client):
        resp = client.get("/health")
        assert resp.json()["brain"] is False

    def test_health_brain_true_when_loaded(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        resp = client.get("/health")
        assert resp.json()["brain"] is True

    def test_health_driver_false_when_not_loaded(self, client):
        resp = client.get("/health")
        assert resp.json()["driver"] is False

    def test_health_driver_true_when_loaded(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        resp = client.get("/health")
        assert resp.json()["driver"] is True

    def test_health_channels_empty(self, client):
        resp = client.get("/health")
        assert resp.json()["channels"] == []

    def test_health_channels_lists_active(self, client, api_mod):
        api_mod.state.channels = {"telegram": MagicMock(), "discord": MagicMock()}
        resp = client.get("/health")
        channels = resp.json()["channels"]
        assert "telegram" in channels
        assert "discord" in channels

    def test_health_no_auth_required_when_token_set(self, client, api_mod):
        """Health endpoint must remain accessible even when API auth is on."""
        api_mod.API_TOKEN = "secret-token-123"
        resp = client.get("/health")
        assert resp.status_code == 200


# =====================================================================
# Auth enforcement
# =====================================================================
class TestAuthEnforcement:
    def test_open_access_when_no_token_configured(self, client):
        """When API_TOKEN is None, protected endpoints are accessible."""
        resp = client.get("/api/status")
        assert resp.status_code == 200

    def test_401_when_token_required_but_missing(self, client, api_mod):
        api_mod.API_TOKEN = "secret"
        resp = client.get("/api/status")
        assert resp.status_code == 401

    def test_401_when_token_wrong(self, client, api_mod):
        api_mod.API_TOKEN = "correct-token"
        resp = client.get(
            "/api/status",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_200_when_token_correct(self, client, api_mod):
        api_mod.API_TOKEN = "correct-token"
        resp = client.get(
            "/api/status",
            headers={"Authorization": "Bearer correct-token"},
        )
        assert resp.status_code == 200

    def test_401_missing_bearer_prefix(self, client, api_mod):
        api_mod.API_TOKEN = "tok"
        resp = client.get(
            "/api/status",
            headers={"Authorization": "tok"},
        )
        assert resp.status_code == 401

    def test_multiple_protected_endpoints_require_auth(self, client, api_mod):
        """All protected endpoints should return 401 when token is set."""
        api_mod.API_TOKEN = "secret"
        protected = [
            ("GET", "/api/status"),
            ("POST", "/api/command"),
            ("POST", "/api/action"),
            ("POST", "/api/stop"),
            ("POST", "/api/estop/clear"),
            ("POST", "/api/fs/read"),
            ("POST", "/api/fs/write"),
            ("GET", "/api/fs/ls"),
            ("GET", "/api/fs/tree"),
            ("GET", "/api/fs/proc"),
            ("GET", "/api/fs/memory"),
            ("POST", "/api/auth/token"),
            ("GET", "/api/auth/whoami"),
            ("GET", "/api/rcan/peers"),
            ("POST", "/rcan"),
            ("GET", "/cap/status"),
            ("POST", "/cap/teleop"),
            ("POST", "/cap/chat"),
            ("GET", "/cap/vision"),
            ("GET", "/api/roles"),
            ("GET", "/api/fs/permissions"),
            ("GET", "/api/whatsapp/status"),
        ]
        for method, path in protected:
            if method == "GET":
                resp = client.get(path)
            else:
                resp = client.post(path, json={})
            assert resp.status_code == 401, (
                f"{method} {path} returned {resp.status_code}, expected 401"
            )


# =====================================================================
# GET /api/status
# =====================================================================
class TestStatusEndpoint:
    def test_status_fields(self, client):
        resp = client.get("/api/status")
        body = resp.json()
        assert "config_loaded" in body
        assert "providers" in body
        assert "channels_available" in body
        assert "channels_active" in body
        assert "last_thought" in body
        assert "ruri" in body

    def test_status_config_not_loaded(self, client):
        resp = client.get("/api/status")
        assert resp.json()["config_loaded"] is False
        assert resp.json()["robot_name"] is None

    def test_status_config_loaded(self, client, api_mod):
        api_mod.state.config = {"metadata": {"robot_name": "TestBot"}}
        resp = client.get("/api/status")
        body = resp.json()
        assert body["config_loaded"] is True
        assert body["robot_name"] == "TestBot"

    def test_status_includes_ruri(self, client, api_mod):
        api_mod.state.ruri = "rcan://opencastor.testbot.12345678"
        resp = client.get("/api/status")
        assert resp.json()["ruri"] == "rcan://opencastor.testbot.12345678"

    def test_status_last_thought(self, client, api_mod):
        api_mod.state.last_thought = {
            "raw_text": "hello",
            "action": {"type": "stop"},
            "timestamp": 1234567890.0,
        }
        resp = client.get("/api/status")
        assert resp.json()["last_thought"]["raw_text"] == "hello"


# =====================================================================
# POST /api/command
# =====================================================================
class TestCommandEndpoint:
    def test_command_503_when_brain_not_loaded(self, client):
        resp = client.post("/api/command", json={"instruction": "go forward"})
        assert resp.status_code == 503
        assert "Brain not initialized" in resp.json()["error"]

    def test_command_success(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain("turning left", {"type": "move", "angular": -0.5})
        resp = client.post("/api/command", json={"instruction": "turn left"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["raw_text"] == "turning left"
        assert body["action"]["type"] == "move"
        assert body["action"]["angular"] == -0.5

    def test_command_updates_last_thought(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain("done", {"type": "stop"})
        client.post("/api/command", json={"instruction": "stop"})
        assert api_mod.state.last_thought is not None
        assert api_mod.state.last_thought["raw_text"] == "done"

    def test_command_with_image_base64(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        img_b64 = base64.b64encode(b"\x89PNG fake image").decode()
        resp = client.post(
            "/api/command",
            json={"instruction": "what do you see?", "image_base64": img_b64},
        )
        assert resp.status_code == 200
        # Verify brain.think was called with the decoded bytes
        call_args = api_mod.state.brain.think.call_args
        assert call_args[0][0] == base64.b64decode(img_b64)

    def test_command_executes_action_on_driver(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain("go", {"type": "move", "linear": 0.8})
        api_mod.state.driver = _make_mock_driver()
        resp = client.post("/api/command", json={"instruction": "forward"})
        assert resp.status_code == 200
        api_mod.state.driver.move.assert_called_once_with(0.8, 0.0)

    def test_command_no_driver_still_succeeds(self, client, api_mod):
        """Command should succeed even without a driver -- just no motor output."""
        api_mod.state.brain = _make_mock_brain("thinking", {"type": "move", "linear": 0.3})
        # state.driver is None
        resp = client.post("/api/command", json={"instruction": "think"})
        assert resp.status_code == 200

    def test_command_stop_action_on_driver(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain("stopping", {"type": "stop"})
        api_mod.state.driver = _make_mock_driver()
        resp = client.post("/api/command", json={"instruction": "halt"})
        assert resp.status_code == 200
        api_mod.state.driver.stop.assert_called_once()

    def test_command_requires_instruction_field(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        resp = client.post("/api/command", json={})
        assert resp.status_code == 422  # Pydantic validation error

    def test_command_none_action_from_brain(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain("confused", None)
        resp = client.post("/api/command", json={"instruction": "do nothing"})
        assert resp.status_code == 200
        assert resp.json()["action"] is None


# =====================================================================
# POST /api/action
# =====================================================================
class TestActionEndpoint:
    def test_action_503_when_no_driver(self, client):
        resp = client.post("/api/action", json={"type": "move", "linear": 0.5})
        assert resp.status_code == 503
        assert "No hardware driver active" in resp.json()["error"]

    def test_action_move(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        resp = client.post(
            "/api/action",
            json={"type": "move", "linear": 0.5, "angular": -0.2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "executed"
        assert body["action"]["type"] == "move"
        api_mod.state.driver.move.assert_called_once_with(0.5, -0.2)

    def test_action_stop(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        resp = client.post("/api/action", json={"type": "stop"})
        assert resp.status_code == 200
        api_mod.state.driver.stop.assert_called_once()

    def test_action_requires_type_field(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        resp = client.post("/api/action", json={"linear": 0.5})
        assert resp.status_code == 422

    def test_action_grip(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        resp = client.post(
            "/api/action",
            json={"type": "grip", "state": "close"},
        )
        assert resp.status_code == 200
        assert resp.json()["action"]["state"] == "close"

    def test_action_wait(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        resp = client.post(
            "/api/action",
            json={"type": "wait", "duration_ms": 500},
        )
        assert resp.status_code == 200
        assert resp.json()["action"]["duration_ms"] == 500


# =====================================================================
# POST /api/stop (emergency stop)
# =====================================================================
class TestStopEndpoint:
    def test_stop_without_driver(self, client):
        resp = client.post("/api/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_stop_calls_driver_stop(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        resp = client.post("/api/stop")
        assert resp.status_code == 200
        api_mod.state.driver.stop.assert_called_once()

    def test_stop_triggers_fs_estop(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.post("/api/stop")
        assert resp.status_code == 200
        api_mod.state.fs.estop.assert_called_once_with(principal="api")

    def test_stop_with_auth(self, client, api_mod):
        api_mod.API_TOKEN = "tok"
        api_mod.state.driver = _make_mock_driver()
        resp = client.post(
            "/api/stop",
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200
        api_mod.state.driver.stop.assert_called_once()


# =====================================================================
# POST /api/estop/clear
# =====================================================================
class TestEStopClearEndpoint:
    def test_clear_estop_no_fs(self, client):
        resp = client.post("/api/estop/clear")
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_fs"

    def test_clear_estop_success(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.clear_estop.return_value = True
        resp = client.post("/api/estop/clear")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"

    def test_clear_estop_denied(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.clear_estop.return_value = False
        resp = client.post("/api/estop/clear")
        assert resp.status_code == 403
        assert "Insufficient permissions" in resp.json()["error"]


# =====================================================================
# Virtual Filesystem endpoints
# =====================================================================
class TestFSReadEndpoint:
    def test_fs_read_no_fs(self, client):
        resp = client.post("/api/fs/read", json={"path": "/proc/uptime"})
        assert resp.status_code == 503

    def test_fs_read_success(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.read.return_value = 42.5
        resp = client.post("/api/fs/read", json={"path": "/proc/uptime"})
        assert resp.status_code == 200
        assert resp.json()["path"] == "/proc/uptime"
        assert resp.json()["data"] == 42.5

    def test_fs_read_not_found(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.read.return_value = None
        api_mod.state.fs.exists.return_value = False
        resp = client.post("/api/fs/read", json={"path": "/no/such/path"})
        assert resp.status_code == 404

    def test_fs_read_returns_none_but_exists(self, client, api_mod):
        """A path can exist and have None data (e.g., /dev/motor before first write)."""
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.read.return_value = None
        api_mod.state.fs.exists.return_value = True
        resp = client.post("/api/fs/read", json={"path": "/dev/motor"})
        assert resp.status_code == 200
        assert resp.json()["data"] is None


class TestFSWriteEndpoint:
    def test_fs_write_no_fs(self, client):
        resp = client.post("/api/fs/write", json={"path": "/tmp/test", "data": "hello"})
        assert resp.status_code == 503

    def test_fs_write_success(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.post("/api/fs/write", json={"path": "/tmp/test", "data": "hello"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "written"
        api_mod.state.fs.write.assert_called_once_with("/tmp/test", "hello", principal="api")

    def test_fs_write_denied(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.write.return_value = False
        resp = client.post("/api/fs/write", json={"path": "/etc/readonly", "data": "x"})
        assert resp.status_code == 403

    def test_fs_write_complex_data(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        data = {"sensors": [1, 2, 3], "active": True}
        resp = client.post("/api/fs/write", json={"path": "/tmp/sensors", "data": data})
        assert resp.status_code == 200


class TestFSLsEndpoint:
    def test_fs_ls_no_fs(self, client):
        resp = client.get("/api/fs/ls")
        assert resp.status_code == 503

    def test_fs_ls_root(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/ls")
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == "/"
        assert "dev" in body["children"]

    def test_fs_ls_with_path_param(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.ls.return_value = ["motor", "camera", "speaker"]
        resp = client.get("/api/fs/ls?path=/dev")
        assert resp.status_code == 200
        assert resp.json()["path"] == "/dev"
        assert "motor" in resp.json()["children"]

    def test_fs_ls_not_a_directory(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.ls.return_value = None
        resp = client.get("/api/fs/ls?path=/dev/motor")
        assert resp.status_code == 404


class TestFSTreeEndpoint:
    def test_fs_tree_no_fs(self, client):
        resp = client.get("/api/fs/tree")
        assert resp.status_code == 503

    def test_fs_tree_success(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/tree")
        assert resp.status_code == 200
        assert "tree" in resp.json()

    def test_fs_tree_with_depth(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/tree?depth=1")
        assert resp.status_code == 200
        api_mod.state.fs.tree.assert_called_with("/", depth=1)


class TestFSProcEndpoint:
    def test_fs_proc_no_fs(self, client):
        resp = client.get("/api/fs/proc")
        assert resp.status_code == 503

    def test_fs_proc_success(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/proc")
        assert resp.status_code == 200
        assert resp.json()["uptime_s"] == 42.0


class TestFSMemoryEndpoint:
    def test_fs_memory_no_fs(self, client):
        resp = client.get("/api/fs/memory")
        assert resp.status_code == 503

    def test_fs_memory_all_tiers(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/memory")
        assert resp.status_code == 200
        body = resp.json()
        assert "episodic" in body
        assert "semantic" in body
        assert "procedural" in body

    def test_fs_memory_episodic_only(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/memory?tier=episodic")
        assert resp.status_code == 200
        body = resp.json()
        assert "episodic" in body
        assert "semantic" not in body

    def test_fs_memory_semantic_only(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/memory?tier=semantic")
        assert resp.status_code == 200
        body = resp.json()
        assert "semantic" in body
        assert "episodic" not in body

    def test_fs_memory_procedural_only(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/memory?tier=procedural")
        assert resp.status_code == 200
        body = resp.json()
        assert "procedural" in body
        assert "episodic" not in body

    def test_fs_memory_limit(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/memory?tier=episodic&limit=5")
        assert resp.status_code == 200
        api_mod.state.fs.memory.get_episodes.assert_called_with(limit=5)


class TestFSPermissionsEndpoint:
    def test_fs_permissions_no_fs(self, client):
        resp = client.get("/api/fs/permissions")
        assert resp.status_code == 503

    def test_fs_permissions_success(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/permissions")
        assert resp.status_code == 200
        body = resp.json()
        assert "acls" in body
        assert "capabilities" in body


# =====================================================================
# Auth token endpoints
# =====================================================================
class TestAuthTokenEndpoint:
    def test_issue_token_no_jwt_secret(self, client):
        resp = client.post(
            "/api/auth/token",
            json={"subject": "user1", "role": "GUEST"},
        )
        assert resp.status_code == 501
        assert "JWT not configured" in resp.json()["error"]

    def test_issue_token_invalid_role(self, client, monkeypatch):
        monkeypatch.setenv("OPENCASTOR_JWT_SECRET", "testsecret")
        # This will fail if the RCAN RBAC module raises KeyError for invalid role
        resp = client.post(
            "/api/auth/token",
            json={"subject": "user1", "role": "NONEXISTENT_ROLE"},
        )
        # Should be 400 (invalid role) or 500 (import error) but not 200
        assert resp.status_code in (400, 500)


class TestWhoamiEndpoint:
    def test_whoami_anonymous(self, client):
        resp = client.get("/api/auth/whoami")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "anonymous"
        assert body["role"] == "GUEST"
        assert body["auth_method"] == "none"

    def test_whoami_with_bearer_token(self, client, api_mod):
        api_mod.API_TOKEN = "my-token"
        resp = client.get(
            "/api/auth/whoami",
            headers={"Authorization": "Bearer my-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "api"
        assert body["role"] == "LEASEE"
        assert body["auth_method"] == "bearer_token"


# =====================================================================
# GET /api/rcan/peers
# =====================================================================
class TestRCANPeersEndpoint:
    def test_peers_no_mdns(self, client):
        resp = client.get("/api/rcan/peers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["peers"] == []
        assert "note" in body

    def test_peers_with_mdns(self, client, api_mod):
        browser = MagicMock()
        browser.peers = {
            "bot1": {"ruri": "rcan://opencastor.bot1.11111111", "port": 8000},
            "bot2": {"ruri": "rcan://opencastor.bot2.22222222", "port": 8001},
        }
        api_mod.state.mdns_browser = browser
        resp = client.get("/api/rcan/peers")
        assert resp.status_code == 200
        assert len(resp.json()["peers"]) == 2


# =====================================================================
# POST /rcan (RCAN message endpoint)
# =====================================================================
class TestRCANMessageEndpoint:
    def test_rcan_message_no_router(self, client):
        resp = client.post("/rcan", json={"type": "command", "payload": {}})
        assert resp.status_code == 501
        assert "RCAN router not initialized" in resp.json()["error"]

    def test_rcan_message_invalid_body(self, client, api_mod):
        router = MagicMock()
        api_mod.state.rcan_router = router
        # Provide a body that will trigger an exception when RCANMessage.from_dict is called
        resp = client.post("/rcan", json={"invalid": "structure"})
        assert resp.status_code == 400


# =====================================================================
# Capability endpoints
# =====================================================================
class TestCapStatusEndpoint:
    def test_cap_status_basic(self, client, api_mod):
        resp = client.get("/cap/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "ruri" in body
        assert "uptime_s" in body
        assert "brain" in body
        assert "driver" in body
        assert "channels_active" in body
        assert "capabilities" in body

    def test_cap_status_with_registry(self, client, api_mod):
        registry = MagicMock()
        registry.names = ["status", "chat", "teleop"]
        api_mod.state.capability_registry = registry
        resp = client.get("/cap/status")
        assert resp.json()["capabilities"] == ["status", "chat", "teleop"]

    def test_cap_status_includes_proc_when_fs_available(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/cap/status")
        assert "proc" in resp.json()


class TestCapTeleopEndpoint:
    def test_teleop_no_driver(self, client):
        resp = client.post("/cap/teleop", json={"type": "move", "linear": 0.5})
        assert resp.status_code == 503

    def test_teleop_success(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        resp = client.post(
            "/cap/teleop",
            json={"type": "move", "linear": 0.7, "angular": -0.1},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "executed"
        api_mod.state.driver.move.assert_called_once_with(0.7, -0.1)


class TestCapChatEndpoint:
    def test_chat_no_brain(self, client):
        resp = client.post("/cap/chat", json={"instruction": "hello"})
        assert resp.status_code == 503

    def test_chat_success(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain("hi there", {"type": "wait", "duration_ms": 100})
        resp = client.post("/cap/chat", json={"instruction": "hello"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["raw_text"] == "hi there"
        assert body["action"]["type"] == "wait"

    def test_chat_with_image(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain("i see stuff", None)
        img = base64.b64encode(b"fakejpeg").decode()
        resp = client.post(
            "/cap/chat",
            json={"instruction": "describe", "image_base64": img},
        )
        assert resp.status_code == 200
        # Verify decoded image was passed to brain
        call_args = api_mod.state.brain.think.call_args
        assert call_args[0][0] == base64.b64decode(img)


class TestCapVisionEndpoint:
    def test_vision_no_fs(self, client):
        resp = client.get("/cap/vision")
        assert resp.status_code == 200
        assert resp.json()["camera"]["status"] == "offline"

    def test_vision_with_fs_no_frame(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.ns.read.return_value = None
        resp = client.get("/cap/vision")
        assert resp.status_code == 200
        assert resp.json()["camera"]["status"] == "no_frame"

    def test_vision_with_fs_has_frame(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        api_mod.state.fs.ns.read.return_value = {"resolution": "640x480", "ts": 1234567890}
        resp = client.get("/cap/vision")
        assert resp.status_code == 200
        assert resp.json()["camera"]["resolution"] == "640x480"


# =====================================================================
# GET /api/roles
# =====================================================================
class TestRolesEndpoint:
    def test_roles_returns_200(self, client):
        resp = client.get("/api/roles")
        # May succeed if castor.rcan.rbac is available, or 500 if not
        assert resp.status_code in (200, 500)

    def test_roles_structure_when_available(self, client):
        resp = client.get("/api/roles")
        if resp.status_code == 200:
            body = resp.json()
            assert "roles" in body
            assert "principals" in body


# =====================================================================
# Webhook endpoints
# =====================================================================
class TestWhatsAppWebhook:
    def test_whatsapp_webhook_no_channel(self, client):
        resp = client.post("/webhooks/whatsapp", data={"Body": "hello"})
        assert resp.status_code == 503

    def test_whatsapp_webhook_with_channel(self, client, api_mod):
        channel = MagicMock()
        channel.handle_webhook = AsyncMock(return_value="reply text")
        api_mod.state.channels["whatsapp_twilio"] = channel
        resp = client.post(
            "/webhooks/whatsapp",
            data={"Body": "hello", "From": "whatsapp:+1234567890"},
        )
        assert resp.status_code == 200
        assert resp.json()["reply"] == "reply text"


class TestSlackWebhook:
    def test_slack_url_verification(self, client):
        resp = client.post(
            "/webhooks/slack",
            json={"type": "url_verification", "challenge": "abc123xyz"},
        )
        assert resp.status_code == 200
        assert resp.json()["challenge"] == "abc123xyz"

    def test_slack_event(self, client):
        resp = client.post(
            "/webhooks/slack",
            json={"type": "event_callback", "event": {"text": "hello"}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestWhatsAppStatus:
    def test_whatsapp_status_not_configured(self, client):
        resp = client.get("/api/whatsapp/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_configured"

    def test_whatsapp_status_connected(self, client, api_mod):
        channel = MagicMock()
        channel.connected = True
        api_mod.state.channels["whatsapp"] = channel
        resp = client.get("/api/whatsapp/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "connected"

    def test_whatsapp_status_disconnected(self, client, api_mod):
        channel = MagicMock()
        channel.connected = False
        api_mod.state.channels["whatsapp"] = channel
        resp = client.get("/api/whatsapp/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "disconnected"


# =====================================================================
# CORS
# =====================================================================
class TestCORS:
    def test_cors_preflight(self, client):
        resp = client.options(
            "/api/status",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers

    def test_cors_header_on_response(self, client):
        resp = client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers


# =====================================================================
# Edge cases and integration scenarios
# =====================================================================
class TestEdgeCases:
    def test_invalid_json_body(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        resp = client.post(
            "/api/command",
            content=b"this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_empty_instruction(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        resp = client.post("/api/command", json={"instruction": ""})
        assert resp.status_code == 200  # Empty string is still valid

    def test_very_long_instruction(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        resp = client.post(
            "/api/command",
            json={"instruction": "go " * 10000},
        )
        assert resp.status_code == 200

    def test_concurrent_state_changes(self, client, api_mod):
        """Ensure health endpoint reflects latest state after mutation."""
        assert client.get("/health").json()["brain"] is False
        api_mod.state.brain = _make_mock_brain()
        assert client.get("/health").json()["brain"] is True
        api_mod.state.brain = None
        assert client.get("/health").json()["brain"] is False

    def test_action_exclude_none_fields(self, client, api_mod):
        """ActionRequest should exclude None fields in response."""
        api_mod.state.driver = _make_mock_driver()
        resp = client.post("/api/action", json={"type": "stop"})
        action = resp.json()["action"]
        # None fields should be absent from the response
        assert "linear" not in action
        assert "angular" not in action
        assert "state" not in action
        assert "duration_ms" not in action

    def test_health_uptime_increases(self, client, api_mod):
        """Uptime should be a positive number that reflects time since boot."""
        api_mod.state.boot_time = time.time() - 100
        resp = client.get("/health")
        assert resp.json()["uptime_s"] >= 99

    def test_fs_write_null_data(self, client, api_mod):
        """Writing None/null data should be valid."""
        api_mod.state.fs = _make_mock_fs()
        resp = client.post("/api/fs/write", json={"path": "/tmp/null"})
        assert resp.status_code == 200

    def test_fs_tree_default_params(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.get("/api/fs/tree")
        assert resp.status_code == 200
        api_mod.state.fs.tree.assert_called_with("/", depth=3)


# =====================================================================
# Request model validation
# =====================================================================
class TestRequestValidation:
    def test_command_extra_fields_ignored(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        resp = client.post(
            "/api/command",
            json={"instruction": "go", "extra_field": "should be ignored"},
        )
        assert resp.status_code == 200

    def test_action_type_required(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        resp = client.post("/api/action", json={})
        assert resp.status_code == 422

    def test_fs_read_path_required(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.post("/api/fs/read", json={})
        assert resp.status_code == 422

    def test_fs_write_path_required(self, client, api_mod):
        api_mod.state.fs = _make_mock_fs()
        resp = client.post("/api/fs/write", json={"data": "hello"})
        assert resp.status_code == 422

    def test_token_request_subject_required(self, client):
        resp = client.post("/api/auth/token", json={"role": "GUEST"})
        assert resp.status_code == 422

    def test_token_request_defaults(self, client):
        """Verify default values in TokenRequest model."""
        resp = client.post("/api/auth/token", json={"subject": "test"})
        # Will fail with 501 (JWT not configured) but should pass validation
        assert resp.status_code == 501  # Not 422


# =====================================================================
# Response format consistency
# =====================================================================
class TestResponseFormat:
    def test_health_response_keys(self, client):
        body = client.get("/health").json()
        expected_keys = {"status", "uptime_s", "brain", "driver", "channels"}
        assert expected_keys == set(body.keys())

    def test_command_response_keys(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        body = client.post("/api/command", json={"instruction": "go"}).json()
        assert "raw_text" in body
        assert "action" in body

    def test_stop_response_keys(self, client):
        body = client.post("/api/stop").json()
        assert body == {"status": "stopped"}

    def test_action_response_keys(self, client, api_mod):
        api_mod.state.driver = _make_mock_driver()
        body = client.post("/api/action", json={"type": "stop"}).json()
        assert "status" in body
        assert "action" in body
        assert body["status"] == "executed"


# ---------------------------------------------------------------------------
# POST /api/command/stream  (#68)
# ---------------------------------------------------------------------------

class TestStreamCommand:
    def test_no_brain_returns_503(self, client):
        resp = client.post("/api/command/stream", json={"instruction": "go"})
        assert resp.status_code == 503

    def test_streams_ndjson_with_think_stream(self, client, api_mod):
        brain = MagicMock()
        brain.think_stream.return_value = iter(["mov", "ing"])
        brain._clean_json = MagicMock(return_value={"type": "move"})
        api_mod.state.brain = brain

        resp = client.post("/api/command/stream", json={"instruction": "go"})
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers["content-type"]

        import json as _json
        lines = [l for l in resp.text.splitlines() if l.strip()]
        parsed = [_json.loads(l) for l in lines]
        # All lines except last have done=False
        assert all(not p["done"] for p in parsed[:-1])
        # Last line has done=True
        assert parsed[-1]["done"] is True
        assert "action" in parsed[-1]

    def test_falls_back_to_think_when_no_think_stream(self, client, api_mod):
        brain = MagicMock(spec=["think", "_clean_json"])
        brain.think.return_value = MagicMock(raw_text="ok", action={"type": "stop"})
        brain._clean_json = MagicMock(return_value={"type": "stop"})
        api_mod.state.brain = brain

        resp = client.post("/api/command/stream", json={"instruction": "halt"})
        assert resp.status_code == 200

        import json as _json
        lines = [l for l in resp.text.splitlines() if l.strip()]
        parsed = [_json.loads(l) for l in lines]
        assert len(parsed) >= 2
        assert parsed[-1]["done"] is True


# ---------------------------------------------------------------------------
# GET /api/driver/health  (#69)
# ---------------------------------------------------------------------------

class TestDriverHealth:
    def test_no_driver_returns_503(self, client):
        resp = client.get("/api/driver/health")
        assert resp.status_code == 503

    def test_returns_health_dict_with_driver_type(self, client, api_mod):
        driver = _make_mock_driver()
        driver.health_check = MagicMock(
            return_value={"ok": True, "mode": "mock", "error": None}
        )
        api_mod.state.driver = driver

        body = client.get("/api/driver/health").json()
        assert body["ok"] is True
        assert body["mode"] == "mock"
        assert "driver_type" in body

    def test_unhealthy_driver_still_returns_200(self, client, api_mod):
        driver = _make_mock_driver()
        driver.health_check = MagicMock(
            return_value={"ok": False, "mode": "hardware", "error": "ping failed"}
        )
        api_mod.state.driver = driver

        body = client.get("/api/driver/health").json()
        assert body["ok"] is False
        assert body["error"] == "ping failed"


# ---------------------------------------------------------------------------
# GET /api/learner/stats  (#70)
# ---------------------------------------------------------------------------

class TestLearnerStats:
    def test_no_learner_returns_available_false(self, client):
        body = client.get("/api/learner/stats").json()
        assert body == {"available": False}

    def test_with_learner_returns_stats(self, client, api_mod):
        from castor.learner.sisyphus import SisyphusStats

        learner = MagicMock()
        stats = SisyphusStats(
            episodes_analyzed=5,
            improvements_applied=3,
            improvements_rejected=1,
            total_duration_ms=500.0,
        )
        learner.stats = stats
        api_mod.state.learner = learner

        body = client.get("/api/learner/stats").json()
        assert body["available"] is True
        assert body["episodes_analyzed"] == 5
        assert body["improvements_applied"] == 3
        assert body["improvements_rejected"] == 1
        assert body["avg_duration_ms"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# GET /api/learner/episodes  (#70)
# ---------------------------------------------------------------------------

class TestLearnerEpisodes:
    def test_returns_empty_list_when_no_episodes(self, client, monkeypatch):
        from castor.learner import episode_store as es_mod

        mock_store = MagicMock()
        mock_store.list_recent.return_value = []
        monkeypatch.setattr(es_mod, "EpisodeStore", lambda: mock_store)

        body = client.get("/api/learner/episodes").json()
        assert body["count"] == 0
        assert body["episodes"] == []

    def test_returns_episodes_list(self, client, monkeypatch):
        from castor.learner import episode_store as es_mod
        from castor.learner.episode import Episode

        ep = Episode(goal="navigate", success=True, duration_s=1.5)
        mock_store = MagicMock()
        mock_store.list_recent.return_value = [ep]
        monkeypatch.setattr(es_mod, "EpisodeStore", lambda: mock_store)

        body = client.get("/api/learner/episodes").json()
        assert body["count"] == 1
        assert body["episodes"][0]["goal"] == "navigate"
        assert body["episodes"][0]["success"] is True

    def test_limit_capped_at_100(self, client, monkeypatch):
        from castor.learner import episode_store as es_mod

        mock_store = MagicMock()
        mock_store.list_recent.return_value = []
        monkeypatch.setattr(es_mod, "EpisodeStore", lambda: mock_store)

        client.get("/api/learner/episodes?limit=999")
        mock_store.list_recent.assert_called_once_with(n=100)


# ---------------------------------------------------------------------------
# POST /api/learner/episode  (#74)
# ---------------------------------------------------------------------------

class TestSubmitEpisode:
    def test_missing_goal_returns_422(self, client):
        resp = client.post("/api/learner/episode", json={"success": True})
        assert resp.status_code == 422

    def test_saves_episode_and_returns_id(self, client, monkeypatch):
        from castor.learner import episode_store as es_mod

        mock_store = MagicMock()
        monkeypatch.setattr(es_mod, "EpisodeStore", lambda: mock_store)

        resp = client.post(
            "/api/learner/episode",
            json={"goal": "dock", "success": False, "duration_s": 2.0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "episode_id" in body
        assert body["saved"] is True
        mock_store.save.assert_called_once()

    def test_run_improvement_flag_calls_learner(self, client, api_mod, monkeypatch):
        from castor.learner import episode_store as es_mod
        from castor.learner.sisyphus import ImprovementResult

        mock_store = MagicMock()
        monkeypatch.setattr(es_mod, "EpisodeStore", lambda: mock_store)

        learner = MagicMock()
        result = ImprovementResult(episode_id="test-id", applied=False)
        learner.run_episode.return_value = result
        api_mod.state.learner = learner

        resp = client.post(
            "/api/learner/episode?run_improvement=true",
            json={"goal": "spin", "success": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "improvement" in body
        learner.run_episode.assert_called_once()


# ---------------------------------------------------------------------------
# GET /api/command/history  (#75)
# ---------------------------------------------------------------------------

class TestCommandHistory:
    def test_empty_initially(self, client):
        body = client.get("/api/command/history").json()
        assert body["history"] == []
        assert body["count"] == 0

    def test_populated_after_command(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        client.post("/api/command", json={"instruction": "go forward"})

        body = client.get("/api/command/history").json()
        assert body["count"] >= 1
        entry = body["history"][0]
        assert entry["instruction"] == "go forward"
        assert "raw_text" in entry
        assert "action" in entry
        assert "timestamp" in entry

    def test_limit_param(self, client, api_mod):
        api_mod.state.brain = _make_mock_brain()
        for _ in range(5):
            client.post("/api/command", json={"instruction": "test"})

        body = client.get("/api/command/history?limit=3").json()
        assert len(body["history"]) <= 3

    def test_limit_capped_at_50(self, client, api_mod):
        body = client.get("/api/command/history?limit=999").json()
        # Should not raise; count ≤ 50
        assert body["count"] <= 50
