"""Tests for robot-to-robot mesh networking.

Issue #220 — MeshNode, PeerConfig, route_to_peer, relay.
"""

from __future__ import annotations

from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MESH_CFG = {
    "mesh": {
        "enabled": True,
        "peers": [
            {"name": "bob", "host": "bob.local", "port": 8000, "token": "tok-bob"},
            {"name": "alice", "host": "10.0.0.2", "port": 8001, "token": ""},
        ],
    }
}

_EMPTY_MESH_CFG = {"mesh": {"enabled": False, "peers": []}}


def _make_mesh(config=None):
    from castor.mesh import MeshNode

    return MeshNode(config or _MESH_CFG)


# ---------------------------------------------------------------------------
# PeerConfig
# ---------------------------------------------------------------------------


class TestPeerConfig:
    def test_basic_attrs(self):
        from castor.mesh import PeerConfig

        p = PeerConfig(name="bob", host="bob.local", port=8000, token="abc")
        assert p.name == "bob"
        assert p.host == "bob.local"
        assert p.port == 8000
        assert p.token == "abc"

    def test_base_url(self):
        from castor.mesh import PeerConfig

        p = PeerConfig(name="x", host="192.168.1.1", port=9000)
        assert p.base_url == "http://192.168.1.1:9000"

    def test_env_var_token_resolution(self, monkeypatch):
        from castor.mesh import PeerConfig

        monkeypatch.setenv("BOB_TOKEN", "secret-token")
        p = PeerConfig(name="b", host="h", port=8000, token="${BOB_TOKEN}")
        assert p.token == "secret-token"

    def test_env_var_missing_returns_empty(self, monkeypatch):
        from castor.mesh import PeerConfig

        monkeypatch.delenv("MISSING_VAR", raising=False)
        p = PeerConfig(name="b", host="h", port=8000, token="${MISSING_VAR}")
        assert p.token == ""

    def test_literal_token_unchanged(self):
        from castor.mesh import PeerConfig

        p = PeerConfig(name="b", host="h", port=8000, token="literal-token")
        assert p.token == "literal-token"


# ---------------------------------------------------------------------------
# MeshNode init
# ---------------------------------------------------------------------------


class TestMeshNodeInit:
    def test_peer_count(self):
        mesh = _make_mesh()
        assert mesh.peer_count == 2

    def test_peer_names(self):
        mesh = _make_mesh()
        assert set(mesh.peer_names) == {"bob", "alice"}

    def test_enabled_flag(self):
        mesh = _make_mesh()
        assert mesh.enabled is True

    def test_disabled_flag(self):
        mesh = _make_mesh(_EMPTY_MESH_CFG)
        assert mesh.enabled is False

    def test_empty_peers(self):
        mesh = _make_mesh(_EMPTY_MESH_CFG)
        assert mesh.peer_count == 0


# ---------------------------------------------------------------------------
# PeerStatus
# ---------------------------------------------------------------------------


class TestPeerStatus:
    def test_to_dict_ok(self):
        from castor.mesh import PeerStatus

        ps = PeerStatus(name="bob", base_url="http://bob.local:8000", ok=True, latency_ms=12.5)
        d = ps.to_dict()
        assert d["name"] == "bob"
        assert d["ok"] is True
        assert d["latency_ms"] == 12.5

    def test_to_dict_error(self):
        from castor.mesh import PeerStatus

        ps = PeerStatus(
            name="bob",
            base_url="http://bob.local:8000",
            ok=False,
            error="Connection refused",
        )
        d = ps.to_dict()
        assert d["ok"] is False
        assert "Connection refused" in d["error"]


# ---------------------------------------------------------------------------
# RelayResult
# ---------------------------------------------------------------------------


class TestRelayResult:
    def test_to_dict(self):
        from castor.mesh import RelayResult

        r = RelayResult(peer_name="alice", ok=True, response={"action": "move"}, latency_ms=5.0)
        d = r.to_dict()
        assert d["peer_name"] == "alice"
        assert d["ok"] is True
        assert d["response"] == {"action": "move"}


# ---------------------------------------------------------------------------
# MeshNode.list_peers — mocked HTTP
# ---------------------------------------------------------------------------


class TestListPeers:
    def test_returns_list_of_peer_statuses(self):
        mesh = _make_mesh()
        with patch("castor.mesh._http_get", return_value={"status": "ok"}):
            statuses = mesh.list_peers()
        assert len(statuses) == 2
        assert all(s.ok for s in statuses)

    def test_failed_peer_marked_not_ok(self):
        mesh = _make_mesh()

        def fake_get(url, **kwargs):
            if "bob" in url:
                raise ConnectionError("refused")
            return {"status": "ok"}

        with patch("castor.mesh._http_get", side_effect=fake_get):
            statuses = mesh.list_peers()
        bob_status = next(s for s in statuses if s.name == "bob")
        assert bob_status.ok is False
        assert "refused" in bob_status.error


# ---------------------------------------------------------------------------
# MeshNode.route_to_peer — mocked HTTP
# ---------------------------------------------------------------------------


class TestRouteToPeer:
    def test_successful_relay(self):
        mesh = _make_mesh()
        with patch("castor.mesh._http_post", return_value={"ok": True, "reply": "done"}):
            result = mesh.route_to_peer("bob", "go forward")
        assert result.ok is True
        assert result.response["reply"] == "done"

    def test_unknown_peer_returns_error(self):
        mesh = _make_mesh()
        result = mesh.route_to_peer("charlie", "do something")
        assert result.ok is False
        assert "charlie" in result.error

    def test_disabled_mesh_returns_error(self):
        mesh = _make_mesh(_EMPTY_MESH_CFG)
        result = mesh.route_to_peer("bob", "go forward")
        assert result.ok is False
        assert "disabled" in result.error

    def test_http_error_returns_failed_result(self):
        mesh = _make_mesh()
        with patch("castor.mesh._http_post", side_effect=ConnectionError("timeout")):
            result = mesh.route_to_peer("bob", "stop")
        assert result.ok is False
        assert "timeout" in result.error

    def test_relay_dict_alias(self):
        mesh = _make_mesh()
        with patch("castor.mesh._http_post", return_value={"ok": True}):
            d = mesh.relay("alice", "wave")
        assert isinstance(d, dict)
        assert d["peer_name"] == "alice"


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


class TestMeshSingleton:
    def test_get_mesh_creates_instance(self):
        from castor.mesh import get_mesh, reset_mesh

        reset_mesh()
        mesh = get_mesh(_MESH_CFG)
        assert mesh is not None

    def test_get_mesh_returns_same_instance(self):
        from castor.mesh import get_mesh, reset_mesh

        reset_mesh()
        m1 = get_mesh(_MESH_CFG)
        m2 = get_mesh()
        assert m1 is m2

    def test_reset_mesh_forces_new_instance(self):
        from castor.mesh import get_mesh, reset_mesh

        reset_mesh()
        m1 = get_mesh(_MESH_CFG)
        reset_mesh()
        m2 = get_mesh(_MESH_CFG)
        assert m1 is not m2
