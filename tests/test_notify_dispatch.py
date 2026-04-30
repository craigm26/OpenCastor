"""Tests for castor.notify_dispatch.NotifyDispatcher.

Covers the cross-cutting fan-out used by HiTLGateManager._notify and
AuthorityRequestHandler._notify_owner. The dispatcher must be best-effort —
per-channel exceptions are absorbed and logged, never raised.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

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
