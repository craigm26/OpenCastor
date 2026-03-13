"""
Microsoft Teams channel — outbound webhook + inbound bot activity.

Outbound: sends adaptive cards / message-cards via incoming webhook URL.
Inbound:  handles Bot Framework Activity payloads from /webhooks/teams endpoint.

Env:
  TEAMS_WEBHOOK_URL    — incoming webhook URL (outbound notifications)
  TEAMS_APP_ID         — Azure AD App ID (for bot auth)
  TEAMS_APP_PASSWORD   — Azure AD App Password
  TEAMS_TENANT_ID      — Azure tenant ID

Install: pip install aiohttp   (already a dep; no extra package needed)

Setup:
  1. In Teams admin, add "Incoming Webhook" connector to a channel → copy URL
  2. For two-way bot: register app in Azure AD, enable Bot channel, add Teams
"""

import logging
from typing import Optional
from collections.abc import Callable

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.Teams")

try:
    import aiohttp

    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


class TeamsChannel(BaseChannel):
    """Microsoft Teams channel (webhook + bot framework)."""

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)
        self._webhook_url: str = config.get("webhook_url", "")
        self._app_id: str = config.get("app_id", "")
        self._app_password: str = config.get("app_password", "")
        self._running = False

    async def start(self):
        self._running = True
        if not HAS_AIOHTTP:
            logger.warning("aiohttp not installed — Teams channel unavailable")
            return
        logger.info(
            "Teams channel started (webhook=%s, bot=%s)",
            bool(self._webhook_url),
            bool(self._app_id),
        )

    async def stop(self):
        self._running = False
        logger.info("Teams channel stopped")

    async def send_message(self, chat_id: str, text: str):
        """Send a message to Teams via incoming webhook.

        *chat_id* is ignored for webhook mode; it is used as the service URL
        base when replying to a specific bot conversation.
        """
        if not self._webhook_url:
            logger.warning("TEAMS_WEBHOOK_URL not configured — cannot send message")
            return
        if not HAS_AIOHTTP:
            logger.warning("aiohttp not installed — Teams send skipped")
            return

        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "0076D7",
            "summary": text[:72],
            "sections": [{"activityText": text}],
        }

        async def _check_response(resp) -> None:
            if resp.status not in (200, 202):
                body = await resp.text()
                logger.error("Teams webhook HTTP %s: %s", resp.status, body[:200])

        try:
            async with aiohttp.ClientSession() as sess:
                post_result = sess.post(
                    self._webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                if hasattr(post_result, "__aenter__"):
                    async with post_result as resp:
                        await _check_response(resp)
                else:
                    resp = await post_result
                    await _check_response(resp)
        except Exception as exc:
            logger.error("Teams send_message error: %s", exc)

    def handle_bot_activity(self, payload: dict) -> str:
        """Process an inbound Teams Bot Framework Activity.

        Called by the /webhooks/teams API endpoint.
        Returns the reply text (empty string = no reply).
        """
        activity_type = payload.get("type", "")
        if activity_type != "message":
            return ""

        text = (payload.get("text") or "").strip()
        from_info = payload.get("from", {})
        sender = from_info.get("name") or from_info.get("id", "unknown")
        conv_id = payload.get("conversation", {}).get("id", "teams")

        logger.debug("Teams inbound from %s: %s", sender, text[:80])
        if self._on_message_callback and text:
            try:
                reply = self._on_message_callback("teams", conv_id, text)
                return reply or ""
            except Exception as exc:
                logger.error("Teams on_message error: %s", exc)
        return ""
