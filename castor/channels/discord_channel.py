"""
Discord channel integration via discord.py.

Setup:
    1. Create a Discord application at https://discord.com/developers
    2. Create a bot and copy the token
    3. Enable MESSAGE CONTENT intent in the bot settings
    4. Set DISCORD_BOT_TOKEN in .env
    5. Invite the bot to your server with the generated OAuth2 URL
"""

import asyncio
import logging
from typing import Callable, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.Discord")

try:
    import discord

    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False


class DiscordChannel(BaseChannel):
    """Discord bot integration."""

    name = "discord"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)

        if not HAS_DISCORD:
            raise ImportError(
                "discord.py required for Discord. Install with: pip install 'opencastor[discord]'"
            )

        self.bot_token = config.get("bot_token")
        if not self.bot_token:
            raise ValueError("DISCORD_BOT_TOKEN is required. Set it in your .env file.")

        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)
        self._setup_handlers()
        self.logger.info("Discord channel initialized")

    def _setup_handlers(self):
        @self.client.event
        async def on_ready():
            self.logger.info(f"Discord bot connected as {self.client.user}")

        @self.client.event
        async def on_message(message: discord.Message):
            # Ignore own messages
            if message.author == self.client.user:
                return

            # Only respond when mentioned or in DMs
            is_dm = isinstance(message.channel, discord.DMChannel)
            is_mentioned = self.client.user in message.mentions

            if not is_dm and not is_mentioned:
                return

            text = message.content
            # Strip the bot mention from the message
            if is_mentioned:
                text = text.replace(f"<@{self.client.user.id}>", "").strip()

            if not text:
                return

            chat_id = str(message.channel.id)
            reply = await self.handle_message(chat_id, text)
            if reply:
                # Discord has a 2000 char limit
                for i in range(0, len(reply), 2000):
                    await message.channel.send(reply[i : i + 2000])

    async def start(self):
        """Start the Discord bot in the background."""
        asyncio.create_task(self.client.start(self.bot_token))
        self.logger.info("Discord bot starting...")

    async def stop(self):
        await self.client.close()
        self.logger.info("Discord bot stopped")

    async def send_message(self, chat_id: str, text: str):
        channel = self.client.get_channel(int(chat_id))
        if channel:
            for i in range(0, len(text), 2000):
                await channel.send(text[i : i + 2000])
