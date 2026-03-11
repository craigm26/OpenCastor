"""
ODrive / VESC brushless motor driver.

Supports:
  - ODrive v3.x/v0.5.x via native ``odrive`` Python library (USB)
  - VESC via ``pyvesc`` + pyserial (UART/USB serial)
  - Mock fallback when neither is installed

RCAN config (ODrive)::

    drivers:
    - id: brushless
      protocol: odrive
      axis0: 0
      axis1: 1
      max_velocity: 20.0      # turns/sec
      control_mode: velocity  # velocity | position | torque

RCAN config (VESC)::

    drivers:
    - id: brushless
      protocol: vesc
      port: /dev/ttyUSB0
      max_velocity: 5000    # ERPM

Install::

    pip install odrive     (ODrive)
    pip install pyvesc pyserial  (VESC)

Issue #266 additions:
  - ``control_mode`` RCAN field: ``velocity`` | ``position`` | ``torque``
  - ``set_position(axis, pos_turns)`` — position-control setpoint
  - ``get_encoder()`` → ``{pos_turns, vel_turns_s, error}``
"""

from __future__ import annotations

import logging

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.ODrive")

# ---------------------------------------------------------------------------
# Optional SDK guards
# ---------------------------------------------------------------------------

try:
    import odrive as _odrive

    HAS_ODRIVE = True
except ImportError:
    HAS_ODRIVE = False
    _odrive = None  # type: ignore[assignment]

try:
    import pyvesc
    import serial as _serial

    HAS_VESC = True
except ImportError:
    HAS_VESC = False

# ---------------------------------------------------------------------------
# Control mode constants (ODrive)
# ---------------------------------------------------------------------------

_CONTROL_MODE_VELOCITY = 2  # ODrive CONTROL_MODE_VELOCITY_CONTROL
_CONTROL_MODE_POSITION = 3  # ODrive CONTROL_MODE_POSITION_CONTROL
_CONTROL_MODE_TORQUE = 1  # ODrive CONTROL_MODE_TORQUE_CONTROL

_CONTROL_MODE_MAP = {
    "velocity": _CONTROL_MODE_VELOCITY,
    "position": _CONTROL_MODE_POSITION,
    "torque": _CONTROL_MODE_TORQUE,
}


class ODriveDriver(DriverBase):
    """ODrive / VESC brushless motor controller.

    Args:
        config: RCAN driver config dict.  Relevant keys:
                ``protocol`` (``"odrive"`` | ``"vesc"``),
                ``axis0`` (int, default 0),
                ``axis1`` (int, default 1),
                ``max_velocity`` (float, default 20.0 turns/s or ERPM),
                ``control_mode`` (``"velocity"`` | ``"position"`` | ``"torque"``).
    """

    def __init__(self, config: dict):
        self.config = config
        self._protocol = config.get("protocol", "odrive").lower()
        self._max_vel = float(config.get("max_velocity", 20.0))
        self._axis0_idx = int(config.get("axis0", 0))
        self._axis1_idx = int(config.get("axis1", 1))
        self._control_mode_name: str = config.get("control_mode", "velocity").lower()
        self._control_mode_int: int = _CONTROL_MODE_MAP.get(
            self._control_mode_name, _CONTROL_MODE_VELOCITY
        )
        self._mode = "mock"
        self._odrv = None
        self._vesc_serial = None

        if self._protocol == "odrive" and HAS_ODRIVE:
            try:
                self._odrv = _odrive.find_any(timeout=5)
                if self._odrv is not None:
                    self._mode = "hardware"
                    self._apply_control_mode()
                    logger.info(
                        "ODrive connected: serial=%s control_mode=%s",
                        getattr(self._odrv, "serial_number", "?"),
                        self._control_mode_name,
                    )
                else:
                    logger.warning("ODrive not found — mock mode")
            except Exception as exc:
                logger.warning("ODrive init error: %s — mock mode", exc)

        elif self._protocol == "vesc" and HAS_VESC:
            port = config.get("port", "/dev/ttyUSB0")
            if str(port or "").lower() == "auto":
                port = self._auto_detect_vesc_port() or "/dev/ttyUSB0"
            try:
                self._vesc_serial = _serial.Serial(port, baudrate=115200, timeout=0.05)
                self._mode = "hardware"
                logger.info("VESC connected on %s", port)
            except Exception as exc:
                logger.warning("VESC init failed on %s: %s — mock mode", port, exc)

        if self._mode == "mock":
            logger.info("ODrive/VESC driver running in mock mode (protocol=%s)", self._protocol)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _auto_detect_vesc_port():
        """Use hardware_detect to find the first VESC serial port."""
        try:
            from castor.hardware_detect import detect_odrive_usb, detect_vesc_usb

            # Prefer explicit VESC detection first
            vesc_ports = detect_vesc_usb()
            if vesc_ports:
                logger.info("ODriveDriver auto-detected VESC port: %s", vesc_ports[0])
                return vesc_ports[0]
            # Fall back to ODrive USB
            odrive_ports = detect_odrive_usb()
            if odrive_ports:
                logger.info("ODriveDriver auto-detected ODrive port: %s", odrive_ports[0])
                return odrive_ports[0]
        except Exception as exc:
            logger.warning("ODriveDriver auto-detect failed: %s", exc)
        return None

    def _odrive_axis(self, idx: int):
        """Return the ODrive axis object for *idx* (0 or 1).

        Args:
            idx: Axis index (0 or 1).

        Returns:
            ODrive axis object or ``None`` if not connected.
        """
        if self._odrv is None:
            return None
        return self._odrv.axis0 if idx == 0 else self._odrv.axis1

    def _apply_control_mode(self) -> None:
        """Push the RCAN ``control_mode`` setting to both ODrive axes."""
        if self._odrv is None:
            return
        for idx in (self._axis0_idx, self._axis1_idx):
            ax = self._odrive_axis(idx)
            if ax is None:
                continue
            try:
                ax.controller.config.control_mode = self._control_mode_int
            except Exception as exc:
                logger.warning("ODrive control_mode set error (axis %d): %s", idx, exc)

    # ── DriverBase interface ────────────────────────────────────────────────────

    def move(self, action: dict) -> None:
        """Move the robot using velocity setpoints.

        Maps ``linear`` / ``angular`` to left/right velocity targets.

        Args:
            action: Dict with ``linear`` (float) and ``angular`` (float) keys.
                    Values in [-1, 1] range; scaled by ``max_velocity``.
        """
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
                erpm = int(vel_l)
                self._vesc_serial.write(pyvesc.encode(pyvesc.SetRPM(erpm)))
            except Exception as exc:
                logger.error("VESC move error: %s", exc)

    def set_position(self, axis: int, pos_turns: float) -> None:
        """Set a position-control setpoint for a single axis.

        Requires ``control_mode: position`` in the RCAN config.

        Args:
            axis:       Axis index (0 or 1).
            pos_turns:  Target position in turns (encoder counts from origin).
        """
        if self._mode == "mock":
            logger.debug("MOCK set_position axis=%d pos=%.4f turns", axis, pos_turns)
            return

        if self._protocol == "odrive" and self._odrv:
            ax = self._odrive_axis(axis)
            if ax is None:
                logger.warning("set_position: axis %d not available", axis)
                return
            try:
                ax.controller.input_pos = float(pos_turns)
            except Exception as exc:
                logger.error("ODrive set_position error (axis %d): %s", axis, exc)

    def get_encoder(self, axis: int = 0) -> dict:
        """Read encoder state for *axis*.

        Args:
            axis: Axis index (0 or 1).

        Returns:
            Dict with:
              - ``pos_turns`` (float): Current position in turns.
              - ``vel_turns_s`` (float): Current velocity in turns/second.
              - ``error`` (int | None): ODrive axis error code, or ``None``.
        """
        if self._mode == "mock":
            return {"pos_turns": 0.0, "vel_turns_s": 0.0, "error": None}

        if self._protocol == "odrive" and self._odrv:
            ax = self._odrive_axis(axis)
            if ax is None:
                return {"pos_turns": 0.0, "vel_turns_s": 0.0, "error": "axis unavailable"}
            try:
                pos = float(ax.encoder.pos_estimate)
                vel = float(ax.encoder.vel_estimate)
                err = getattr(ax.encoder, "error", None)
                return {"pos_turns": pos, "vel_turns_s": vel, "error": err}
            except Exception as exc:
                return {"pos_turns": 0.0, "vel_turns_s": 0.0, "error": str(exc)}

        return {"pos_turns": 0.0, "vel_turns_s": 0.0, "error": "protocol unsupported"}

    def stop(self) -> None:
        """Halt both axes (velocity → 0)."""
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

    def close(self) -> None:
        """Stop motors and release resources."""
        self.stop()
        if self._vesc_serial:
            try:
                self._vesc_serial.close()
            except Exception:
                pass

    def health_check(self) -> dict:
        """Return driver status dict.

        Returns:
            Dict with ``ok``, ``mode``, ``protocol``, ``control_mode``,
            optional ``vbus_v``, and ``error``.
        """
        base = {
            "protocol": self._protocol,
            "control_mode": self._control_mode_name,
        }
        if self._mode == "mock":
            return {"ok": True, "mode": "mock", "error": None, **base}
        if self._protocol == "odrive" and self._odrv:
            try:
                vbus = round(self._odrv.vbus_voltage, 2)
                return {"ok": True, "mode": "hardware", "vbus_v": vbus, "error": None, **base}
            except Exception as exc:
                return {"ok": False, "mode": "hardware", "error": str(exc), **base}
        if self._protocol == "vesc" and self._vesc_serial:
            return {
                "ok": self._vesc_serial.is_open,
                "mode": "hardware",
                "port": self._vesc_serial.port,
                "error": None,
                **base,
            }
        return {"ok": False, "mode": "mock", "error": "no device connected", **base}
