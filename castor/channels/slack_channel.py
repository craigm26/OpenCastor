"""
Slack channel integration via slack-bolt.

Setup:
    1. Create a Slack app at https://api.slack.com/apps
    2. Enable Socket Mode and create an App-Level Token (xapp-...)
    3. Add bot scopes: chat:write, app_mentions:read, im:read, im:history
    4. Install the app to your workspace
    5. Set SLACK_BOT_TOKEN, SLACK_APP_TOKEN in .env
"""

import logging
from typing import Callable, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.Slack")

try:
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_bolt.async_app import AsyncApp

    HAS_SLACK = True
except ImportError:
    HAS_SLACK = False


_SLACK_AUDIO_MIME_PREFIXES = ("audio/",)
_SLACK_AUDIO_EXTENSIONS = (
    ".ogg",
    ".oga",
    ".mp3",
    ".mp4",
    ".m4a",
    ".wav",
    ".flac",
    ".webm",
    ".opus",
)


def _find_slack_audio_file(files: list) -> Optional[dict]:
    """Return the first audio file dict from a Slack files list, or None."""
    for f in files:
        mime = (f.get("mimetype") or "").lower()
        name = (f.get("name") or "").lower()
        if any(mime.startswith(p) for p in _SLACK_AUDIO_MIME_PREFIXES):
            return f
        if any(name.endswith(ext) for ext in _SLACK_AUDIO_EXTENSIONS):
            return f
    return None


async def _download_and_transcribe_slack(
    channel_obj, file_info: dict, bot_token: str
) -> Optional[str]:
    """Download a Slack audio file and transcribe it."""
    import httpx

    try:
        from castor import voice as voice_mod
    except ImportError:
        logger.warning("castor.voice not available — voice input ignored")
        return None

    try:
        url = file_info.get("url_private_download") or file_info.get("url_private") or ""
        if not url:
            return None

        headers = {"Authorization": f"Bearer {bot_token}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, follow_redirects=True)
            resp.raise_for_status()
            audio_bytes = resp.content

        name = (file_info.get("name") or "audio.ogg").lower()
        hint = "ogg"
        for ext in _SLACK_AUDIO_EXTENSIONS:
            if name.endswith(ext):
                hint = ext.lstrip(".")
                break

        text = voice_mod.transcribe_bytes(audio_bytes, hint_format=hint)
        if text:
            logger.info("Slack voice → text: %r", text[:80])
        return text
    except Exception as exc:
        logger.error("Slack audio download/transcription error: %s", exc)
        return None


class SlackChannel(BaseChannel):
    """Slack bot integration using Socket Mode."""

    name = "slack"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)

        if not HAS_SLACK:
            raise ImportError(
                "slack-bolt required for Slack. Install with: pip install 'opencastor[slack]'"
            )

        self.bot_token = config.get("bot_token")
        self.app_token = config.get("app_token")
        if not self.bot_token or not self.app_token:
            raise ValueError(
                "SLACK_BOT_TOKEN and SLACK_APP_TOKEN are required. Set them in your .env file."
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

            try:
                reply = await self.handle_message(chat_id, text)
                if reply:
                    await say(reply[:4000])
            except Exception as exc:
                self.logger.error("Slack handle_mention handler error: %s", exc)

        @self.app.event("message")
        async def handle_dm(event, say):
            # Only handle DMs (no subtype = direct message)
            if event.get("channel_type") != "im":
                return
            subtype = event.get("subtype")
            # Allow file_share subtype (audio uploads); skip other subtypes
            if subtype and subtype != "file_share":
                return

            chat_id = event.get("channel", "")

            # Handle audio file uploads
            files = event.get("files", [])
            audio_file = _find_slack_audio_file(files)
            if audio_file is not None:
                text = await _download_and_transcribe_slack(self, audio_file, self.bot_token)
                if text:
                    reply = await self.handle_message(chat_id, text)
                    if reply:
                        await say(reply[:4000])
                else:
                    await say("⚠️ Could not transcribe audio. Please try again or send text.")
                return

            text = event.get("text", "").strip()
            if not text:
                return

            try:
                reply = await self.handle_message(chat_id, text)
                if reply:
                    await say(reply[:4000])
            except Exception as exc:
                self.logger.error("Slack handle_dm handler error: %s", exc)

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
