"""Tests for RCAN mDNS Discovery.

These tests verify the mDNS module structure and graceful degradation
without requiring a live network or the zeroconf library.
"""

import pytest

from castor.rcan.mdns import (
    SERVICE_TYPE,
    RCANServiceBroadcaster,
    RCANServiceBrowser,
    _get_local_ip,
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
