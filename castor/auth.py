"""
OpenCastor Unified Auth Manager.
Resolves credentials for AI providers and messaging channels using a
layered approach inspired by OpenClaw's auth-profiles pattern:

    1. Explicit environment variable
    2. .env file (loaded via python-dotenv)
    3. RCAN config fallback
"""

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger("OpenCastor.Auth")

# Map of provider name -> (env var name, config key)
PROVIDER_AUTH_MAP: Dict[str, tuple] = {
    "google": ("GOOGLE_API_KEY", "api_key"),
    "openai": ("OPENAI_API_KEY", "api_key"),
    "anthropic": ("ANTHROPIC_API_KEY", "api_key"),
    "openrouter": ("OPENROUTER_API_KEY", "api_key"),
    "ollama": ("OLLAMA_BASE_URL", "url"),
}

# Map of channel name -> list of (env var, config key) tuples
CHANNEL_AUTH_MAP: Dict[str, list] = {
    "whatsapp": [],  # QR code auth -- no env vars needed
    "whatsapp_twilio": [
        ("TWILIO_ACCOUNT_SID", "account_sid"),
        ("TWILIO_AUTH_TOKEN", "auth_token"),
        ("TWILIO_WHATSAPP_NUMBER", "whatsapp_number"),
    ],
    "telegram": [
        ("TELEGRAM_BOT_TOKEN", "bot_token"),
    ],
    "discord": [
        ("DISCORD_BOT_TOKEN", "bot_token"),
    ],
    "slack": [
        ("SLACK_BOT_TOKEN", "bot_token"),
        ("SLACK_APP_TOKEN", "app_token"),
        ("SLACK_SIGNING_SECRET", "signing_secret"),
    ],
    "mqtt": [
        ("MQTT_BROKER_HOST", "broker_host"),
        ("MQTT_USERNAME", "username"),
        ("MQTT_PASSWORD", "password"),
    ],
}


def load_dotenv_if_available():
    """Load env files: ~/.opencastor/env (primary) and local .env (fallback)."""
    # Load ~/.opencastor/env first (survives uninstall)
    opencastor_env = os.path.expanduser("~/.opencastor/env")
    if os.path.exists(opencastor_env):
        try:
            with open(opencastor_env) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip()
                        # Don't override already-set env vars
                        if key and not os.getenv(key):
                            os.environ[key] = value
            logger.debug("Loaded ~/.opencastor/env")
        except Exception:
            pass

    # Also load local .env for backward compatibility
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
        logger.debug("Loaded .env file")
    except ImportError:
        pass


def resolve_provider_key(provider: str, config: Optional[Dict] = None) -> Optional[str]:
    """
    Resolve an API key for the given provider.

    Resolution order:
        1. Environment variable (e.g. GOOGLE_API_KEY)
        2. RCAN config dict (e.g. config["api_key"])

    Returns None if no key is found.
    """
    auth_entry = PROVIDER_AUTH_MAP.get(provider.lower())
    if not auth_entry:
        logger.warning(f"Unknown provider: {provider}")
        return None

    env_var, config_key = auth_entry

    # 1. Environment variable
    value = os.getenv(env_var)
    if value:
        logger.debug(f"Resolved {provider} key from environment ({env_var})")
        return value

    # 2. Config fallback
    if config and config.get(config_key):
        logger.debug(f"Resolved {provider} key from config ({config_key})")
        return config[config_key]

    return None


def resolve_channel_credentials(channel: str, config: Optional[Dict] = None) -> Dict[str, str]:
    """
    Resolve all credentials for the given messaging channel.

    Returns a dict of config_key -> value for all resolved credentials.
    Missing keys are omitted.
    """
    auth_entries = CHANNEL_AUTH_MAP.get(channel.lower(), [])
    credentials = {}

    for env_var, config_key in auth_entries:
        value = os.getenv(env_var)
        if value:
            credentials[config_key] = value
        elif config and config.get(config_key):
            credentials[config_key] = config[config_key]

    return credentials


def check_provider_ready(provider: str, config: Optional[Dict] = None) -> bool:
    """Check whether the given provider has credentials available."""
    if provider.lower() == "ollama":
        return True  # Ollama doesn't need an API key

    # Check OpenCastor's own token store for Anthropic
    if provider.lower() == "anthropic":
        token_path = os.path.expanduser("~/.opencastor/anthropic-token")
        if os.path.exists(token_path):
            try:
                with open(token_path) as f:
                    if f.read().strip():
                        return True
            except Exception:
                pass

    google_auth_mode = os.getenv("GOOGLE_AUTH_MODE", "").lower()
    if provider.lower() == "google" and google_auth_mode == "adc":
        return True

    hf_auth_mode = os.getenv("HF_AUTH_MODE", "").lower()
    if provider.lower() == "huggingface" and hf_auth_mode == "cli":
        return True

    return resolve_provider_key(provider, config) is not None


def check_channel_ready(channel: str, config: Optional[Dict] = None) -> bool:
    """Check whether the given channel has all required credentials.

    Channels with no required credentials (e.g. QR-code-based WhatsApp)
    are always considered ready.  Unknown channels return False.
    """
    if channel.lower() not in CHANNEL_AUTH_MAP:
        return False
    required = CHANNEL_AUTH_MAP[channel.lower()]
    if len(required) == 0:
        return True  # No credentials needed (e.g. WhatsApp QR code auth)
    creds = resolve_channel_credentials(channel, config)
    return len(creds) == len(required)


def list_available_providers(config: Optional[Dict] = None) -> Dict[str, bool]:
    """Return a map of provider -> ready status for all known providers."""
    return {name: check_provider_ready(name, config) for name in PROVIDER_AUTH_MAP}


def list_available_channels(config: Optional[Dict] = None) -> Dict[str, bool]:
    """Return a map of channel -> ready status for all known channels."""
    return {name: check_channel_ready(name, config) for name in CHANNEL_AUTH_MAP}


def check_jwt_configured() -> bool:
    """Return True if OPENCASTOR_JWT_SECRET is set."""
    return bool(os.getenv("OPENCASTOR_JWT_SECRET"))
