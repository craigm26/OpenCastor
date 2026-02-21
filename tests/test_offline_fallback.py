"""Tests for castor.offline_fallback — OfflineFallbackManager (#72)."""

from unittest.mock import MagicMock, patch

import pytest

from castor.offline_fallback import OfflineFallbackManager


def _make_primary():
    primary = MagicMock()
    primary.__class__.__name__ = "MockProvider"
    return primary


def _make_fallback_provider(ok: bool = True):
    fb = MagicMock()
    fb.health_check.return_value = {"ok": ok, "latency_ms": 12, "error": None}
    return fb


# ---------------------------------------------------------------------------
# Disabled / no fallback config
# ---------------------------------------------------------------------------

class TestDisabledFallback:
    def test_get_active_provider_returns_primary_when_disabled(self):
        primary = _make_primary()
        mgr = OfflineFallbackManager(config={}, primary_provider=primary)
        assert mgr.get_active_provider() is primary

    def test_is_using_fallback_false_when_no_config(self):
        mgr = OfflineFallbackManager(config={}, primary_provider=_make_primary())
        assert mgr.is_using_fallback is False

    def test_fallback_ready_false_when_no_fallback(self):
        mgr = OfflineFallbackManager(config={}, primary_provider=_make_primary())
        assert mgr.fallback_ready is False

    def test_probe_returns_false_when_no_fallback(self):
        mgr = OfflineFallbackManager(config={}, primary_provider=_make_primary())
        assert mgr.probe_fallback() is False

    def test_start_is_noop_when_no_fallback(self):
        """start() should not raise when no fallback provider is configured."""
        mgr = OfflineFallbackManager(config={}, primary_provider=_make_primary())
        # Should not raise
        mgr.start()
        mgr.stop()


# ---------------------------------------------------------------------------
# Enabled fallback — healthy probe
# ---------------------------------------------------------------------------

class TestEnabledFallback:
    def _make_manager(self, fb_ok: bool = True, **extra_config):
        config = {
            "offline_fallback": {
                "enabled": True,
                "provider": "ollama",
                "model": "llama3.2:3b",
                **extra_config,
            }
        }
        primary = _make_primary()
        fb_provider = _make_fallback_provider(ok=fb_ok)

        mgr = OfflineFallbackManager(config=config, primary_provider=primary)
        # Inject the fake fallback provider directly
        mgr._fallback = fb_provider
        return mgr, primary, fb_provider

    def test_probe_fallback_ok_sets_fallback_ready(self):
        mgr, _, _ = self._make_manager(fb_ok=True)
        result = mgr.probe_fallback()
        assert result is True
        assert mgr.fallback_ready is True

    def test_probe_fallback_fail_sets_fallback_ready_false(self):
        mgr, _, _ = self._make_manager(fb_ok=False)
        result = mgr.probe_fallback()
        assert result is False
        assert mgr.fallback_ready is False

    def test_probe_fallback_exception_returns_false(self):
        mgr, primary, fb = self._make_manager()
        fb.health_check.side_effect = RuntimeError("connection refused")
        assert mgr.probe_fallback() is False
        assert mgr.fallback_ready is False


# ---------------------------------------------------------------------------
# Provider switching
# ---------------------------------------------------------------------------

class TestProviderSwitching:
    def _make_manager(self):
        config = {
            "offline_fallback": {
                "enabled": True,
                "provider": "ollama",
                "model": "llama3.2:3b",
            }
        }
        primary = _make_primary()
        fb_provider = _make_fallback_provider()

        mgr = OfflineFallbackManager(config=config, primary_provider=primary)
        mgr._fallback = fb_provider
        return mgr, primary, fb_provider

    def test_get_active_returns_primary_when_online(self):
        mgr, primary, _ = self._make_manager()
        mgr._using_fallback = False
        assert mgr.get_active_provider() is primary

    def test_get_active_returns_fallback_when_offline(self):
        mgr, _, fb = self._make_manager()
        mgr._using_fallback = True
        assert mgr.get_active_provider() is fb

    def test_apply_state_online_clears_fallback_flag(self):
        mgr, _, _ = self._make_manager()
        mgr._using_fallback = True
        mgr._apply_state(online=True, initial=False)
        assert mgr.is_using_fallback is False

    def test_apply_state_offline_sets_fallback_flag(self):
        mgr, _, _ = self._make_manager()
        mgr._using_fallback = False
        mgr._apply_state(online=False, initial=False)
        assert mgr.is_using_fallback is True

    def test_alert_channel_called_on_switch(self):
        mgr, _, _ = self._make_manager()
        alert_fn = MagicMock()
        mgr._channel_send = alert_fn
        mgr._config["alert_channel"] = "test_channel"
        mgr._using_fallback = False

        mgr._apply_state(online=False, initial=False)
        alert_fn.assert_called_once()

    def test_no_alert_on_initial_state(self):
        mgr, _, _ = self._make_manager()
        alert_fn = MagicMock()
        mgr._channel_send = alert_fn
        mgr._config["alert_channel"] = "test_channel"

        mgr._apply_state(online=True, initial=True)
        alert_fn.assert_not_called()


# ---------------------------------------------------------------------------
# start() / stop() lifecycle with mocked connectivity
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_stop_is_safe_without_start(self):
        mgr = OfflineFallbackManager(config={}, primary_provider=_make_primary())
        mgr.stop()  # Should not raise

    def test_start_probes_fallback_immediately(self):
        config = {
            "offline_fallback": {
                "enabled": True,
                "provider": "ollama",
                "model": "llama3.2:3b",
                "check_interval_s": 60,
            }
        }
        primary = _make_primary()
        fb_provider = _make_fallback_provider(ok=True)

        mgr = OfflineFallbackManager(config=config, primary_provider=primary)
        mgr._fallback = fb_provider

        with patch("castor.offline_fallback.ConnectivityMonitor") as mock_cm_cls, \
             patch("castor.offline_fallback.is_online", return_value=True):
            mock_cm = MagicMock()
            mock_cm_cls.return_value = mock_cm

            mgr.start()

        fb_provider.health_check.assert_called_once()
        mock_cm.start.assert_called_once()
        mgr.stop()


# ---------------------------------------------------------------------------
# Runtime connectivity-change provider switching  (#83)
# ---------------------------------------------------------------------------

class TestConnectivityChange:
    """Verify that _on_connectivity_change correctly switches active provider."""

    def _make_manager(self):
        config = {
            "offline_fallback": {
                "enabled": True,
                "provider": "ollama",
                "model": "llama3.2:3b",
            }
        }
        primary = _make_primary()
        fb = _make_fallback_provider(ok=True)
        mgr = OfflineFallbackManager(config=config, primary_provider=primary)
        mgr._fallback = fb
        return mgr, primary, fb

    def test_get_active_switches_to_fallback_when_offline(self):
        mgr, primary, fb = self._make_manager()
        assert mgr.get_active_provider() is primary

        mgr._on_connectivity_change(online=False)

        assert mgr.is_using_fallback is True
        assert mgr.get_active_provider() is fb

    def test_get_active_returns_primary_when_online_again(self):
        mgr, primary, fb = self._make_manager()
        # Simulate: we were offline
        mgr._using_fallback = True

        mgr._on_connectivity_change(online=True)

        assert mgr.is_using_fallback is False
        assert mgr.get_active_provider() is primary

    def test_repeated_offline_events_do_not_double_switch(self):
        """Multiple offline events should not flip back to primary."""
        mgr, primary, fb = self._make_manager()
        mgr._on_connectivity_change(online=False)
        mgr._on_connectivity_change(online=False)

        assert mgr.is_using_fallback is True
        assert mgr.get_active_provider() is fb

    def test_online_event_when_already_online_is_noop(self):
        mgr, primary, fb = self._make_manager()
        mgr._on_connectivity_change(online=True)  # already online

        assert mgr.is_using_fallback is False
        assert mgr.get_active_provider() is primary

    def test_alert_sent_on_going_offline(self):
        mgr, _, _ = self._make_manager()
        alert_fn = MagicMock()
        mgr._channel_send = alert_fn
        mgr._config["alert_channel"] = "slack"

        mgr._on_connectivity_change(online=False)

        alert_fn.assert_called_once()
        # Alert message should mention the fallback provider
        msg = alert_fn.call_args[0][0]
        assert "ollama" in msg.lower() or "fallback" in msg.lower()

    def test_alert_sent_on_coming_back_online(self):
        mgr, _, _ = self._make_manager()
        mgr._using_fallback = True
        alert_fn = MagicMock()
        mgr._channel_send = alert_fn
        mgr._config["alert_channel"] = "slack"

        mgr._on_connectivity_change(online=True)

        alert_fn.assert_called_once()
