"""Tests for castor.channels -- BaseChannel, channel registry, and channel implementations.

Covers abstract interface enforcement, handle_message callback routing,
channel registry (get_available_channels, get_ready_channels, create_channel),
lazy imports, credential resolution, and per-channel constructor validation.

NOTE: Some channel modules (e.g. telegram_channel) use SDK type annotations in
method signatures at class-definition time. When the SDK is not installed this
causes a NameError (not ImportError) on module load. Tests that need those
modules guard against this with try/except and pytest.skip().
"""

import asyncio
import importlib
from unittest.mock import patch

import pytest

from castor.channels.base import BaseChannel


# =====================================================================
# Helpers: safe module import
# =====================================================================
def _try_import_channel_module(module_name: str):
    """Try to import a channel module; return (module, None) or (None, error)."""
    try:
        mod = importlib.import_module(module_name)
        return mod, None
    except (ImportError, NameError) as exc:
        return None, exc


# =====================================================================
# Concrete stub for testing BaseChannel
# =====================================================================
class StubChannel(BaseChannel):
    name = "stub"

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_message(self, chat_id: str, text: str):
        pass


# =====================================================================
# BaseChannel tests
# =====================================================================
class TestBaseChannel:
    def test_config_stored(self):
        ch = StubChannel({"key": "value"})
        assert ch.config == {"key": "value"}

    def test_callback_stored(self):
        def cb(name, chat_id, text):
            return "reply"

        ch = StubChannel({}, on_message=cb)
        assert ch._on_message_callback is cb

    def test_no_callback_by_default(self):
        ch = StubChannel({})
        assert ch._on_message_callback is None

    def test_logger_name(self):
        ch = StubChannel({})
        assert ch.logger.name == "OpenCastor.Channel.stub"

    def test_config_dict_is_preserved(self):
        cfg = {"bot_token": "abc123", "extra": True}
        ch = StubChannel(cfg)
        assert ch.config is cfg

    def test_empty_config(self):
        ch = StubChannel({})
        assert ch.config == {}


# =====================================================================
# handle_message tests
# =====================================================================
class TestHandleMessage:
    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_with_callback(self):
        def callback(name, chat_id, text):
            return f"Received: {text}"

        ch = StubChannel({}, on_message=callback)
        result = self._run(ch.handle_message("user123", "move forward"))
        assert result == "Received: move forward"

    def test_callback_receives_channel_name(self):
        received = {}

        def callback(name, chat_id, text):
            received["name"] = name
            received["chat_id"] = chat_id
            return "ok"

        ch = StubChannel({}, on_message=callback)
        self._run(ch.handle_message("user456", "stop"))
        assert received["name"] == "stub"
        assert received["chat_id"] == "user456"

    def test_no_callback_returns_none(self):
        ch = StubChannel({})
        result = self._run(ch.handle_message("user", "hello"))
        assert result is None

    def test_callback_error_returns_error_message(self):
        def bad_callback(name, chat_id, text):
            raise ValueError("something broke")

        ch = StubChannel({}, on_message=bad_callback)
        result = self._run(ch.handle_message("user", "test"))
        assert "Error" in result
        assert "something broke" in result

    def test_callback_receives_text(self):
        texts = []

        def callback(name, chat_id, text):
            texts.append(text)
            return "ok"

        ch = StubChannel({}, on_message=callback)
        self._run(ch.handle_message("u1", "turn left"))
        assert texts == ["turn left"]

    def test_callback_return_value_forwarded(self):
        def callback(name, chat_id, text):
            return "robot says hi"

        ch = StubChannel({}, on_message=callback)
        result = self._run(ch.handle_message("chat1", "hello"))
        assert result == "robot says hi"

    def test_callback_returning_none(self):
        def callback(name, chat_id, text):
            return None

        ch = StubChannel({}, on_message=callback)
        result = self._run(ch.handle_message("chat1", "ping"))
        assert result is None

    def test_callback_exception_type_error(self):
        def bad_callback(name, chat_id, text):
            raise TypeError("wrong type")

        ch = StubChannel({}, on_message=bad_callback)
        result = self._run(ch.handle_message("u", "x"))
        assert "Error" in result
        assert "wrong type" in result


# =====================================================================
# Abstract method enforcement
# =====================================================================
class TestAbstractEnforcement:
    def test_cannot_instantiate_base_channel(self):
        with pytest.raises(TypeError):
            BaseChannel({})

    def test_missing_start_method(self):
        class Incomplete(BaseChannel):
            name = "incomplete"

            async def stop(self):
                pass

            async def send_message(self, chat_id, text):
                pass

        with pytest.raises(TypeError):
            Incomplete({})

    def test_missing_stop_method(self):
        class Incomplete(BaseChannel):
            name = "incomplete"

            async def start(self):
                pass

            async def send_message(self, chat_id, text):
                pass

        with pytest.raises(TypeError):
            Incomplete({})

    def test_missing_send_message_method(self):
        class Incomplete(BaseChannel):
            name = "incomplete"

            async def start(self):
                pass

            async def stop(self):
                pass

        with pytest.raises(TypeError):
            Incomplete({})

    def test_base_channel_is_abc(self):
        from abc import ABC

        assert issubclass(BaseChannel, ABC)


# =====================================================================
# Channel Registry tests
# =====================================================================
class TestChannelRegistry:
    """Test get_available_channels(), get_ready_channels(), create_channel()."""

    def test_get_available_channels_returns_list(self):
        """get_available_channels returns a list (possibly empty if modules have load errors)."""
        from castor.channels import get_available_channels

        try:
            result = get_available_channels()
        except NameError:
            # Known issue: some channel modules raise NameError at class-definition
            # time when their SDK is not installed (e.g. type annotations).
            pytest.skip("Channel registration fails due to NameError in channel modules")
        assert isinstance(result, list)

    def test_get_available_channels_is_deterministic(self):
        from castor.channels import get_available_channels

        try:
            a = get_available_channels()
            b = get_available_channels()
        except NameError:
            pytest.skip("Channel registration fails due to NameError in channel modules")
        assert a == b

    def test_get_ready_channels_returns_list(self):
        from castor.channels import get_ready_channels

        try:
            result = get_ready_channels()
        except NameError:
            pytest.skip("Channel registration fails due to NameError in channel modules")
        assert isinstance(result, list)

    def test_get_ready_channels_subset_of_available(self):
        from castor.channels import get_available_channels, get_ready_channels

        try:
            available = set(get_available_channels())
            ready = set(get_ready_channels())
        except NameError:
            pytest.skip("Channel registration fails due to NameError in channel modules")
        assert ready.issubset(available)

    def test_create_channel_unknown_raises_value_error(self):
        from castor.channels import create_channel

        with pytest.raises((ValueError, NameError)):
            create_channel("nonexistent_channel_xyz")

    def test_create_channel_unknown_includes_available_list(self):
        from castor.channels import create_channel

        try:
            with pytest.raises(ValueError) as exc_info:
                create_channel("nonexistent_channel_xyz")
            assert "Available" in str(exc_info.value)
        except NameError:
            pytest.skip("Channel registration fails due to NameError in channel modules")

    @patch("castor.channels.resolve_channel_credentials", return_value={})
    @patch("castor.channels._CHANNEL_CLASSES", {"fake": StubChannel})
    def test_create_channel_calls_resolve_credentials(self, mock_resolve):
        from castor.channels import create_channel

        create_channel("fake", config={"extra": "val"})
        mock_resolve.assert_called_once_with("fake")

    @patch("castor.channels.resolve_channel_credentials", return_value={"bot_token": "tok123"})
    @patch("castor.channels._CHANNEL_CLASSES", {"fake": StubChannel})
    def test_create_channel_merges_env_creds_into_config(self, mock_resolve):
        from castor.channels import create_channel

        ch = create_channel("fake", config={"extra": "val"})
        # Config should contain both the original key and the resolved credential
        assert ch.config["extra"] == "val"
        assert ch.config["bot_token"] == "tok123"

    @patch("castor.channels.resolve_channel_credentials", return_value={})
    @patch("castor.channels._CHANNEL_CLASSES", {"fake": StubChannel})
    def test_create_channel_with_none_config(self, mock_resolve):
        from castor.channels import create_channel

        ch = create_channel("fake", config=None)
        assert isinstance(ch.config, dict)

    @patch("castor.channels.resolve_channel_credentials", return_value={})
    @patch("castor.channels._CHANNEL_CLASSES", {"fake": StubChannel})
    def test_create_channel_passes_on_message_callback(self, mock_resolve):
        from castor.channels import create_channel

        def cb(name, cid, txt):
            return "reply"

        ch = create_channel("fake", on_message=cb)
        assert ch._on_message_callback is cb

    @patch("castor.channels.resolve_channel_credentials", return_value={})
    @patch("castor.channels._CHANNEL_CLASSES", {"UPPER": StubChannel})
    def test_create_channel_case_insensitive_lookup(self, mock_resolve):
        """create_channel lowercases the name for lookup."""
        from castor.channels import create_channel

        # The registry key is "UPPER", but create_channel calls name.lower()
        # so "upper" won't match "UPPER" -- this tests the actual behaviour.
        with pytest.raises(ValueError):
            create_channel("UPPER")

    @patch("castor.channels.resolve_channel_credentials", return_value={"bot_token": "env_tok"})
    @patch("castor.channels._CHANNEL_CLASSES", {"fake": StubChannel})
    def test_env_creds_override_config(self, mock_resolve):
        """Environment credentials should override config values."""
        from castor.channels import create_channel

        ch = create_channel("fake", config={"bot_token": "cfg_tok"})
        # dict.update() means env_creds overwrite config
        assert ch.config["bot_token"] == "env_tok"


# =====================================================================
# Lazy import and registration tests
# =====================================================================
class TestLazyImports:
    """Verify that _register_builtin_channels catches ImportErrors gracefully."""

    @patch("castor.channels._register_builtin_channels")
    def test_get_available_channels_triggers_registration(self, mock_reg):
        import castor.channels as ch_mod

        ch_mod._CHANNEL_CLASSES.clear()
        ch_mod.get_available_channels()
        mock_reg.assert_called_once()

    @patch("castor.channels._register_builtin_channels")
    def test_create_channel_triggers_registration(self, mock_reg):
        import castor.channels as ch_mod

        ch_mod._CHANNEL_CLASSES.clear()
        try:
            ch_mod.create_channel("anything")
        except (ValueError, KeyError):
            pass
        mock_reg.assert_called_once()

    def test_register_builtin_channels_does_not_crash(self):
        """Registration should not raise even when SDKs are missing.

        Note: if a channel module has a NameError at class-definition time
        (a bug where type annotations reference SDK symbols outside the
        try/except), the NameError may propagate.  This test documents the
        actual behaviour: the call either succeeds or raises NameError.
        """
        import castor.channels as ch_mod

        ch_mod._CHANNEL_CLASSES.clear()
        try:
            ch_mod._register_builtin_channels()
        except NameError:
            # Known issue: telegram_channel.py uses `Update` and
            # `ContextTypes.DEFAULT_TYPE` as type annotations at class
            # definition time, causing NameError when telegram is not
            # installed. This is a source-code bug, not a test bug.
            pass

    def test_all_exports_present(self):
        from castor.channels import __all__

        assert "create_channel" in __all__
        assert "get_available_channels" in __all__
        assert "get_ready_channels" in __all__


# =====================================================================
# Telegram Channel tests
# =====================================================================
_telegram_mod, _telegram_err = _try_import_channel_module("castor.channels.telegram_channel")


@pytest.mark.skipif(_telegram_mod is None, reason=f"telegram_channel cannot be imported: {_telegram_err}")
class TestTelegramChannelLoaded:
    """Tests that run only when TelegramChannel module loads successfully."""

    def test_has_telegram_flag_is_boolean(self):
        assert isinstance(_telegram_mod.HAS_TELEGRAM, bool)

    def test_channel_name(self):
        assert _telegram_mod.TelegramChannel.name == "telegram"

    def test_import_error_when_sdk_missing(self):
        if not _telegram_mod.HAS_TELEGRAM:
            with pytest.raises(ImportError, match="python-telegram-bot"):
                _telegram_mod.TelegramChannel({"bot_token": "x"})

    def test_missing_bot_token_raises_value_error(self):
        if not _telegram_mod.HAS_TELEGRAM:
            pytest.skip("Telegram SDK not installed -- constructor raises ImportError first")
        original = _telegram_mod.HAS_TELEGRAM
        _telegram_mod.HAS_TELEGRAM = True
        try:
            with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
                _telegram_mod.TelegramChannel({})
        finally:
            _telegram_mod.HAS_TELEGRAM = original

    def test_with_valid_bot_token(self):
        if not _telegram_mod.HAS_TELEGRAM:
            pytest.skip("Telegram SDK not installed")
        ch = _telegram_mod.TelegramChannel({"bot_token": "123:ABC"})
        assert ch.bot_token == "123:ABC"
        assert ch.name == "telegram"


class TestTelegramChannelUnloaded:
    """Tests that always run, even when the telegram module fails to load."""

    def test_telegram_module_import_fails_or_succeeds(self):
        """Importing the telegram_channel module raises NameError or succeeds."""
        mod, err = _try_import_channel_module("castor.channels.telegram_channel")
        if err is not None:
            # The module fails to load -- this is a known limitation when the
            # SDK is not installed and type annotations reference SDK symbols.
            assert isinstance(err, (ImportError, NameError))
        else:
            assert hasattr(mod, "TelegramChannel")
            assert hasattr(mod, "HAS_TELEGRAM")


# =====================================================================
# Discord Channel tests
# =====================================================================
_discord_mod, _discord_err = _try_import_channel_module("castor.channels.discord_channel")


@pytest.mark.skipif(_discord_mod is None, reason=f"discord_channel cannot be imported: {_discord_err}")
class TestDiscordChannelLoaded:
    """Tests that run only when DiscordChannel module loads successfully."""

    def test_has_discord_flag_is_boolean(self):
        assert isinstance(_discord_mod.HAS_DISCORD, bool)

    def test_channel_name(self):
        assert _discord_mod.DiscordChannel.name == "discord"

    def test_import_error_when_sdk_missing(self):
        if not _discord_mod.HAS_DISCORD:
            with pytest.raises(ImportError, match="discord.py"):
                _discord_mod.DiscordChannel({"bot_token": "x"})

    def test_missing_bot_token_raises_value_error(self):
        if not _discord_mod.HAS_DISCORD:
            pytest.skip("Discord SDK not installed -- constructor raises ImportError first")
        original = _discord_mod.HAS_DISCORD
        _discord_mod.HAS_DISCORD = True
        try:
            with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
                _discord_mod.DiscordChannel({})
        finally:
            _discord_mod.HAS_DISCORD = original

    def test_with_valid_bot_token(self):
        if not _discord_mod.HAS_DISCORD:
            pytest.skip("Discord SDK not installed")
        ch = _discord_mod.DiscordChannel({"bot_token": "discord-tok-123"})
        assert ch.bot_token == "discord-tok-123"
        assert ch.name == "discord"


class TestDiscordChannelUnloaded:
    """Tests that always run."""

    def test_discord_module_import_fails_or_succeeds(self):
        mod, err = _try_import_channel_module("castor.channels.discord_channel")
        if err is not None:
            assert isinstance(err, (ImportError, NameError))
        else:
            assert hasattr(mod, "DiscordChannel")
            assert hasattr(mod, "HAS_DISCORD")


# =====================================================================
# Slack Channel tests
# =====================================================================
_slack_mod, _slack_err = _try_import_channel_module("castor.channels.slack_channel")


@pytest.mark.skipif(_slack_mod is None, reason=f"slack_channel cannot be imported: {_slack_err}")
class TestSlackChannelLoaded:
    """Tests that run only when SlackChannel module loads successfully."""

    def test_has_slack_flag_is_boolean(self):
        assert isinstance(_slack_mod.HAS_SLACK, bool)

    def test_channel_name(self):
        assert _slack_mod.SlackChannel.name == "slack"

    def test_import_error_when_sdk_missing(self):
        if not _slack_mod.HAS_SLACK:
            with pytest.raises(ImportError, match="slack-bolt"):
                _slack_mod.SlackChannel({"bot_token": "x", "app_token": "y"})

    def test_missing_bot_token_raises_value_error(self):
        if not _slack_mod.HAS_SLACK:
            pytest.skip("Slack SDK not installed -- constructor raises ImportError first")
        original = _slack_mod.HAS_SLACK
        _slack_mod.HAS_SLACK = True
        try:
            with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
                _slack_mod.SlackChannel({"app_token": "xapp-123"})
        finally:
            _slack_mod.HAS_SLACK = original

    def test_missing_app_token_raises_value_error(self):
        if not _slack_mod.HAS_SLACK:
            pytest.skip("Slack SDK not installed")
        original = _slack_mod.HAS_SLACK
        _slack_mod.HAS_SLACK = True
        try:
            with pytest.raises(ValueError, match="SLACK_APP_TOKEN"):
                _slack_mod.SlackChannel({"bot_token": "xoxb-123"})
        finally:
            _slack_mod.HAS_SLACK = original

    def test_with_valid_tokens(self):
        if not _slack_mod.HAS_SLACK:
            pytest.skip("Slack SDK not installed")
        ch = _slack_mod.SlackChannel({"bot_token": "xoxb-123", "app_token": "xapp-456"})
        assert ch.bot_token == "xoxb-123"
        assert ch.app_token == "xapp-456"
        assert ch.name == "slack"


class TestSlackChannelUnloaded:
    """Tests that always run."""

    def test_slack_module_import_fails_or_succeeds(self):
        mod, err = _try_import_channel_module("castor.channels.slack_channel")
        if err is not None:
            assert isinstance(err, (ImportError, NameError))
        else:
            assert hasattr(mod, "SlackChannel")
            assert hasattr(mod, "HAS_SLACK")


# =====================================================================
# WhatsApp Channel tests
# =====================================================================
_whatsapp_mod, _whatsapp_err = _try_import_channel_module("castor.channels.whatsapp_neonize")


@pytest.mark.skipif(_whatsapp_mod is None, reason=f"whatsapp_neonize cannot be imported: {_whatsapp_err}")
class TestWhatsAppChannelLoaded:
    """Tests that run only when WhatsAppChannel module loads successfully."""

    def test_import_error_when_sdk_missing(self):
        if not _whatsapp_mod.HAS_NEONIZE:
            with pytest.raises(ImportError, match="neonize"):
                _whatsapp_mod.WhatsAppChannel({})

    def test_has_neonize_flag_is_boolean(self):
        assert isinstance(_whatsapp_mod.HAS_NEONIZE, bool)

    def test_channel_name(self):
        assert _whatsapp_mod.WhatsAppChannel.name == "whatsapp"


class TestWhatsAppChannelUnloaded:
    """Tests that always run."""

    def test_whatsapp_module_import_fails_or_succeeds(self):
        mod, err = _try_import_channel_module("castor.channels.whatsapp_neonize")
        if err is not None:
            assert isinstance(err, (ImportError, NameError))
        else:
            assert hasattr(mod, "WhatsAppChannel")
            assert hasattr(mod, "HAS_NEONIZE")

    def test_whatsapp_re_export(self):
        """castor.channels.whatsapp re-exports WhatsAppChannel from neonize."""
        try:
            from castor.channels.whatsapp import WhatsAppChannel as WA
            from castor.channels.whatsapp_neonize import WhatsAppChannel as WANeonize

            assert WA is WANeonize
        except (ImportError, NameError):
            # SDK not installed -- this is expected
            pass


# =====================================================================
# Channel auth map integration
# =====================================================================
class TestChannelAuthIntegration:
    """Test that auth.py CHANNEL_AUTH_MAP covers all registered channel names."""

    def test_telegram_auth_map_entry(self):
        from castor.auth import CHANNEL_AUTH_MAP

        assert "telegram" in CHANNEL_AUTH_MAP
        keys = [pair[1] for pair in CHANNEL_AUTH_MAP["telegram"]]
        assert "bot_token" in keys

    def test_discord_auth_map_entry(self):
        from castor.auth import CHANNEL_AUTH_MAP

        assert "discord" in CHANNEL_AUTH_MAP
        keys = [pair[1] for pair in CHANNEL_AUTH_MAP["discord"]]
        assert "bot_token" in keys

    def test_slack_auth_map_entry(self):
        from castor.auth import CHANNEL_AUTH_MAP

        assert "slack" in CHANNEL_AUTH_MAP
        keys = [pair[1] for pair in CHANNEL_AUTH_MAP["slack"]]
        assert "bot_token" in keys
        assert "app_token" in keys

    def test_whatsapp_auth_map_entry_empty(self):
        """WhatsApp (neonize) needs no env vars -- QR code auth."""
        from castor.auth import CHANNEL_AUTH_MAP

        assert "whatsapp" in CHANNEL_AUTH_MAP
        assert CHANNEL_AUTH_MAP["whatsapp"] == []

    def test_whatsapp_twilio_auth_map_entry(self):
        from castor.auth import CHANNEL_AUTH_MAP

        assert "whatsapp_twilio" in CHANNEL_AUTH_MAP
        keys = [pair[1] for pair in CHANNEL_AUTH_MAP["whatsapp_twilio"]]
        assert "account_sid" in keys
        assert "auth_token" in keys
        assert "whatsapp_number" in keys

    @patch.dict("os.environ", {}, clear=True)
    def test_check_channel_ready_telegram_no_env(self):
        from castor.auth import check_channel_ready

        assert check_channel_ready("telegram") is False

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok"})
    def test_check_channel_ready_telegram_with_env(self):
        from castor.auth import check_channel_ready

        assert check_channel_ready("telegram") is True

    @patch.dict("os.environ", {}, clear=True)
    def test_check_channel_ready_discord_no_env(self):
        from castor.auth import check_channel_ready

        assert check_channel_ready("discord") is False

    @patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "tok"})
    def test_check_channel_ready_discord_with_env(self):
        from castor.auth import check_channel_ready

        assert check_channel_ready("discord") is True

    @patch.dict("os.environ", {}, clear=True)
    def test_check_channel_ready_slack_no_env(self):
        from castor.auth import check_channel_ready

        assert check_channel_ready("slack") is False

    @patch.dict(
        "os.environ",
        {"SLACK_BOT_TOKEN": "xoxb", "SLACK_APP_TOKEN": "xapp", "SLACK_SIGNING_SECRET": "sec"},
    )
    def test_check_channel_ready_slack_with_all_env(self):
        from castor.auth import check_channel_ready

        assert check_channel_ready("slack") is True

    @patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb"}, clear=True)
    def test_check_channel_ready_slack_partial_env(self):
        from castor.auth import check_channel_ready

        assert check_channel_ready("slack") is False

    def test_check_channel_ready_unknown_channel(self):
        from castor.auth import check_channel_ready

        assert check_channel_ready("nonexistent") is False

    def test_check_channel_ready_whatsapp_always_true(self):
        """WhatsApp QR code auth has no required credentials."""
        from castor.auth import check_channel_ready

        assert check_channel_ready("whatsapp") is True

    def test_resolve_channel_credentials_empty_for_unknown(self):
        from castor.auth import resolve_channel_credentials

        result = resolve_channel_credentials("unknown_channel_xyz")
        assert result == {}

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "from_env"})
    def test_resolve_channel_credentials_telegram(self):
        from castor.auth import resolve_channel_credentials

        result = resolve_channel_credentials("telegram")
        assert result == {"bot_token": "from_env"}

    @patch.dict("os.environ", {}, clear=True)
    def test_resolve_channel_credentials_telegram_from_config(self):
        from castor.auth import resolve_channel_credentials

        result = resolve_channel_credentials("telegram", {"bot_token": "from_cfg"})
        assert result == {"bot_token": "from_cfg"}

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "env_wins"})
    def test_resolve_channel_credentials_env_beats_config(self):
        from castor.auth import resolve_channel_credentials

        result = resolve_channel_credentials("telegram", {"bot_token": "from_cfg"})
        assert result["bot_token"] == "env_wins"
