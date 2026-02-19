"""Tests for SwarmPeer."""

from __future__ import annotations

import time

import pytest

from castor.swarm.peer import SwarmPeer


def _make_peer(
    robot_id: str = "robot-1",
    robot_name: str = "Castor1",
    host: str = "192.168.1.10",
    port: int = 8000,
    capabilities: list[str] | None = None,
    last_seen: float | None = None,
    load_score: float = 0.0,
) -> SwarmPeer:
    if capabilities is None:
        capabilities = ["navigation", "vision"]
    if last_seen is None:
        last_seen = time.time()
    return SwarmPeer(
        robot_id=robot_id,
        robot_name=robot_name,
        host=host,
        port=port,
        capabilities=capabilities,
        last_seen=last_seen,
        load_score=load_score,
    )


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_fresh_idle_peer_is_available(self):
        peer = _make_peer(last_seen=time.time(), load_score=0.0)
        assert peer.is_available is True

    def test_high_load_not_available(self):
        peer = _make_peer(last_seen=time.time(), load_score=0.8)
        assert peer.is_available is False

    def test_load_just_below_threshold_available(self):
        peer = _make_peer(last_seen=time.time(), load_score=0.799)
        assert peer.is_available is True

    def test_stale_peer_not_available(self):
        peer = _make_peer(last_seen=time.time() - 31.0, load_score=0.0)
        assert peer.is_available is False

    def test_seen_exactly_30s_ago_not_available(self):
        peer = _make_peer(last_seen=time.time() - 30.0, load_score=0.0)
        assert peer.is_available is False

    def test_seen_29s_ago_and_low_load_available(self):
        peer = _make_peer(last_seen=time.time() - 29.0, load_score=0.5)
        assert peer.is_available is True


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------


class TestIsStale:
    def test_fresh_peer_not_stale(self):
        peer = _make_peer(last_seen=time.time())
        assert peer.is_stale is False

    def test_peer_seen_61s_ago_is_stale(self):
        peer = _make_peer(last_seen=time.time() - 61.0)
        assert peer.is_stale is True

    def test_peer_seen_exactly_60s_ago_not_stale(self):
        # Boundary: > 60s means stale, so 59.9s should not be stale.
        # (We avoid exactly 60.0 because tiny scheduling jitter tips it over.)
        peer = _make_peer(last_seen=time.time() - 59.9)
        assert peer.is_stale is False

    def test_peer_seen_59s_ago_not_stale(self):
        peer = _make_peer(last_seen=time.time() - 59.0)
        assert peer.is_stale is False


# ---------------------------------------------------------------------------
# can_do
# ---------------------------------------------------------------------------


class TestCanDo:
    def test_has_capability(self):
        peer = _make_peer(capabilities=["navigation", "vision"])
        assert peer.can_do("navigation") is True

    def test_missing_capability(self):
        peer = _make_peer(capabilities=["navigation"])
        assert peer.can_do("arm-control") is False

    def test_empty_capabilities(self):
        peer = _make_peer(capabilities=[])
        assert peer.can_do("anything") is False


# ---------------------------------------------------------------------------
# to_dict / from_dict
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_roundtrip(self):
        peer = _make_peer(capabilities=["nav", "vision"], load_score=0.3)
        d = peer.to_dict()
        restored = SwarmPeer.from_dict(d)
        assert restored.robot_id == peer.robot_id
        assert restored.robot_name == peer.robot_name
        assert restored.host == peer.host
        assert restored.port == peer.port
        assert restored.capabilities == peer.capabilities
        assert restored.last_seen == pytest.approx(peer.last_seen)
        assert restored.load_score == pytest.approx(peer.load_score)

    def test_to_dict_keys(self):
        peer = _make_peer()
        d = peer.to_dict()
        assert set(d.keys()) == {
            "robot_id",
            "robot_name",
            "host",
            "port",
            "capabilities",
            "last_seen",
            "load_score",
        }

    def test_capabilities_list_is_copy(self):
        peer = _make_peer(capabilities=["a", "b"])
        d = peer.to_dict()
        d["capabilities"].append("c")
        assert "c" not in peer.capabilities


# ---------------------------------------------------------------------------
# from_mdns
# ---------------------------------------------------------------------------


class TestFromMdns:
    def _service_info(self, **kwargs) -> dict:
        base = {
            "name": "castor-alpha",
            "host": "10.0.0.5",
            "port": 8080,
            "properties": {
                "robot_uuid": "uuid-abc",
                "robot_name": "Alpha",
                "capabilities": "nav,vision,arm",
                "load_score": "0.25",
            },
        }
        base.update(kwargs)
        return base

    def test_basic_from_mdns(self):
        info = self._service_info()
        peer = SwarmPeer.from_mdns(info)
        assert peer.robot_id == "uuid-abc"
        assert peer.robot_name == "Alpha"
        assert peer.host == "10.0.0.5"
        assert peer.port == 8080
        assert peer.capabilities == ["nav", "vision", "arm"]
        assert peer.load_score == pytest.approx(0.25)

    def test_from_mdns_fallback_robot_id_from_name(self):
        info = {
            "name": "castor-beta",
            "host": "10.0.0.6",
            "port": 9000,
            "properties": {},
        }
        peer = SwarmPeer.from_mdns(info)
        assert peer.robot_id == "castor-beta"
        assert peer.robot_name == "castor-beta"
        assert peer.capabilities == []
        assert peer.load_score == pytest.approx(0.0)

    def test_from_mdns_last_seen_is_recent(self):
        peer = SwarmPeer.from_mdns(self._service_info())
        assert abs(peer.last_seen - time.time()) < 2.0

    def test_from_mdns_empty_capabilities_string(self):
        info = self._service_info()
        info["properties"]["capabilities"] = ""
        peer = SwarmPeer.from_mdns(info)
        assert peer.capabilities == []
