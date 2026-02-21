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
    # Stub types so class-body annotations don't raise NameError at import time
    Update = object  # type: ignore[assignment,misc]
    class ContextTypes:  # type: ignore[no-redef]
        DEFAULT_TYPE = object


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
            raise ValueError("TELEGRAM_BOT_TOKEN is required. Set it in your .env file.")

        self.app: Optional[Application] = None
        self.logger.info("Telegram channel initialized")

    async def start(self):
        """Build the Telegram application and start polling."""
        self.app = Application.builder().token(self.bot_token).build()

        # Register handlers
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self.app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._on_voice))

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

        try:
            reply = await self.handle_message(chat_id, text)
            if reply:
                await update.message.reply_text(reply[:4096])
        except Exception as exc:
            self.logger.error("Telegram _on_text handler error: %s", exc)

    async def _on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming voice notes and audio files — transcribe then route as text."""
        chat_id = str(update.effective_chat.id)
        msg = update.message

        # Prefer voice note; fall back to audio file attachment
        audio_obj = msg.voice or msg.audio
        if audio_obj is None:
            return

        try:
            from castor import voice as voice_mod

            tg_file = await audio_obj.get_file()
            audio_bytes = await tg_file.download_as_bytearray()

            mime = getattr(audio_obj, "mime_type", "") or ""
            hint = "ogg"
            if "mp3" in mime or "mpeg" in mime:
                hint = "mp3"
            elif "mp4" in mime or "m4a" in mime:
                hint = "m4a"
            elif "wav" in mime:
                hint = "wav"

            text = voice_mod.transcribe_bytes(bytes(audio_bytes), hint_format=hint)
            if not text:
                await msg.reply_text("⚠️ Could not transcribe audio. Please try again or send text.")
                return

            self.logger.info("Telegram voice → text: %r", text[:80])
            reply = await self.handle_message(chat_id, text)
            if reply:
                await msg.reply_text(reply[:4096])
        except ImportError:
            self.logger.warning("castor.voice not available — voice input ignored")
            await msg.reply_text("🔇 Voice transcription not available on this server.")
        except Exception as exc:
            self.logger.error("Telegram _on_voice handler error: %s", exc)
            await msg.reply_text("⚠️ Error processing voice message.")
