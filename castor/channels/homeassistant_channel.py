"""
castor/channels/homeassistant_channel.py — Home Assistant channel (issue #106).

Integrates OpenCastor as a Home Assistant entity so robot commands can be
triggered from HA automations, dashboards, or voice assistants.

The channel polls the ``input_text.castor_command`` HA entity for state
changes and forwards new values to the robot brain.  It also exposes:

* ``switch.castor_<name>``       — ON when running, OFF when stopped
* ``sensor.castor_last_action``  — most recent brain action JSON string

RCAN config example::

    channels:
    - type: homeassistant
      ha_url: http://homeassistant.local:8123
      ha_token: ${HA_LONG_LIVED_TOKEN}
      entity_id: input_text.castor_command
      robot_name: mybot          # optional, used in entity names
      poll_interval_s: 2         # optional, default 2

Environment variables::

    HA_LONG_LIVED_TOKEN   Long-lived access token from HA profile page

Install::

    pip install opencastor[homeassistant]   # or: pip install aiohttp
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable, Dict, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.HomeAssistant")

try:
    import aiohttp

    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    logger.debug("aiohttp not installed — HomeAssistant channel unavailable")


class HomeAssistantChannel(BaseChannel):
    """Home Assistant channel: poll HA state changes and send replies via REST API.

    Requires ``aiohttp`` and a long-lived HA access token.
    Degrades gracefully if aiohttp is missing (logs a helpful message).
    """

    def __init__(
        self,
        config: Optional[Dict] = None,
        on_message: Optional[Callable] = None,
    ) -> None:
        super().__init__(config or {}, on_message=on_message)
        cfg = config or {}

        self._ha_url: str = cfg.get("ha_url", "http://homeassistant.local:8123").rstrip("/")
        self._ha_token: str = cfg.get(
            "ha_token", os.environ.get("HA_LONG_LIVED_TOKEN", "")
        )
        self._entity_id: str = cfg.get("entity_id", "input_text.castor_command")
        self._robot_name: str = cfg.get("robot_name", "castor")
        self._poll_interval: float = float(cfg.get("poll_interval_s", 2))

        self._switch_entity = f"switch.castor_{self._robot_name}"
        self._sensor_entity = f"sensor.castor_{self._robot_name}_last_action"

        self._last_seen_state: Optional[str] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

        if not self._ha_token:
            logger.warning(
                "HomeAssistant channel: HA_LONG_LIVED_TOKEN not set. "
                "Create one at: HA → Profile → Long-Lived Access Tokens"
            )

    # ------------------------------------------------------------------
    # BaseChannel interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start polling HA for command state changes."""
        if not HAS_AIOHTTP:
            logger.error(
                "aiohttp required for HomeAssistant channel. "
                "Install: pip install opencastor[homeassistant]"
            )
            return
        if not self._ha_token:
            logger.error("HomeAssistant channel: no token configured — not starting")
            return

        self._running = True
        logger.info(
            "HomeAssistant channel started: polling %s/%s every %.1fs",
            self._ha_url,
            self._entity_id,
            self._poll_interval,
        )
        self._task = asyncio.create_task(self._poll_loop(), name="ha-poll")

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("HomeAssistant channel stopped")

    async def send_message(self, chat_id: str, text: str) -> None:
        """Push a reply to HA as a notification and update the sensor entity."""
        try:
            await self._update_sensor(text)
        except Exception as exc:
            logger.warning("Could not update HA sensor: %s", exc)

    # ------------------------------------------------------------------
    # Internal polling
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Poll HA entity state every poll_interval_s and forward changes."""
        headers = {
            "Authorization": f"Bearer {self._ha_token}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            # Register switch entity as ON (running)
            await self._set_switch_state(session, "on")

            while self._running:
                try:
                    state = await self._fetch_state(session, self._entity_id)
                    if state and state != self._last_seen_state:
                        self._last_seen_state = state
                        logger.info("HA command: %r", state)
                        await self._dispatch(state)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.debug("HA poll error: %s", exc)

                await asyncio.sleep(self._poll_interval)

            await self._set_switch_state(session, "off")

    async def _fetch_state(self, session: aiohttp.ClientSession, entity_id: str) -> Optional[str]:
        """Fetch current state of an HA entity."""
        url = f"{self._ha_url}/api/states/{entity_id}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("state")
            logger.debug("HA fetch %s → HTTP %d", entity_id, resp.status)
            return None

    async def _update_sensor(self, value: str) -> None:
        """Update the castor_last_action sensor in HA."""
        headers = {
            "Authorization": f"Bearer {self._ha_token}",
            "Content-Type": "application/json",
        }
        url = f"{self._ha_url}/api/states/{self._sensor_entity}"
        payload = {"state": value[:255]}  # HA state max 255 chars
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status not in (200, 201):
                    logger.debug("HA sensor update → HTTP %d", resp.status)

    async def _set_switch_state(self, session: aiohttp.ClientSession, state: str) -> None:
        """Set the castor switch entity state (on/off) in HA."""
        service = "turn_on" if state == "on" else "turn_off"
        url = f"{self._ha_url}/api/services/switch/{service}"
        payload = {"entity_id": self._switch_entity}
        try:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=5)
            ):
                pass
        except Exception as exc:
            logger.debug("HA switch state set error: %s", exc)

    async def _dispatch(self, text: str) -> None:
        """Forward a command text to the on_message callback."""
        if not self._on_message:
            return
        chat_id = self._entity_id
        try:
            result = self._on_message("homeassistant", chat_id, text)
            if asyncio.iscoroutine(result):
                reply = await result
            else:
                reply = await asyncio.to_thread(lambda: result) if callable(result) else result

            if reply:
                await self.send_message(chat_id, str(reply))
        except Exception as exc:
            logger.exception("HA dispatch error: %s", exc)
