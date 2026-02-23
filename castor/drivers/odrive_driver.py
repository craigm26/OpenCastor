"""
ODrive / VESC brushless motor driver.

Supports:
  - ODrive v3.x/v0.5.x via native `odrive` Python library (USB)
  - VESC via `pyvesc` + pyserial (UART/USB serial)
  - Mock fallback when neither is installed

RCAN config (ODrive):
  drivers:
  - id: brushless
    protocol: odrive
    axis0: 0
    axis1: 1
    max_velocity: 20.0    # turns/sec

RCAN config (VESC):
  drivers:
  - id: brushless
    protocol: vesc
    port: /dev/ttyUSB0
    max_velocity: 5000    # ERPM

Install: pip install odrive   (ODrive)
         pip install pyvesc pyserial  (VESC)
"""

import logging

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.ODrive")

try:
    import odrive as _odrive

    HAS_ODRIVE = True
except ImportError:
    HAS_ODRIVE = False

try:
    import pyvesc
    import serial as _serial

    HAS_VESC = True
except ImportError:
    HAS_VESC = False


class ODriveDriver(DriverBase):
    """ODrive / VESC brushless motor controller."""

    def __init__(self, config: dict):
        self.config = config
        self._protocol = config.get("protocol", "odrive").lower()
        self._max_vel = float(config.get("max_velocity", 20.0))
        self._axis0_idx = int(config.get("axis0", 0))
        self._axis1_idx = int(config.get("axis1", 1))
        self._mode = "mock"
        self._odrv = None
        self._vesc_serial = None

        if self._protocol == "odrive" and HAS_ODRIVE:
            try:
                self._odrv = _odrive.find_any(timeout=5)
                if self._odrv is not None:
                    self._mode = "hardware"
                    logger.info("ODrive connected: serial=%s", getattr(self._odrv, "serial_number", "?"))
                else:
                    logger.warning("ODrive not found — mock mode")
            except Exception as exc:
                logger.warning("ODrive init error: %s — mock mode", exc)

        elif self._protocol == "vesc" and HAS_VESC:
            port = config.get("port", "/dev/ttyUSB0")
            try:
                self._vesc_serial = _serial.Serial(port, baudrate=115200, timeout=0.05)
                self._mode = "hardware"
                logger.info("VESC connected on %s", port)
            except Exception as exc:
                logger.warning("VESC init failed on %s: %s — mock mode", port, exc)

        if self._mode == "mock":
            logger.info("ODrive/VESC driver running in mock mode (protocol=%s)", self._protocol)

    # ── Helpers ───────────────────────────────────────────────────────

    def _odrive_axis(self, idx: int):
        if self._odrv is None:
            return None
        return self._odrv.axis0 if idx == 0 else self._odrv.axis1

    # ── DriverBase interface ──────────────────────────────────────────

    def move(self, action: dict):
        linear = float(action.get("linear", 0.0))
        angular = float(action.get("angular", 0.0))
        vel_l = (linear - angular) * self._max_vel
        vel_r = (linear + angular) * self._max_vel

        if self._mode == "mock":
            logger.debug("MOCK %s move: left=%.2f right=%.2f", self._protocol, vel_l, vel_r)
            return

        if self._protocol == "odrive" and self._odrv:
            try:
                ax0 = self._odrive_axis(self._axis0_idx)
                ax1 = self._odrive_axis(self._axis1_idx)
                if ax0:
                    ax0.controller.input_vel = vel_l
                if ax1:
                    ax1.controller.input_vel = vel_r
            except Exception as exc:
                logger.error("ODrive move error: %s", exc)

        elif self._protocol == "vesc" and self._vesc_serial:
            try:
                # VESC SetRPM — use left motor RPM as primary signal
                erpm = int(vel_l)
                self._vesc_serial.write(pyvesc.encode(pyvesc.SetRPM(erpm)))
            except Exception as exc:
                logger.error("VESC move error: %s", exc)

    def stop(self):
        if self._mode == "mock":
            logger.debug("MOCK %s stop", self._protocol)
            return
        if self._protocol == "odrive" and self._odrv:
            try:
                for idx in (self._axis0_idx, self._axis1_idx):
                    ax = self._odrive_axis(idx)
                    if ax:
                        ax.controller.input_vel = 0
            except Exception as exc:
                logger.error("ODrive stop error: %s", exc)
        elif self._protocol == "vesc" and self._vesc_serial:
            try:
                self._vesc_serial.write(pyvesc.encode(pyvesc.SetRPM(0)))
            except Exception:
                pass

    def close(self):
        self.stop()
        if self._vesc_serial:
            try:
                self._vesc_serial.close()
            except Exception:
                pass

    def health_check(self) -> dict:
        if self._mode == "mock":
            return {"ok": True, "mode": "mock", "protocol": self._protocol, "error": None}
        if self._protocol == "odrive" and self._odrv:
            try:
                vbus = round(self._odrv.vbus_voltage, 2)
                return {"ok": True, "mode": "hardware", "vbus_v": vbus, "error": None}
            except Exception as exc:
                return {"ok": False, "mode": "hardware", "error": str(exc)}
        if self._protocol == "vesc" and self._vesc_serial:
            return {
                "ok": self._vesc_serial.is_open,
                "mode": "hardware",
                "port": self._vesc_serial.port,
                "error": None,
            }
        return {"ok": False, "mode": "mock", "error": "no device connected"}
