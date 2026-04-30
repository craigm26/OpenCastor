"""castor/notify_dispatch — channel-name → chat_id → BaseChannel resolver.

Used by:
  - HiTLGateManager._notify (HiTL gate `notify: [whatsapp]` lists)
  - AuthorityRequestHandler._notify_owner (single `owner_channel`)

Best-effort: per-channel exceptions are absorbed and logged; `fan_out` and
`notify_owner` never raise into the caller's request path.

See docs/superpowers/specs/2026-04-29-notify-wiring-design.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("OpenCastor.NotifyDispatch")


class NotifyDispatcher:
    """Resolves channel names through `chat_ids` and dispatches via the
    runtime channel registry (typically `state.channels`).

    `channels_ref` is a 0-arg callable so the dispatcher always sees the
    current channel dict, even after hot-reload swaps the reference.
    """

    def __init__(
        self,
        channels_ref: Callable[[], dict[str, Any]],
        chat_ids: dict[str, str],
        owner_channel: str | None = None,
    ) -> None:
        self._channels_ref = channels_ref
        self._chat_ids = dict(chat_ids)
        self._owner_channel = owner_channel

    async def fan_out(self, channel_names: list[str], message: str) -> dict[str, bool]:
        """Send `message` to each named channel's configured chat_id.

        Returns {channel_name: ok}. Per-channel exceptions are absorbed.
        """
        results: dict[str, bool] = {}
        channels = self._channels_ref()
        for name in channel_names:
            chat_id = self._chat_ids.get(name)
            if chat_id is None:
                logger.warning("no chat_id configured for channel '%s', skipping", name)
                results[name] = False
                continue
            ch = channels.get(name)
            if ch is None:
                logger.warning(
                    "channel '%s' has chat_id but is not active this run, skipping",
                    name,
                )
                results[name] = False
                continue
            try:
                ok = await ch.send_message_with_retry(chat_id, message)
                results[name] = bool(ok)
            except Exception as exc:  # noqa: BLE001 — best-effort by contract
                logger.error("notify dispatch failed for channel '%s': %s", name, exc)
                results[name] = False
        logger.info("notify dispatch result: %s", results)
        return results

    async def notify_owner(self, message: str) -> bool:
        """Send `message` to the configured owner channel.

        Returns True on success, False on any failure. Never raises.
        """
        if not self._owner_channel:
            logger.warning(
                "notify_owner called but no owner_channel configured; "
                "AUTHORITY_ACCESS notification dropped"
            )
            return False
        result = await self.fan_out([self._owner_channel], message)
        return result.get(self._owner_channel, False)
