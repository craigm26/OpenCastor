"""
Pollen Robotics Reachy 2 / Reachy Mini driver for OpenCastor.

Connects via Ethernet using reachy2-sdk (gRPC).

RCAN config::

    drivers:
      - id: reachy
        protocol: reachy
        host: auto   # or 192.168.x.x / reachy.local
        arms: [left, right]
        head: true

Install: pip install opencastor[reachy]
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.ReachyDriver")

try:
    from reachy2_sdk import ReachySDK  # type: ignore[import]

    HAS_REACHY2_SDK = True
except ImportError:
    HAS_REACHY2_SDK = False
    ReachySDK = None  # type: ignore[assignment]


class ReachyDriver(DriverBase):
    """Driver for Pollen Robotics Reachy 2 and Reachy Mini humanoid robots.

    Connects over Ethernet via the ``reachy2-sdk`` gRPC client.  Falls back to
    mock mode when the SDK is not installed or the robot is unreachable.

    Args:
        config: RCAN driver config dict. Relevant keys:

            - ``host`` (str): Hostname/IP, or ``"auto"`` to auto-discover via mDNS.
            - ``arms`` (list[str]): Which arms to enable — ``"left"``, ``"right"``,
              or both. Default: ``["right"]``.
            - ``head`` (bool): Whether to enable head control. Default: ``True``.
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._host: Optional[str] = config.get("host", "reachy.local")
        self._arms: List[str] = config.get("arms", ["right"])
        self._head_enabled: bool = bool(config.get("head", True))
        self._mode = "mock"
        self._reachy = None

        if str(self._host or "").lower() == "auto":
            self._host = self._auto_detect_host()

        if not HAS_REACHY2_SDK:
            logger.warning(
                "reachy2-sdk not installed — ReachyDriver running in mock mode. "
                "Install with: pip install opencastor[reachy]"
            )
            return

        self._init_hardware()

    # ------------------------------------------------------------------
    # Init helpers
    # ------------------------------------------------------------------

    def _auto_detect_host(self) -> Optional[str]:
        """Use hardware_detect to find a Reachy robot on the network."""
        try:
            from castor.hardware_detect import detect_reachy_network

            hosts = detect_reachy_network()
            if hosts:
                logger.info("ReachyDriver auto-detected host: %s", hosts[0])
                return hosts[0]
        except Exception as exc:
            logger.warning("ReachyDriver auto-detect failed: %s", exc)
        return None

    def _init_hardware(self) -> None:
        """Connect to Reachy via gRPC."""
        if not self._host:
            logger.warning("ReachyDriver: no host available — mock mode")
            return
        try:
            self._reachy = ReachySDK(host=self._host)
            self._reachy.connect()
            self._mode = "hardware"
            logger.info("ReachyDriver connected to %s", self._host)
        except Exception as exc:
            logger.warning(
                "ReachyDriver hardware init failed (%s): %s — mock mode", self._host, exc
            )
            self._reachy = None

    # ------------------------------------------------------------------
    # DriverBase interface
    # ------------------------------------------------------------------

    def move(self, linear: float = 0.0, angular: float = 0.0) -> None:
        """Send a velocity command.

        For Reachy 2 with a mobile base, maps *linear* and *angular* to base
        velocity.  For Reachy Mini (no base), maps *angular* to head pan.

        Args:
            linear: Forward speed in ``[-1.0, 1.0]``.
            angular: Turn rate / head pan in ``[-1.0, 1.0]``.
        """
        if self._mode == "mock":
            logger.debug("MOCK Reachy move: linear=%.3f angular=%.3f", linear, angular)
            return

        try:
            mobile_base = getattr(self._reachy, "mobile_base", None)
            if mobile_base is not None:
                # Reachy 2 with mobile base
                mobile_base.set_speed(linear, 0.0, angular)
            elif self._head_enabled:
                # Reachy Mini — pan head instead
                head = getattr(self._reachy, "head", None)
                if head is not None:
                    head.look_at(1.0, -angular * 0.5, 0.0)
        except Exception as exc:
            logger.warning("ReachyDriver.move failed: %s", exc)

    def stop(self) -> None:
        """Halt all motion and cancel active trajectories."""
        if self._mode == "mock":
            logger.debug("MOCK Reachy stop")
            return
        try:
            mobile_base = getattr(self._reachy, "mobile_base", None)
            if mobile_base is not None:
                mobile_base.set_speed(0.0, 0.0, 0.0)
        except Exception as exc:
            logger.warning("ReachyDriver.stop failed: %s", exc)

    def close(self) -> None:
        """Disconnect from the robot."""
        if self._reachy is not None:
            try:
                self._reachy.disconnect()
            except Exception:
                pass
            self._reachy = None
        self._mode = "mock"

    def health_check(self) -> dict:
        """Check connectivity and joint state.

        Returns:
            Dict with keys ``ok``, ``mode``, ``error``, ``host``.
        """
        if self._mode == "mock":
            return {"ok": True, "mode": "mock", "error": None, "host": self._host}

        try:
            is_connected = getattr(self._reachy, "is_connected", lambda: True)()
            return {
                "ok": bool(is_connected),
                "mode": "hardware",
                "error": None if is_connected else "robot not responding",
                "host": self._host,
            }
        except Exception as exc:
            return {"ok": False, "mode": "hardware", "error": str(exc), "host": self._host}

    # ------------------------------------------------------------------
    # Extended API
    # ------------------------------------------------------------------

    def move_arm(self, side: str, joint_positions: Dict[str, float]) -> None:
        """Move an arm to the specified joint positions.

        Args:
            side: ``"left"`` or ``"right"``.
            joint_positions: Mapping of joint name → angle in degrees.
        """
        if self._mode == "mock":
            logger.debug("MOCK Reachy move_arm %s: %s", side, joint_positions)
            return

        try:
            arm = getattr(self._reachy, f"{side}_arm", None)
            if arm is None:
                logger.warning("ReachyDriver: arm '%s' not available", side)
                return
            arm.goto(joint_positions, duration=1.0)
        except Exception as exc:
            logger.warning("ReachyDriver.move_arm(%s) failed: %s", side, exc)

    def get_joint_positions(self) -> Dict[str, float]:
        """Read present joint positions for all enabled arms.

        Returns:
            Mapping of ``"left.<joint>"`` / ``"right.<joint>"`` → degrees.
        """
        if self._mode == "mock":
            return {}

        positions: Dict[str, float] = {}
        for side in self._arms:
            try:
                arm = getattr(self._reachy, f"{side}_arm", None)
                if arm is None:
                    continue
                for jname, jobj in arm.joints.items():
                    positions[f"{side}.{jname}"] = float(jobj.present_position)
            except Exception as exc:
                logger.warning("ReachyDriver.get_joint_positions(%s) failed: %s", side, exc)

        if self._head_enabled:
            try:
                head = getattr(self._reachy, "head", None)
                if head is not None:
                    for jname, jobj in head.joints.items():
                        positions[f"head.{jname}"] = float(jobj.present_position)
            except Exception as exc:
                logger.warning("ReachyDriver.get_joint_positions(head) failed: %s", exc)

        return positions

    def look_at(self, x: float, y: float, z: float) -> None:
        """Point the robot's head toward a Cartesian target point.

        Args:
            x: Forward distance in metres.
            y: Lateral offset in metres (positive = left).
            z: Vertical offset in metres.
        """
        if self._mode == "mock":
            logger.debug("MOCK Reachy look_at: x=%.2f y=%.2f z=%.2f", x, y, z)
            return

        try:
            head = getattr(self._reachy, "head", None)
            if head is not None:
                head.look_at(x, y, z)
        except Exception as exc:
            logger.warning("ReachyDriver.look_at failed: %s", exc)
