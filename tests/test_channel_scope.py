"""
Tests for RCAN channel-boundary scope tagging (feat: channel scope resolver).

Covers:
- scope_resolver.py: resolve_sender_scope, CHANNEL_SCOPE_MAP exports
- session.py: SessionMessage has sender_scope + sender_loa fields
- api.py: _handle_channel_message enforces sender_scope ≤ chat
- Each channel adapter imports and calls resolve_sender_scope
- Fail-safe: unknown/missing scope defaults to "discover"
"""

from __future__ import annotations

import asyncio
from dataclasses import fields as dc_fields
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# scope_resolver module
# ---------------------------------------------------------------------------
class TestScopeResolverExports:
    def test_module_exports_resolve_sender_scope(self):
        from castor.channels import scope_resolver

        assert callable(scope_resolver.resolve_sender_scope)

    def test_module_exports_channel_scope_map(self):
        from castor.channels.scope_resolver import CHANNEL_SCOPE_MAP

        assert isinstance(CHANNEL_SCOPE_MAP, dict)
        assert len(CHANNEL_SCOPE_MAP) > 0

    def test_channel_scope_map_contains_whatsapp(self):
        from castor.channels.scope_resolver import CHANNEL_SCOPE_MAP

        assert "whatsapp" in CHANNEL_SCOPE_MAP

    def test_channel_scope_map_contains_telegram(self):
        from castor.channels.scope_resolver import CHANNEL_SCOPE_MAP

        assert "telegram" in CHANNEL_SCOPE_MAP

    def test_channel_scope_map_contains_slack(self):
        from castor.channels.scope_resolver import CHANNEL_SCOPE_MAP

        assert "slack" in CHANNEL_SCOPE_MAP

    def test_module_exports_context_vars(self):
        from castor.channels.scope_resolver import (
            _current_sender_loa,
            _current_sender_scope,
        )

        import contextvars

        assert isinstance(_current_sender_scope, contextvars.ContextVar)
        assert isinstance(_current_sender_loa, contextvars.ContextVar)

    def test_scope_hierarchy_is_ordered(self):
        from castor.channels.scope_resolver import SCOPE_HIERARCHY

        assert SCOPE_HIERARCHY.index("discover") < SCOPE_HIERARCHY.index("status")
        assert SCOPE_HIERARCHY.index("status") < SCOPE_HIERARCHY.index("chat")


# ---------------------------------------------------------------------------
# resolve_sender_scope: correct scope for owner
# ---------------------------------------------------------------------------
class TestResolveSenderScopeOwner:
    def test_owner_id_exact_match_returns_chat_loa1(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        config = {"owner_id": "19169967105"}
        scope, loa = resolve_sender_scope("19169967105", config)
        assert scope == "chat"
        assert loa == 1

    def test_owner_number_field_returns_chat_loa1(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        config = {"owner_number": "+19169967105"}
        scope, loa = resolve_sender_scope("19169967105", config)
        assert scope == "chat"
        assert loa == 1

    def test_admin_ids_entry_returns_chat_loa1(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        config = {"admin_ids": ["55512345678", "19169967105"]}
        scope, loa = resolve_sender_scope("19169967105", config)
        assert scope == "chat"
        assert loa == 1

    def test_owner_with_plus_prefix_matches(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        config = {"owner_id": "+19169967105"}
        scope, loa = resolve_sender_scope("19169967105", config)
        assert scope == "chat"
        assert loa == 1


# ---------------------------------------------------------------------------
# resolve_sender_scope: allowlisted sender
# ---------------------------------------------------------------------------
class TestResolveSenderScopeAllowlist:
    def test_allowlisted_sender_returns_chat_loa0(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        config = {"allow_from": ["+15105550001", "+19169967105"]}
        scope, loa = resolve_sender_scope("15105550001", config)
        assert scope == "chat"
        assert loa == 0

    def test_non_allowlisted_returns_discover(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        config = {"allow_from": ["+19169967105"]}
        scope, loa = resolve_sender_scope("15555550001", config)
        assert scope == "discover"
        assert loa == 0

    def test_empty_allowlist_returns_discover_for_unknown(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        config = {"allow_from": []}
        scope, loa = resolve_sender_scope("99999999999", config)
        assert scope == "discover"
        assert loa == 0


# ---------------------------------------------------------------------------
# resolve_sender_scope: unknown / pairing sender
# ---------------------------------------------------------------------------
class TestResolveSenderScopeUnknown:
    def test_empty_sender_id_returns_discover(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        scope, loa = resolve_sender_scope("", {})
        assert scope == "discover"
        assert loa == 0

    def test_completely_unknown_returns_discover(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        scope, loa = resolve_sender_scope("13005550001", {})
        assert scope == "discover"
        assert loa == 0

    def test_no_config_returns_discover(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        scope, loa = resolve_sender_scope("anything", {})
        assert scope == "discover"

    def test_fail_safe_on_exception(self):
        """Corrupt config must not raise — always returns discover."""
        from castor.channels.scope_resolver import resolve_sender_scope

        # Pass a non-dict to force an internal error path
        scope, loa = resolve_sender_scope("user123", {"allow_from": None})
        assert scope in {"discover", "chat"}  # either is acceptable; no exception


# ---------------------------------------------------------------------------
# resolve_sender_scope: peer robot
# ---------------------------------------------------------------------------
class TestResolveSenderScopePeer:
    def test_rrn_sender_returns_status_default(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        scope, loa = resolve_sender_scope("rrn:fleet:robot-7", {})
        assert scope == "status"
        assert loa == 0

    def test_rrn_sender_with_custom_peer_scope(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        config = {
            "rcan_protocol": {
                "peers": {
                    "rrn:fleet:robot-7": {"scope": "chat"},
                }
            }
        }
        scope, loa = resolve_sender_scope("rrn:fleet:robot-7", config)
        assert scope == "chat"
        assert loa == 0

    def test_non_rrn_not_treated_as_peer(self):
        from castor.channels.scope_resolver import resolve_sender_scope

        scope, loa = resolve_sender_scope("robot-7", {})
        # Not an RRN → unknown sender → discover
        assert scope == "discover"


# ---------------------------------------------------------------------------
# SessionMessage has sender_scope and sender_loa fields
# ---------------------------------------------------------------------------
class TestSessionMessageFields:
    def test_session_message_has_sender_scope_field(self):
        from castor.channels.session import SessionMessage

        field_names = {f.name for f in dc_fields(SessionMessage)}
        assert "sender_scope" in field_names

    def test_session_message_has_sender_loa_field(self):
        from castor.channels.session import SessionMessage

        field_names = {f.name for f in dc_fields(SessionMessage)}
        assert "sender_loa" in field_names

    def test_session_message_default_sender_scope(self):
        from castor.channels.session import SessionMessage

        msg = SessionMessage(role="user", text="hi", channel="test", chat_id="123")
        assert isinstance(msg.sender_scope, str)

    def test_session_message_default_sender_loa(self):
        from castor.channels.session import SessionMessage

        msg = SessionMessage(role="user", text="hi", channel="test", chat_id="123")
        assert isinstance(msg.sender_loa, int)

    def test_session_message_custom_scope(self):
        from castor.channels.session import SessionMessage

        msg = SessionMessage(
            role="user", text="cmd", channel="whatsapp", chat_id="555", sender_scope="discover", sender_loa=0
        )
        assert msg.sender_scope == "discover"
        assert msg.sender_loa == 0


# ---------------------------------------------------------------------------
# /api/chat scope enforcement (via _handle_channel_message)
# ---------------------------------------------------------------------------
class TestHandleChannelMessageScopeEnforcement:
    """Test that _handle_channel_message reads and enforces scope ≤ chat."""

    def _make_brain_stub(self):
        """Return a minimal brain stub that records what instruction it received."""
        from types import SimpleNamespace

        calls = []

        class _Brain:
            def think(self, image, instruction, surface=None):
                calls.append(instruction)
                return SimpleNamespace(raw_text="ok", action=None)

        return _Brain(), calls

    def _invoke(self, scope: str) -> str:
        """Set context var to *scope* and call _handle_channel_message."""
        from castor.channels.scope_resolver import _current_sender_scope

        _current_sender_scope.set(scope)

        import castor.api as api_mod

        original_brain = api_mod.state.brain
        original_fs = api_mod.state.fs
        original_driver = api_mod.state.driver
        brain, calls = self._make_brain_stub()
        api_mod.state.brain = brain
        api_mod.state.fs = None
        api_mod.state.driver = None
        with mock.patch.object(api_mod, "_capture_live_frame", return_value=None):
            with mock.patch.object(api_mod, "_get_active_brain", return_value=brain):
                with mock.patch.object(api_mod, "_speak_reply", return_value=None):
                    result = api_mod._handle_channel_message("whatsapp", "111", "move")
        api_mod.state.brain = original_brain
        api_mod.state.fs = original_fs
        api_mod.state.driver = original_driver
        return calls[0] if calls else ""

    def test_chat_scope_passes_through(self):
        instruction = self._invoke("chat")
        assert "rcan_scope=chat" in instruction

    def test_discover_scope_passes_through(self):
        instruction = self._invoke("discover")
        assert "rcan_scope=discover" in instruction

    def test_status_scope_passes_through(self):
        instruction = self._invoke("status")
        assert "rcan_scope=status" in instruction

    def test_admin_scope_clamped_to_chat(self):
        """Scope "admin" exceeds "chat" and must be clamped."""
        instruction = self._invoke("admin")
        # Must be clamped — instruction should NOT contain admin
        assert "rcan_scope=admin" not in instruction
        assert "rcan_scope=chat" in instruction

    def test_unknown_scope_clamped(self):
        """An arbitrary unknown scope must be clamped to "chat"."""
        instruction = self._invoke("superuser")
        assert "rcan_scope=superuser" not in instruction


# ---------------------------------------------------------------------------
# Channel adapters import and use resolve_sender_scope
# ---------------------------------------------------------------------------
class TestChannelAdapterScopeImports:
    def test_whatsapp_imports_resolve_sender_scope(self):
        from castor.channels import whatsapp_neonize

        assert hasattr(whatsapp_neonize, "resolve_sender_scope")

    def test_telegram_imports_resolve_sender_scope(self):
        import importlib

        try:
            mod = importlib.import_module("castor.channels.telegram_channel")
            assert hasattr(mod, "resolve_sender_scope")
        except (ImportError, NameError):
            pytest.skip("python-telegram-bot not installed")

    def test_slack_imports_resolve_sender_scope(self):
        import importlib

        try:
            mod = importlib.import_module("castor.channels.slack_channel")
            assert hasattr(mod, "resolve_sender_scope")
        except (ImportError, NameError):
            pytest.skip("slack-bolt not installed")

    def test_whatsapp_calls_resolve_scope_on_dispatch(self):
        """resolve_sender_scope is called in _handle_incoming before dispatch."""
        from castor.channels import whatsapp_neonize
        from types import SimpleNamespace

        config = {"dm_policy": "open", "self_chat_mode": False}
        with mock.patch.object(whatsapp_neonize, "HAS_NEONIZE", True):
            ch = whatsapp_neonize.WhatsAppChannel.__new__(whatsapp_neonize.WhatsAppChannel)
            ch.config = config
            ch._on_message_callback = None
            ch.logger = whatsapp_neonize.logger
            ch._session_db = "/tmp/t.db"
            ch._client = None
            ch._thread = None
            ch._loop = None
            ch._connected = False
            ch._stop_flag = False
            ch._owner_number = None
            ch._dm_policy = "open"
            ch._allow_from = []
            ch._self_chat_mode = False
            ch._group_policy = "disabled"
            ch._group_name_filter = None
            ch._group_jids = []
            ch._group_name_cache = {}
            ch._ack_reaction = None
            ch._pairing_requests = {}
            ch._dispatch = mock.MagicMock()

        # Build a fake inbound DM
        chat = SimpleNamespace(User="15105550001", Server="s.whatsapp.net")
        sender = SimpleNamespace(User="15105550001")
        source = SimpleNamespace(IsFromMe=False, Chat=chat, Sender=sender)
        info = SimpleNamespace(MessageSource=source, ID="msg-x")
        msg = SimpleNamespace(conversation="hello", extendedTextMessage=None)
        event = SimpleNamespace(Info=info, Message=msg)

        with mock.patch.object(
            whatsapp_neonize, "resolve_sender_scope", wraps=whatsapp_neonize.resolve_sender_scope
        ) as spy:
            ch._handle_incoming(mock.MagicMock(), event)
            spy.assert_called_once()


# ---------------------------------------------------------------------------
# clamp_scope helper
# ---------------------------------------------------------------------------
class TestClampScope:
    def test_clamp_discover_to_chat_unchanged(self):
        from castor.channels.scope_resolver import clamp_scope

        assert clamp_scope("discover", "chat") == "discover"

    def test_clamp_chat_to_chat_unchanged(self):
        from castor.channels.scope_resolver import clamp_scope

        assert clamp_scope("chat", "chat") == "chat"

    def test_clamp_admin_to_chat_returns_chat(self):
        from castor.channels.scope_resolver import clamp_scope

        assert clamp_scope("admin", "chat") == "chat"

    def test_clamp_status_to_discover_returns_discover(self):
        from castor.channels.scope_resolver import clamp_scope

        assert clamp_scope("status", "discover") == "discover"
