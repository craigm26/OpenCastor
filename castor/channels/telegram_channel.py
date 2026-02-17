"""
Telegram channel integration via python-telegram-bot.

Setup:
    1. Create a bot with @BotFather on Telegram
    2. Copy the bot token
    3. Set TELEGRAM_BOT_TOKEN in .env
    4. The bot uses long-polling by default (no webhook needed)
"""

import logging
from typing import Callable, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.Telegram")

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False


class TelegramChannel(BaseChannel):
    """Telegram bot integration using long-polling."""

    name = "telegram"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)

        if not HAS_TELEGRAM:
            raise ImportError(
                "python-telegram-bot required for Telegram. Install with: "
                "pip install 'opencastor[telegram]'"
            )

        self.bot_token = config.get("bot_token")
        if not self.bot_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is required. Set it in your .env file."
            )

        self.app: Optional[Application] = None
        self.logger.info("Telegram channel initialized")

    async def start(self):
        """Build the Telegram application and start polling."""
        self.app = Application.builder().token(self.bot_token).build()

        # Register handlers
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text)
        )

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        self.logger.info("Telegram bot polling started")

    async def stop(self):
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            self.logger.info("Telegram bot stopped")

    async def send_message(self, chat_id: str, text: str):
        if self.app:
            await self.app.bot.send_message(chat_id=int(chat_id), text=text[:4096])

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle the /start command."""
        await update.message.reply_text(
            "OpenCastor connected. Send me commands and I'll relay them to the robot."
        )

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming text messages."""
        chat_id = str(update.effective_chat.id)
        text = update.message.text

        reply = await self.handle_message(chat_id, text)
        if reply:
            await update.message.reply_text(reply[:4096])
