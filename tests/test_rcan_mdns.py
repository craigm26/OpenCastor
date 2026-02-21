"""Tests for RCAN mDNS Discovery.

These tests verify the mDNS module structure and graceful degradation
without requiring a live network or the zeroconf library.
"""

import socket

from castor.rcan.mdns import (
    SERVICE_TYPE,
    RCANServiceBroadcaster,
    RCANServiceBrowser,
    _get_local_ip,
    _parse_service_info,
)


class TestServiceType:
    """RCAN service type constant."""

    def test_service_type(self):
        assert SERVICE_TYPE == "_rcan._tcp.local."


class TestBroadcaster:
    """Broadcaster creation and lifecycle."""

    def test_create_broadcaster(self):
        b = RCANServiceBroadcaster(
            ruri="rcan://opencastor.rover.abc12345",
            robot_name="Test Bot",
            port=8000,
            capabilities=["nav", "vision"],
            model="rover",
        )
        assert b.ruri == "rcan://opencastor.rover.abc12345"
        assert b.robot_name == "Test Bot"
        assert b.port == 8000
        assert b.capabilities == ["nav", "vision"]

    def test_default_status_fn(self):
        b = RCANServiceBroadcaster(ruri="rcan://a.b.c")
        assert b._status_fn() == "active"

    def test_custom_status_fn(self):
        b = RCANServiceBroadcaster(
            ruri="rcan://a.b.c",
            status_fn=lambda: "idle",
        )
        assert b._status_fn() == "idle"

    def test_stop_when_not_started(self):
        b = RCANServiceBroadcaster(ruri="rcan://a.b.c")
        b.stop()  # Should not raise


class TestBrowser:
    """Browser creation and lifecycle."""

    def test_create_browser(self):
        b = RCANServiceBrowser()
        assert b.peers == {}

    def test_peers_initially_empty(self):
        b = RCANServiceBrowser()
        assert len(b.peers) == 0

    def test_stop_when_not_started(self):
        b = RCANServiceBrowser()
        b.stop()  # Should not raise

    def test_callbacks_optional(self):
        b = RCANServiceBrowser(on_found=None, on_removed=None)
        assert b._on_found is None
        assert b._on_removed is None


class TestHelpers:
    """Helper functions."""

    def test_get_local_ip_returns_string(self):
        ip = _get_local_ip()
        assert isinstance(ip, str)
        parts = ip.split(".")
        assert len(parts) == 4


class TestGracefulDegradation:
    """Verify everything works even if zeroconf is not installed."""

    def test_broadcaster_start_without_zeroconf(self):
        """If zeroconf isn't available, start() logs warning but doesn't crash."""
        b = RCANServiceBroadcaster(ruri="rcan://a.b.c")
        if not b.enabled:
            b.start()  # Should log warning, not raise
            b.stop()

    def test_browser_start_without_zeroconf(self):
        """If zeroconf isn't available, start() logs warning but doesn't crash."""
        b = RCANServiceBrowser()
        if not b.enabled:
            b.start()
            b.stop()


class TestParseServiceInfo:
    """_parse_service_info extracts peer dict from a mocked ServiceInfo."""

    class _MockServiceInfo:
        """Minimal zeroconf ServiceInfo lookalike."""

        def __init__(self, props, addresses, port, name="TestBot._rcan._tcp.local."):
            self.name = name
            self.port = port
            self.properties = props
            self._addresses = addresses

        def parsed_addresses(self):
            return self._addresses

    def _make_info(self, props_override=None):
        props = {
            b"ruri": b"rcan://opencastor.rover.abc123",
            b"model": b"rover",
            b"caps": b"nav,vision",
            b"name": b"Test Bot",
            b"status": b"active",
            b"roles": b"GUEST,USER",
            b"version": b"1.0.0",
        }
        if props_override:
            props.update(props_override)
        return self._MockServiceInfo(
            props=props,
            addresses=["192.168.1.100"],
            port=8000,
        )

    def test_robot_name(self):
        peer = _parse_service_info(self._make_info())
        assert peer["robot_name"] == "Test Bot"

    def test_ruri(self):
        peer = _parse_service_info(self._make_info())
        assert peer["ruri"] == "rcan://opencastor.rover.abc123"

    def test_model(self):
        peer = _parse_service_info(self._make_info())
        assert peer["model"] == "rover"

    def test_capabilities_split(self):
        peer = _parse_service_info(self._make_info())
        assert "nav" in peer["capabilities"]
        assert "vision" in peer["capabilities"]

    def test_empty_capabilities(self):
        peer = _parse_service_info(self._make_info({b"caps": b""}))
        assert peer["capabilities"] == []

    def test_status(self):
        peer = _parse_service_info(self._make_info())
        assert peer["status"] == "active"

    def test_port(self):
        peer = _parse_service_info(self._make_info())
        assert peer["port"] == 8000

    def test_addresses(self):
        peer = _parse_service_info(self._make_info())
        assert "192.168.1.100" in peer["addresses"]

    def test_discovered_at_is_recent(self):
        import time

        peer = _parse_service_info(self._make_info())
        assert abs(peer["discovered_at"] - time.time()) < 2.0

    def test_string_keys_in_properties(self):
        """Properties may arrive as str keys (not bytes) — should still parse."""
        info = self._MockServiceInfo(
            props={
                "ruri": "rcan://opencastor.arm.xyz",
                "name": "Arm Bot",
                "caps": "grip",
                "status": "idle",
                "model": "arm",
                "roles": "GUEST",
                "version": "1.0.0",
            },
            addresses=["10.0.0.2"],
            port=9000,
        )
        peer = _parse_service_info(info)
        assert peer["robot_name"] == "Arm Bot"
        assert peer["status"] == "idle"
        assert peer["port"] == 9000

    def test_fallback_addresses_via_raw_bytes(self):
        """Peer with .addresses list of packed IPv4 bytes (no parsed_addresses())."""

        class _LegacyInfo:
            name = "legacy._rcan._tcp.local."
            port = 8000
            properties = {b"name": b"Legacy", b"caps": b"", b"status": b"active",
                          b"ruri": b"rcan://x", b"model": b"m", b"roles": b"G", b"version": b"1"}
            addresses = [socket.inet_aton("172.16.0.5")]

        peer = _parse_service_info(_LegacyInfo())
        assert "172.16.0.5" in peer["addresses"]


class TestBrowserCallbacks:
    """RCANServiceBrowser on_found / on_removed callbacks."""

    def test_on_found_callback_registered(self):
        found = []
        b = RCANServiceBrowser(on_found=lambda p: found.append(p))
        assert b._on_found is not None

    def test_on_removed_callback_registered(self):
        removed = []
        b = RCANServiceBrowser(on_removed=lambda n: removed.append(n))
        assert b._on_removed is not None

    def test_peers_snapshot_is_copy(self):
        """Modifying the returned peers dict must not affect internal state."""
        b = RCANServiceBrowser()
        snap = b.peers
        snap["injected"] = "bad"
        assert "injected" not in b.peers
