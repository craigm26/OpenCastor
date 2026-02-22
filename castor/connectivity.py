"""
castor.connectivity — Internet and provider reachability checks.

Provides lightweight, fast connectivity probes so the gateway can detect
when the internet is down and switch to an offline fallback provider.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Callable, Optional

logger = logging.getLogger("OpenCastor.Connectivity")

# DNS hosts to probe — we check two to avoid false negatives
_DNS_PROBES = ["1.1.1.1", "8.8.8.8"]

# Per-provider API hostnames
_PROVIDER_HOSTS: dict[str, str] = {
    "anthropic": "api.anthropic.com",
    "openai": "api.openai.com",
    "huggingface": "router.huggingface.co",
    "google": "generativelanguage.googleapis.com",
    "ollama": "localhost",  # local — always "reachable" by definition
    "llamacpp": "localhost",
    "mlx": "localhost",
}


# ── Fast probes ────────────────────────────────────────────────────────────────


def is_online(timeout: float = 3.0) -> bool:
    """Return True if internet connectivity is available.

    Uses a TCP connect to two well-known DNS resolvers (port 53).
    Fast, has no external dependencies, works even if HTTP is blocked.
    """
    for host in _DNS_PROBES:
        try:
            sock = socket.create_connection((host, 53), timeout=timeout)
            sock.close()
            return True
        except OSError:
            continue
    return False


def check_provider_reachable(provider_name: str, timeout: float = 5.0) -> bool:
    """Return True if the given provider's API endpoint is reachable.

    Local providers (ollama, llamacpp, mlx) always return True.
    """
    host = _PROVIDER_HOSTS.get(provider_name.lower(), "")
    if not host or host == "localhost":
        return True  # local — assume available
    try:
        sock = socket.create_connection((host, 443), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False


# ── Background monitor ─────────────────────────────────────────────────────────


class ConnectivityMonitor:
    """Background thread that polls internet connectivity and fires callbacks.

    Usage::

        def on_change(online: bool):
            print("Internet", "back" if online else "down")

        monitor = ConnectivityMonitor(on_change=on_change, interval=30)
        monitor.start()
        ...
        monitor.stop()
    """

    def __init__(
        self,
        on_change: Optional[Callable[[bool], None]] = None,
        interval: float = 30.0,
        timeout: float = 3.0,
    ):
        self._on_change = on_change
        self._interval = interval
        self._timeout = timeout
        self._last_state: Optional[bool] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background monitoring thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="connectivity-monitor", daemon=True)
        self._thread.start()
        logger.debug("ConnectivityMonitor started (interval=%.0fs)", self._interval)

    def stop(self) -> None:
        """Stop the background monitoring thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    @property
    def online(self) -> Optional[bool]:
        """Last known connectivity state. None if not yet checked."""
        return self._last_state

    def _run(self) -> None:
        while not self._stop_event.is_set():
            current = is_online(self._timeout)
            if current != self._last_state:
                prev = self._last_state
                self._last_state = current
                status = "online" if current else "offline"
                if prev is not None:
                    logger.info("Connectivity changed: %s", status)
                else:
                    logger.info("Connectivity: %s", status)
                if self._on_change:
                    try:
                        self._on_change(current)
                    except Exception as exc:
                        logger.error("on_change callback error: %s", exc)
            self._stop_event.wait(self._interval)
