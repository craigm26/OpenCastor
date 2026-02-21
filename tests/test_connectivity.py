"""Tests for castor.connectivity — internet and provider reachability checks."""

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from castor.connectivity import (
    ConnectivityMonitor,
    check_provider_reachable,
    is_online,
)


class TestIsOnline:
    def test_returns_true_on_success(self):
        with patch("socket.create_connection") as mock_conn:
            mock_sock = MagicMock()
            mock_conn.return_value = mock_sock
            assert is_online(timeout=1.0) is True
            mock_sock.close.assert_called_once()

    def test_returns_false_when_all_fail(self):
        with patch("socket.create_connection", side_effect=OSError):
            assert is_online(timeout=0.1) is False

    def test_returns_true_on_second_probe(self):
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("first probe fails")
            return MagicMock()

        with patch("socket.create_connection", side_effect=side_effect):
            assert is_online(timeout=0.5) is True


class TestCheckProviderReachable:
    def test_local_providers_always_reachable(self):
        # Local providers (ollama, llamacpp, mlx) should always return True
        # without making any network calls
        for provider in ["ollama", "llamacpp", "mlx"]:
            assert check_provider_reachable(provider) is True

    def test_unknown_provider_without_host(self):
        # Unknown provider with no host mapping → True (fail open)
        assert check_provider_reachable("unknown-local") is True

    def test_cloud_provider_success(self):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value = MagicMock()
            assert check_provider_reachable("anthropic") is True

    def test_cloud_provider_failure(self):
        with patch("socket.create_connection", side_effect=OSError):
            assert check_provider_reachable("anthropic") is False


class TestConnectivityMonitor:
    def test_fires_callback_on_change(self):
        events = []

        def on_change(online: bool):
            events.append(online)

        monitor = ConnectivityMonitor(on_change=on_change, interval=0.05)

        call_seq = [False, False, True, True, False]
        idx = 0

        def fake_is_online(timeout):
            nonlocal idx
            val = call_seq[idx % len(call_seq)]
            idx += 1
            return val

        with patch("castor.connectivity.is_online", side_effect=fake_is_online):
            monitor.start()
            time.sleep(0.4)
            monitor.stop()

        # Should have seen at least one change event
        assert len(events) >= 1
        # Consecutive same values should NOT fire duplicate callbacks
        for i in range(1, len(events)):
            assert events[i] != events[i - 1]

    def test_initial_state_is_none(self):
        monitor = ConnectivityMonitor()
        assert monitor.online is None

    def test_stop_is_idempotent(self):
        monitor = ConnectivityMonitor(interval=60)
        monitor.stop()  # should not raise
        monitor.start()
        monitor.stop()
        monitor.stop()  # double-stop should not raise

    def test_callback_exception_does_not_crash_monitor(self):
        """A crashing callback must not kill the monitor thread."""
        call_count = 0

        def bad_callback(online):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("oops")

        monitor = ConnectivityMonitor(on_change=bad_callback, interval=0.05)
        with patch("castor.connectivity.is_online", return_value=True):
            monitor.start()
            time.sleep(0.2)
            monitor.stop()

        assert monitor._thread is not None
        assert call_count >= 1
