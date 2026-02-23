"""
Matrix / Element channel — federated open messaging.

Uses the matrix-nio async library to join rooms and exchange messages.
Supports optional E2E encryption when `matrix-nio[e2e]` is installed.

Env:
  MATRIX_HOMESERVER_URL   — e.g. https://matrix.org  (or self-hosted)
  MATRIX_USER_ID          — @yourbot:matrix.org
  MATRIX_ACCESS_TOKEN     — from POST /_matrix/client/v3/login

Install: pip install matrix-nio

Setup:
  1. Register a Matrix account for the bot
  2. Obtain an access token:
       curl -X POST https://matrix.org/_matrix/client/v3/login \\
         -d '{"type":"m.login.password","user":"bot","password":"..."}'
  3. Set MATRIX_HOMESERVER_URL, MATRIX_USER_ID, MATRIX_ACCESS_TOKEN in .env
"""

import asyncio
import logging
from typing import Callable, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.Matrix")

try:
    from nio import AsyncClient, MatrixRoom, RoomMessageText

    HAS_NIO = True
except ImportError:
    HAS_NIO = False
    # Stub types so type annotations don't crash at import time
    MatrixRoom = object
    RoomMessageText = object


class MatrixChannel(BaseChannel):
    """Matrix/Element federated messaging channel via matrix-nio."""

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)
        self._homeserver: str = config.get("homeserver_url", "https://matrix.org")
        self._user_id: str = config.get("user_id", "")
        self._access_token: str = config.get("access_token", "")
        self._client: Optional[object] = None
        self._running = False

    async def start(self):
        if not HAS_NIO:
            logger.warning(
                "matrix-nio not installed — Matrix channel unavailable (pip install matrix-nio)"
            )
            return
        if not self._user_id or not self._access_token:
            logger.warning("MATRIX_USER_ID / MATRIX_ACCESS_TOKEN not set")
            return

        self._client = AsyncClient(self._homeserver, self._user_id)
        self._client.access_token = self._access_token
        self._client.add_event_callback(self._on_room_message, RoomMessageText)
        self._running = True
        logger.info("Matrix channel started as %s on %s", self._user_id, self._homeserver)

        try:
            await self._client.sync_forever(timeout=30_000, full_state=True)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Matrix sync error: %s", exc)

    async def stop(self):
        self._running = False
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
        logger.info("Matrix channel stopped")

    async def send_message(self, room_id: str, text: str):
        """Send a plain-text message to *room_id*."""
        if self._client is None:
            logger.warning("Matrix client not ready — cannot send message")
            return
        try:
            await self._client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": text},
            )
        except Exception as exc:
            logger.error("Matrix send_message error: %s", exc)

    async def _on_room_message(self, room: MatrixRoom, event: RoomMessageText):
        """Handle incoming room message events."""
        # Ignore messages from the bot itself
        if event.sender == self._user_id:
            return

        text = (getattr(event, "body", "") or "").strip()
        sender = event.sender
        room_id = room.room_id if hasattr(room, "room_id") else str(room)

        logger.debug("Matrix msg from %s in %s: %s", sender, room_id, text[:80])
        if self._on_message_callback and text:
            try:
                reply = self._on_message_callback("matrix", room_id, text)
                if reply:
                    await self.send_message(room_id, reply)
            except Exception as exc:
                logger.error("Matrix on_message error: %s", exc)
