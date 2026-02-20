"""Tests for castor.channels.whatsapp_neonize — neonize-based WhatsApp channel."""

import os
from types import SimpleNamespace
from unittest import mock

import pytest


# =====================================================================
# Session DB path resolution
# =====================================================================
class TestGetSessionDbPath:
    def test_default_path(self):
        from castor.channels.whatsapp_neonize import _get_session_db_path

        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENCASTOR_DATA_DIR", None)
            path = _get_session_db_path()
            expected = os.path.join(os.path.expanduser("~"), ".opencastor", "whatsapp_session.db")
            assert path == expected

    def test_explicit_config(self):
        from castor.channels.whatsapp_neonize import _get_session_db_path

        path = _get_session_db_path({"session_db": "/tmp/test_session.db"})
        assert path == "/tmp/test_session.db"

    def test_env_var_override(self, tmp_path):
        from castor.channels.whatsapp_neonize import _get_session_db_path

        data_dir = str(tmp_path / "castor_data")
        with mock.patch.dict(os.environ, {"OPENCASTOR_DATA_DIR": data_dir}):
            path = _get_session_db_path()
            assert path == os.path.join(data_dir, "whatsapp_session.db")
            assert os.path.isdir(data_dir)

    def test_config_takes_precedence_over_env(self, tmp_path):
        from castor.channels.whatsapp_neonize import _get_session_db_path

        data_dir = str(tmp_path / "env_dir")
        with mock.patch.dict(os.environ, {"OPENCASTOR_DATA_DIR": data_dir}):
            path = _get_session_db_path({"session_db": "/tmp/explicit.db"})
            assert path == "/tmp/explicit.db"


# =====================================================================
# Number normalization
# =====================================================================
class TestNormalizeNumber:
    def test_strips_plus(self):
        from castor.channels.whatsapp_neonize import _normalize_number

        assert _normalize_number("+19169967105") == "19169967105"

    def test_strips_dashes(self):
        from castor.channels.whatsapp_neonize import _normalize_number

        assert _normalize_number("1-916-996-7105") == "19169967105"

    def test_bare_number_unchanged(self):
        from castor.channels.whatsapp_neonize import _normalize_number

        assert _normalize_number("19169967105") == "19169967105"

    def test_empty_string(self):
        from castor.channels.whatsapp_neonize import _normalize_number

        assert _normalize_number("") == ""

    def test_none_handled(self):
        from castor.channels.whatsapp_neonize import _normalize_number

        assert _normalize_number(None) == ""


# =====================================================================
# Import error handling
# =====================================================================
class TestImportError:
    def test_raises_when_neonize_missing(self):
        from castor.channels import whatsapp_neonize

        original = whatsapp_neonize.HAS_NEONIZE
        try:
            whatsapp_neonize.HAS_NEONIZE = False
            with pytest.raises(ImportError, match="neonize"):
                whatsapp_neonize.WhatsAppChannel({})
        finally:
            whatsapp_neonize.HAS_NEONIZE = original


# =====================================================================
# Helpers
# =====================================================================

def _make_channel(config: dict = None):
    """Build a WhatsAppChannel with neonize mocked out.

    _dispatch is replaced with a MagicMock so tests never touch asyncio.
    """
    from castor.channels import whatsapp_neonize

    config = config or {}
    with mock.patch.object(whatsapp_neonize, "HAS_NEONIZE", True):
        ch = whatsapp_neonize.WhatsAppChannel.__new__(whatsapp_neonize.WhatsAppChannel)
        ch.config = config
        ch._on_message_callback = None
        ch.logger = whatsapp_neonize.logger
        ch._session_db = "/tmp/test.db"
        ch._client = None
        ch._thread = None
        ch._loop = None  # keep None so _dispatch guard triggers
        ch._connected = False
        ch._stop_flag = False
        ch._owner_number = None
        ch._dm_policy = config.get("dm_policy", "allowlist")
        ch._allow_from = [
            whatsapp_neonize._normalize_number(n)
            for n in config.get("allow_from", [])
        ]
        ch._self_chat_mode = bool(config.get("self_chat_mode", True))
        ch._group_policy = config.get("group_policy", "disabled")
        ch._ack_reaction = config.get("ack_reaction")
        ch._pairing_requests = {}
        # ← key: replace _dispatch so no asyncio involved
        ch._dispatch = mock.MagicMock()
        return ch


def _make_message_event(
    is_from_me=False,
    chat_user="19169967105",
    chat_server="s.whatsapp.net",
    sender_user=None,
    text="hello",
):
    chat = SimpleNamespace(User=chat_user, Server=chat_server)
    sender = SimpleNamespace(User=sender_user or chat_user)
    source = SimpleNamespace(IsFromMe=is_from_me, Chat=chat, Sender=sender)
    info = SimpleNamespace(MessageSource=source, ID="msg-001")
    ext = SimpleNamespace(text=text)
    msg = SimpleNamespace(conversation=text, extendedTextMessage=ext)
    return SimpleNamespace(Info=info, Message=msg)


# =====================================================================
# _is_allowed
# =====================================================================
class TestIsAllowed:
    def test_owner_number_allowed(self):
        ch = _make_channel({"allow_from": ["+19169967105"]})
        assert ch._is_allowed("19169967105") is True

    def test_unknown_number_denied(self):
        ch = _make_channel({"allow_from": ["+19169967105"]})
        assert ch._is_allowed("15555550001") is False

    def test_empty_allowlist_permits_all(self):
        ch = _make_channel({"allow_from": []})
        assert ch._is_allowed("99999999999") is True

    def test_e164_matches_bare(self):
        ch = _make_channel({"allow_from": ["+19169967105"]})
        assert ch._is_allowed("+19169967105") is True

    def test_multiple_numbers(self):
        ch = _make_channel({"allow_from": ["+19169967105", "+15105550000"]})
        assert ch._is_allowed("15105550000") is True
        assert ch._is_allowed("13005550000") is False


# =====================================================================
# DM policy — allowlist
# =====================================================================
class TestDmPolicyAllowlist:
    def test_allowed_sender_dispatches(self):
        ch = _make_channel(
            {"dm_policy": "allowlist", "allow_from": ["+19169967105"], "self_chat_mode": False}
        )
        ch._owner_number = "19169967105"
        msg = _make_message_event(is_from_me=False, chat_user="19169967105", text="status?")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        ch._dispatch.assert_called_once()

    def test_blocked_sender_gets_deny_message(self):
        ch = _make_channel(
            {"dm_policy": "allowlist", "allow_from": ["+19169967105"], "self_chat_mode": False}
        )
        msg = _make_message_event(is_from_me=False, chat_user="15555550001", text="hi bot")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        ch._dispatch.assert_not_called()
        fake_client.send_message.assert_called_once()
        deny_text = fake_client.send_message.call_args[0][1]
        assert "denied" in deny_text.lower() or "⛔" in deny_text

    def test_open_policy_allows_anyone(self):
        ch = _make_channel({"dm_policy": "open", "allow_from": [], "self_chat_mode": False})
        msg = _make_message_event(is_from_me=False, chat_user="15555550001", text="hi")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        ch._dispatch.assert_called_once()
        for call in fake_client.send_message.call_args_list:
            assert "denied" not in str(call).lower()


# =====================================================================
# Self-chat mode
# =====================================================================
class TestSelfChatMode:
    def test_self_chat_enabled_dispatches_own_message(self):
        ch = _make_channel(
            {"dm_policy": "allowlist", "allow_from": ["+19169967105"], "self_chat_mode": True}
        )
        ch._owner_number = "19169967105"
        msg = _make_message_event(is_from_me=True, chat_user="19169967105", text="hey robot")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        ch._dispatch.assert_called_once()
        fake_client.send_message.assert_not_called()

    def test_self_chat_disabled_skips_own_message(self):
        ch = _make_channel({"self_chat_mode": False})
        ch._owner_number = "19169967105"
        msg = _make_message_event(is_from_me=True, chat_user="19169967105", text="hey robot")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        ch._dispatch.assert_not_called()
        fake_client.send_message.assert_not_called()

    def test_self_chat_skips_messages_sent_to_others(self):
        """IsFromMe=True but sent to someone else → skip."""
        ch = _make_channel({"self_chat_mode": True})
        ch._owner_number = "19169967105"
        msg = _make_message_event(is_from_me=True, chat_user="15555550001", text="hey friend")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        ch._dispatch.assert_not_called()


# =====================================================================
# Group policy
# =====================================================================
class TestGroupPolicy:
    def test_group_disabled_ignores_group_messages(self):
        ch = _make_channel({"group_policy": "disabled"})
        msg = _make_message_event(
            is_from_me=False,
            chat_user="120363000000001234",
            chat_server="g.us",
            text="hello group",
        )
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        ch._dispatch.assert_not_called()

    def test_group_open_dispatches(self):
        ch = _make_channel({"group_policy": "open"})
        msg = _make_message_event(
            is_from_me=False,
            chat_user="120363000000001234",
            chat_server="g.us",
            text="hello group",
        )
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        ch._dispatch.assert_called_once()


# =====================================================================
# Pairing policy
# =====================================================================
class TestPairingPolicy:
    def test_unknown_sender_gets_pairing_message(self):
        ch = _make_channel({"dm_policy": "pairing", "allow_from": ["+19169967105"]})
        msg = _make_message_event(is_from_me=False, chat_user="15555550001", text="hi")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        ch._dispatch.assert_not_called()
        fake_client.send_message.assert_called_once()
        pairing_text = fake_client.send_message.call_args[0][1]
        assert "👋" in pairing_text or "code" in pairing_text.lower()

    def test_pairing_request_stored(self):
        ch = _make_channel({"dm_policy": "pairing", "allow_from": ["+19169967105"]})
        msg = _make_message_event(is_from_me=False, chat_user="15555550001", text="hi")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        assert "15555550001" in ch._pairing_requests

    def test_approve_pairing_adds_to_allowlist(self):
        ch = _make_channel({"dm_policy": "pairing", "allow_from": ["+19169967105"]})
        ch._pairing_requests["15555550001"] = "ABC123"
        result = ch.approve_pairing("ABC123")
        assert result == "15555550001"
        assert "15555550001" in ch._allow_from

    def test_approve_invalid_code_returns_none(self):
        ch = _make_channel({"dm_policy": "pairing"})
        ch._pairing_requests["15555550001"] = "ABC123"
        assert ch.approve_pairing("WRONG1") is None

    def test_list_pairing_requests(self):
        ch = _make_channel({"dm_policy": "pairing"})
        ch._pairing_requests["15555550001"] = "ABC123"
        requests = ch.list_pairing_requests()
        assert len(requests) == 1
        assert requests[0]["code"] == "ABC123"
        assert "+15555550001" in requests[0]["number"]


# =====================================================================
# Owner auto-added to allowFrom on connect
# =====================================================================
class TestOwnerAutoAdd:
    def test_owner_added_on_connect(self):
        ch = _make_channel({"allow_from": []})
        ch._owner_number = "19169967105"
        if ch._owner_number and ch._owner_number not in ch._allow_from:
            ch._allow_from.append(ch._owner_number)
        assert "19169967105" in ch._allow_from

    def test_owner_not_duplicated_if_already_present(self):
        ch = _make_channel({"allow_from": ["+19169967105"]})
        ch._owner_number = "19169967105"
        if ch._owner_number and ch._owner_number not in ch._allow_from:
            ch._allow_from.append(ch._owner_number)
        assert ch._allow_from.count("19169967105") == 1


# =====================================================================
# Ack reaction
# =====================================================================
class TestAckReaction:
    def test_ack_reaction_sent_on_allowed_message(self):
        ch = _make_channel(
            {"dm_policy": "open", "allow_from": [], "self_chat_mode": False, "ack_reaction": "👀"}
        )
        msg = _make_message_event(is_from_me=False, chat_user="15555550001", text="yo")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        fake_client.send_reaction.assert_called_once_with(mock.ANY, "👀", "msg-001")

    def test_no_ack_when_not_configured(self):
        ch = _make_channel({"dm_policy": "open", "allow_from": [], "self_chat_mode": False})
        msg = _make_message_event(is_from_me=False, chat_user="15555550001", text="yo")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        fake_client.send_reaction.assert_not_called()

    def test_no_ack_on_denied_message(self):
        """Denied messages should not get an ack reaction."""
        ch = _make_channel(
            {
                "dm_policy": "allowlist",
                "allow_from": ["+19169967105"],
                "ack_reaction": "👀",
                "self_chat_mode": False,
            }
        )
        msg = _make_message_event(is_from_me=False, chat_user="15555550001", text="denied?")
        fake_client = mock.MagicMock()
        ch._handle_incoming(fake_client, msg)
        fake_client.send_reaction.assert_not_called()
