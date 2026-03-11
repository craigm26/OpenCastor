"""
Feetech STS3215 / SCServo serial bus driver for OpenCastor.

Supports SO-ARM100/101, LeRobot kits, and any Feetech half-duplex UART servo.

RCAN config::

    drivers:
      - id: arm
        protocol: feetech
        port: /dev/ttyACM0   # or 'auto'
        baud: 1000000
        servo_ids: [1, 2, 3, 4, 5, 6]
        joint_names: [shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper]
        operating_mode: position  # position | velocity | torque

Install: pip install opencastor[lerobot]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.FeetechDriver")

try:
    from feetech_servo_sdk import (  # type: ignore[import]
        GroupSyncRead,
        GroupSyncWrite,
        PacketHandler,
        PortHandler,
    )

    HAS_SCSERVO = True
except ImportError:
    HAS_SCSERVO = False
    PortHandler = None  # type: ignore[assignment]
    PacketHandler = None  # type: ignore[assignment]
    GroupSyncWrite = None  # type: ignore[assignment]
    GroupSyncRead = None  # type: ignore[assignment]

# STS3215 control table addresses
_ADDR_TORQUE_ENABLE = 40
_ADDR_GOAL_POSITION = 42
_ADDR_PRESENT_POSITION = 56
_ADDR_PRESENT_LOAD = 60

_PROTOCOL_VERSION = 0  # SCServo uses protocol 0
_POS_MIN = 0
_POS_MAX = 4095
_POS_CENTER = 2048


@dataclass
class FeetechCalibrationResult:
    """Result of a Feetech servo calibration sequence."""

    success: bool
    home_positions: Dict[int, int] = field(default_factory=dict)
    joint_names: Dict[int, str] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON storage."""
        return {
            "success": self.success,
            "home_positions": self.home_positions,
            "joint_names": self.joint_names,
            "error": self.error,
        }


class FeetechDriver(DriverBase):
    """Driver for Feetech STS3215 / SCServo half-duplex serial bus servos.

    Supports SO-ARM100/101, LeRobot kits, and similar 6-DOF arms using Feetech
    servo motors.  Falls back to mock mode when ``scservo_sdk`` is not installed
    or the serial port cannot be opened.

    Args:
        config: RCAN driver config dict. Relevant keys:

            - ``port`` (str): Serial port path, or ``"auto"`` to detect.
            - ``baud`` (int): Baud rate. Default: 1 000 000.
            - ``servo_ids`` (list[int]): Servo IDs on the bus. Default: ``[1]``.
            - ``joint_names`` (list[str]): Human-readable names aligned with ``servo_ids``.
            - ``operating_mode`` (str): ``"position"``, ``"velocity"``, or ``"torque"``.
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._port: Optional[str] = config.get("port", "/dev/ttyACM0")
        self._baud: int = int(config.get("baud", 1_000_000))
        self._servo_ids: List[int] = [int(i) for i in config.get("servo_ids", [1])]
        self._operating_mode: str = config.get("operating_mode", "position")
        self._mode = "mock"

        # Build joint name mapping: id → name
        joint_names: List[str] = config.get("joint_names", [])
        self._joint_names: Dict[int, str] = {
            sid: (joint_names[idx] if idx < len(joint_names) else f"joint_{sid}")
            for idx, sid in enumerate(self._servo_ids)
        }
        self._id_by_name: Dict[str, int] = {v: k for k, v in self._joint_names.items()}

        # Home offsets set by calibrate()
        self._home_offsets: Dict[int, int] = {sid: _POS_CENTER for sid in self._servo_ids}

        self._port_handler = None
        self._packet_handler = None

        # Auto-detect port
        if str(self._port or "").lower() == "auto":
            self._port = self._auto_detect_port()

        if not HAS_SCSERVO:
            logger.warning(
                "scservo_sdk not installed — FeetechDriver running in mock mode. "
                "Install with: pip install opencastor[lerobot]"
            )
            return

        self._init_hardware()

    # ------------------------------------------------------------------
    # Init helpers
    # ------------------------------------------------------------------

    def _auto_detect_port(self) -> Optional[str]:
        """Use hardware_detect to find a Feetech servo board."""
        try:
            from castor.hardware_detect import detect_feetech_usb

            ports = detect_feetech_usb()
            if ports:
                logger.info("FeetechDriver auto-detected port: %s", ports[0])
                return ports[0]
        except Exception as exc:
            logger.warning("FeetechDriver auto-detect failed: %s", exc)
        return None

    def _init_hardware(self) -> None:
        """Open serial port and ping all servo IDs."""
        if not self._port:
            logger.warning("FeetechDriver: no port available — mock mode")
            return
        try:
            self._port_handler = PortHandler(self._port)
            self._packet_handler = PacketHandler(_PROTOCOL_VERSION)
            if not self._port_handler.openPort():
                raise OSError(f"Cannot open port {self._port}")
            if not self._port_handler.setBaudRate(self._baud):
                raise OSError(f"Cannot set baud rate {self._baud}")
            self._mode = "hardware"
            logger.info(
                "FeetechDriver connected on %s @ %d baud — %d servos",
                self._port,
                self._baud,
                len(self._servo_ids),
            )
        except Exception as exc:
            logger.warning("FeetechDriver hardware init failed: %s — mock mode", exc)
            self._port_handler = None
            self._packet_handler = None

    # ------------------------------------------------------------------
    # DriverBase interface
    # ------------------------------------------------------------------

    def move(self, linear: float = 0.0, angular: float = 0.0) -> None:
        """Map linear → wrist position, angular → shoulder pan position.

        Args:
            linear: Wrist flex value in range ``[-1.0, 1.0]``.
            angular: Shoulder pan value in range ``[-1.0, 1.0]``.
        """
        if self._mode == "mock":
            logger.debug("MOCK Feetech move: linear=%.3f angular=%.3f", linear, angular)
            return

        positions: Dict[str, float] = {}
        if self._servo_ids:
            # shoulder_pan is the first servo (id 1 by convention)
            first_name = self._joint_names.get(self._servo_ids[0], "shoulder_pan")
            positions[first_name] = angular
        if len(self._servo_ids) >= 4:
            # wrist_flex is servo 4 by convention
            wrist_name = self._joint_names.get(self._servo_ids[3], "wrist_flex")
            positions[wrist_name] = linear

        if positions:
            self.set_joint_positions(positions)

    def stop(self) -> None:
        """Disable holding torque on all servos."""
        self.set_torque_enable(False)

    def close(self) -> None:
        """Release the serial port."""
        if self._port_handler is not None:
            try:
                self._port_handler.closePort()
            except Exception:
                pass
            self._port_handler = None
        self._mode = "mock"

    def health_check(self) -> dict:
        """Ping each servo and report firmware version.

        Returns:
            Dict with keys ``ok``, ``mode``, ``error``, ``servos``.
        """
        if self._mode == "mock":
            return {
                "ok": True,
                "mode": "mock",
                "error": None,
                "servos": {sid: "mock" for sid in self._servo_ids},
            }

        servo_status: Dict[int, str] = {}
        all_ok = True
        for sid in self._servo_ids:
            try:
                model_num, result, error = self._packet_handler.ping(self._port_handler, sid)
                if result == 0 and error == 0:
                    servo_status[sid] = f"ok (model={model_num})"
                else:
                    servo_status[sid] = f"error result={result} err={error}"
                    all_ok = False
            except Exception as exc:
                servo_status[sid] = f"exception: {exc}"
                all_ok = False

        return {
            "ok": all_ok,
            "mode": "hardware",
            "error": None if all_ok else "one or more servos not responding",
            "port": self._port,
            "baud": self._baud,
            "servos": servo_status,
        }

    # ------------------------------------------------------------------
    # Extended API
    # ------------------------------------------------------------------

    def set_joint_positions(self, positions: Dict[str, float]) -> None:
        """Write goal positions to servos by joint name.

        Args:
            positions: Mapping of joint name → normalised position in ``[-1.0, 1.0]``.
                       Internally converted to servo ticks (0–4095, centre=2048).
        """
        if self._mode == "mock":
            logger.debug("MOCK set_joint_positions: %s", positions)
            return

        for name, value in positions.items():
            sid = self._id_by_name.get(name)
            if sid is None:
                logger.warning("Unknown joint name: %s", name)
                continue
            ticks = int(_POS_CENTER + value * (_POS_MAX - _POS_CENTER))
            ticks = max(_POS_MIN, min(_POS_MAX, ticks))
            try:
                self._packet_handler.write2ByteTxRx(
                    self._port_handler, sid, _ADDR_GOAL_POSITION, ticks
                )
            except Exception as exc:
                logger.warning("set_joint_positions failed for %s (id=%d): %s", name, sid, exc)

    def get_joint_positions(self) -> Dict[str, float]:
        """Read present positions from all servos.

        Returns:
            Mapping of joint name → normalised position in ``[-1.0, 1.0]``.
        """
        if self._mode == "mock":
            return {name: 0.0 for name in self._joint_names.values()}

        result: Dict[str, float] = {}
        for sid in self._servo_ids:
            name = self._joint_names.get(sid, f"joint_{sid}")
            try:
                ticks, res, err = self._packet_handler.read2ByteTxRx(
                    self._port_handler, sid, _ADDR_PRESENT_POSITION
                )
                if res == 0 and err == 0:
                    norm = (ticks - _POS_CENTER) / (_POS_MAX - _POS_CENTER)
                    result[name] = max(-1.0, min(1.0, norm))
                else:
                    result[name] = 0.0
            except Exception as exc:
                logger.warning("get_joint_positions failed for %s: %s", name, exc)
                result[name] = 0.0
        return result

    def set_torque_enable(self, enabled: bool) -> None:
        """Enable or disable holding torque on all servos.

        Args:
            enabled: ``True`` to engage holding torque; ``False`` to release.
        """
        if self._mode == "mock":
            logger.debug("MOCK set_torque_enable: %s", enabled)
            return

        value = 1 if enabled else 0
        for sid in self._servo_ids:
            try:
                self._packet_handler.write1ByteTxRx(
                    self._port_handler, sid, _ADDR_TORQUE_ENABLE, value
                )
            except Exception as exc:
                logger.warning("set_torque_enable failed for servo %d: %s", sid, exc)

    def calibrate(self) -> FeetechCalibrationResult:
        """Read current servo positions and store them as home offsets.

        Returns:
            :class:`FeetechCalibrationResult` with captured home positions.
        """
        if self._mode == "mock":
            self._home_offsets = {sid: _POS_CENTER for sid in self._servo_ids}
            return FeetechCalibrationResult(
                success=True,
                home_positions=dict(self._home_offsets),
                joint_names=dict(self._joint_names),
            )

        home: Dict[int, int] = {}
        for sid in self._servo_ids:
            try:
                ticks, res, err = self._packet_handler.read2ByteTxRx(
                    self._port_handler, sid, _ADDR_PRESENT_POSITION
                )
                if res == 0 and err == 0:
                    home[sid] = ticks
                else:
                    home[sid] = _POS_CENTER
            except Exception as exc:
                logger.warning("calibrate read failed for servo %d: %s", sid, exc)
                home[sid] = _POS_CENTER

        self._home_offsets = home
        logger.info("FeetechDriver calibrated: %s", home)
        return FeetechCalibrationResult(
            success=True,
            home_positions=home,
            joint_names=dict(self._joint_names),
        )
