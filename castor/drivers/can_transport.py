"""Generic CAN bus transport for OpenCastor drivers.

Wraps python-can with a simple send/recv interface.
Used by AcbDriver (transport: can) and future CAN-enabled drivers.

Install: pip install python-can>=4.0

Frame ID layout: ``(node_id << 5) | cmd_id``  (11-bit standard CAN ID)
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("OpenCastor.CanTransport")

try:
    import can as _can

    HAS_PYTHON_CAN = True
except ImportError:
    HAS_PYTHON_CAN = False
    _can = None  # type: ignore[assignment]


class CanTransport:
    """Thin CAN bus abstraction for OpenCastor drivers.

    Builds 11-bit CAN arbitration IDs as ``(node_id << 5) | cmd_id``.

    Args:
        interface: python-can interface type (e.g. ``"socketcan"``, ``"virtual"``).
        channel:   CAN channel/device (e.g. ``"can0"``, ``"vcan0"``).
        bitrate:   Bus bitrate in bits/second (default 1 Mbit/s).
    """

    def __init__(self, interface: str, channel: str, bitrate: int = 1_000_000):
        self._interface = interface
        self._channel = channel
        self._bitrate = bitrate
        self._bus = None

        if not HAS_PYTHON_CAN:
            logger.warning("python-can not installed — CanTransport running in mock mode")
            return

        try:
            self._bus = _can.interface.Bus(
                interface=interface,
                channel=channel,
                bitrate=bitrate,
            )
            logger.info("CanTransport connected: %s/%s @ %d bps", interface, channel, bitrate)
        except Exception as exc:
            logger.warning(
                "CanTransport init failed (%s/%s): %s — mock mode", interface, channel, exc
            )

    @staticmethod
    def _make_arb_id(node_id: int, cmd_id: int) -> int:
        """Build 11-bit CAN arbitration ID: ``(node_id << 5) | cmd_id``."""
        return ((node_id & 0x3F) << 5) | (cmd_id & 0x1F)

    def send(self, node_id: int, cmd_id: int, data: bytes) -> None:
        """Send a CAN frame.

        Args:
            node_id: Device node ID (0–63).
            cmd_id:  Command identifier (0–31).
            data:    Up to 8 bytes of payload (truncated silently if longer).
        """
        if not self._bus:
            logger.debug("MOCK CAN send: node=%d cmd=0x%02x data=%s", node_id, cmd_id, data.hex())
            return

        arb_id = self._make_arb_id(node_id, cmd_id)
        msg = _can.Message(arbitration_id=arb_id, data=data[:8], is_extended_id=False)
        try:
            self._bus.send(msg)
        except Exception as exc:
            logger.error("CAN send error (node=%d cmd=0x%02x): %s", node_id, cmd_id, exc)

    def recv(self, timeout: float = 0.1) -> Optional[tuple[int, int, bytes]]:
        """Receive a CAN frame.

        Args:
            timeout: Wait timeout in seconds.

        Returns:
            ``(node_id, cmd_id, data)`` tuple or ``None`` on timeout/error.
        """
        if not self._bus:
            logger.debug("MOCK CAN recv: timeout=%.3f", timeout)
            return None

        try:
            msg = self._bus.recv(timeout=timeout)
            if msg is None:
                return None
            arb = msg.arbitration_id
            node_id = (arb >> 5) & 0x3F
            cmd_id = arb & 0x1F
            return node_id, cmd_id, bytes(msg.data)
        except Exception as exc:
            logger.debug("CAN recv error: %s", exc)
            return None

    @property
    def connected(self) -> bool:
        """True when the CAN bus is open and ready."""
        return self._bus is not None

    def close(self) -> None:
        """Shut down the CAN bus connection."""
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None
