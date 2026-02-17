"""
Base class for all messaging channel integrations.
Channels receive commands from users on external platforms (WhatsApp, Telegram,
Discord, Slack) and forward them to the robot's brain.
"""

import logging
from abc import ABC, abstractmethod
from typing import Callable, Optional

logger = logging.getLogger("OpenCastor.Channels")


class BaseChannel(ABC):
    """Abstract base class for messaging channel integrations."""

    name: str = "base"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        """
        Args:
            config: Channel-specific configuration dict.
            on_message: Callback invoked when a message arrives.
                        Signature: on_message(channel_name, chat_id, text) -> str
                        Returns the reply text to send back to the user.
        """
        self.config = config
        self._on_message_callback = on_message
        self.logger = logging.getLogger(f"OpenCastor.Channel.{self.name}")

    async def handle_message(self, chat_id: str, text: str) -> Optional[str]:
        """
        Process an incoming message and return a reply.
        Subclasses call this from their platform-specific message handler.
        """
        self.logger.info(f"[{self.name}] Message from {chat_id}: {text[:80]}")

        if self._on_message_callback:
            try:
                reply = self._on_message_callback(self.name, chat_id, text)
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
