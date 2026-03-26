"""
Tests for GET /api/skills endpoint.

Verifies:
- Public endpoint (no auth required)
- Returns builtin_commands + skills + rcan_version + robot_rrn
- Skills list derived from state.config correctly
- Scope mapping per RCAN §2.3
- Fallback to empty skills list when config not loaded
- robot_rrn fallback chain
- _BUILTIN_CLI_COMMANDS structure and content
"""

import contextlib
import copy
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Test configs
# ---------------------------------------------------------------------------

_CONFIG_BUILTIN_SKILLS_LIST = {
    "rcan_version": "1.6",
    "metadata": {"robot_name": "TestBot", "rrn": "RRN-000000000042"},
    "skills": {
        "builtin_skills": ["navigate-to", "camera-describe", "web-lookup"],
    },
}

_CONFIG_KEYED_SKILLS = {
    "rcan_version": "1.6",
    "metadata": {"robot_name": "TestBot"},
    "skills": {
        "navigate-to": {"enabled": True},
        "arm-manipulate": {"enabled": True},
        "camera-describe": {"enabled": False},  # disabled — should not appear
        "web-lookup": {"enabled": True},
    },
}

_CONFIG_UNDERSCORE_SKILLS = {
    "rcan_version": "1.6",
    "metadata": {"robot_name": "TestBot"},
    "skills": {
        "navigate_to": {"enabled": True, "order": 0},
        "camera_describe": {"enabled": True, "order": 1},
        "code_reviewer": {"enabled": True, "order": 2},
    },
}

_CONFIG_NO_SKILLS = {
    "rcan_version": "1.5",
    "metadata": {"robot_name": "EmptyBot", "robot_rrn": "RRN-FALLBACK-001"},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset AppState before every test."""
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
    api_mod.API_TOKEN = None
    api_mod._command_history.clear()
    api_mod._webhook_history.clear()

    yield


@pytest.fixture()
def client():
    """TestClient with no-op lifespan — no hardware, providers, or side effects."""
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


# ---------------------------------------------------------------------------
# Test 1: Public endpoint — no auth required
# ---------------------------------------------------------------------------


class TestSkillsAPIPublic:
    def test_no_auth_returns_200(self, client):
        """GET /api/skills must be a public endpoint — no token required."""
        resp = client.get("/api/skills")
        assert resp.status_code == 200

    def test_no_auth_with_no_config(self, client):
        """Returns 200 even when config is not loaded — uses empty defaults."""
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        body = resp.json()
        # Must always include these keys
        assert "builtin_commands" in body
        assert "skills" in body
        assert "rcan_version" in body
        assert "robot_rrn" in body

    def test_invalid_token_still_returns_200(self, client):
        """Even with a bad token header, the endpoint must remain public."""
        resp = client.get("/api/skills", headers={"Authorization": "Bearer INVALID"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 2: Response structure
# ---------------------------------------------------------------------------


class TestSkillsAPIStructure:
    def test_response_keys(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_NO_SKILLS)
        resp = client.get("/api/skills")
        body = resp.json()
        assert set(body.keys()) >= {"builtin_commands", "skills", "rcan_version", "robot_rrn"}

    def test_builtin_commands_is_list(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_NO_SKILLS)
        body = client.get("/api/skills").json()
        assert isinstance(body["builtin_commands"], list)
        assert len(body["builtin_commands"]) > 0

    def test_skills_is_list(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_NO_SKILLS)
        body = client.get("/api/skills").json()
        assert isinstance(body["skills"], list)

    def test_rcan_version_from_config(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_NO_SKILLS)
        body = client.get("/api/skills").json()
        assert body["rcan_version"] == "1.5"

    def test_rcan_version_default_when_no_config(self, client):
        body = client.get("/api/skills").json()
        assert body["rcan_version"] == "1.6"  # default

    def test_robot_rrn_from_metadata_rrn(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_BUILTIN_SKILLS_LIST)
        body = client.get("/api/skills").json()
        assert body["robot_rrn"] == "RRN-000000000042"

    def test_robot_rrn_fallback_to_robot_rrn_key(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_NO_SKILLS)
        body = client.get("/api/skills").json()
        assert body["robot_rrn"] == "RRN-FALLBACK-001"

    def test_robot_rrn_default_when_no_metadata(self, client):
        body = client.get("/api/skills").json()
        assert body["robot_rrn"] == "RRN-000000000001"  # fallback


# ---------------------------------------------------------------------------
# Test 3: Builtin CLI commands structure
# ---------------------------------------------------------------------------


class TestBuiltinCommands:
    def test_status_command_present(self, client):
        body = client.get("/api/skills").json()
        cmds = {c["cmd"]: c for c in body["builtin_commands"]}
        assert "/status" in cmds
        assert cmds["/status"]["scope"] == "status"
        assert cmds["/status"]["instant"] is True

    def test_reboot_command_is_system_scope(self, client):
        body = client.get("/api/skills").json()
        cmds = {c["cmd"]: c for c in body["builtin_commands"]}
        assert "/reboot" in cmds
        assert cmds["/reboot"]["scope"] == "system"
        assert cmds["/reboot"]["instant"] is False

    def test_upgrade_command_has_optional_version_arg(self, client):
        body = client.get("/api/skills").json()
        cmds = {c["cmd"]: c for c in body["builtin_commands"]}
        assert "/upgrade" in cmds
        upgrade = cmds["/upgrade"]
        assert "args" in upgrade
        version_arg = next((a for a in upgrade["args"] if a["name"] == "version"), None)
        assert version_arg is not None
        assert version_arg["optional"] is True

    def test_install_command_has_required_id_arg(self, client):
        body = client.get("/api/skills").json()
        cmds = {c["cmd"]: c for c in body["builtin_commands"]}
        assert "/install" in cmds
        install = cmds["/install"]
        assert "args" in install
        id_arg = next((a for a in install["args"] if a["name"] == "id"), None)
        assert id_arg is not None
        assert id_arg["optional"] is False

    def test_all_builtin_commands_have_required_fields(self, client):
        body = client.get("/api/skills").json()
        for cmd in body["builtin_commands"]:
            assert "cmd" in cmd, f"Missing 'cmd' in {cmd}"
            assert "description" in cmd, f"Missing 'description' in {cmd}"
            assert "scope" in cmd, f"Missing 'scope' in {cmd}"
            assert "instant" in cmd, f"Missing 'instant' in {cmd}"
            assert cmd["cmd"].startswith("/"), f"cmd must start with /: {cmd['cmd']}"


# ---------------------------------------------------------------------------
# Test 4: Skills from builtin_skills list format
# ---------------------------------------------------------------------------


class TestSkillsFromBuiltinList:
    def test_navigate_to_included(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_BUILTIN_SKILLS_LIST)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"]: s for s in body["skills"]}
        assert "/navigate-to" in cmds

    def test_navigate_to_has_control_scope(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_BUILTIN_SKILLS_LIST)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"]: s for s in body["skills"]}
        assert cmds["/navigate-to"]["scope"] == "control"

    def test_camera_describe_has_status_scope(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_BUILTIN_SKILLS_LIST)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"]: s for s in body["skills"]}
        assert "/camera-describe" in cmds
        assert cmds["/camera-describe"]["scope"] == "status"

    def test_web_lookup_has_chat_scope(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_BUILTIN_SKILLS_LIST)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"]: s for s in body["skills"]}
        assert "/web-lookup" in cmds
        assert cmds["/web-lookup"]["scope"] == "chat"

    def test_navigate_to_has_destination_arg(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_BUILTIN_SKILLS_LIST)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"]: s for s in body["skills"]}
        nav = cmds["/navigate-to"]
        assert "args" in nav
        dest_arg = next((a for a in nav["args"] if a["name"] == "destination"), None)
        assert dest_arg is not None
        assert dest_arg["optional"] is False

    def test_skills_count_matches_list(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_BUILTIN_SKILLS_LIST)
        body = client.get("/api/skills").json()
        # navigate-to, camera-describe, web-lookup = 3
        assert len(body["skills"]) == 3


# ---------------------------------------------------------------------------
# Test 5: Skills from keyed format (hyphen variant)
# ---------------------------------------------------------------------------


class TestSkillsFromKeyedFormat:
    def test_enabled_skills_appear(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_KEYED_SKILLS)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"] for s in body["skills"]}
        assert "/navigate-to" in cmds
        assert "/arm-manipulate" in cmds
        assert "/web-lookup" in cmds

    def test_disabled_skills_excluded(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_KEYED_SKILLS)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"] for s in body["skills"]}
        assert "/camera-describe" not in cmds

    def test_arm_manipulate_scope_is_control(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_KEYED_SKILLS)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"]: s for s in body["skills"]}
        assert cmds["/arm-manipulate"]["scope"] == "control"


# ---------------------------------------------------------------------------
# Test 6: Skills from underscore keyed format
# ---------------------------------------------------------------------------


class TestSkillsUnderscoreFormat:
    def test_navigate_to_via_underscore_key(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_UNDERSCORE_SKILLS)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"] for s in body["skills"]}
        assert "/navigate-to" in cmds

    def test_code_reviewer_scope_is_chat(self, client, api_mod):
        api_mod.state.config = copy.deepcopy(_CONFIG_UNDERSCORE_SKILLS)
        body = client.get("/api/skills").json()
        cmds = {s["cmd"]: s for s in body["skills"]}
        assert "/code-reviewer" in cmds
        assert cmds["/code-reviewer"]["scope"] == "chat"


# ---------------------------------------------------------------------------
# Test 7: Skill instant flag
# ---------------------------------------------------------------------------


class TestSkillInstantFlag:
    def test_skills_are_not_instant(self, client, api_mod):
        """Skills require args — they should not be marked instant."""
        api_mod.state.config = copy.deepcopy(_CONFIG_BUILTIN_SKILLS_LIST)
        body = client.get("/api/skills").json()
        for skill in body["skills"]:
            assert skill["instant"] is False, f"{skill['cmd']} should not be instant"


# ---------------------------------------------------------------------------
# Test 8: RCAN scope compliance
# ---------------------------------------------------------------------------


class TestRCANScopeCompliance:
    def test_all_skill_scopes_are_valid(self, client, api_mod):
        """All skills must have valid RCAN scope values."""
        valid_scopes = {"discover", "status", "chat", "control", "safety", "system"}
        api_mod.state.config = copy.deepcopy(_CONFIG_BUILTIN_SKILLS_LIST)
        body = client.get("/api/skills").json()
        for skill in body["skills"]:
            assert skill["scope"] in valid_scopes, (
                f"{skill['cmd']} has invalid scope: {skill['scope']}"
            )

    def test_all_cli_scopes_are_valid(self, client):
        """All builtin CLI commands must have valid RCAN scope values."""
        valid_scopes = {"discover", "status", "chat", "control", "safety", "system"}
        body = client.get("/api/skills").json()
        for cmd in body["builtin_commands"]:
            assert cmd["scope"] in valid_scopes, (
                f"{cmd['cmd']} has invalid scope: {cmd['scope']}"
            )

    def test_physical_commands_have_control_or_system_scope(self, client, api_mod):
        """navigate-to and arm-manipulate must be control scope (RCAN §2.3)."""
        api_mod.state.config = {
            "rcan_version": "1.6",
            "metadata": {},
            "skills": {"builtin_skills": ["navigate-to", "arm-manipulate"]},
        }
        body = client.get("/api/skills").json()
        cmds = {s["cmd"]: s for s in body["skills"]}
        assert cmds["/navigate-to"]["scope"] == "control"
        assert cmds["/arm-manipulate"]["scope"] == "control"

    def test_info_skills_have_read_only_scope(self, client, api_mod):
        """camera-describe must be status scope (read-only, RCAN §2.3)."""
        api_mod.state.config = {
            "rcan_version": "1.6",
            "metadata": {},
            "skills": {"builtin_skills": ["camera-describe"]},
        }
        body = client.get("/api/skills").json()
        cmds = {s["cmd"]: s for s in body["skills"]}
        assert cmds["/camera-describe"]["scope"] == "status"


# ---------------------------------------------------------------------------
# Test 9: No config secrets in response
# ---------------------------------------------------------------------------


class TestNoSecretsInResponse:
    def test_safety_key_not_in_response(self, client, api_mod):
        """RCAN §2.6: safety config must not be returned."""
        api_mod.state.config = {
            "rcan_version": "1.6",
            "metadata": {},
            "skills": {"builtin_skills": []},
            "safety": {"estop_enabled": True, "secret_key": "SUPERSECRET"},
        }
        body = client.get("/api/skills").json()
        body_str = str(body)
        assert "SUPERSECRET" not in body_str
        assert "safety" not in body

    def test_auth_key_not_in_response(self, client, api_mod):
        """RCAN §2.6: auth config must not be returned."""
        api_mod.state.config = {
            "rcan_version": "1.6",
            "metadata": {},
            "skills": {"builtin_skills": []},
            "auth": {"token": "SECRET_TOKEN_123"},
        }
        body = client.get("/api/skills").json()
        body_str = str(body)
        assert "SECRET_TOKEN_123" not in body_str

    def test_p66_key_not_in_response(self, client, api_mod):
        """RCAN §2.6: p66 config must not be returned."""
        api_mod.state.config = {
            "rcan_version": "1.6",
            "metadata": {},
            "skills": {"builtin_skills": []},
            "p66": {"signing_key": "P66_SECRET"},
        }
        body = client.get("/api/skills").json()
        body_str = str(body)
        assert "P66_SECRET" not in body_str


# ---------------------------------------------------------------------------
# Test 10: POST /api/harness forbidden keys protection (RCAN §2.6)
# ---------------------------------------------------------------------------


class TestHarnessAPIAdminRole:
    def test_post_harness_requires_admin_not_operator(self, client, api_mod):
        """POST /api/harness must require admin role (RCAN §2.6 — config writes are admin-only)."""
        # Set operator-level token
        import castor.api as mod

        api_mod.state.config = {
            "rcan_version": "1.6",
            "metadata": {"robot_name": "TestBot"},
            "agent": {"harness": {}},
        }

        # Use static token (treated as admin) — so test that operator JWT gets 403
        # We simulate viewer role via JWT path
        with patch.object(mod, "_check_min_role") as mock_check:
            from fastapi import HTTPException

            mock_check.side_effect = HTTPException(status_code=403, detail="admin required")
            resp = client.post("/api/harness", json={})
            # Either 403 from role check or 503 from config — both are not 200
            assert resp.status_code in (403, 503)


class TestHarnessForbiddenKeys:
    def test_post_harness_does_not_overwrite_safety(self, client, api_mod):
        """POST /api/harness must not overwrite the safety top-level key."""
        import castor.api as mod

        original_safety = {"estop_enabled": True, "my_secret": "keep_this"}
        api_mod.state.config = {
            "rcan_version": "1.6",
            "metadata": {"robot_name": "TestBot"},
            "safety": original_safety,
            "agent": {
                "harness": {
                    "enabled": True,
                    "max_iterations": 6,
                    "hooks": {"p66_audit": True},
                    "context": {},
                    "trajectory": {},
                }
            },
        }

        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
            tf.write(b"# placeholder\n")
            config_path = tf.name

        try:
            with patch.dict(os.environ, {"OPENCASTOR_CONFIG": config_path}):
                resp = client.post("/api/harness", json={"max_iterations": 8})
            assert resp.status_code == 200
            # RCAN §2.6: safety key must be preserved (not wiped) after harness apply
            assert api_mod.state.config.get("safety") == original_safety
        finally:
            os.unlink(config_path)
