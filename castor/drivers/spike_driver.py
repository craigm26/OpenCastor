"""LEGO SPIKE Prime serial driver with optional BLE hooks.

Primary mode is USB CDC serial to the SPIKE hub REPL.  Commands are emitted as
MicroPython snippets that pair the configured drive motors and issue tank-drive
updates. BLE availability is detected as an optional capability hook.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.SPIKE")

try:
    import serial

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

try:
    import bleak  # noqa: F401

    HAS_BLEAK = True
except ImportError:
    HAS_BLEAK = False


class SpikeHubDriver(DriverBase):
    """Drive LEGO SPIKE Prime over serial (with BLE fallback hooks)."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._mode = "mock"
        self._error: Optional[str] = "driver not connected"
        self._lock = threading.Lock()

        serial_cfg = config.get("connection", {})
        self._port = str(serial_cfg.get("port", "/dev/ttyACM0")).strip()
        self._baud = int(serial_cfg.get("baud", 115200))
        self._timeout_s = float(serial_cfg.get("timeout_s", 1.0))
        self._serial: Optional[serial.Serial] = None if HAS_SERIAL else None  # type: ignore[name-defined]

        ble_cfg = serial_cfg.get("ble_alternative", {}) if isinstance(serial_cfg, dict) else {}
        self._ble_enabled = bool(ble_cfg.get("enabled", False))
        self._ble_name = str(ble_cfg.get("device_name", "LEGO Hub")).strip()

        spike_entries = [
            d for d in config.get("drivers", []) if d.get("protocol") == "spike_hub_serial"
        ]
        self._left_port, self._right_port = self._select_drive_ports(spike_entries)
        self._left_mult = self._direction_for_port(spike_entries, self._left_port)
        self._right_mult = self._direction_for_port(spike_entries, self._right_port)
        self._pair_initialized = False

        if self._ble_enabled and HAS_BLEAK:
            self._mode = "ble-hook"
            self._error = "BLE hook mode configured (serial disabled)"
            logger.info(
                "SPIKE BLE hook enabled for device '%s' (serial commands disabled)", self._ble_name
            )
            return

        if not HAS_SERIAL:
            self._error = "pyserial not available"
            logger.warning("SPIKE serial driver unavailable; pyserial not installed")
            return

        self._ensure_serial()

    @staticmethod
    def _select_drive_ports(entries: list[dict[str, Any]]) -> tuple[str, str]:
        left = None
        right = None
        fallback = []
        for cfg in entries:
            if str(cfg.get("device", "motor")).lower() != "motor":
                continue
            port = str(cfg.get("port", "")).strip().upper()
            if not port:
                continue
            fallback.append(port)
            cfg_id = str(cfg.get("id", "")).lower()
            if left is None and "left" in cfg_id:
                left = port
            if right is None and "right" in cfg_id:
                right = port

        if left is None and fallback:
            left = fallback[0]
        if right is None:
            right = fallback[1] if len(fallback) > 1 else left
        return left or "A", right or "B"

    @staticmethod
    def _direction_for_port(entries: list[dict[str, Any]], port: str) -> int:
        for cfg in entries:
            if str(cfg.get("port", "")).strip().upper() != port:
                continue
            value = cfg.get("direction", 1)
            try:
                return -1 if int(value) < 0 else 1
            except Exception:
                return 1
        return 1

    @staticmethod
    def _coerce_motion(linear_or_action: Any, angular: float) -> tuple[float, float]:
        if isinstance(linear_or_action, dict):
            linear = float(linear_or_action.get("linear", 0.0))
            ang = float(linear_or_action.get("angular", 0.0))
        else:
            linear = float(linear_or_action)
            ang = float(angular)
        return linear, ang

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    def _ensure_serial(self) -> bool:
        if self._serial is not None and self._serial.is_open:
            return True
        if not HAS_SERIAL:
            return False
        try:
            self._serial = serial.Serial(  # type: ignore[name-defined]
                self._port,
                self._baud,
                timeout=self._timeout_s,
                write_timeout=self._timeout_s,
            )
            self._mode = "hardware-serial"
            self._error = None
            logger.info("SPIKE serial connected on %s @ %s baud", self._port, self._baud)
            return True
        except Exception as exc:
            self._mode = "mock"
            self._error = str(exc)
            return False

    def _write_repl(self, snippet: str) -> bool:
        if not self._ensure_serial():
            return False
        assert self._serial is not None
        try:
            self._serial.write((snippet.strip() + "\r\n").encode("utf-8"))
            self._serial.flush()
            return True
        except Exception as exc:
            self._mode = "mock"
            self._error = str(exc)
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
            return False

    def _move_serial(self, left_pct: int, right_pct: int) -> bool:
        with self._lock:
            if not self._pair_initialized:
                setup = (
                    "import motor_pair;"
                    f"motor_pair.pair(motor_pair.PAIR_1, '{self._left_port}', '{self._right_port}')"
                )
                if not self._write_repl(setup):
                    return False
                self._pair_initialized = True
            cmd = (
                "import motor_pair;"
                f"motor_pair.move_tank(motor_pair.PAIR_1, {left_pct}, {right_pct})"
            )
            return self._write_repl(cmd)

    def move(self, linear: float = 0.0, angular: float = 0.0) -> None:
        linear, angular = self._coerce_motion(linear, angular)
        left = self._clamp(linear - angular, -1.0, 1.0) * self._left_mult
        right = self._clamp(linear + angular, -1.0, 1.0) * self._right_mult
        left_pct = int(round(left * 100))
        right_pct = int(round(right * 100))

        if self._mode == "ble-hook":
            logger.info("[MOCK SPIKE BLE] left=%d right=%d", left_pct, right_pct)
            return

        if not self._move_serial(left_pct, right_pct):
            logger.info("[MOCK SPIKE] left=%d right=%d", left_pct, right_pct)

    def stop(self) -> None:
        if self._mode == "ble-hook":
            logger.info("[MOCK SPIKE BLE] stop")
            return
        if not self._move_serial(0, 0):
            logger.info("[MOCK SPIKE] stop")

    def close(self) -> None:
        self.stop()
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def health_check(self) -> dict[str, Any]:
        if self._mode == "ble-hook":
            return {
                "ok": HAS_BLEAK,
                "mode": "ble-hook",
                "error": self._error,
            }
        if self._ensure_serial():
            return {"ok": True, "mode": "hardware", "transport": "serial", "error": None}
        return {"ok": False, "mode": "mock", "error": self._error}
