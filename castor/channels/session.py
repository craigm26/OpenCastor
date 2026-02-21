"""
Multi-channel session store for OpenCastor.

Maintains a shared conversation context per user identity so that
switching between Telegram, WhatsApp, Discord, etc. preserves continuity.

Usage::

    from castor.channels.session import ChannelSessionStore

    store = ChannelSessionStore()

    # Resolve a canonical user ID from any channel identity
    user_id = store.resolve_user(channel="telegram", chat_id="123456789")

    # Push a message into the shared context
    store.push(user_id, role="user", text="Move forward", channel="telegram")

    # Build a context summary for the brain
    context = store.build_context(user_id, max_messages=10)
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, Deque, List, Optional

logger = logging.getLogger("OpenCastor.Channels.Session")

# Maximum messages kept per user in the rolling context window
_DEFAULT_MAX_MESSAGES = 20
# Max seconds a session stays alive after the last message (1 hour)
_SESSION_TTL = 3600.0


@dataclass
class SessionMessage:
    """A single message in the shared session context."""

    role: str          # "user" | "brain"
    text: str
    channel: str       # originating channel name
    chat_id: str       # channel-specific chat/sender ID
    timestamp: float = field(default_factory=time.time)


class UserSession:
    """Rolling conversation context for a single canonical user identity."""

    def __init__(self, user_id: str, max_messages: int = _DEFAULT_MAX_MESSAGES):
        self.user_id = user_id
        self._messages: Deque[SessionMessage] = deque(maxlen=max_messages)
        self._last_activity: float = time.time()

    def push(self, role: str, text: str, channel: str, chat_id: str) -> None:
        self._messages.append(SessionMessage(role=role, text=text, channel=channel, chat_id=chat_id))
        self._last_activity = time.time()

    def build_context(self, max_messages: int = 10) -> str:
        """Return a human-readable context summary for the last N messages."""
        recent = list(self._messages)[-max_messages:]
        if not recent:
            return ""
        lines = []
        for msg in recent:
            prefix = "User" if msg.role == "user" else "Robot"
            lines.append(f"[{msg.channel}] {prefix}: {msg.text}")
        return "Conversation history:\n" + "\n".join(lines)

    @property
    def is_expired(self) -> bool:
        return time.time() - self._last_activity > _SESSION_TTL


class ChannelSessionStore:
    """Thread-safe store that maps (channel, chat_id) pairs to shared UserSessions.

    Multiple channels can share the same UserSession by registering identity
    links (e.g., a Telegram chat_id and a WhatsApp JID that belong to the
    same human operator).
    """

    def __init__(self, max_messages: int = _DEFAULT_MAX_MESSAGES):
        self._max_messages = max_messages
        self._lock = Lock()
        # (channel, chat_id) → user_id
        self._identity_map: Dict[tuple, str] = {}
        # user_id → UserSession
        self._sessions: Dict[str, UserSession] = {}
        # channel → set of user_ids active on that channel
        self._channel_users: Dict[str, set] = defaultdict(set)

    def resolve_user(self, channel: str, chat_id: str) -> str:
        """Return the canonical user_id for (channel, chat_id), creating one if needed."""
        key = (channel, str(chat_id))
        with self._lock:
            if key not in self._identity_map:
                # Default: one user_id per (channel, chat_id) pair
                user_id = f"{channel}:{chat_id}"
                self._identity_map[key] = user_id
            user_id = self._identity_map[key]

            if user_id not in self._sessions:
                self._sessions[user_id] = UserSession(user_id, self._max_messages)

            self._channel_users[channel].add(user_id)
            self._reap_expired()
            return user_id

    def link_identities(self, channel_a: str, chat_id_a: str, channel_b: str, chat_id_b: str) -> str:
        """Merge two (channel, chat_id) identities under a shared user_id.

        After linking, both identities share the same conversation history.
        Returns the canonical user_id.
        """
        with self._lock:
            key_a = (channel_a, str(chat_id_a))
            key_b = (channel_b, str(chat_id_b))

            uid_a = self._identity_map.get(key_a, f"{channel_a}:{chat_id_a}")
            uid_b = self._identity_map.get(key_b, f"{channel_b}:{chat_id_b}")

            # Keep uid_a as canonical, merge uid_b's session into it
            canonical = uid_a
            self._identity_map[key_a] = canonical
            self._identity_map[key_b] = canonical

            if canonical not in self._sessions:
                self._sessions[canonical] = UserSession(canonical, self._max_messages)

            if uid_b in self._sessions and uid_b != canonical:
                # Merge old messages from uid_b into canonical session
                for msg in self._sessions[uid_b]._messages:
                    self._sessions[canonical]._messages.append(msg)
                del self._sessions[uid_b]

            logger.info(f"Linked {channel_a}:{chat_id_a} ↔ {channel_b}:{chat_id_b} → {canonical}")
            return canonical

    def push(self, user_id: str, role: str, text: str, channel: str, chat_id: str) -> None:
        """Append a message to the user's shared session."""
        with self._lock:
            if user_id not in self._sessions:
                self._sessions[user_id] = UserSession(user_id, self._max_messages)
            self._sessions[user_id].push(role=role, text=text, channel=channel, chat_id=chat_id)

    def build_context(self, user_id: str, max_messages: int = 10) -> str:
        """Return the context summary for a user's session."""
        with self._lock:
            session = self._sessions.get(user_id)
            if not session:
                return ""
            return session.build_context(max_messages=max_messages)

    def get_session(self, user_id: str) -> Optional[UserSession]:
        with self._lock:
            return self._sessions.get(user_id)

    def _reap_expired(self) -> None:
        """Remove sessions that have been idle beyond TTL (called under lock)."""
        expired = [uid for uid, s in self._sessions.items() if s.is_expired]
        for uid in expired:
            del self._sessions[uid]
            # Clean up identity map entries pointing to this uid
            stale_keys = [k for k, v in self._identity_map.items() if v == uid]
            for k in stale_keys:
                del self._identity_map[k]


# Module-level singleton — shared across all channels in the same process
_global_store: Optional[ChannelSessionStore] = None


def get_session_store() -> ChannelSessionStore:
    """Return the process-wide ChannelSessionStore, creating it on first call."""
    global _global_store
    if _global_store is None:
        _global_store = ChannelSessionStore()
    return _global_store
