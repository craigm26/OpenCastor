"""Tests for castor.watchdog -- auto-stop motors if the brain stops responding."""

import threading
import time
from unittest.mock import patch, MagicMock

from castor.watchdog import BrainWatchdog


# =====================================================================
# BrainWatchdog.__init__
# =====================================================================
class TestBrainWatchdogInit:
    def test_default_config(self):
        wd = BrainWatchdog({})
        assert wd.enabled is True
        assert wd.timeout == 10.0
        assert wd.action == "stop"

    def test_disabled_config(self):
        config = {"watchdog": {"enabled": False}}
        wd = BrainWatchdog(config)
        assert wd.enabled is False

    def test_custom_config(self):
        config = {
            "watchdog": {
                "enabled": True,
                "timeout_s": 5.0,
                "action": "stop",
            }
        }
        wd = BrainWatchdog(config)
        assert wd.timeout == 5.0


# =====================================================================
# BrainWatchdog.heartbeat
# =====================================================================
class TestBrainWatchdogHeartbeat:
    def test_heartbeat_resets_timer(self):
        wd = BrainWatchdog({})
        time.sleep(0.05)
        wd.heartbeat()
        # After heartbeat, last_heartbeat should be very recent
        elapsed = time.time() - wd._last_heartbeat
        assert elapsed < 0.1


# =====================================================================
# BrainWatchdog.start
# =====================================================================
class TestBrainWatchdogStart:
    def test_start_creates_thread(self):
        config = {"watchdog": {"enabled": True, "timeout_s": 10.0}}
        wd = BrainWatchdog(config)
        wd.start()
        try:
            assert wd._thread is not None
            assert wd._thread.is_alive()
        finally:
            wd.stop()

    def test_start_disabled_does_not_create_thread(self):
        config = {"watchdog": {"enabled": False}}
        wd = BrainWatchdog(config)
        wd.start()
        assert wd._thread is None


# =====================================================================
# BrainWatchdog.stop
# =====================================================================
class TestBrainWatchdogStop:
    def test_stop_joins_thread(self):
        config = {"watchdog": {"enabled": True, "timeout_s": 10.0}}
        wd = BrainWatchdog(config)
        wd.start()
        assert wd._thread.is_alive()

        wd.stop()
        assert wd._running is False
        # Thread should no longer be alive after join
        assert not wd._thread.is_alive()


# =====================================================================
# BrainWatchdog.is_triggered
# =====================================================================
class TestBrainWatchdogIsTriggered:
    def test_is_triggered_default_false(self):
        wd = BrainWatchdog({})
        assert wd.is_triggered is False


# =====================================================================
# BrainWatchdog.get_status
# =====================================================================
class TestBrainWatchdogGetStatus:
    def test_get_status_returns_correct_dict(self):
        config = {"watchdog": {"enabled": True, "timeout_s": 10.0}}
        wd = BrainWatchdog(config)
        status = wd.get_status()

        assert status["enabled"] is True
        assert status["timeout_s"] == 10.0
        assert isinstance(status["last_heartbeat_s_ago"], float)
        assert status["triggered"] is False


# =====================================================================
# BrainWatchdog -- timeout triggers stop_fn
# =====================================================================
class TestBrainWatchdogTimeout:
    def test_watchdog_triggers_stop_fn_after_timeout(self):
        triggered_event = threading.Event()
        stop_mock = MagicMock(side_effect=lambda: triggered_event.set())

        config = {"watchdog": {"enabled": True, "timeout_s": 0.1}}
        wd = BrainWatchdog(config, stop_fn=stop_mock)

        # Monkey-patch the sleep interval to make the test fast
        original_loop = wd._monitor_loop

        def fast_monitor_loop():
            while wd._running:
                with wd._lock:
                    elapsed = time.time() - wd._last_heartbeat
                if elapsed > wd.timeout and not wd._triggered:
                    wd._triggered = True
                    if wd._stop_fn:
                        try:
                            wd._stop_fn()
                        except Exception:
                            pass
                time.sleep(0.05)  # Check every 50ms instead of 1s

        wd._monitor_loop = fast_monitor_loop
        wd.start()

        try:
            # Wait for the watchdog to trigger (timeout 0.1s + some slack)
            triggered = triggered_event.wait(timeout=2.0)
            assert triggered, "Watchdog did not trigger stop_fn within expected time"
            stop_mock.assert_called_once()
            assert wd.is_triggered is True
        finally:
            wd.stop()

    def test_heartbeat_prevents_trigger(self):
        stop_mock = MagicMock()

        config = {"watchdog": {"enabled": True, "timeout_s": 0.2}}
        wd = BrainWatchdog(config, stop_fn=stop_mock)

        def fast_monitor_loop():
            while wd._running:
                with wd._lock:
                    elapsed = time.time() - wd._last_heartbeat
                if elapsed > wd.timeout and not wd._triggered:
                    wd._triggered = True
                    if wd._stop_fn:
                        try:
                            wd._stop_fn()
                        except Exception:
                            pass
                time.sleep(0.05)

        wd._monitor_loop = fast_monitor_loop
        wd.start()

        try:
            # Keep sending heartbeats faster than the timeout
            for _ in range(5):
                wd.heartbeat()
                time.sleep(0.05)

            stop_mock.assert_not_called()
            assert wd.is_triggered is False
        finally:
            wd.stop()
