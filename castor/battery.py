"""
OpenCastor Battery Monitor -- read ADC voltage and warn on low battery.

Supports ADS1115 (common on Amazon robot kits) and INA219 voltage
sensors via I2C.  Falls back to a no-op if hardware is unavailable.

RCAN config keys (under ``battery``):
  - ``enabled``: true/false (default: false)
  - ``sensor``: ``"ads1115"`` or ``"ina219"`` (default: ads1115)
  - ``channel``: ADC channel number (default: 0)
  - ``warn_voltage``: voltage threshold for warnings (default: 6.5)
  - ``critical_voltage``: voltage for emergency stop (default: 6.0)
  - ``voltage_divider_ratio``: resistor divider ratio (default: 3.0)
"""

import logging
import threading
import time

logger = logging.getLogger("OpenCastor.Battery")


class BatteryMonitor:
    """Reads battery voltage and triggers callbacks on low levels."""

    def __init__(self, config: dict, on_warn=None, on_critical=None):
        bat_cfg = config.get("battery", {})
        self.enabled = bat_cfg.get("enabled", False)
        self.warn_voltage = bat_cfg.get("warn_voltage", 6.5)
        self.critical_voltage = bat_cfg.get("critical_voltage", 6.0)
        self.divider_ratio = bat_cfg.get("voltage_divider_ratio", 3.0)
        self.channel = bat_cfg.get("channel", 0)
        self.sensor_type = bat_cfg.get("sensor", "ads1115")

        self._on_warn = on_warn
        self._on_critical = on_critical
        self._adc = None
        self._running = False
        self._thread = None
        self._last_voltage = None
        self._warned = False

        if not self.enabled:
            return

        try:
            if self.sensor_type == "ads1115":
                import adafruit_ads1x15.ads1115 as ADS
                import board
                import busio
                from adafruit_ads1x15.analog_in import AnalogIn

                i2c = busio.I2C(board.SCL, board.SDA)
                ads = ADS.ADS1115(i2c)
                self._adc = AnalogIn(ads, self.channel)
                logger.info("Battery monitor online (ADS1115)")
            elif self.sensor_type == "ina219":
                import board
                import busio
                from adafruit_ina219 import INA219

                i2c = busio.I2C(board.SCL, board.SDA)
                self._adc = INA219(i2c)
                logger.info("Battery monitor online (INA219)")
        except ImportError:
            logger.info("Battery sensor SDK not installed -- monitor disabled")
            self.enabled = False
        except Exception as exc:
            logger.warning(f"Battery sensor init failed: {exc}")
            self.enabled = False

    @property
    def voltage(self) -> float:
        """Return the last read battery voltage, or 0.0 if unavailable."""
        return self._last_voltage if self._last_voltage is not None else 0.0

    def read_voltage(self) -> float:
        """Read the current battery voltage."""
        if not self.enabled or self._adc is None:
            return 0.0

        try:
            if self.sensor_type == "ads1115":
                raw_v = self._adc.voltage
                voltage = raw_v * self.divider_ratio
            elif self.sensor_type == "ina219":
                voltage = self._adc.bus_voltage + self._adc.shunt_voltage / 1000
            else:
                return 0.0

            self._last_voltage = round(voltage, 2)
            return self._last_voltage
        except Exception as exc:
            logger.debug(f"Battery read error: {exc}")
            return 0.0

    def start(self, interval: float = 10.0):
        """Start background monitoring thread."""
        if not self.enabled:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop, args=(interval,), daemon=True
        )
        self._thread.start()

    def stop(self):
        """Stop the background monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _monitor_loop(self, interval: float):
        """Background loop that checks voltage periodically."""
        while self._running:
            voltage = self.read_voltage()

            if voltage > 0:
                if voltage <= self.critical_voltage:
                    logger.critical(
                        f"BATTERY CRITICAL: {voltage}V "
                        f"(threshold: {self.critical_voltage}V)"
                    )
                    if self._on_critical:
                        self._on_critical(voltage)
                elif voltage <= self.warn_voltage and not self._warned:
                    logger.warning(
                        f"Battery low: {voltage}V "
                        f"(threshold: {self.warn_voltage}V)"
                    )
                    self._warned = True
                    if self._on_warn:
                        self._on_warn(voltage)
                elif voltage > self.warn_voltage:
                    self._warned = False

            time.sleep(interval)
