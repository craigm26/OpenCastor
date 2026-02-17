"""Tests for castor.channels -- BaseChannel and handle_message."""

import asyncio
from typing import Optional

import pytest

from castor.channels.base import BaseChannel


# =====================================================================
# Concrete stub for testing
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
        cb = lambda name, chat_id, text: "reply"
        ch = StubChannel({}, on_message=cb)
        assert ch._on_message_callback is cb

    def test_no_callback_by_default(self):
        ch = StubChannel({})
        assert ch._on_message_callback is None

    def test_logger_name(self):
        ch = StubChannel({})
        assert ch.logger.name == "OpenCastor.Channel.stub"


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


# =====================================================================
# Abstract method enforcement
# =====================================================================
class TestAbstractEnforcement:
    def test_cannot_instantiate_base_channel(self):
        with pytest.raises(TypeError):
            BaseChannel({})
