"""Tests for castor.notify_dispatch.NotifyDispatcher.

Covers the cross-cutting fan-out used by HiTLGateManager._notify and
AuthorityRequestHandler._notify_owner. The dispatcher must be best-effort —
per-channel exceptions are absorbed and logged, never raised.
"""

from __future__ import annotations

import pytest

from castor.channels.base import BaseChannel


class _FakeChannel(BaseChannel):
    """Recorder + configurable-failure channel double."""

    name = "fake"

    def __init__(self, name: str, raises: Exception | None = None):
        # Bypass BaseChannel.__init__ — we don't need rate-limiting machinery
        self.name = name
        self.sends: list[tuple[str, str]] = []
        self._raises = raises
        self.logger = __import__("logging").getLogger(f"OpenCastor.Channel.{name}")

    async def start(self) -> None:  # pragma: no cover — required abstract
        pass

    async def stop(self) -> None:  # pragma: no cover — required abstract
        pass

    async def send_message(self, chat_id: str, text: str) -> None:
        if self._raises is not None:
            raise self._raises
        self.sends.append((chat_id, text))


@pytest.mark.asyncio
async def test_fan_out_happy_path_two_channels():
    from castor.notify_dispatch import NotifyDispatcher

    wa = _FakeChannel("whatsapp")
    tg = _FakeChannel("telegram")
    channels = {"whatsapp": wa, "telegram": tg}

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100", "telegram": "12345678"},
    )

    result = await dispatcher.fan_out(["whatsapp", "telegram"], "hello bob")

    assert result == {"whatsapp": True, "telegram": True}
    assert wa.sends == [("+15555550100", "hello bob")]
    assert tg.sends == [("12345678", "hello bob")]


class _FakeChannelNoRetry(BaseChannel):
    """_FakeChannel variant that overrides send_message_with_retry to return
    a configurable bool directly, skipping the real retry loop. Lets us
    test the dispatcher's bool-capture contract without 7s sleeps."""

    def __init__(self, name: str, returns_ok: bool = True):
        self.name = name
        self._returns_ok = returns_ok
        self.calls: list[tuple[str, str]] = []
        self.logger = __import__("logging").getLogger(f"OpenCastor.Channel.{name}")

    async def start(self) -> None:
        pass  # pragma: no cover

    async def stop(self) -> None:
        pass  # pragma: no cover

    async def send_message(self, chat_id: str, text: str) -> None:  # pragma: no cover
        # Not exercised — dispatcher calls send_message_with_retry
        pass

    async def send_message_with_retry(self, chat_id: str, text: str, **_) -> bool:
        self.calls.append((chat_id, text))
        return self._returns_ok


@pytest.mark.asyncio
async def test_fan_out_captures_send_with_retry_bool_false():
    """When send_message_with_retry returns False (retries exhausted), the
    dispatcher must report False — NOT True. Regression for the
    'try/except is dead code because retry catches everything' bug."""
    from castor.notify_dispatch import NotifyDispatcher

    failing = _FakeChannelNoRetry("whatsapp", returns_ok=False)
    succeeding = _FakeChannelNoRetry("telegram", returns_ok=True)
    channels = {"whatsapp": failing, "telegram": succeeding}

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100", "telegram": "12345678"},
    )

    result = await dispatcher.fan_out(["whatsapp", "telegram"], "hello")

    assert result == {"whatsapp": False, "telegram": True}
    assert failing.calls == [("+15555550100", "hello")]
    assert succeeding.calls == [("12345678", "hello")]


class _FakeChannelRaises(BaseChannel):
    """Test double whose send_message_with_retry raises directly.

    Exercises the dispatcher's outer try/except — the contract that
    even if a future channel adapter violates the BaseChannel
    no-raise convention, the dispatcher absorbs and reports False.
    """

    def __init__(self, name: str, exc: Exception):
        self.name = name
        self._exc = exc
        self.logger = __import__("logging").getLogger(f"OpenCastor.Channel.{name}")

    async def start(self) -> None:
        pass  # pragma: no cover

    async def stop(self) -> None:
        pass  # pragma: no cover

    async def send_message(self, chat_id: str, text: str) -> None:  # pragma: no cover
        # Not used — dispatcher calls send_message_with_retry
        pass

    async def send_message_with_retry(self, chat_id: str, text: str, **_) -> bool:
        raise self._exc


@pytest.mark.asyncio
async def test_fan_out_absorbs_unexpected_raise_from_channel(caplog):
    """If a channel's send_message_with_retry raises (violating the
    no-raise contract), the dispatcher must absorb it, log ERROR, and
    continue with sibling channels."""
    import logging

    from castor.notify_dispatch import NotifyDispatcher

    wa = _FakeChannelRaises("whatsapp", exc=RuntimeError("boom"))
    tg = _FakeChannelNoRetry("telegram", returns_ok=True)
    channels = {"whatsapp": wa, "telegram": tg}

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100", "telegram": "12345678"},
    )

    with caplog.at_level(logging.ERROR):
        result = await dispatcher.fan_out(["whatsapp", "telegram"], "hello")

    assert result == {"whatsapp": False, "telegram": True}
    assert tg.calls == [("12345678", "hello")]
    # The dispatcher's own logger.error fires for the absorbed exception
    assert any(
        "notify dispatch failed" in r.message and "whatsapp" in r.message and "boom" in r.message
        for r in caplog.records
        if r.levelname == "ERROR"
    )


@pytest.mark.asyncio
async def test_fan_out_missing_chat_id_skips_with_warning(caplog):
    import logging

    from castor.notify_dispatch import NotifyDispatcher

    tg = _FakeChannelNoRetry("telegram", returns_ok=True)
    channels = {"telegram": tg}

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"telegram": "12345678"},  # no 'whatsapp' entry
    )

    with caplog.at_level(logging.WARNING, logger="OpenCastor.NotifyDispatch"):
        result = await dispatcher.fan_out(["whatsapp", "telegram"], "hello")

    assert result == {"whatsapp": False, "telegram": True}
    assert tg.calls == [("12345678", "hello")]
    assert any("no chat_id configured for channel 'whatsapp'" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_fan_out_inactive_channel_skips_with_warning(caplog):
    import logging

    from castor.notify_dispatch import NotifyDispatcher

    channels: dict = {}  # no channels active this run

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100"},
    )

    with caplog.at_level(logging.WARNING, logger="OpenCastor.NotifyDispatch"):
        result = await dispatcher.fan_out(["whatsapp"], "hello")

    assert result == {"whatsapp": False}
    assert any("not active this run" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_channels_ref_is_re_read_every_call():
    """Mutating the channel dict between calls must be visible — the
    dispatcher must not snapshot."""
    from castor.notify_dispatch import NotifyDispatcher

    wa = _FakeChannelNoRetry("whatsapp", returns_ok=True)
    channels: dict = {}  # initially empty

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100"},
    )

    r1 = await dispatcher.fan_out(["whatsapp"], "first")
    assert r1 == {"whatsapp": False}  # channel not yet active

    channels["whatsapp"] = wa  # now becomes active
    r2 = await dispatcher.fan_out(["whatsapp"], "second")
    assert r2 == {"whatsapp": True}
    assert wa.calls == [("+15555550100", "second")]


@pytest.mark.asyncio
async def test_notify_owner_happy_path():
    from castor.notify_dispatch import NotifyDispatcher

    wa = _FakeChannelNoRetry("whatsapp", returns_ok=True)
    channels = {"whatsapp": wa}

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100"},
        owner_channel="whatsapp",
    )

    ok = await dispatcher.notify_owner("AUTHORITY ACCESS REQUEST: …")

    assert ok is True
    assert wa.calls == [("+15555550100", "AUTHORITY ACCESS REQUEST: …")]


@pytest.mark.asyncio
async def test_notify_owner_no_owner_channel_returns_false_with_warning(caplog):
    import logging

    from castor.notify_dispatch import NotifyDispatcher

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: {},
        chat_ids={},
        owner_channel=None,
    )

    with caplog.at_level(logging.WARNING, logger="OpenCastor.NotifyDispatch"):
        ok = await dispatcher.notify_owner("anything")

    assert ok is False
    assert any("owner_channel" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_notify_owner_owner_channel_missing_chat_id_returns_false():
    from castor.notify_dispatch import NotifyDispatcher

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: {},
        chat_ids={},
        owner_channel="whatsapp",  # set but no chat_ids[whatsapp] entry
    )

    ok = await dispatcher.notify_owner("anything")
    assert ok is False
