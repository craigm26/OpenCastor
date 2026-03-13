"""
castor/drivers/esp32_ble_driver.py — ESP32 BLE (Bluetooth Low Energy) driver.

Controls an ESP32-based robot over Bluetooth Low Energy using the bleak library.
Commands are sent as JSON-encoded bytes over a GATT characteristic (UUID configurable).

Env:
  ESP32_BLE_ADDRESS   — BLE device MAC address or UUID (required for hardware mode)
  ESP32_BLE_CHAR_UUID — GATT characteristic UUID for command write (default: generic)
  ESP32_BLE_TIMEOUT   — Connection timeout in seconds (default 5.0)

Install: pip install opencastor[ble]   # installs bleak>=0.21

Usage::

    from castor.drivers.esp32_ble_driver import ESP32BLEDriver

    drv = ESP32BLEDriver({"ble_address": "AA:BB:CC:DD:EE:FF"})
    drv.move({"linear": 0.5, "angular": 0.0})
    drv.stop()
    drv.close()

REST API (via castor/api.py):
    GET /api/health → includes BLE connection status from health_check()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.ESP32BLE")

# Optional bleak dependency (HAS_BLEAK pattern)
HAS_BLEAK = False
try:
    from bleak import BleakClient  # type: ignore[import]

    HAS_BLEAK = True
except ImportError:
    BleakClient = None  # type: ignore[assignment,misc]

# Default GATT characteristic UUID for command writes
_DEFAULT_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Nordic UART TX


class ESP32BLEDriver:
    """Bluetooth Low Energy driver for ESP32-based robots.

    Sends JSON command objects over a GATT write characteristic.  Falls back
    to mock mode when the ``bleak`` library is not installed or no BLE address
    is configured.

    Protocol:
        Each command is a JSON object (UTF-8) written to the configured GATT
        characteristic.  The ESP32 firmware should parse the JSON and act on
        the ``type`` field.  Example:

        .. code-block:: json

            {"type": "move", "linear": 0.5, "angular": 0.0}
            {"type": "stop"}
            {"type": "grip", "open": true}

    Args:
        config: Dict with optional keys:

            * ``ble_address`` (str) — BLE device MAC or UUID.
            * ``ble_char_uuid`` (str) — GATT characteristic UUID.
            * ``ble_timeout`` (float) — connection timeout in seconds.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        config = config or {}
        self._address: str = config.get("ble_address", "") or os.getenv("ESP32_BLE_ADDRESS", "")
        self._char_uuid: str = config.get("ble_char_uuid", "") or os.getenv(
            "ESP32_BLE_CHAR_UUID", _DEFAULT_CHAR_UUID
        )
        self._timeout: float = float(
            config.get("ble_timeout", os.getenv("ESP32_BLE_TIMEOUT", "5.0"))
        )
        self._mode: str = "mock"
        self._connected: bool = False
        self._error: Optional[str] = None
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[Any] = None  # BleakClient instance

        if not HAS_BLEAK:
            logger.info(
                "ESP32BLEDriver: bleak not installed — mock mode "
                "(install: pip install opencastor[ble])"
            )
            return

        if not self._address:
            logger.info(
                "ESP32BLEDriver: no BLE address configured — mock mode "
                "(set ESP32_BLE_ADDRESS or ble_address config key)"
            )
            return

        # Start a dedicated asyncio event loop in a background thread
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="ESP32BLE-asyncio",
        )
        self._loop_thread.start()
        self._mode = "hardware"
        logger.info(
            "ESP32BLEDriver: initialised (address=%s, char=%s, timeout=%.1fs)",
            self._address,
            self._char_uuid,
            self._timeout,
        )

    # ── Internal asyncio helpers ──────────────────────────────────────────────

    def _run_coro(self, coro) -> Any:
        """Submit *coro* to the background event loop and block until complete."""
        if self._loop is None:
            raise RuntimeError("No asyncio loop available (mock mode?)")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=self._timeout + 2.0)

    async def _connect_async(self) -> None:
        """Async: connect to the ESP32 BLE device."""
        self._client = BleakClient(self._address, timeout=self._timeout)  # type: ignore
        await self._client.connect()
        self._connected = True
        logger.info("ESP32BLEDriver: connected to %s", self._address)

    async def _disconnect_async(self) -> None:
        """Async: disconnect from the BLE device."""
        if self._client is not None:
            await self._client.disconnect()
        self._connected = False
        logger.info("ESP32BLEDriver: disconnected from %s", self._address)

    async def _write_async(self, payload: bytes) -> None:
        """Async: write *payload* to the GATT characteristic."""
        if self._client is None or not self._connected:
            await self._connect_async()
        await self._client.write_gatt_char(self._char_uuid, payload, response=False)

    # ── Public driver API ─────────────────────────────────────────────────────

    def _send_command(self, command: dict[str, Any]) -> None:
        """Serialise *command* as JSON and write it over BLE.

        In mock mode, logs the command at DEBUG level and returns without error.

        Args:
            command: Dict with at minimum a ``"type"`` key.
        """
        payload = json.dumps(command).encode("utf-8")
        if self._mode == "mock" or not HAS_BLEAK:
            logger.debug("ESP32BLEDriver [mock]: send_command %r", command)
            return
        try:
            self._run_coro(self._write_async(payload))
        except Exception as exc:
            self._error = str(exc)
            self._connected = False
            logger.error("ESP32BLEDriver: write failed: %s", exc)

    def move(self, params: Optional[dict[str, Any]] = None) -> None:
        """Send a ``move`` command to the ESP32.

        Args:
            params: Optional dict with ``linear`` (m/s) and ``angular`` (rad/s)
                    values.  Defaults to ``{"linear": 0.5, "angular": 0.0}``.
        """
        params = params or {}
        cmd: dict[str, Any] = {
            "type": "move",
            "linear": float(params.get("linear", 0.5)),
            "angular": float(params.get("angular", 0.0)),
        }
        self._send_command(cmd)
        logger.debug("ESP32BLEDriver.move: %r", cmd)

    def stop(self) -> None:
        """Send a ``stop`` command to the ESP32."""
        self._send_command({"type": "stop"})
        logger.debug("ESP32BLEDriver.stop")

    def grip(self, open_gripper: bool = True) -> None:
        """Send a ``grip`` command to the ESP32.

        Args:
            open_gripper: ``True`` to open the gripper, ``False`` to close.
        """
        self._send_command({"type": "grip", "open": open_gripper})
        logger.debug("ESP32BLEDriver.grip: open=%s", open_gripper)

    def close(self) -> None:
        """Disconnect from the BLE device and clean up resources."""
        if self._mode == "hardware" and HAS_BLEAK:
            try:
                self._run_coro(self._disconnect_async())
            except Exception as exc:
                logger.debug("ESP32BLEDriver.close: disconnect error: %s", exc)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._mode = "mock"
        self._connected = False

    def health_check(self) -> dict[str, Any]:
        """Return BLE driver health status.

        Returns:
            Dict with keys:

            * ``ok`` — ``True`` in mock mode or when BLE is connected.
            * ``mode`` — ``"hardware"`` or ``"mock"``.
            * ``connected`` — ``True`` when a BLE connection is active.
            * ``address`` — configured BLE address.
            * ``char_uuid`` — GATT characteristic UUID.
            * ``has_bleak`` — ``True`` when the bleak library is installed.
            * ``error`` — last error string or ``None``.
        """
        connected = self._connected if self._mode == "hardware" else False
        return {
            "ok": self._mode == "mock" or connected,
            "mode": self._mode,
            "connected": connected,
            "address": self._address or None,
            "char_uuid": self._char_uuid,
            "has_bleak": HAS_BLEAK,
            "error": self._error,
        }
