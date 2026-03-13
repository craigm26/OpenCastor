"""Arduino serial driver using a simple JSON line-protocol.

Targets Arduino boards (Uno/Mega/Nano/Leonardo/Micro and CH340 clones) running
the companion ``firmware/arduino_l298n_bridge.ino`` sketch.  The protocol is a
single JSON object per line, both directions:

Host → Arduino::

    {"cmd":"drive","left":150,"right":-100}   # tank drive (PWM, -255..255)
    {"cmd":"stop"}                             # emergency stop
    {"cmd":"sensor","id":"hcsr04"}             # query HC-SR04
    {"cmd":"servo","pin":3,"angle":90}         # set servo angle

Arduino → Host (ack / sensor data)::

    {"ack":true}
    {"sensor":"hcsr04","distance_mm":342}
    {"error":"unknown command"}

When ``pyserial`` is not installed or the port cannot be opened the driver
falls back to **mock mode** and logs every command without sending it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.Arduino")

try:
    import serial
    import serial.serialutil

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class ArduinoSerialDriver(DriverBase):
    """Drive an Arduino + L298N (or similar H-bridge) over USB serial.

    Config keys (from the ``drivers`` list entry in the RCAN file):

    * ``port``        – serial port, e.g. ``/dev/ttyACM0`` (default)
    * ``baud``        – baud rate (default 115200)
    * ``max_pwm``     – peak PWM value sent to Arduino (default 255)
    * ``deadband_pwm``– minimum PWM to overcome stiction (default 40)
    * ``timeout_s``   – read timeout in seconds (default 0.5)
    * ``invert_left`` – flip left-motor polarity (default false)
    * ``invert_right``– flip right-motor polarity (default false)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._port = str(config.get("port", "/dev/ttyACM0")).strip()
        self._baud = int(config.get("baud", 115200))
        self._max_pwm = int(config.get("max_pwm", 255))
        self._deadband = int(config.get("deadband_pwm", 40))
        self._timeout_s = float(config.get("timeout_s", 0.5))
        self._invert_left = bool(config.get("invert_left", False))
        self._invert_right = bool(config.get("invert_right", False))

        self._ser: Optional[Any] = None  # serial.Serial when HAS_SERIAL
        self._lock = threading.Lock()
        self._mode = "mock"
        self._last_error: Optional[str] = None

        if not HAS_SERIAL:
            self._last_error = "pyserial not installed; run: pip install pyserial"
            logger.warning("Arduino driver: pyserial missing — mock mode active")
            return

        self._open_serial()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_serial(self) -> None:
        """Try to open the serial port; stay in mock mode on failure."""
        try:
            self._ser = serial.Serial(
                port=self._port,
                baudrate=self._baud,
                timeout=self._timeout_s,
            )
            # Give the Arduino time to reset after DTR toggle
            time.sleep(2.0)
            self._ser.reset_input_buffer()
            self._mode = "hardware"
            self._last_error = None
            logger.info("Arduino driver connected on %s @ %d baud", self._port, self._baud)
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("Arduino driver: cannot open %s — mock mode (%s)", self._port, exc)

    def _send(self, payload: dict) -> Optional[dict]:
        """Serialize *payload* as a JSON line and read back the ack.

        Returns the parsed reply dict, or ``None`` if not in hardware mode.
        """
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        with self._lock:
            if self._mode != "hardware" or self._ser is None:
                logger.debug("Arduino mock TX: %s", line.rstrip())
                return None
            try:
                self._ser.write(line.encode())
                self._ser.flush()
                raw = self._ser.readline()
                if raw:
                    return json.loads(raw.decode(errors="replace").strip())
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("Arduino serial error: %s — switching to mock", exc)
                self._mode = "mock"
        return None

    def _mix_tank(self, linear: float, angular: float) -> tuple[int, int]:
        """Convert normalised linear/angular (-1..1) to left/right PWM ints."""
        left_f = _clamp(linear + angular, -1.0, 1.0)
        right_f = _clamp(linear - angular, -1.0, 1.0)

        if self._invert_left:
            left_f = -left_f
        if self._invert_right:
            right_f = -right_f

        def _to_pwm(v: float) -> int:
            if abs(v) < 1e-6:
                return 0
            sign = 1 if v > 0 else -1
            mag = abs(v) * self._max_pwm
            if mag < self._deadband:
                mag = float(self._deadband)
            return sign * int(round(min(mag, self._max_pwm)))

        return _to_pwm(left_f), _to_pwm(right_f)

    # ------------------------------------------------------------------
    # DriverBase interface
    # ------------------------------------------------------------------

    def move(self, **kwargs: Any) -> None:
        """Send a drive command.

        Accepts ``linear``/``angular`` (preferred) or legacy ``linear_x``/
        ``angular_z`` keys, all normalised to the range ``[-1.0, 1.0]``.
        """
        linear = float(kwargs.get("linear", kwargs.get("linear_x", 0.0)))
        angular = float(kwargs.get("angular", kwargs.get("angular_z", 0.0)))
        left, right = self._mix_tank(linear, angular)
        self._send({"cmd": "drive", "left": left, "right": right})

    def stop(self) -> None:
        """Send an emergency stop command."""
        self._send({"cmd": "stop"})

    def close(self) -> None:
        """Close the serial connection."""
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
        self._mode = "mock"
        logger.info("Arduino driver closed")

    def health_check(self) -> dict:
        """Return driver health status.

        Tries a lightweight ping by sending a ``{"cmd":"ping"}`` and checking
        for any reply.  Falls back gracefully when in mock mode.
        """
        if self._mode != "hardware":
            return {
                "ok": False,
                "mode": "mock",
                "error": self._last_error or "not connected",
            }

        reply = self._send({"cmd": "ping"})
        if reply is not None:
            return {"ok": True, "mode": "hardware", "error": None}

        # If we sent but got no reply, the port may still be fine (older firmware
        # might not respond to ping) — treat as healthy if no exception was raised.
        if self._mode == "hardware":
            return {"ok": True, "mode": "hardware", "error": None}

        return {
            "ok": False,
            "mode": "mock",
            "error": self._last_error or "serial error",
        }

    # ------------------------------------------------------------------
    # Extra helpers (accessible via ToolRegistry / API extension)
    # ------------------------------------------------------------------

    def query_sensor(self, sensor_id: str) -> Optional[dict]:
        """Query a sensor by ID (e.g. ``hcsr04``).

        Returns the parsed sensor JSON from the Arduino, or ``None`` in mock
        mode.
        """
        return self._send({"cmd": "sensor", "id": sensor_id})

    def set_servo(self, pin: int, angle: int) -> Optional[dict]:
        """Set a servo to *angle* degrees (0–180) on the given Arduino *pin*."""
        angle = int(_clamp(angle, 0, 180))
        return self._send({"cmd": "servo", "pin": int(pin), "angle": angle})
