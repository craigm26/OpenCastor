"""
INA219 Power / Battery Monitor.

Reads bus voltage, shunt current, and power from a Texas Instruments INA219
sensor over I2C. Runs a background polling thread and fires a callback when
battery voltage drops below a configurable threshold.

Wiring (RPi):  VCC → 3.3 V, GND → GND, SDA → GPIO 2, SCL → GPIO 3.

Env vars:
  INA219_I2C_ADDRESS  — hex address, default "0x40"
  INA219_SHUNT_OHMS   — float, default "0.1"
  INA219_LOW_BATT_V   — float, default "6.5" (adjust for battery chemistry)

Install:  pip install adafruit-circuitpython-ina219
"""

import logging
import os
import threading
import time
from typing import Optional
from collections.abc import Callable

logger = logging.getLogger("OpenCastor.INA219")

try:
    import board
    import busio
    from adafruit_ina219 import INA219 as _INA219

    HAS_INA219 = True
except ImportError:
    HAS_INA219 = False

_singleton: Optional["BatteryMonitor"] = None
_lock = threading.Lock()


class BatteryMonitor:
    """Continuous INA219 power telemetry with low-battery alerting."""

    def __init__(self, i2c_address: int = 0x40, shunt_ohms: float = 0.1):
        self._address = i2c_address
        self._shunt_ohms = shunt_ohms
        self._low_batt_v: float = float(os.getenv("INA219_LOW_BATT_V", "6.5"))
        self._sensor = None
        self._mode = "mock"
        self._latest: dict = {"voltage_v": 0.0, "current_ma": 0.0, "power_mw": 0.0}
        self._alert_cb: Optional[Callable] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        if HAS_INA219:
            try:
                i2c = busio.I2C(board.SCL, board.SDA)
                self._sensor = _INA219(i2c, addr=i2c_address)
                self._mode = "hardware"
                logger.info("INA219 initialized at 0x%02x", i2c_address)
            except Exception as exc:
                logger.warning("INA219 init failed: %s — mock mode", exc)
        else:
            logger.info(
                "INA219 running in mock mode (install: pip install adafruit-circuitpython-ina219)"
            )

    # ── Public API ────────────────────────────────────────────────────

    def start(
        self,
        poll_interval_s: float = 1.0,
        on_low_battery: Optional[Callable[[dict], None]] = None,
    ):
        """Start background polling. *on_low_battery* called with latest reading on alert."""
        self._alert_cb = on_low_battery
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, args=(poll_interval_s,), daemon=True
        )
        self._thread.start()
        logger.info("INA219 polling started (interval=%.1fs)", poll_interval_s)

    def stop(self):
        """Stop the background polling thread."""
        self._running = False

    def read(self) -> dict:
        """Return a snapshot of current power readings."""
        if self._mode == "mock" or self._sensor is None:
            return {"voltage_v": 0.0, "current_ma": 0.0, "power_mw": 0.0, "mode": "mock"}
        try:
            voltage = round(self._sensor.bus_voltage + self._sensor.shunt_voltage / 1000.0, 3)
            current = round(self._sensor.current, 2)
            power = round(self._sensor.power, 2)
            return {
                "voltage_v": voltage,
                "current_ma": current,
                "power_mw": power,
                "mode": "hardware",
            }
        except Exception as exc:
            logger.error("INA219 read error: %s", exc)
            return {
                "voltage_v": 0.0,
                "current_ma": 0.0,
                "power_mw": 0.0,
                "mode": "error",
                "error": str(exc),
            }

    @property
    def latest(self) -> dict:
        """Last polled reading (updated in background)."""
        return dict(self._latest)

    @property
    def mode(self) -> str:
        return self._mode

    # ── Internal ──────────────────────────────────────────────────────

    def _poll_loop(self, interval: float):
        while self._running:
            reading = self.read()
            self._latest = reading
            v = reading.get("voltage_v", 0.0)
            if v > 0 and v < self._low_batt_v and self._alert_cb:
                try:
                    self._alert_cb(reading)
                except Exception as exc:
                    logger.error("Low-battery callback error: %s", exc)
            time.sleep(interval)


def get_monitor(
    i2c_address: Optional[int] = None,
    shunt_ohms: Optional[float] = None,
) -> BatteryMonitor:
    """Return the process-wide BatteryMonitor singleton."""
    global _singleton
    with _lock:
        if _singleton is None:
            addr = (
                i2c_address
                if i2c_address is not None
                else int(os.getenv("INA219_I2C_ADDRESS", "0x40"), 16)
            )
            ohms = (
                shunt_ohms
                if shunt_ohms is not None
                else float(os.getenv("INA219_SHUNT_OHMS", "0.1"))
            )
            _singleton = BatteryMonitor(addr, ohms)
    return _singleton
