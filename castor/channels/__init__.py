"""
OpenCastor Channel Registry.
Discovers and manages messaging channel integrations.
"""

import logging
from typing import Callable, Dict, List, Optional

from castor.auth import check_channel_ready, resolve_channel_credentials

__all__ = [
    "create_channel",
    "get_available_channels",
    "get_ready_channels",
    "get_session_store",
]

logger = logging.getLogger("OpenCastor.Channels")

# Registry of channel name -> class (lazy-populated)
_CHANNEL_CLASSES: Dict[str, type] = {}


def _register_builtin_channels():
    """Import and register all built-in channel implementations."""
    global _CHANNEL_CLASSES

    try:
        from castor.channels.whatsapp import WhatsAppChannel

        _CHANNEL_CLASSES["whatsapp"] = WhatsAppChannel
    except ImportError:
        logger.debug("WhatsApp channel unavailable (neonize not installed)")

    try:
        from castor.channels.whatsapp_twilio import WhatsAppTwilioChannel

        _CHANNEL_CLASSES["whatsapp_twilio"] = WhatsAppTwilioChannel
    except ImportError:
        logger.debug("WhatsApp (Twilio) channel unavailable (twilio not installed)")

    try:
        from castor.channels.telegram_channel import TelegramChannel

        _CHANNEL_CLASSES["telegram"] = TelegramChannel
    except ImportError:
        logger.debug("Telegram channel unavailable (python-telegram-bot not installed)")

    try:
        from castor.channels.discord_channel import DiscordChannel

        _CHANNEL_CLASSES["discord"] = DiscordChannel
    except ImportError:
        logger.debug("Discord channel unavailable (discord.py not installed)")

    try:
        from castor.channels.slack_channel import SlackChannel

        _CHANNEL_CLASSES["slack"] = SlackChannel
    except ImportError:
        logger.debug("Slack channel unavailable (slack-bolt not installed)")


def get_available_channels() -> List[str]:
    """Return names of channels whose SDKs are installed."""
    if not _CHANNEL_CLASSES:
        _register_builtin_channels()
    return list(_CHANNEL_CLASSES.keys())


def get_ready_channels() -> List[str]:
    """Return names of channels that are both installed and have credentials configured."""
    return [ch for ch in get_available_channels() if check_channel_ready(ch)]


def _builtin_create_channel(
    name: str,
    config: Optional[dict] = None,
    on_message: Optional[Callable] = None,
):
    """Built-in channel factory: instantiate a channel by name.

    Uses module-level ``_CHANNEL_CLASSES`` and ``resolve_channel_credentials``
    so that test patches on ``castor.channels.*`` continue to work correctly.
    """
    if not _CHANNEL_CLASSES:
        _register_builtin_channels()

    cls = _CHANNEL_CLASSES.get(name.lower())
    if cls is None:
        raise ValueError(f"Unknown channel '{name}'. Available: {list(_CHANNEL_CLASSES.keys())}")

    # Merge environment credentials into config
    merged = dict(config or {})
    env_creds = resolve_channel_credentials(name)
    merged.update(env_creds)

    return cls(merged, on_message=on_message)


def create_channel(
    name: str,
    config: Optional[dict] = None,
    on_message: Optional[Callable] = None,
):
    """Factory: instantiate a channel by name.

    Thin wrapper around :meth:`~castor.registry.ComponentRegistry.create_channel`
    that preserves backward compatibility.  Plugin-registered channels take
    precedence; built-in channels fall back to :func:`_builtin_create_channel`.

    Args:
        name: Channel name (whatsapp, telegram, discord, slack).
        config: Optional extra config dict.  Credentials are auto-resolved
                from environment variables and merged.
        on_message: Callback(channel_name, chat_id, text) -> reply_str.
    """
    from castor.registry import get_registry

    return get_registry().create_channel(name, config, on_message)


def get_session_store():
    """Return the process-wide ChannelSessionStore for multi-channel routing."""
    from castor.channels.session import get_session_store as _get

    return _get()
