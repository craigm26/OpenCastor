"""
WhatsApp channel integration via neonize (WhatsApp Web protocol).

OpenClaw-style access control and self-chat support built-in.

No Twilio account needed -- just scan a QR code from your phone.

Setup:
    1. pip install 'opencastor[whatsapp]'
    2. castor gateway --config your_robot.rcan.yaml
    3. Scan the QR code that appears in the terminal with WhatsApp on your phone
    4. Done! Session persists in a local SQLite database.

RCAN config block (under channels.whatsapp):
    enabled: true
    dm_policy: allowlist          # allowlist | open | pairing
    allow_from:                   # E.164 or bare numbers
      - "+19169967105"
    self_chat_mode: true          # owner can message their own number → robot responds
    group_policy: disabled        # allowlist | open | disabled
    group_name_filter: "alex"     # only respond to groups whose name contains this string
    group_jids: []                # explicit group JID allowlist (overrides group_name_filter)
    ack_reaction: "👀"            # optional reaction emoji on receipt
"""

import asyncio
import logging
import os
import re
import threading
from typing import Callable, List, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.WhatsApp")

try:
    from neonize.client import NewClient
    from neonize.events import ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv, PairStatusEv

    HAS_NEONIZE = True
except ImportError:
    HAS_NEONIZE = False

# ── Pairing message sent to unknown senders ────────────────────────────────
_PAIRING_MSG = (
    "👋 Hi! I'm a robot assistant running OpenCastor.\n"
    "Access is restricted to approved users.\n"
    "Send this code to the robot's owner to get access: *{code}*"
)

_PAIRING_DENY_MSG = "⛔ Access denied. Ask the robot's owner to add your number."


def _get_session_db_path(config: Optional[dict] = None) -> str:
    """Resolve the path for the neonize session database."""
    if config and config.get("session_db"):
        return config["session_db"]
    data_dir = os.getenv("OPENCASTOR_DATA_DIR")
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "whatsapp_session.db")
    default_dir = os.path.join(os.path.expanduser("~"), ".opencastor")
    os.makedirs(default_dir, exist_ok=True)
    return os.path.join(default_dir, "whatsapp_session.db")


def _normalize_number(number: str) -> str:
    """Strip non-digit characters for comparison (+1 919... → 1919...)."""
    return re.sub(r"\D", "", number or "")


class WhatsAppChannel(BaseChannel):
    """WhatsApp messaging via neonize — OpenClaw-style access control.

    Config keys (all optional, under channels.whatsapp):
        dm_policy         : "allowlist" | "pairing" | "open"  (default: allowlist)
        allow_from        : list of E.164 phone numbers allowed to DM
        self_chat_mode    : bool — owner can message their own number (default: true)
        group_policy      : "allowlist" | "open" | "disabled"  (default: disabled)
        group_name_filter : str — only respond to groups whose name contains this substring
                            (case-insensitive). E.g. "alex" matches groups named "alex",
                            "🤖 alex commands", etc.
        group_jids        : list of group JID user parts to restrict to
                            (e.g. ["120363XXXXXXXXXX"]). Overrides group_name_filter when set.
                            Tip: set group_policy:open temporarily and check gateway logs to
                            find your group's JID, then switch to group_jids.
        ack_reaction      : emoji to react with on receipt (default: none)
        session_db        : path to neonize SQLite session file
    """

    name = "whatsapp"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)

        if not HAS_NEONIZE:
            raise ImportError(
                "neonize is required for WhatsApp. Install with: pip install 'opencastor[whatsapp]'"
            )

        self._session_db = _get_session_db_path(config)
        self._client: Optional[NewClient] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connected = False
        self._stop_flag = False

        # Owner JID (set on ConnectedEv from client.get_me())
        self._owner_number: Optional[str] = None  # normalized digits only

        # Access control config
        self._dm_policy: str = config.get("dm_policy", "allowlist")
        self._allow_from: List[str] = [_normalize_number(n) for n in config.get("allow_from", [])]
        self._self_chat_mode: bool = bool(config.get("self_chat_mode", True))
        self._group_policy: str = config.get("group_policy", "disabled")
        self._ack_reaction: Optional[str] = config.get("ack_reaction")

        # Group filtering: name-based (substring) or explicit JID allowlist
        self._group_name_filter: Optional[str] = config.get("group_name_filter") or None
        self._group_jids: List[str] = [str(j).strip() for j in config.get("group_jids", []) if j]

        # Cache for group JID → subject lookups (avoids repeated API calls)
        self._group_name_cache: dict = {}  # {chat_user: str | None}

        # Pending pairing requests: {normalized_number: code}
        self._pairing_requests: dict = {}

        self.logger.info(
            f"WhatsApp channel initialized — "
            f"dm_policy={self._dm_policy}, "
            f"allow_from={self._allow_from or '(none)'}, "
            f"self_chat={self._self_chat_mode}, "
            f"group_policy={self._group_policy}, "
            f"group_jids={self._group_jids or '(none)'}, "
            f"group_name_filter={self._group_name_filter!r}, "
            f"session={self._session_db}"
        )

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Startup ──────────────────────────────────────────────────────────

    async def start(self):
        """Start the neonize client.

        First run: QR code printed to terminal — scan with WhatsApp.
        Subsequent runs: reconnect automatically from saved session.
        """
        self._loop = asyncio.get_running_loop()
        self._client = NewClient(self._session_db)

        @self._client.event(ConnectedEv)
        def _on_connected(client: "NewClient", event: ConnectedEv):
            self._connected = True
            try:
                me = client.get_me()
                self._owner_number = _normalize_number(me.JID.User)
                # Auto-add owner to allowFrom if not already there
                if self._owner_number and self._owner_number not in self._allow_from:
                    self._allow_from.append(self._owner_number)
                self.logger.info(
                    f"WhatsApp connected as {me.PushName} "
                    f"(+{self._owner_number}) — "
                    f"allow_from={self._allow_from}"
                )
            except Exception:
                self.logger.info("WhatsApp connected")

        @self._client.event(PairStatusEv)
        def _on_pair_status(client: "NewClient", status: PairStatusEv):
            self.logger.info(f"WhatsApp paired: {status.ID.User}")

        @self._client.event(DisconnectedEv)
        def _on_disconnected(client: "NewClient", event: DisconnectedEv):
            self._connected = False
            self.logger.warning("WhatsApp disconnected — will reconnect automatically")

        @self._client.event(LoggedOutEv)
        def _on_logged_out(client: "NewClient", event: LoggedOutEv):
            self._connected = False
            self.logger.error(
                "WhatsApp logged out — delete session DB and restart to re-authenticate"
            )

        @self._client.event(MessageEv)
        def _on_message(client: "NewClient", message: MessageEv):
            self._handle_incoming(client, message)

        self._thread = threading.Thread(
            target=self._run_client, name="whatsapp-neonize", daemon=True
        )
        self._thread.start()
        self.logger.info(
            "WhatsApp channel starting — "
            "scan the QR code in the terminal if this is your first connection"
        )

    def _run_client(self):
        try:
            self._client.connect()
        except Exception as e:
            if not self._stop_flag:
                self.logger.error(f"WhatsApp client error: {e}")

    # ── Access control ────────────────────────────────────────────────────

    def _is_allowed(self, number: str) -> bool:
        """Return True if this number is permitted to send DMs."""
        norm = _normalize_number(number)
        if not self._allow_from:
            return True  # no allowlist → open
        return norm in self._allow_from

    def _is_group_jid(self, chat_server: str) -> bool:
        return "g.us" in str(chat_server)

    # ── Incoming message handler ──────────────────────────────────────────

    def _handle_incoming(self, client: "NewClient", message: "MessageEv"):
        """Route incoming WhatsApp messages through access control."""
        try:
            info = message.Info
            source = info.MessageSource
            is_from_me = source.IsFromMe
            chat = source.Chat
            chat_server = str(chat.Server)
            chat_user = str(chat.User)
            is_group = self._is_group_jid(chat_server)

            # ── Group policy (evaluated before self-chat so owner can message groups) ──
            if is_group:
                if self._group_policy == "disabled":
                    return
                if self._group_policy == "allowlist":
                    sender_user = str(source.Sender.User) if hasattr(source, "Sender") else ""
                    if not self._is_allowed(sender_user):
                        return
                # open: fall through

                # ── Group JID / name filter ────────────────────────────────
                # Always log the group JID so users can find it for configuration
                if self._group_jids:
                    # Explicit JID allowlist: fast path, no API call needed
                    if chat_user not in self._group_jids:
                        self.logger.debug(
                            f"Group message skipped — JID {chat_user}@g.us not in group_jids"
                        )
                        return
                elif self._group_name_filter:
                    # Name-based filter: fetch group subject (cached)
                    group_subject = self._get_group_name(client, chat_user)
                    self.logger.info(
                        f"Group message from JID={chat_user}@g.us name={group_subject!r}"
                    )
                    if (
                        group_subject is None
                        or self._group_name_filter.lower() not in group_subject.lower()
                    ):
                        self.logger.debug(
                            f"Group message skipped — "
                            f"name {group_subject!r} doesn't match filter {self._group_name_filter!r}"
                        )
                        return
                else:
                    # No filter: log JID to help users configure it
                    group_subject = self._get_group_name(client, chat_user)
                    self.logger.info(
                        f"Group message from JID={chat_user}@g.us name={group_subject!r} "
                        f'(tip: set group_jids: ["{chat_user}"] or '
                        f'group_name_filter: "{group_subject or chat_user}" to filter)'
                    )

            # ── Self-chat handling (DMs only) ────────────────────────────
            # When the owner messages their own number (WhatsApp "Saved Messages"),
            # IsFromMe is True. Allow through if self_chat_mode is on.
            # Group messages already handled above — skip this block for groups.
            if is_from_me and not is_group:
                if not self._self_chat_mode:
                    return
                # Only process if it's the owner chatting with themselves
                # (chat JID == owner's own number)
                if self._owner_number and chat_user != self._owner_number:
                    return  # Message they sent to someone else — skip
                # Fall through: owner talking to themselves → process

            # ── DM access control ─────────────────────────────────────────
            if not is_group and not is_from_me:
                sender_number = chat_user  # For DMs, chat JID == sender
                if self._dm_policy == "allowlist":
                    if not self._is_allowed(sender_number):
                        self._send_sync(client, chat, _PAIRING_DENY_MSG)
                        self.logger.info(
                            f"WhatsApp DM denied from +{sender_number} (dm_policy=allowlist)"
                        )
                        return
                elif self._dm_policy == "pairing":
                    if not self._is_allowed(sender_number):
                        self._handle_pairing_request(client, chat, sender_number, message)
                        return
                # open: fall through

            # ── Extract text or audio ─────────────────────────────────────
            msg = message.Message
            text = msg.conversation or ""
            if not text and hasattr(msg, "extendedTextMessage"):
                ext = msg.extendedTextMessage
                if ext and hasattr(ext, "text"):
                    text = ext.text or ""

            # Handle voice/audio messages
            if not text:
                audio_msg = None
                for attr in ("audioMessage", "voiceMessage"):
                    candidate = getattr(msg, attr, None)
                    if candidate is not None:
                        audio_msg = candidate
                        break

                if audio_msg is not None:
                    text = self._transcribe_audio_message(client, audio_msg) or ""

            if not text:
                return

            # ── Ack reaction ──────────────────────────────────────────────
            if self._ack_reaction:
                try:
                    client.send_reaction(chat, self._ack_reaction, info.ID)
                except Exception:
                    pass

            # ── Build chat_id and dispatch ────────────────────────────────
            chat_id = f"{chat_user}@{chat_server}"
            self._dispatch(self._process_and_reply(client, chat, chat_id, text))

        except Exception as e:
            self.logger.error(f"Error handling incoming message: {e}")

    def _transcribe_audio_message(self, client, audio_msg) -> Optional[str]:
        """Download and transcribe a WhatsApp audio/voice message.

        Args:
            client: neonize NewClient instance.
            audio_msg: neonize audioMessage or voiceMessage protobuf object.

        Returns:
            Transcribed text, or None if unavailable.
        """
        try:
            from castor import voice as voice_mod
        except ImportError:
            self.logger.warning("castor.voice not available — voice message ignored")
            return None

        try:
            # neonize: client.download_media_message(audio_msg) → bytes
            audio_bytes = client.download_media_message(audio_msg)
            if not audio_bytes:
                return None

            # WhatsApp voice notes are OGG/Opus; audio files vary
            mime = getattr(audio_msg, "mimetype", "") or ""
            hint = "ogg"
            if "mp4" in mime or "m4a" in mime:
                hint = "m4a"
            elif "mp3" in mime or "mpeg" in mime:
                hint = "mp3"
            elif "wav" in mime:
                hint = "wav"

            text = voice_mod.transcribe_bytes(audio_bytes, hint_format=hint)
            if text:
                self.logger.info("WhatsApp voice → text: %r", text[:80])
            return text
        except Exception as exc:
            self.logger.error("WhatsApp audio transcription failed: %s", exc)
            return None

    def _get_group_name(self, client, chat_user: str) -> Optional[str]:
        """Fetch and cache the subject (display name) of a WhatsApp group.

        Returns the group name string, or None if it can't be fetched.
        """
        if chat_user in self._group_name_cache:
            return self._group_name_cache[chat_user]

        try:
            try:
                from neonize.utils.jid import build_jid

                jid = build_jid(chat_user, "g.us")
            except ImportError:
                jid = f"{chat_user}@g.us"
            info = client.get_group_info(jid)
            # GroupInfo.GroupName is a sub-message; .Name is the actual string
            subject = getattr(info.GroupName, "Name", None) or str(info.GroupName) or None
            self._group_name_cache[chat_user] = subject
            return subject
        except Exception as exc:
            self.logger.debug(f"Could not fetch group info for {chat_user}: {exc}")
            self._group_name_cache[chat_user] = None
            return None

    def _handle_pairing_request(self, client, chat_jid, sender_number: str, message):
        """Send a pairing code to an unknown sender."""
        import hashlib

        code = hashlib.sha1(sender_number.encode()).hexdigest()[:6].upper()
        self._pairing_requests[_normalize_number(sender_number)] = code
        msg = _PAIRING_MSG.format(code=code)
        self._send_sync(client, chat_jid, msg)
        self.logger.info(f"WhatsApp pairing request from +{sender_number} — code: {code}")

    def _send_sync(self, client, chat_jid, text: str):
        """Fire-and-forget sync send (used in event callbacks)."""
        try:
            client.send_message(chat_jid, text[:4096])
        except Exception as e:
            self.logger.error(f"WhatsApp sync send failed: {e}")

    def _dispatch(self, coro) -> None:
        """Schedule a coroutine on the gateway event loop (thread-safe).

        Extracted as a method so tests can mock it without touching asyncio.
        """
        if self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            future.result(timeout=30)
        except Exception as e:
            self.logger.error(f"WhatsApp dispatch error: {e}")

    # ── Async reply path ──────────────────────────────────────────────────

    async def _process_and_reply(self, client, chat_jid, chat_id: str, text: str):
        """Call the AI brain and send the reply."""
        reply = await self.handle_message(chat_id, text)
        if reply:
            await self._loop.run_in_executor(
                None, lambda: client.send_message(chat_jid, reply[:4096])
            )

    # ── Outbound send (called by main.py / gateway) ───────────────────────

    async def send_message(self, chat_id: str, text: str):
        """Send a WhatsApp message to a chat.

        Args:
            chat_id: Recipient — "phone@s.whatsapp.net", bare number, or E.164.
            text: Message body (auto-chunked at 4096 chars).
        """
        if not self._client or not self._connected:
            self.logger.warning("Cannot send — WhatsApp not connected")
            return
        try:
            from neonize.utils.jid import build_jid

            if "@" in chat_id:
                user, server = chat_id.split("@", 1)
                jid = build_jid(user, server)
            else:
                jid = build_jid(_normalize_number(chat_id), "s.whatsapp.net")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: self._client.send_message(jid, text[:4096]))
            self.logger.info(f"Sent WhatsApp message to {chat_id}")
        except Exception as e:
            self.logger.error(f"Failed to send WhatsApp message: {e}")

    # ── Pairing management (CLI-accessible) ──────────────────────────────

    def approve_pairing(self, code: str) -> Optional[str]:
        """Approve a pending pairing request by code.

        Returns the approved number on success, None if code not found.
        """
        for number, pending_code in list(self._pairing_requests.items()):
            if pending_code.upper() == code.upper():
                self._allow_from.append(number)
                del self._pairing_requests[number]
                self.logger.info(f"Approved WhatsApp pairing for +{number}")
                return number
        return None

    def list_pairing_requests(self) -> List[dict]:
        """Return pending pairing requests as [{number, code}]."""
        return [{"number": f"+{n}", "code": c} for n, c in self._pairing_requests.items()]

    # ── Teardown ──────────────────────────────────────────────────────────

    async def stop(self):
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
