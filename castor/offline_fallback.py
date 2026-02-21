"""
castor.offline_fallback — Automatic provider switching when internet is unavailable.

When the primary AI provider (e.g. Anthropic, HuggingFace) requires internet
and connectivity is lost, OfflineFallbackManager swaps to a local provider
(Ollama, LlamaCpp, or MLX) so the robot stays responsive.

RCAN config block::

    offline_fallback:
      enabled: true
      provider: ollama          # ollama | llamacpp | mlx
      model: llama3.2:3b        # model to use for the fallback provider
      check_interval_s: 30      # connectivity check frequency (seconds)
      alert_channel: whatsapp   # channel to notify on switch (optional)
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from castor.connectivity import ConnectivityMonitor, is_online

logger = logging.getLogger("OpenCastor.OfflineFallback")

# Providers that work fully offline (local inference)
_LOCAL_PROVIDERS = {"ollama", "llamacpp", "mlx"}


class OfflineFallbackManager:
    """Manages primary ↔ fallback provider switching based on connectivity.

    Usage (from api.py on_startup)::

        fallback = OfflineFallbackManager(
            config=state.config,
            primary_provider=state.brain,
            channel_send_fn=lambda text: channel.send_message(chat_id, text),
        )
        fallback.start()
        state.offline_fallback = fallback

    Then replace ``state.brain.think(...)`` with::

        state.offline_fallback.get_active_provider().think(...)
    """

    def __init__(
        self,
        config: Dict[str, Any],
        primary_provider,  # BaseProvider instance
        channel_send_fn: Optional[Callable[[str], None]] = None,
    ):
        self._config = config.get("offline_fallback", {})
        self._primary = primary_provider
        self._fallback = None
        self._channel_send = channel_send_fn
        self._using_fallback = False
        self._fallback_ready = False
        self._monitor: Optional[ConnectivityMonitor] = None

        # Build fallback provider if configured
        if self._config.get("enabled") and self._config.get("provider"):
            self._fallback = self._build_fallback_provider()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the connectivity monitor and probe the fallback provider."""
        if self._fallback is None:
            return  # nothing to manage
        # Probe at startup so operators know immediately if fallback is broken
        self.probe_fallback()
        interval = float(self._config.get("check_interval_s", 30))
        self._monitor = ConnectivityMonitor(
            on_change=self._on_connectivity_change,
            interval=interval,
        )
        self._monitor.start()
        # Do an immediate check
        online = is_online()
        self._apply_state(online, initial=True)

    def stop(self) -> None:
        if self._monitor:
            self._monitor.stop()

    # ── Provider access ───────────────────────────────────────────────────────

    def get_active_provider(self):
        """Return whichever provider is currently active (primary or fallback)."""
        if self._using_fallback and self._fallback:
            return self._fallback
        return self._primary

    @property
    def is_using_fallback(self) -> bool:
        return self._using_fallback

    @property
    def fallback_ready(self) -> bool:
        """True if the fallback provider has been probed and is responding."""
        return self._fallback_ready

    def probe_fallback(self) -> bool:
        """Probe the fallback provider with a lightweight health check.

        Called at ``start()`` so operators discover unreachable fallbacks at
        startup rather than during a live outage.  Returns True if reachable.
        """
        if self._fallback is None:
            self._fallback_ready = False
            return False
        try:
            result = self._fallback.health_check()
            self._fallback_ready = bool(result.get("ok", False))
            if self._fallback_ready:
                logger.info(
                    "Offline fallback probe OK (%.0fms)",
                    result.get("latency_ms", 0),
                )
            else:
                logger.warning(
                    "Offline fallback probe failed: %s",
                    result.get("error", "unknown"),
                )
        except Exception as exc:
            logger.warning("Offline fallback probe exception: %s", exc)
            self._fallback_ready = False
        return self._fallback_ready

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_connectivity_change(self, online: bool) -> None:
        self._apply_state(online, initial=False)

    def _apply_state(self, online: bool, initial: bool = False) -> None:
        primary_name = type(self._primary).__name__.replace("Provider", "")
        fallback_name = self._config.get("provider", "local")
        fallback_model = self._config.get("model", "")
        alert_channel = self._config.get("alert_channel", "")

        if online:
            if self._using_fallback or initial:
                self._using_fallback = False
                if not initial:
                    msg = (
                        f"Internet restored. Switched back to {primary_name}. "
                        f"(Offline fallback was: {fallback_name}/{fallback_model})"
                    )
                    logger.info(msg)
                    self._notify(alert_channel, msg)
                else:
                    logger.info("Connectivity: online — using %s", primary_name)
        else:
            if not self._using_fallback:
                self._using_fallback = True
                msg = (
                    f"Internet unavailable. Switching to offline fallback: "
                    f"{fallback_name}/{fallback_model}"
                )
                logger.warning(msg)
                if not initial:
                    self._notify(alert_channel, msg)
            else:
                if initial:
                    logger.warning(
                        "Starting offline — using fallback: %s/%s",
                        fallback_name, fallback_model,
                    )

    def _notify(self, channel: str, text: str) -> None:
        if channel and self._channel_send:
            try:
                self._channel_send(text)
            except Exception as exc:
                logger.debug("Notification error: %s", exc)

    def _build_fallback_provider(self):
        """Instantiate the configured offline fallback provider."""
        from castor.providers import get_provider

        fallback_config = {
            "provider": self._config.get("provider", "ollama"),
            "model":    self._config.get("model", "llama3.2:3b"),
        }
        try:
            provider = get_provider(fallback_config)
            logger.info(
                "Offline fallback ready: %s/%s",
                fallback_config["provider"],
                fallback_config["model"],
            )
            return provider
        except Exception as exc:
            logger.warning("Could not init offline fallback provider: %s", exc)
            return None
