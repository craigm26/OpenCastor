"""tests/test_mcp_server.py — MCP server: auth, LoA gating, tool listing."""

from __future__ import annotations

import secrets
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from castor.mcp_auth import (
    _hash_token,
    generate_token,
    list_clients,
    resolve_loa,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rcan_yaml(tmp_path: Path) -> Path:
    """Minimal RCAN yaml with one MCP client per LoA level."""
    tokens = {
        "read_only": secrets.token_urlsafe(32),
        "operator": secrets.token_urlsafe(32),
        "admin": secrets.token_urlsafe(32),
    }
    data = {
        "rrn": "RRN-000000000001",
        "rcan_version": "2.2",
        "mcp_clients": [
            {"name": "read-only-agent", "token_hash": _hash_token(tokens["read_only"]), "loa": 0},
            {"name": "operator-agent", "token_hash": _hash_token(tokens["operator"]), "loa": 1},
            {"name": "admin-agent", "token_hash": _hash_token(tokens["admin"]), "loa": 3},
        ],
    }
    path = tmp_path / "bob.rcan.yaml"
    path.write_text(yaml.dump(data))
    return path, tokens


# ---------------------------------------------------------------------------
# mcp_auth tests
# ---------------------------------------------------------------------------


class TestMcpAuth:
    def test_resolve_loa_read_only(self, rcan_yaml):
        path, tokens = rcan_yaml
        assert resolve_loa(tokens["read_only"], path) == 0

    def test_resolve_loa_operator(self, rcan_yaml):
        path, tokens = rcan_yaml
        assert resolve_loa(tokens["operator"], path) == 1

    def test_resolve_loa_admin(self, rcan_yaml):
        path, tokens = rcan_yaml
        assert resolve_loa(tokens["admin"], path) == 3

    def test_resolve_loa_unknown_token(self, rcan_yaml):
        path, _ = rcan_yaml
        assert resolve_loa("not-a-real-token", path) is None

    def test_resolve_loa_empty_token(self, rcan_yaml):
        path, _ = rcan_yaml
        assert resolve_loa("", path) is None

    def test_resolve_loa_missing_config(self, tmp_path):
        assert resolve_loa("any-token", tmp_path / "missing.yaml") is None

    def test_resolve_loa_no_mcp_clients_block(self, tmp_path):
        path = tmp_path / "minimal.yaml"
        path.write_text(yaml.dump({"rrn": "RRN-000000000001"}))
        assert resolve_loa("any-token", path) is None

    def test_hash_token_is_deterministic(self):
        token = "hello-world"
        assert _hash_token(token) == _hash_token(token)

    def test_hash_token_prefix(self):
        assert _hash_token("x").startswith("sha256:")

    def test_generate_token_appends_to_yaml(self, tmp_path):
        path = tmp_path / "bob.rcan.yaml"
        path.write_text(yaml.dump({"rrn": "RRN-000000000001", "mcp_clients": []}))
        raw = generate_token("new-agent", loa=1, config_path=path)
        assert len(raw) > 20
        reloaded = yaml.safe_load(path.read_text())
        names = [c["name"] for c in reloaded["mcp_clients"]]
        assert "new-agent" in names

    def test_generate_token_replaces_existing_name(self, tmp_path):
        path = tmp_path / "bob.rcan.yaml"
        path.write_text(yaml.dump({"mcp_clients": [{"name": "dup", "token_hash": "x", "loa": 0}]}))
        generate_token("dup", loa=1, config_path=path)
        cfg = yaml.safe_load(path.read_text())
        entries = [c for c in cfg["mcp_clients"] if c["name"] == "dup"]
        assert len(entries) == 1
        assert entries[0]["loa"] == 1

    def test_generate_token_is_usable(self, tmp_path):
        path = tmp_path / "bob.rcan.yaml"
        path.write_text(yaml.dump({"mcp_clients": []}))
        raw = generate_token("agent", loa=2, config_path=path)
        assert resolve_loa(raw, path) == 2

    def test_list_clients_returns_names(self, rcan_yaml):
        path, _ = rcan_yaml
        clients = list_clients(path)
        names = [c["name"] for c in clients]
        assert "read-only-agent" in names
        assert "admin-agent" in names

    def test_list_clients_no_raw_tokens(self, rcan_yaml):
        """list_clients must never return raw token values."""
        path, tokens = rcan_yaml
        clients = list_clients(path)
        for client in clients:
            for raw in tokens.values():
                assert raw not in str(client)

    def test_list_clients_missing_file(self, tmp_path):
        assert list_clients(tmp_path / "missing.yaml") == []

    def test_dev_mode_token(self, monkeypatch):
        monkeypatch.setenv("CASTOR_MCP_DEV", "1")
        result = resolve_loa("dev", config_path=Path("/nonexistent/path.yaml"))
        assert result == 3

    def test_dev_mode_only_for_dev_token(self, monkeypatch):
        monkeypatch.setenv("CASTOR_MCP_DEV", "1")
        result = resolve_loa("not-dev", config_path=Path("/nonexistent/path.yaml"))
        assert result is None


# ---------------------------------------------------------------------------
# LoA enforcement on MCP server tools
# ---------------------------------------------------------------------------


class TestLoaEnforcement:
    """Test that _check_loa raises PermissionError for insufficient LoA."""

    def test_read_tools_pass_loa0(self):
        import castor.mcp_server as srv

        original = srv._CLIENT_LOA
        try:
            srv._CLIENT_LOA = 0
            srv._check_loa(0)  # Should not raise
        finally:
            srv._CLIENT_LOA = original

    def test_operate_tool_blocked_at_loa0(self):
        import castor.mcp_server as srv

        original = srv._CLIENT_LOA
        try:
            srv._CLIENT_LOA = 0
            with pytest.raises(PermissionError, match="LoA"):
                srv._check_loa(1)
        finally:
            srv._CLIENT_LOA = original

    def test_admin_tool_blocked_at_loa1(self):
        import castor.mcp_server as srv

        original = srv._CLIENT_LOA
        try:
            srv._CLIENT_LOA = 1
            with pytest.raises(PermissionError, match="LoA"):
                srv._check_loa(3)
        finally:
            srv._CLIENT_LOA = original

    def test_admin_tool_passes_at_loa3(self):
        import castor.mcp_server as srv

        original = srv._CLIENT_LOA
        try:
            srv._CLIENT_LOA = 3
            srv._check_loa(3)  # Should not raise
        finally:
            srv._CLIENT_LOA = original

    def test_higher_loa_covers_lower_requirements(self):
        import castor.mcp_server as srv

        original = srv._CLIENT_LOA
        try:
            srv._CLIENT_LOA = 3
            srv._check_loa(0)
            srv._check_loa(1)
            srv._check_loa(3)
        finally:
            srv._CLIENT_LOA = original

    def test_error_message_includes_required_loa(self):
        import castor.mcp_server as srv

        original = srv._CLIENT_LOA
        try:
            srv._CLIENT_LOA = 0
            with pytest.raises(PermissionError, match="LoA ≥ 3"):
                srv._check_loa(3)
        finally:
            srv._CLIENT_LOA = original


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    def _get_tool_names(self):
        """Return set of registered tool names, compatible with stub and real FastMCP."""
        from castor.mcp_server import _MCP_AVAILABLE, mcp

        if not _MCP_AVAILABLE:
            # Stub: _tools dict maps name → function
            return set(mcp._tools.keys())

        # Real FastMCP v1.x: tools live in _tool_manager._tools
        mgr = getattr(mcp, "_tool_manager", None)
        if mgr is not None and hasattr(mgr, "_tools"):
            return set(mgr._tools.keys())

        # Fallback: async list_tools
        import asyncio

        async def _get():
            try:
                tools = await mcp._mcp_server.list_tools()
                return {t.name for t in tools.tools}
            except Exception:
                return set()

        return asyncio.run(_get())

    def test_all_expected_tools_registered(self):
        tool_names = self._get_tool_names()
        if not tool_names:
            pytest.skip("Cannot introspect tools in this MCP version")

        expected = {
            "robot_status",
            "robot_telemetry",
            "fleet_list",
            "rrf_lookup",
            "robot_command",
            "harness_get",
            "research_run",
            "contribute_toggle",
            "components_list",
            "harness_set",
            "system_upgrade",
            "loa_enable",
            "fleet_status",
            "fleet_broadcast",
            "fleet_estop",
            "fleet_navigate",
            "stream_telemetry",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    def test_tool_count_is_twelve(self):
        """19 tools: 12 core + robot_ping + compliance_report + 4 fleet + stream_telemetry."""
        tool_names = self._get_tool_names()
        if not tool_names:
            pytest.skip("Cannot count tools in this MCP version")
        assert len(tool_names) == 19


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


class TestConfigHelpers:
    def test_default_rrn_from_env(self, monkeypatch):
        monkeypatch.setenv("CASTOR_RRN", "RRN-000000000099")
        import castor.mcp_server as srv

        # _default_rrn reads env at call time
        with patch.dict("os.environ", {"CASTOR_RRN": "RRN-000000000099"}):
            assert "RRN-000000000099" in srv._default_rrn()

    def test_gateway_url_default(self, monkeypatch):
        monkeypatch.delenv("CASTOR_GATEWAY_URL", raising=False)
        from castor.mcp_server import _gateway_url

        with patch("castor.mcp_server._load_config", return_value={}):
            assert "8001" in _gateway_url() or "127.0.0.1" in _gateway_url()


class TestAdditionalTools:
    """Tests for robot_ping, compliance_report (added tools)."""

    def test_robot_ping_blocked_below_loa0(self):
        import castor.mcp_server as srv

        original = srv._CLIENT_LOA
        try:
            srv._CLIENT_LOA = -1
            with pytest.raises(PermissionError):
                srv._check_loa(0)
        finally:
            srv._CLIENT_LOA = original

    def test_additional_tools_registered(self):
        """robot_ping and compliance_report must be in the tool registry."""
        helper = TestToolRegistration()
        names = helper._get_tool_names()
        if not names:
            pytest.skip("Cannot introspect tools")
        assert "robot_ping" in names
        assert "compliance_report" in names

    def test_total_tool_count_is_fourteen(self):
        """19 tools total: 12 core + robot_ping + compliance_report + 4 fleet + stream_telemetry."""
        helper = TestToolRegistration()
        names = helper._get_tool_names()
        if not names:
            pytest.skip("Cannot count tools")
        assert len(names) == 19
