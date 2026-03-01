"""Tests for BaseChannel.send_message_with_retry (issue #273)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _DummyChannel:
    """Minimal concrete channel for testing the mixin."""

    name = "dummy"
    logger = MagicMock()

    async def send_message(self, chat_id: str, text: str):
        pass  # default: succeed

    async def start(self):
        pass

    async def stop(self):
        pass

    # Inject the retry method directly
    from castor.channels.base import BaseChannel

    send_message_with_retry = BaseChannel.send_message_with_retry


@pytest.mark.asyncio
async def test_retry_succeeds_on_first_try():
    """If send_message succeeds immediately, returns True."""
    ch = _DummyChannel()
    result = await ch.send_message_with_retry("chat1", "hello", max_retries=3, base_delay_s=0.0)
    assert result is True


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt():
    """Returns True if first call fails but second succeeds."""
    call_count = 0

    async def flaky_send(chat_id, text):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("transient")

    ch = _DummyChannel()
    ch.send_message = flaky_send
    result = await ch.send_message_with_retry("chat1", "hi", max_retries=3, base_delay_s=0.001)
    assert result is True
    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted_returns_false():
    """Returns False after all retries are exhausted."""

    async def always_fail(chat_id, text):
        raise RuntimeError("always fail")

    ch = _DummyChannel()
    ch.send_message = always_fail
    result = await ch.send_message_with_retry("chat1", "x", max_retries=2, base_delay_s=0.001)
    assert result is False


@pytest.mark.asyncio
async def test_retry_total_attempts_is_max_retries_plus_one():
    """Total attempts = max_retries + 1 (initial + retries)."""
    call_count = 0

    async def count_calls(chat_id, text):
        nonlocal call_count
        call_count += 1
        raise OSError("fail")

    ch = _DummyChannel()
    ch.send_message = count_calls
    await ch.send_message_with_retry("x", "msg", max_retries=3, base_delay_s=0.001)
    assert call_count == 4  # 1 initial + 3 retries


@pytest.mark.asyncio
async def test_retry_logs_warning_on_each_failure():
    """A warning is logged for each non-final failure."""
    call_count = 0
    warn_count = 0

    async def flaky(chat_id, text):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("transient")

    ch = _DummyChannel()
    ch.send_message = flaky

    def count_warns(*args, **kwargs):
        nonlocal warn_count
        warn_count += 1

    ch.logger.warning = count_warns
    result = await ch.send_message_with_retry("x", "y", max_retries=3, base_delay_s=0.001)
    assert result is True
    assert warn_count >= 2  # warned at least for each failed attempt
