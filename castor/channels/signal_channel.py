"""
castor/channels/signal_channel.py — Signal Messenger channel integration.

Connects to the signal-cli JSON-RPC REST API to send and receive messages.
The signal-cli daemon must be running and exposing the REST API endpoint.

Setup:
    1. Install signal-cli: https://github.com/AsamK/signal-cli
    2. Register your phone number: signal-cli -a +1234567890 register
    3. Start the JSON-RPC REST daemon:
       signal-cli -a +1234567890 daemon --http localhost:8080
    4. Set SIGNAL_API_URL and SIGNAL_SENDER in .env

Env:
    SIGNAL_API_URL    — signal-cli REST API base URL (default http://localhost:8080)
    SIGNAL_SENDER     — Your registered Signal number (e.g. +12025551234)
    SIGNAL_RECIPIENT  — Default recipient number or group ID (optional)
    SIGNAL_POLL_INTERVAL_S — Polling interval in seconds (default 1.0)

Config keys (RCAN channels block):
    api_url:          signal-cli API URL
    sender:           Registered sender phone number
    recipient:        Default recipient phone number or group ID
    poll_interval_s:  Polling interval in seconds

Install: pip install requests (stdlib fallback available for sending)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional
from collections.abc import Callable

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.Signal")

HAS_REQUESTS = False
try:
    import requests  # type: ignore[import]

    HAS_REQUESTS = True
except ImportError:
    pass


class SignalChannel(BaseChannel):
    """Signal Messenger integration via signal-cli REST API.

    Polls the signal-cli daemon for incoming messages and forwards them to the
    robot's ``on_message`` callback.  Replies are sent back to the originating
    number or group.

    Args:
        config: Channel configuration dict with optional keys:

            * ``api_url`` (str) — signal-cli REST API base URL.
            * ``sender`` (str) — Registered sender phone number.
            * ``recipient`` (str) — Default recipient number or group ID.
            * ``poll_interval_s`` (float) — Polling interval in seconds.

        on_message: Callback invoked for each incoming message.
    """

    name = "signal"

    def __init__(self, config: dict, on_message: Optional[Callable] = None) -> None:
        super().__init__(config, on_message)

        self._api_url: str = (
            config.get("api_url", "") or os.getenv("SIGNAL_API_URL", "http://localhost:8080")
        ).rstrip("/")
        self._sender: str = config.get("sender", "") or os.getenv("SIGNAL_SENDER", "")
        self._recipient: str = config.get("recipient", "") or os.getenv("SIGNAL_RECIPIENT", "")
        self._poll_interval_s: float = float(
            config.get("poll_interval_s", os.getenv("SIGNAL_POLL_INTERVAL_S", "1.0"))
        )

        self._running: bool = False
        self._poll_thread: Optional[threading.Thread] = None
        self._last_received_ts: float = time.time()

        logger.info(
            "Signal channel: api_url=%s sender=%s poll_interval=%.1fs",
            self._api_url,
            self._sender or "(not set)",
            self._poll_interval_s,
        )

    # ── Internal HTTP helpers ─────────────────────────────────────────────────

    def _post(self, path: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """POST a JSON payload to the signal-cli REST API.

        Args:
            path:    API path (e.g. ``"/v2/send"``).
            payload: JSON-serialisable dict.

        Returns:
            Parsed response JSON dict, or ``None`` on error.
        """
        url = f"{self._api_url}{path}"
        try:
            if HAS_REQUESTS:
                resp = requests.post(url, json=payload, timeout=5.0)
                resp.raise_for_status()
                try:
                    return resp.json()
                except Exception:
                    return {"status": "ok"}
            else:
                import urllib.request as _urllib_req

                data = json.dumps(payload).encode("utf-8")
                req = _urllib_req.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with _urllib_req.urlopen(req, timeout=5.0) as r:
                    body = r.read()
                try:
                    return json.loads(body)
                except Exception:
                    return {"status": "ok"}
        except Exception as exc:
            logger.error("Signal channel: POST %s failed: %s", path, exc)
            return None

    def _get(self, path: str) -> Optional[Any]:
        """GET from the signal-cli REST API.

        Args:
            path: API path (e.g. ``"/v1/receive/+12025551234"``).

        Returns:
            Parsed JSON body, or ``None`` on error.
        """
        url = f"{self._api_url}{path}"
        try:
            if HAS_REQUESTS:
                resp = requests.get(url, timeout=5.0)
                resp.raise_for_status()
                return resp.json()
            else:
                import urllib.request as _urllib_req

                with _urllib_req.urlopen(url, timeout=5.0) as r:
                    return json.loads(r.read())
        except Exception as exc:
            logger.debug("Signal channel: GET %s failed: %s", path, exc)
            return None

    # ── Message receive polling ───────────────────────────────────────────────

    def _receive_messages(self) -> list[dict[str, Any]]:
        """Fetch pending messages from the signal-cli daemon.

        Returns:
            List of message envelope dicts.  Empty list on error.
        """
        if not self._sender:
            return []
        result = self._get(f"/v1/receive/{self._sender}")
        if isinstance(result, list):
            return result
        return []

    def _poll_loop(self) -> None:
        """Background thread: poll for incoming messages and dispatch callbacks."""
        logger.info("Signal channel: poll loop started (interval=%.1fs)", self._poll_interval_s)
        while self._running:
            try:
                messages = self._receive_messages()
                for envelope in messages:
                    try:
                        self._dispatch_envelope(envelope)
                    except Exception as exc:
                        logger.warning("Signal channel: dispatch error: %s", exc)
            except Exception as exc:
                logger.warning("Signal channel: poll error: %s", exc)
            time.sleep(self._poll_interval_s)
        logger.info("Signal channel: poll loop stopped")

    def _dispatch_envelope(self, envelope: dict[str, Any]) -> None:
        """Parse a signal-cli message envelope and invoke the on_message callback.

        Args:
            envelope: Raw message envelope dict from signal-cli.
        """
        # signal-cli REST v1 envelope structure
        data_msg = envelope.get("dataMessage") or envelope.get("data_message") or {}
        sender = envelope.get("source", "") or envelope.get("sender", "")
        text = data_msg.get("message", "") or data_msg.get("body", "")

        if not text or not sender:
            return

        chat_id = sender
        logger.info("Signal channel: received message from %s: %r", sender, text[:100])

        if not self._check_rate_limit(chat_id):
            logger.warning("Signal channel: rate limit hit for %s", chat_id)
            return

        if self._on_message_callback is not None:
            try:
                reply = self._on_message_callback("signal", chat_id, text)
                if reply:
                    self.send_message(reply, recipient=sender)
            except Exception as exc:
                logger.error("Signal channel: on_message_callback error: %s", exc)

    # ── BaseChannel interface ─────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background message-polling thread."""
        if self._running:
            logger.debug("Signal channel: already running")
            return
        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="SignalChannel-poll",
        )
        self._poll_thread.start()
        logger.info("Signal channel: started (sender=%s)", self._sender)

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._running = False
        if self._poll_thread is not None and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=3.0)
        logger.info("Signal channel: stopped")

    def send_message(
        self,
        message: str,
        recipient: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> bool:
        """Send a text message via signal-cli REST API.

        Args:
            message:   Text message to send.
            recipient: Phone number to send to (overrides config default).
            group_id:  Signal group ID to send to (overrides recipient).

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        target = recipient or self._recipient
        payload: dict[str, Any] = {
            "message": message,
            "number": self._sender,
        }
        if group_id:
            payload["group_id"] = group_id
        elif target:
            payload["recipients"] = [target]
        else:
            logger.warning("Signal channel: no recipient for message — dropping")
            return False

        result = self._post("/v2/send", payload)
        if result is not None:
            logger.debug("Signal channel: sent message to %s", target or group_id)
            return True
        return False
