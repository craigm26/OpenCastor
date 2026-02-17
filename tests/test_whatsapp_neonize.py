"""Tests for castor.channels.whatsapp_neonize -- neonize-based WhatsApp channel."""

import os
from unittest import mock

import pytest


# =====================================================================
# Session DB path resolution
# =====================================================================
class TestGetSessionDbPath:
    def test_default_path(self):
        from castor.channels.whatsapp_neonize import _get_session_db_path

        with mock.patch.dict(os.environ, {}, clear=True):
            # Remove OPENCASTOR_DATA_DIR if present
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
# Import error handling
# =====================================================================
class TestImportError:
    def test_raises_when_neonize_missing(self):
        """WhatsAppChannel should raise ImportError when neonize is not installed."""
        # We need to simulate neonize not being available by patching HAS_NEONIZE
        from castor.channels import whatsapp_neonize

        original = whatsapp_neonize.HAS_NEONIZE
        try:
            whatsapp_neonize.HAS_NEONIZE = False
            with pytest.raises(ImportError, match="neonize"):
                whatsapp_neonize.WhatsAppChannel({})
        finally:
            whatsapp_neonize.HAS_NEONIZE = original


# =====================================================================
# Channel instantiation (with mocked neonize)
# =====================================================================
class TestWhatsAppChannelInit:
    def test_instantiation_with_mock_neonize(self):
        """Channel should instantiate when HAS_NEONIZE is True."""
        from castor.channels import whatsapp_neonize

        original = whatsapp_neonize.HAS_NEONIZE
        try:
            whatsapp_neonize.HAS_NEONIZE = True
            ch = whatsapp_neonize.WhatsAppChannel({})
            assert ch.name == "whatsapp"
            assert ch.connected is False
            assert ch._client is None
        finally:
            whatsapp_neonize.HAS_NEONIZE = original

    def test_session_db_stored(self, tmp_path):
        from castor.channels import whatsapp_neonize

        original = whatsapp_neonize.HAS_NEONIZE
        try:
            whatsapp_neonize.HAS_NEONIZE = True
            db_path = str(tmp_path / "test.db")
            ch = whatsapp_neonize.WhatsAppChannel({"session_db": db_path})
            assert ch._session_db == db_path
        finally:
            whatsapp_neonize.HAS_NEONIZE = original


# =====================================================================
# Re-export from castor.channels.whatsapp
# =====================================================================
class TestReExport:
    def test_import_from_whatsapp_module(self):
        from castor.channels.whatsapp import WhatsAppChannel
        from castor.channels.whatsapp_neonize import WhatsAppChannel as NeonizeChannel

        assert WhatsAppChannel is NeonizeChannel


# =====================================================================
# Twilio channel rename
# =====================================================================
class TestTwilioChannelRename:
    def test_twilio_channel_class_exists(self):
        """WhatsAppTwilioChannel should be importable from the renamed module."""
        # This import will fail if twilio is not installed, which is fine --
        # we just need the class definition itself.
        from castor.channels.whatsapp_twilio import WhatsAppTwilioChannel

        assert WhatsAppTwilioChannel.name == "whatsapp_twilio"
