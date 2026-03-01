"""Tests for castor.channels.teams_channel."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from castor.channels.teams_channel import TeamsChannel


def _make_channel(extra=None):
    cfg = {
        "webhook_url": "https://teams.example.com/webhook/token",
        **(extra or {}),
    }
    return TeamsChannel(cfg, on_message=lambda ch, cid, txt: f"echo: {txt}")


class TestTeamsChannelInit:
    def test_init_with_webhook(self):
        ch = _make_channel()
        assert ch._webhook_url.startswith("https://")

    def test_init_no_webhook(self):
        ch = TeamsChannel({})
        assert ch._webhook_url == ""


class TestTeamsChannelLifecycle:
    @pytest.mark.asyncio
    async def test_start(self):
        ch = _make_channel()
        await ch.start()
        assert ch._running is True

    @pytest.mark.asyncio
    async def test_stop(self):
        ch = _make_channel()
        await ch.start()
        await ch.stop()
        assert ch._running is False

    @pytest.mark.asyncio
    async def test_start_no_aiohttp(self):
        import sys

        with patch.dict(sys.modules, {"aiohttp": None}):
            if "castor.channels.teams_channel" in sys.modules:
                del sys.modules["castor.channels.teams_channel"]
            from castor.channels.teams_channel import TeamsChannel as TC

            ch = TC({"webhook_url": "https://x.com"})
            await ch.start()  # Should not raise


def _mock_aiohttp(sess):
    """Return a MagicMock standing in for the aiohttp module."""
    mock_mod = MagicMock()
    mock_mod.ClientSession.return_value = sess
    mock_mod.ClientTimeout = MagicMock()
    return mock_mod


class TestTeamsChannelSendMessage:
    @pytest.mark.asyncio
    async def test_send_via_webhook(self):
        ch = _make_channel()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_sess.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("castor.channels.teams_channel.HAS_AIOHTTP", True),
            patch("castor.channels.teams_channel.aiohttp", _mock_aiohttp(mock_sess), create=True),
        ):
            await ch.send_message("conv_id", "Hello Teams!")

    @pytest.mark.asyncio
    async def test_send_no_webhook_url(self):
        ch = TeamsChannel({})
        # Should not raise
        await ch.send_message("conv_id", "test")

    @pytest.mark.asyncio
    async def test_send_http_error_logged(self):
        ch = _make_channel()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="error body")
        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_sess.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("castor.channels.teams_channel.HAS_AIOHTTP", True),
            patch("castor.channels.teams_channel.aiohttp", _mock_aiohttp(mock_sess), create=True),
        ):
            await ch.send_message("conv_id", "test")


class TestTeamsChannelBotActivity:
    def test_handle_message_activity(self):
        ch = _make_channel()
        payload = {
            "type": "message",
            "text": "hello robot",
            "from": {"id": "user1", "name": "Alice"},
            "conversation": {"id": "conv-123"},
        }
        reply = ch.handle_bot_activity(payload)
        assert reply == "echo: hello robot"

    def test_handle_non_message_activity(self):
        ch = _make_channel()
        payload = {"type": "conversationUpdate"}
        reply = ch.handle_bot_activity(payload)
        assert reply == ""

    def test_handle_empty_text(self):
        ch = _make_channel()
        payload = {
            "type": "message",
            "text": "  ",
            "from": {"id": "user1"},
            "conversation": {"id": "c1"},
        }
        reply = ch.handle_bot_activity(payload)
        assert reply == ""

    def test_handle_no_on_message(self):
        ch = TeamsChannel({"webhook_url": "https://x.com"})
        payload = {
            "type": "message",
            "text": "hello",
            "from": {"id": "u1"},
            "conversation": {"id": "c1"},
        }
        reply = ch.handle_bot_activity(payload)
        assert reply == ""
