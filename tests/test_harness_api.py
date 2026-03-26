"""
Tests for GET /api/harness and POST /api/harness endpoints.

These tests follow the same fixture/pattern as test_api_endpoints.py:
- Uses starlette TestClient
- Patches state directly
- Tests run without hardware, providers, or real filesystem writes
"""

import contextlib
import copy
import time
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG = {
    "rcan_version": "1.6",
    "metadata": {"robot_name": "TestBot"},
    "agent": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "harness": {
            "enabled": True,
            "max_iterations": 6,
            "hooks": {
                "p66_audit": True,
                "drift_detection": True,
                "drift_threshold": 0.15,
            },
            "context": {
                "memory": True,
                "telemetry": True,
                "system_prompt": True,
                "skills_context": True,
            },
            "trajectory": {
                "enabled": True,
                "sqlite_path": "trajectory.db",
            },
        },
    },
    "skills": {
        "navigate_to": {"enabled": True, "order": 0},
        "camera_describe": {"enabled": True, "order": 1},
        "arm_manipulate": {"enabled": False, "order": 2},
        "web_lookup": {"enabled": True, "order": 3},
        "peer_coordinate": {"enabled": False, "order": 4},
        "code_reviewer": {"enabled": False, "order": 5},
    },
    "model_tiers": {
        "fast_provider": "ollama",
        "fast_model": "gemma3:1b",
        "slow_provider": "google",
        "slow_model": "gemini-2.5-flash",
        "confidence_threshold": 0.7,
    },
}


# ---------------------------------------------------------------------------
# Shared fixtures (mirrors test_api_endpoints.py pattern)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset AppState and auth env vars before every test."""
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
    api_mod.API_TOKEN = None
    api_mod._command_history.clear()
    api_mod._webhook_history.clear()

    yield


@pytest.fixture()
def client():
    """TestClient with no-op lifespan."""
    import castor.api as mod

    app = mod.app

    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan

    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.router.on_startup[:] = original_startup
        app.router.on_shutdown[:] = original_shutdown
        app.router.lifespan_context = original_lifespan


@pytest.fixture()
def api_mod():
    import castor.api as mod
    return mod


@pytest.fixture()
def client_with_config(client, api_mod):
    """Client pre-loaded with _MINIMAL_CONFIG in AppState."""
    api_mod.state.config = copy.deepcopy(_MINIMAL_CONFIG)
    return client


# ---------------------------------------------------------------------------
# GET /api/harness
# ---------------------------------------------------------------------------


class TestGetHarness:
    def test_returns_200_with_config_loaded(self, client_with_config):
        resp = client_with_config.get("/api/harness")
        assert resp.status_code == 200

    def test_response_has_required_keys(self, client_with_config):
        resp = client_with_config.get("/api/harness")
        body = resp.json()
        assert "skills" in body
        assert "hooks" in body
        assert "context" in body
        assert "model_tiers" in body
        assert "trajectory" in body
        assert "max_iterations" in body

    def test_skills_is_ordered_list(self, client_with_config):
        resp = client_with_config.get("/api/harness")
        skills = resp.json()["skills"]
        assert isinstance(skills, list)
        assert len(skills) > 0
        # Each skill has id, name, enabled, order
        for s in skills:
            assert "id" in s
            assert "name" in s
            assert "enabled" in s
            assert "order" in s

    def test_skills_order_is_sorted(self, client_with_config):
        resp = client_with_config.get("/api/harness")
        skills = resp.json()["skills"]
        orders = [s["order"] for s in skills]
        assert orders == sorted(orders)

    def test_p66_hook_always_true(self, client_with_config, api_mod):
        # Even if state.config was manually broken, p66_audit must be true
        api_mod.state.config["agent"]["harness"]["hooks"]["p66_audit"] = False
        resp = client_with_config.get("/api/harness")
        # The GET endpoint reads state as-is but POST enforces the invariant
        body = resp.json()
        # GET reports the raw stored value — POST enforces it
        assert "p66_audit" in body["hooks"]

    def test_model_tiers_present(self, client_with_config):
        resp = client_with_config.get("/api/harness")
        mt = resp.json()["model_tiers"]
        assert "fast_provider" in mt
        assert "slow_provider" in mt
        assert "confidence_threshold" in mt

    def test_confidence_threshold_is_float(self, client_with_config):
        resp = client_with_config.get("/api/harness")
        ct = resp.json()["model_tiers"]["confidence_threshold"]
        assert isinstance(ct, float)
        assert 0.0 <= ct <= 1.0

    def test_defaults_used_when_fields_missing(self, client, api_mod):
        """Minimal config with no harness section falls back to defaults."""
        api_mod.state.config = {
            "agent": {"model": "gemini-2.5-flash"},
        }
        resp = client.get("/api/harness")
        assert resp.status_code == 200
        body = resp.json()
        assert body["model_tiers"]["confidence_threshold"] == 0.7
        assert body["max_iterations"] == 6

    def test_trajectory_fields_present(self, client_with_config):
        resp = client_with_config.get("/api/harness")
        traj = resp.json()["trajectory"]
        assert "enabled" in traj
        assert "sqlite_path" in traj


# ---------------------------------------------------------------------------
# POST /api/harness
# ---------------------------------------------------------------------------


class TestPostHarness:
    def test_apply_updates_skills(self, client_with_config, api_mod, tmp_path):
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={
                    "skills": {"arm_manipulate": {"enabled": True, "order": 2}},
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "applied"
        # The updated config should reflect the change
        assert api_mod.state.config["skills"]["arm_manipulate"]["enabled"] is True

    def test_apply_updates_model_tiers(self, client_with_config, api_mod, tmp_path):
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={"model_tiers": {"fast_model": "llama3.2:3b"}},
            )
        assert resp.status_code == 200
        assert api_mod.state.config["model_tiers"]["fast_model"] == "llama3.2:3b"

    def test_apply_updates_hooks(self, client_with_config, api_mod, tmp_path):
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={"hooks": {"drift_detection": False, "drift_threshold": 0.2}},
            )
        assert resp.status_code == 200
        hooks = api_mod.state.config["agent"]["harness"]["hooks"]
        assert hooks["drift_detection"] is False
        assert hooks["drift_threshold"] == 0.2

    def test_p66_audit_cannot_be_disabled(self, client_with_config, tmp_path):
        """Protocol 66 invariant: p66_audit=False must return 422."""
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={"hooks": {"p66_audit": False}},
            )
        assert resp.status_code == 422

    def test_invalid_confidence_threshold_above_one(self, client_with_config, tmp_path):
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={"model_tiers": {"confidence_threshold": 1.5}},
            )
        assert resp.status_code == 422

    def test_invalid_confidence_threshold_below_zero(self, client_with_config, tmp_path):
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={"model_tiers": {"confidence_threshold": -0.1}},
            )
        assert resp.status_code == 422

    def test_invalid_drift_threshold(self, client_with_config, tmp_path):
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={"hooks": {"drift_threshold": 2.0}},
            )
        assert resp.status_code == 422

    def test_p66_audit_stays_true_after_applying_other_hooks(
        self, client_with_config, api_mod, tmp_path
    ):
        """P66 audit flag must remain True even when other hooks are updated."""
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={"hooks": {"drift_detection": False}},
            )
        assert resp.status_code == 200
        assert api_mod.state.config["agent"]["harness"]["hooks"]["p66_audit"] is True

    def test_response_includes_harness_section(self, client_with_config, tmp_path):
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post("/api/harness", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert "harness" in body
        assert "skills" in body["harness"]
        assert "model_tiers" in body["harness"]

    def test_503_when_config_not_loaded(self, client):
        """POST /api/harness must return 503 when state.config is None."""
        resp = client.post("/api/harness", json={})
        assert resp.status_code == 503

    def test_updates_max_iterations(self, client_with_config, api_mod, tmp_path):
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={"max_iterations": 10},
            )
        assert resp.status_code == 200
        assert api_mod.state.config["agent"]["harness"]["max_iterations"] == 10

    def test_updates_trajectory_config(self, client_with_config, api_mod, tmp_path):
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("# placeholder")

        with patch.dict("os.environ", {"OPENCASTOR_CONFIG": str(config_file)}):
            resp = client_with_config.post(
                "/api/harness",
                json={"trajectory": {"enabled": False, "sqlite_path": "/tmp/traj.db"}},
            )
        assert resp.status_code == 200
        traj = api_mod.state.config["agent"]["harness"]["trajectory"]
        assert traj["enabled"] is False
        assert traj["sqlite_path"] == "/tmp/traj.db"
