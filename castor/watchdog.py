"""
OpenCastor Watchdog -- auto-stop motors if the brain stops responding.

Independent of the latency budget (which only warns), the watchdog
will physically stop all motors if no successful brain response
arrives within the configured timeout.

RCAN config format::

    watchdog:
      enabled: true
      timeout_s: 10.0            # Max time without brain response
      action: stop               # What to do: "stop" (default)

Usage:
    Integrated into main.py automatically.
"""

import logging
import threading
import time

logger = logging.getLogger("OpenCastor.Watchdog")


class BrainWatchdog:
    """Monitors brain responsiveness and stops motors on timeout."""

    def __init__(self, config: dict, stop_fn=None):
        """Initialize the watchdog.

        Args:
            config: RCAN config dict.
            stop_fn: Callable to invoke when the watchdog triggers
                     (typically ``driver.stop``).
        """
        wd_cfg = config.get("watchdog", {})
        self.enabled = wd_cfg.get("enabled", True)  # Enabled by default
        self.timeout = wd_cfg.get("timeout_s", 10.0)
        self.action = wd_cfg.get("action", "stop")

        self._stop_fn = stop_fn
        self._last_heartbeat = time.time()
        self._triggered = False
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

        if self.enabled:
            logger.info(f"Watchdog active: {self.timeout}s timeout")

    def heartbeat(self):
        """Call this after every successful brain response."""
        with self._lock:
            self._last_heartbeat = time.time()
            if self._triggered:
                self._triggered = False
                logger.info("Watchdog: brain responsive again")

    def start(self):
        """Start the watchdog timer thread."""
        if not self.enabled:
            return

        self._running = True
        self._last_heartbeat = time.time()
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="watchdog"
        )
        self._thread.start()

    def stop(self):
        """Stop the watchdog thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _monitor_loop(self):
        """Background thread that checks for brain timeouts."""
        while self._running:
            with self._lock:
                elapsed = time.time() - self._last_heartbeat

            if elapsed > self.timeout and not self._triggered:
                self._triggered = True
                logger.critical(
                    f"WATCHDOG: Brain unresponsive for {elapsed:.1f}s "
                    f"(timeout: {self.timeout}s) -- stopping motors!"
                )
                if self._stop_fn:
                    try:
                        self._stop_fn()
                    except Exception as exc:
                        logger.error(f"Watchdog stop failed: {exc}")

            time.sleep(1.0)

    @property
    def is_triggered(self) -> bool:
        """True if the watchdog has triggered (brain unresponsive)."""
        with self._lock:
            return self._triggered

    def get_status(self) -> dict:
        """Return watchdog status for telemetry."""
        with self._lock:
            elapsed = time.time() - self._last_heartbeat
        return {
            "enabled": self.enabled,
            "timeout_s": self.timeout,
            "last_heartbeat_s_ago": round(elapsed, 1),
            "triggered": self._triggered,
        }
