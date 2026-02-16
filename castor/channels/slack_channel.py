"""
Slack channel integration via slack-bolt.

Setup:
    1. Create a Slack app at https://api.slack.com/apps
    2. Enable Socket Mode and create an App-Level Token (xapp-...)
    3. Add bot scopes: chat:write, app_mentions:read, im:read, im:history
    4. Install the app to your workspace
    5. Set SLACK_BOT_TOKEN, SLACK_APP_TOKEN in .env
"""

import asyncio
import logging
from typing import Callable, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.Slack")

try:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    HAS_SLACK = True
except ImportError:
    HAS_SLACK = False


class SlackChannel(BaseChannel):
    """Slack bot integration using Socket Mode."""

    name = "slack"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)

        if not HAS_SLACK:
            raise ImportError(
                "slack-bolt required for Slack. Install with: "
                "pip install 'opencastor[slack]'"
            )

        self.bot_token = config.get("bot_token")
        self.app_token = config.get("app_token")
        if not self.bot_token or not self.app_token:
            raise ValueError(
                "SLACK_BOT_TOKEN and SLACK_APP_TOKEN are required. "
                "Set them in your .env file."
            )

        self.app = AsyncApp(token=self.bot_token)
        self.handler: Optional[AsyncSocketModeHandler] = None
        self._setup_handlers()
        self.logger.info("Slack channel initialized")

    def _setup_handlers(self):
        @self.app.event("app_mention")
        async def handle_mention(event, say):
            text = event.get("text", "").strip()
            chat_id = event.get("channel", "")
            # Strip the bot mention
            if "<@" in text:
                text = text.split(">", 1)[-1].strip()

            if not text:
                return

            reply = await self.handle_message(chat_id, text)
            if reply:
                await say(reply[:4000])

        @self.app.event("message")
        async def handle_dm(event, say):
            # Only handle DMs (no subtype = direct message)
            if event.get("channel_type") != "im":
                return
            if event.get("subtype"):
                return

            text = event.get("text", "").strip()
            chat_id = event.get("channel", "")

            if not text:
                return

            reply = await self.handle_message(chat_id, text)
            if reply:
                await say(reply[:4000])

    async def start(self):
        """Start the Slack Socket Mode handler."""
        self.handler = AsyncSocketModeHandler(self.app, self.app_token)
        await self.handler.start_async()
        self.logger.info("Slack bot connected via Socket Mode")

    async def stop(self):
        if self.handler:
            await self.handler.close_async()
            self.logger.info("Slack bot stopped")

    async def send_message(self, chat_id: str, text: str):
        await self.app.client.chat_postMessage(channel=chat_id, text=text[:4000])
