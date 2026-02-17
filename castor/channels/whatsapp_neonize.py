"""
WhatsApp channel integration via neonize (WhatsApp Web protocol).

No Twilio account needed -- just scan a QR code from your phone.

Setup:
    1. pip install 'opencastor[whatsapp]'
    2. castor gateway --config your_robot.rcan.yaml
    3. Scan the QR code that appears in the terminal with WhatsApp on your phone
    4. Done! Session persists in a local SQLite database.
"""

import asyncio
import logging
import os
import threading
from typing import Callable, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.WhatsApp")

try:
    from neonize.client import NewClient
    from neonize.events import ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv, PairStatusEv

    HAS_NEONIZE = True
except ImportError:
    HAS_NEONIZE = False


def _get_session_db_path(config: Optional[dict] = None) -> str:
    """Resolve the path for the neonize session database.

    Resolution order:
        1. Explicit ``session_db`` key in config
        2. ``OPENCASTOR_DATA_DIR`` env var  ->  <dir>/whatsapp_session.db
        3. Default  ->  ~/.opencastor/whatsapp_session.db
    """
    if config and config.get("session_db"):
        return config["session_db"]

    data_dir = os.getenv("OPENCASTOR_DATA_DIR")
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "whatsapp_session.db")

    default_dir = os.path.join(os.path.expanduser("~"), ".opencastor")
    os.makedirs(default_dir, exist_ok=True)
    return os.path.join(default_dir, "whatsapp_session.db")


class WhatsAppChannel(BaseChannel):
    """WhatsApp messaging via neonize (WhatsApp Web QR code scan)."""

    name = "whatsapp"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)

        if not HAS_NEONIZE:
            raise ImportError(
                "neonize is required for WhatsApp. Install with: "
                "pip install 'opencastor[whatsapp]'"
            )

        self._session_db = _get_session_db_path(config)
        self._client: Optional["NewClient"] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connected = False
        self._stop_flag = False

        self.logger.info(f"WhatsApp channel initialized (session: {self._session_db})")

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self):
        """Start the neonize client in a background thread.

        On first run a QR code is printed to the terminal -- scan it with
        WhatsApp on your phone.  Subsequent runs reconnect automatically
        using the persisted session.
        """
        self._loop = asyncio.get_running_loop()
        self._client = NewClient(self._session_db)

        # --- Event handlers ---
        @self._client.event(ConnectedEv)
        def _on_connected(client: "NewClient", event: ConnectedEv):
            self._connected = True
            try:
                me = client.get_me()
                self.logger.info(
                    f"WhatsApp connected as {me.PushName} ({me.JID.User})"
                )
            except Exception:
                self.logger.info("WhatsApp connected")

        @self._client.event(PairStatusEv)
        def _on_pair_status(client: "NewClient", status: PairStatusEv):
            self.logger.info(f"WhatsApp paired: {status.ID.User}")

        @self._client.event(DisconnectedEv)
        def _on_disconnected(client: "NewClient", event: DisconnectedEv):
            self._connected = False
            self.logger.warning("WhatsApp disconnected")

        @self._client.event(LoggedOutEv)
        def _on_logged_out(client: "NewClient", event: LoggedOutEv):
            self._connected = False
            self.logger.error(
                "WhatsApp logged out -- delete session DB and restart to re-authenticate"
            )

        @self._client.event(MessageEv)
        def _on_message(client: "NewClient", message: MessageEv):
            self._handle_incoming(client, message)

        # Run the blocking client.connect() in a daemon thread
        self._thread = threading.Thread(
            target=self._run_client, name="whatsapp-neonize", daemon=True
        )
        self._thread.start()

        self.logger.info(
            "WhatsApp channel starting -- scan the QR code in the terminal "
            "if this is your first connection"
        )

    def _run_client(self):
        """Thread target: runs the blocking neonize connect loop."""
        try:
            self._client.connect()
        except Exception as e:
            if not self._stop_flag:
                self.logger.error(f"WhatsApp client error: {e}")

    def _handle_incoming(self, client: "NewClient", message: "MessageEv"):
        """Process an incoming WhatsApp message (runs in neonize's thread)."""
        try:
            info = message.Info
            # Skip messages sent by us
            if info.MessageSource.IsFromMe:
                return

            # Extract text content
            msg = message.Message
            text = msg.conversation or ""
            if not text and hasattr(msg, "extendedTextMessage"):
                ext = msg.extendedTextMessage
                if ext and hasattr(ext, "text"):
                    text = ext.text or ""

            if not text:
                return  # Skip non-text messages (images, stickers, etc.)

            chat = info.MessageSource.Chat
            chat_id = f"{chat.User}@{chat.Server}"

            # Bridge from neonize's sync thread into the async FastAPI loop
            if self._loop is None:
                return

            future = asyncio.run_coroutine_threadsafe(
                self._process_and_reply(client, chat, chat_id, text),
                self._loop,
            )
            # Wait for the reply (with timeout to avoid blocking forever)
            future.result(timeout=30)

        except Exception as e:
            self.logger.error(f"Error handling incoming message: {e}")

    async def _process_and_reply(self, client, chat_jid, chat_id: str, text: str):
        """Async handler: call the brain and send the reply."""
        reply = await self.handle_message(chat_id, text)
        if reply:
            # Send reply back via neonize (sync call, run in executor)
            await self._loop.run_in_executor(
                None, lambda: client.send_message(chat_jid, reply[:4096])
            )

    async def send_message(self, chat_id: str, text: str):
        """Send a WhatsApp message to a chat.

        Args:
            chat_id: Recipient in 'phone@s.whatsapp.net' format.
            text: Message body.
        """
        if not self._client or not self._connected:
            self.logger.warning("Cannot send message -- WhatsApp not connected")
            return

        try:
            from neonize.utils.jid import build_jid

            # Parse chat_id: could be "1234567890@s.whatsapp.net" or just a number
            if "@" in chat_id:
                user, server = chat_id.split("@", 1)
                jid = build_jid(user, server)
            else:
                jid = build_jid(chat_id, "s.whatsapp.net")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self._client.send_message(jid, text[:4096])
            )
            self.logger.info(f"Sent WhatsApp message to {chat_id}")
        except Exception as e:
            self.logger.error(f"Failed to send WhatsApp message: {e}")

    async def stop(self):
        """Disconnect the WhatsApp client and stop the background thread."""
        self._stop_flag = True
        self._connected = False

        if self._client:
            try:
                self._client.disconnect()
            except Exception as e:
                self.logger.debug(f"Disconnect error (ignored): {e}")

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self.logger.info("WhatsApp channel stopped")
