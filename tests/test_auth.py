"""Tests for castor.auth -- credential resolution and readiness checks."""

import os
from unittest.mock import patch

from castor.auth import (
    CHANNEL_AUTH_MAP,
    PROVIDER_AUTH_MAP,
    check_channel_ready,
    check_provider_ready,
    list_available_channels,
    list_available_providers,
    resolve_channel_credentials,
    resolve_provider_key,
)


# =====================================================================
# Auth map structure tests
# =====================================================================
class TestAuthMaps:
    def test_provider_map_has_known_providers(self):
        for name in ("google", "openai", "anthropic", "ollama"):
            assert name in PROVIDER_AUTH_MAP

    def test_provider_map_entries_are_tuples(self):
        for name, entry in PROVIDER_AUTH_MAP.items():
            assert isinstance(entry, tuple)
            assert len(entry) == 2

    def test_channel_map_has_known_channels(self):
        for name in ("whatsapp", "telegram", "discord", "slack"):
            assert name in CHANNEL_AUTH_MAP

    def test_channel_map_entries_are_lists(self):
        for name, entries in CHANNEL_AUTH_MAP.items():
            assert isinstance(entries, list)
            for entry in entries:
                assert isinstance(entry, tuple)
                assert len(entry) == 2


# =====================================================================
# resolve_provider_key tests
# =====================================================================
class TestResolveProviderKey:
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key-123"})
    def test_resolve_from_env(self):
        key = resolve_provider_key("google")
        assert key == "test-key-123"

    @patch.dict(os.environ, {}, clear=True)
    def test_resolve_from_config(self):
        key = resolve_provider_key("google", {"api_key": "config-key"})
        assert key == "config-key"

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "env-key"})
    def test_env_takes_priority_over_config(self):
        key = resolve_provider_key("google", {"api_key": "config-key"})
        assert key == "env-key"

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_none_when_no_key(self):
        key = resolve_provider_key("google")
        assert key is None

    def test_unknown_provider_returns_none(self):
        key = resolve_provider_key("nonexistent")
        assert key is None

    @patch.dict(os.environ, {"OPENAI_API_KEY": "openai-key"})
    def test_case_insensitive(self):
        key = resolve_provider_key("OpenAI")
        assert key == "openai-key"


# =====================================================================
# resolve_channel_credentials tests
# =====================================================================
class TestResolveChannelCredentials:
    def test_whatsapp_neonize_no_creds_needed(self):
        creds = resolve_channel_credentials("whatsapp")
        assert creds == {}

    @patch.dict(
        os.environ,
        {
            "TWILIO_ACCOUNT_SID": "sid123",
            "TWILIO_AUTH_TOKEN": "token456",
            "TWILIO_WHATSAPP_NUMBER": "+1234567890",
        },
    )
    def test_resolve_all_whatsapp_twilio_creds(self):
        creds = resolve_channel_credentials("whatsapp_twilio")
        assert creds["account_sid"] == "sid123"
        assert creds["auth_token"] == "token456"
        assert creds["whatsapp_number"] == "+1234567890"

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "bot-token"})
    def test_resolve_telegram(self):
        creds = resolve_channel_credentials("telegram")
        assert creds["bot_token"] == "bot-token"

    @patch.dict(os.environ, {}, clear=True)
    def test_partial_creds(self):
        creds = resolve_channel_credentials(
            "whatsapp_twilio", {"account_sid": "from-config"}
        )
        assert creds["account_sid"] == "from-config"
        assert "auth_token" not in creds

    @patch.dict(os.environ, {}, clear=True)
    def test_no_creds(self):
        creds = resolve_channel_credentials("telegram")
        assert creds == {}

    def test_unknown_channel(self):
        creds = resolve_channel_credentials("nonexistent")
        assert creds == {}


# =====================================================================
# check_provider_ready tests
# =====================================================================
class TestCheckProviderReady:
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key"})
    def test_ready_with_env_key(self):
        assert check_provider_ready("anthropic") is True

    @patch.dict(os.environ, {}, clear=True)
    def test_not_ready_without_key(self):
        assert check_provider_ready("anthropic") is False

    def test_ollama_always_ready(self):
        assert check_provider_ready("ollama") is True

    @patch.dict(os.environ, {}, clear=True)
    def test_ready_with_config_key(self):
        assert check_provider_ready("google", {"api_key": "key"}) is True


# =====================================================================
# check_channel_ready tests
# =====================================================================
class TestCheckChannelReady:
    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"})
    def test_telegram_ready(self):
        assert check_channel_ready("telegram") is True

    @patch.dict(os.environ, {}, clear=True)
    def test_telegram_not_ready(self):
        assert check_channel_ready("telegram") is False

    def test_whatsapp_neonize_always_ready(self):
        # QR-code-based WhatsApp needs no env credentials
        assert check_channel_ready("whatsapp") is True

    @patch.dict(
        os.environ,
        {
            "TWILIO_ACCOUNT_SID": "sid",
            "TWILIO_AUTH_TOKEN": "token",
        },
        clear=True,
    )
    def test_whatsapp_twilio_partial_not_ready(self):
        # Missing TWILIO_WHATSAPP_NUMBER
        assert check_channel_ready("whatsapp_twilio") is False

    @patch.dict(
        os.environ,
        {
            "TWILIO_ACCOUNT_SID": "sid",
            "TWILIO_AUTH_TOKEN": "token",
            "TWILIO_WHATSAPP_NUMBER": "+1234567890",
        },
    )
    def test_whatsapp_twilio_all_creds_ready(self):
        assert check_channel_ready("whatsapp_twilio") is True

    def test_unknown_channel_not_ready(self):
        assert check_channel_ready("nonexistent") is False


# =====================================================================
# list functions tests
# =====================================================================
class TestListFunctions:
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "key"}, clear=True)
    def test_list_providers(self):
        providers = list_available_providers()
        assert providers["google"] is True
        assert providers["openai"] is False
        assert providers["ollama"] is True  # Always ready

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}, clear=True)
    def test_list_channels(self):
        channels = list_available_channels()
        assert channels["telegram"] is True
        assert channels["whatsapp"] is True  # QR code -- always ready
        assert channels["discord"] is False
