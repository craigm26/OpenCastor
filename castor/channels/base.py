"""
Base class for all messaging channel integrations.
Channels receive commands from users on external platforms (WhatsApp, Telegram,
Discord, Slack) and forward them to the robot's brain.
"""

import asyncio
import inspect
import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Optional

logger = logging.getLogger("OpenCastor.Channels")

# Default rate limit: 10 messages per 60 seconds per chat_id
_DEFAULT_RATE_LIMIT = 10
_DEFAULT_RATE_WINDOW = 60.0


class BaseChannel(ABC):
    """Abstract base class for messaging channel integrations."""

    name: str = "base"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        """
        Args:
            config: Channel-specific configuration dict.
                    Accepts ``rate_limit`` (int, default 10) and
                    ``rate_window`` (float seconds, default 60) for per-chat
                    message throttling.
            on_message: Callback invoked when a message arrives.
                        Signature: on_message(channel_name, chat_id, text) -> str
                        Returns the reply text to send back to the user.
        """
        self.config = config
        self._on_message_callback = on_message
        self.logger = logging.getLogger(f"OpenCastor.Channel.{self.name}")

        # Per-chat_id rate limiting
        rate_cfg = (
            config.get("rate_limit", {}) if isinstance(config.get("rate_limit"), dict) else {}
        )
        self._rate_limit: int = rate_cfg.get(
            "max_messages", config.get("rate_limit_max", _DEFAULT_RATE_LIMIT)
        )
        self._rate_window: float = rate_cfg.get(
            "window_seconds", config.get("rate_limit_window", _DEFAULT_RATE_WINDOW)
        )
        self._rate_timestamps: Dict[str, Deque[float]] = defaultdict(deque)

    def _check_rate_limit(self, chat_id: str) -> bool:
        """Return True if the message is within the rate limit, False if throttled."""
        now = time.monotonic()
        window_start = now - self._rate_window
        q = self._rate_timestamps[chat_id]

        # Evict timestamps outside the window
        while q and q[0] < window_start:
            q.popleft()

        if len(q) >= self._rate_limit:
            return False  # rate limit exceeded

        q.append(now)
        return True

    async def handle_message(self, chat_id: str, text: str) -> Optional[str]:
        """
        Process an incoming message and return a reply.
        Subclasses call this from their platform-specific message handler.

        Applies per-chat_id rate limiting before forwarding to the callback.
        """
        self.logger.info(f"[{self.name}] Message from {chat_id}: {text[:80]}")

        if not self._check_rate_limit(chat_id):
            self.logger.warning(
                f"[{self.name}] Rate limit exceeded for {chat_id} "
                f"({self._rate_limit} msg/{self._rate_window}s)"
            )
            return (
                f"Too many requests. Please wait before sending another command "
                f"(limit: {self._rate_limit} per {int(self._rate_window)}s)."
            )

        if self._on_message_callback:
            try:
                # Push message into shared session store for multi-channel routing
                try:
                    from castor.channels.session import get_session_store

                    store = get_session_store()
                    user_id = store.resolve_user(self.name, chat_id)
                    store.push(user_id, role="user", text=text, channel=self.name, chat_id=chat_id)
                    # Inject conversation context into the text if history exists
                    ctx = store.build_context(user_id, max_messages=6)
                    _enriched_text = f"{text}\n\n{ctx}" if ctx else text
                except Exception:
                    _enriched_text = text
                    user_id = chat_id

                if inspect.iscoroutinefunction(self._on_message_callback):
                    reply = await self._on_message_callback(self.name, chat_id, text)
                else:
                    reply = await asyncio.to_thread(
                        self._on_message_callback, self.name, chat_id, text
                    )

                # Record brain reply in session store
                try:
                    if reply:
                        store.push(
                            user_id,
                            role="brain",
                            text=str(reply)[:300],
                            channel=self.name,
                            chat_id=chat_id,
                        )
                except Exception:
                    pass

                return reply
            except Exception as e:
                self.logger.error(f"Message handler error: {e}")
                return f"Error processing command: {e}"
        return None

    @abstractmethod
    async def start(self):
        """Connect to the messaging platform (login, start polling, etc.)."""
        pass

    @abstractmethod
    async def stop(self):
        """Disconnect gracefully."""
        pass

    @abstractmethod
    async def send_message(self, chat_id: str, text: str):
        """Send a text message to a specific chat/user."""
        pass
