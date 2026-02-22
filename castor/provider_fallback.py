"""
castor.provider_fallback — Automatic provider switching on quota/credit exhaustion.

When the primary AI provider (e.g. HuggingFace) exhausts its free credits or
hits a billing limit, ``ProviderFallbackManager`` transparently switches to a
configured backup provider so the robot stays responsive.

RCAN config block::

    provider_fallback:
      enabled: true
      provider: ollama           # ollama | llamacpp | mlx | google | openai | anthropic
      model: llama3.2:3b         # model to use on the fallback provider
      quota_cooldown_s: 3600     # seconds before retrying the primary (default 1 hour)
      alert_channel: telegram    # channel to notify on switch (optional)

Usage — primary provider call sites replace::

    thought = state.brain.think(image_bytes, instruction)

with::

    thought = state.provider_fallback.think(image_bytes, instruction)

Or use ``get_active_provider()`` to get whichever provider is currently live.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from castor.providers.base import ProviderQuotaError

logger = logging.getLogger("OpenCastor.ProviderFallback")


class ProviderFallbackManager:
    """Wraps the primary provider and auto-switches to a backup on quota errors.

    Args:
        config:           Full RCAN config dict (reads ``provider_fallback`` block).
        primary_provider: The primary ``BaseProvider`` instance.
        channel_send_fn:  Optional callable ``(text: str) -> None`` for alerts.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        primary_provider,
        channel_send_fn: Optional[Callable[[str], None]] = None,
    ):
        self._fb_cfg = config.get("provider_fallback", {})
        self._primary = primary_provider
        self._fallback = None
        self._channel_send = channel_send_fn
        self._using_fallback = False
        self._fallback_ready = False
        self._quota_hit_time: float = 0.0
        self._lock = threading.Lock()

        self._quota_cooldown = float(self._fb_cfg.get("quota_cooldown_s", 3600))
        self._alert_channel = self._fb_cfg.get("alert_channel", "")

        if self._fb_cfg.get("enabled") and self._fb_cfg.get("provider"):
            self._fallback = self._build_fallback()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def probe_fallback(self) -> bool:
        """Health-check the fallback provider at startup."""
        if self._fallback is None:
            return False
        try:
            result = self._fallback.health_check()
            self._fallback_ready = bool(result.get("ok", False))
            if self._fallback_ready:
                logger.info(
                    "Provider fallback probe OK — %s (%.0fms)",
                    self._fb_cfg.get("provider"),
                    result.get("latency_ms", 0),
                )
            else:
                logger.warning(
                    "Provider fallback probe failed: %s",
                    result.get("error", "unknown"),
                )
        except Exception as exc:
            logger.warning("Provider fallback probe error: %s", exc)
            self._fallback_ready = False
        return self._fallback_ready

    # ── Active provider access ─────────────────────────────────────────────────

    def get_active_provider(self):
        """Return the currently active provider (primary or fallback)."""
        with self._lock:
            if self._using_fallback and self._fallback:
                # Check if cooldown has elapsed — try switching back
                if time.time() - self._quota_hit_time >= self._quota_cooldown:
                    logger.info(
                        "Provider fallback: cooldown elapsed (%.0fs) — retrying primary",
                        self._quota_cooldown,
                    )
                    self._using_fallback = False
                else:
                    return self._fallback
            return self._primary

    @property
    def is_using_fallback(self) -> bool:
        return self._using_fallback

    @property
    def fallback_ready(self) -> bool:
        return self._fallback_ready

    # ── Transparent wrappers ───────────────────────────────────────────────────

    def health_check(self) -> dict:
        """Delegate health_check() to whichever provider is currently active."""
        return self.get_active_provider().health_check()

    def think(self, image_bytes: bytes, instruction: str, **kwargs):
        """Call the active provider's think(); auto-switch on ProviderQuotaError."""
        provider = self.get_active_provider()
        try:
            return provider.think(image_bytes, instruction, **kwargs)
        except ProviderQuotaError as exc:
            logger.warning(
                "ProviderQuotaError from %s (HTTP %d): %s — switching to fallback",
                exc.provider_name or type(provider).__name__,
                exc.http_status,
                exc,
            )
            switched = self._on_quota_error(exc)
            if switched and self._fallback:
                return self._fallback.think(image_bytes, instruction, **kwargs)
            # No usable fallback — return error Thought
            from castor.providers.base import Thought

            return Thought(f"Quota exceeded and no fallback available: {exc}", None)

    def _on_quota_error(self, exc: ProviderQuotaError) -> bool:
        """Switch to fallback, record the time, notify operator."""
        with self._lock:
            if self._fallback is None:
                logger.error(
                    "ProviderQuotaError but no provider_fallback configured in RCAN config"
                )
                return False
            self._using_fallback = True
            self._quota_hit_time = time.time()

        primary_name = type(self._primary).__name__.replace("Provider", "")
        fb_name = self._fb_cfg.get("provider", "fallback")
        fb_model = self._fb_cfg.get("model", "")
        msg = (
            f"HuggingFace credits exhausted (HTTP {exc.http_status}). "
            f"Switched to fallback: {fb_name}/{fb_model}. "
            f"Will retry {primary_name} in {int(self._quota_cooldown / 3600)}h."
        )
        logger.warning(msg)
        self._notify(msg)
        return True

    # ── Internal ──────────────────────────────────────────────────────────────

    def _notify(self, text: str) -> None:
        if self._alert_channel and self._channel_send:
            try:
                self._channel_send(text)
            except Exception as exc:
                logger.debug("Provider fallback notification error: %s", exc)

    def _build_fallback(self):
        """Instantiate the configured fallback provider."""
        from castor.providers import get_provider

        fb_config = {
            "provider": self._fb_cfg.get("provider", "ollama"),
            "model": self._fb_cfg.get("model", "llama3.2:3b"),
        }
        try:
            provider = get_provider(fb_config)
            logger.info(
                "Provider fallback ready: %s/%s",
                fb_config["provider"],
                fb_config["model"],
            )
            return provider
        except Exception as exc:
            logger.warning("Could not init provider fallback: %s", exc)
            return None
