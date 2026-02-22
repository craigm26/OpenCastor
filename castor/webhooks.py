"""Outbound webhook notifications for OpenCastor.

Dispatches POST requests to external URLs on robot events such as
episode_complete, estop, command, error, and startup.

Configuration (RCAN ``webhooks:`` block)::

    webhooks:
      - url: https://example.com/robot-events
        events: [episode_complete, estop, error]
        secret: my-hmac-secret   # optional — adds X-Castor-Signature header
        timeout_s: 5
        retry: 2

Environment:
    CASTOR_WEBHOOK_TIMEOUT  — Default per-request timeout in seconds (default: 5)
"""

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional
from urllib.error import URLError

logger = logging.getLogger("OpenCastor.Webhooks")

# Supported event types
WEBHOOK_EVENTS = {
    "startup",
    "episode_complete",
    "estop",
    "estop_clear",
    "command",
    "error",
    "provider_switch",
    "behavior_start",
    "behavior_stop",
}

_DEFAULT_TIMEOUT = int(os.getenv("CASTOR_WEBHOOK_TIMEOUT", "5"))


def _sign_payload(payload: bytes, secret: str) -> str:
    """Return HMAC-SHA256 hex digest for payload."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _dispatch_one(
    url: str,
    event: str,
    data: Dict[str, Any],
    secret: Optional[str],
    timeout: int,
    retry: int,
) -> bool:
    """POST a single webhook. Returns True on success."""
    body = json.dumps(
        {
            "event": event,
            "timestamp": time.time(),
            "data": data,
        }
    ).encode()

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "OpenCastor-Webhook/1.0",
        "X-Castor-Event": event,
    }
    if secret:
        headers["X-Castor-Signature"] = f"sha256={_sign_payload(body, secret)}"

    for attempt in range(max(1, retry + 1)):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout):
                pass
            logger.debug("Webhook OK: %s event=%s attempt=%d", url, event, attempt)
            return True
        except (URLError, OSError) as exc:
            logger.warning(
                "Webhook attempt %d/%d failed: %s event=%s error=%s",
                attempt + 1,
                retry + 1,
                url,
                event,
                exc,
            )
            if attempt < retry:
                time.sleep(0.5 * (attempt + 1))
    return False


class WebhookDispatcher:
    """Fire-and-forget webhook dispatcher.

    Instantiate once and call :meth:`emit` whenever a robot event occurs.
    All HTTP calls happen in background daemon threads so the main loop is
    never blocked.

    Args:
        webhooks: List of webhook config dicts with keys:
            - ``url`` (required)
            - ``events`` (list of event names; ``["*"]`` matches all)
            - ``secret`` (optional HMAC secret)
            - ``timeout_s`` (int, default 5)
            - ``retry`` (int retries on failure, default 1)
    """

    def __init__(self, webhooks: Optional[List[Dict[str, Any]]] = None):
        self._hooks: List[Dict[str, Any]] = []
        for hook in webhooks or []:
            url = hook.get("url", "").strip()
            if not url:
                continue
            events = hook.get("events", ["*"])
            if isinstance(events, str):
                events = [events]
            self._hooks.append(
                {
                    "url": url,
                    "events": set(events),
                    "secret": hook.get("secret"),
                    "timeout": int(hook.get("timeout_s", _DEFAULT_TIMEOUT)),
                    "retry": int(hook.get("retry", 1)),
                }
            )
        logger.info("WebhookDispatcher initialized with %d hook(s)", len(self._hooks))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_hook(
        self,
        url: str,
        events: Optional[List[str]] = None,
        secret: Optional[str] = None,
        timeout_s: int = _DEFAULT_TIMEOUT,
        retry: int = 1,
    ) -> None:
        """Dynamically register a new webhook at runtime."""
        if not url.strip():
            raise ValueError("Webhook URL must not be empty")
        self._hooks.append(
            {
                "url": url.strip(),
                "events": set(events or ["*"]),
                "secret": secret,
                "timeout": timeout_s,
                "retry": retry,
            }
        )
        logger.info("Webhook added: %s events=%s", url, events)

    def remove_hook(self, url: str) -> bool:
        """Remove a webhook by URL. Returns True if found and removed."""
        before = len(self._hooks)
        self._hooks = [h for h in self._hooks if h["url"] != url]
        return len(self._hooks) < before

    def list_hooks(self) -> List[Dict[str, Any]]:
        """Return registered hooks (without secret values)."""
        return [
            {
                "url": h["url"],
                "events": sorted(h["events"]),
                "timeout_s": h["timeout"],
                "retry": h["retry"],
                "has_secret": bool(h.get("secret")),
            }
            for h in self._hooks
        ]

    def emit(self, event: str, data: Optional[Dict[str, Any]] = None) -> int:
        """Fire webhooks matching *event* asynchronously.

        Args:
            event: Event name (see WEBHOOK_EVENTS).
            data: Optional dict with event-specific payload.

        Returns:
            Number of webhooks dispatched (threads started).
        """
        if event not in WEBHOOK_EVENTS:
            logger.debug("Unknown webhook event '%s', emitting anyway", event)

        payload = data or {}
        dispatched = 0
        for hook in self._hooks:
            ev_set = hook["events"]
            if "*" not in ev_set and event not in ev_set:
                continue
            t = threading.Thread(
                target=_dispatch_one,
                args=(
                    hook["url"],
                    event,
                    payload,
                    hook.get("secret"),
                    hook["timeout"],
                    hook["retry"],
                ),
                daemon=True,
                name=f"webhook-{event}",
            )
            t.start()
            dispatched += 1

        return dispatched

    def emit_sync(self, event: str, data: Optional[Dict[str, Any]] = None) -> List[bool]:
        """Synchronous version of :meth:`emit` — blocks until all POSTs finish.

        Useful for testing or shutdown hooks.
        """
        payload = data or {}
        results = []
        for hook in self._hooks:
            ev_set = hook["events"]
            if "*" not in ev_set and event not in ev_set:
                continue
            ok = _dispatch_one(
                hook["url"],
                event,
                payload,
                hook.get("secret"),
                hook["timeout"],
                hook["retry"],
            )
            results.append(ok)
        return results


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_dispatcher: Optional[WebhookDispatcher] = None


def get_dispatcher() -> WebhookDispatcher:
    """Return the process-wide WebhookDispatcher (lazily created)."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = WebhookDispatcher([])
    return _dispatcher


def init_from_config(config: Dict[str, Any]) -> WebhookDispatcher:
    """Initialize the global dispatcher from RCAN config.

    Call once at gateway startup.

    Args:
        config: Parsed RCAN config dict (looks for top-level ``webhooks`` list).

    Returns:
        The initialized dispatcher.
    """
    global _dispatcher
    hooks = config.get("webhooks", [])
    _dispatcher = WebhookDispatcher(hooks)
    return _dispatcher
