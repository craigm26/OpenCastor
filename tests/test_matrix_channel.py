"""Tests for castor.channels.matrix_channel."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from castor.channels.matrix_channel import MatrixChannel


def _make_channel(extra=None):
    cfg = {
        "homeserver_url": "https://matrix.example.com",
        "user_id": "@bot:matrix.example.com",
        "access_token": "syt_test_token",
        **(extra or {}),
    }
    return MatrixChannel(cfg, on_message=lambda ch, rid, txt: f"reply: {txt}")


class TestMatrixChannelInit:
    def test_init_stores_config(self):
        ch = _make_channel()
        assert ch._homeserver == "https://matrix.example.com"
        assert ch._user_id == "@bot:matrix.example.com"
        assert ch._access_token == "syt_test_token"

    def test_init_default_homeserver(self):
        ch = MatrixChannel({})
        assert ch._homeserver == "https://matrix.org"


class TestMatrixChannelLifecycle:
    @pytest.mark.asyncio
    async def test_start_without_nio(self):
        import sys

        with patch.dict(sys.modules, {"nio": None}):
            if "castor.channels.matrix_channel" in sys.modules:
                del sys.modules["castor.channels.matrix_channel"]
            from castor.channels.matrix_channel import MatrixChannel as MC

            ch = MC({"user_id": "@bot:matrix.org", "access_token": "tok"})
            await ch.start()  # Should log warning, not raise

    @pytest.mark.asyncio
    async def test_start_missing_credentials(self):
        ch = MatrixChannel({"homeserver_url": "https://matrix.org"})
        await ch.start()  # Should log warning, not raise

    @pytest.mark.asyncio
    async def test_stop_with_no_client(self):
        ch = _make_channel()
        await ch.stop()  # Client is None, should not raise

    @pytest.mark.asyncio
    async def test_stop_with_client(self):
        ch = _make_channel()
        mock_client = AsyncMock()
        ch._client = mock_client
        await ch.stop()
        mock_client.close.assert_called_once()


class TestMatrixChannelSendMessage:
    @pytest.mark.asyncio
    async def test_send_no_client(self):
        ch = _make_channel()
        # client is None — should not raise
        await ch.send_message("!room:matrix.org", "hello")

    @pytest.mark.asyncio
    async def test_send_with_client(self):
        ch = _make_channel()
        mock_client = AsyncMock()
        ch._client = mock_client
        await ch.send_message("!room:matrix.org", "hello world")
        mock_client.room_send.assert_called_once()
        call = mock_client.room_send.call_args
        assert call[1]["room_id"] == "!room:matrix.org"
        assert call[1]["content"]["body"] == "hello world"

    @pytest.mark.asyncio
    async def test_send_exception_handled(self):
        ch = _make_channel()
        mock_client = AsyncMock()
        mock_client.room_send.side_effect = Exception("network error")
        ch._client = mock_client
        await ch.send_message("!room:matrix.org", "test")  # Should not raise


class TestMatrixChannelOnMessage:
    @pytest.mark.asyncio
    async def test_on_room_message_calls_callback(self):
        ch = _make_channel()
        mock_client = AsyncMock()
        ch._client = mock_client

        mock_room = MagicMock()
        mock_room.room_id = "!test:matrix.org"
        mock_event = MagicMock()
        mock_event.body = "hello robot"
        mock_event.sender = "@user:matrix.org"

        await ch._on_room_message(mock_room, mock_event)
        mock_client.room_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_own_messages(self):
        ch = _make_channel()
        mock_client = AsyncMock()
        ch._client = mock_client

        mock_room = MagicMock()
        mock_room.room_id = "!test:matrix.org"
        mock_event = MagicMock()
        mock_event.body = "my own message"
        mock_event.sender = "@bot:matrix.example.com"  # Same as ch._user_id

        await ch._on_room_message(mock_room, mock_event)
        mock_client.room_send.assert_not_called()
