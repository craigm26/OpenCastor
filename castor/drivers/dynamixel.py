import logging
from typing import Dict, List

from .base import DriverBase

logger = logging.getLogger("OpenCastor.Dynamixel")

# Try to import the official SDK, but don't crash if it's missing (for simulation)
try:
    from dynamixel_sdk import (
        COMM_SUCCESS,
        PacketHandler,
        PortHandler,
    )

    HAS_DYNAMIXEL = True
except ImportError:
    HAS_DYNAMIXEL = False
    logger.warning("Dynamixel SDK not found. Running in mock mode.")


class DynamixelDriver(DriverBase):
    """
    Implementation for Robotis Dynamixel Servos (Protocol 2.0).
    Handles translation from RCAN 'degrees' to hardware 'ticks'.
    """

    # Dynamixel Control Table Addresses (Generic for X-series)
    ADDR_TORQUE_ENABLE = 64
    ADDR_GOAL_POSITION = 116
    ADDR_PRESENT_POSITION = 132

    PROTOCOL_VERSION = 2.0

    def __init__(self, config: Dict):
        self.port_name = config.get("port", "/dev/ttyUSB0")
        self.baud_rate = config.get("baud_rate", 57600)
        self.connected_motors: List[int] = []

        if not HAS_DYNAMIXEL:
            logger.warning("Dynamixel SDK unavailable, driver in mock mode")
            self.portHandler = None
            self.packetHandler = None
            return

        self.portHandler = PortHandler(self.port_name)
        self.packetHandler = PacketHandler(self.PROTOCOL_VERSION)

        if self._open_port():
            logger.info(f"Dynamixel Port Opened: {self.port_name}")
        else:
            logger.error(f"Failed to open Dynamixel Port: {self.port_name}")

    def _open_port(self) -> bool:
        if self.portHandler is None:
            return False
        if self.portHandler.openPort():
            if self.portHandler.setBaudRate(self.baud_rate):
                return True
        return False

    def engage(self, motor_ids: List[int]):
        """Turn on torque for specific motors."""
        if self.portHandler is None:
            return
        for mid in motor_ids:
            dxl_comm_result, dxl_error = self.packetHandler.write1ByteTxRx(
                self.portHandler, mid, self.ADDR_TORQUE_ENABLE, 1
            )
            if dxl_comm_result != COMM_SUCCESS:
                logger.error(
                    f"Failed to engage Motor {mid}: "
                    f"{self.packetHandler.getTxRxResult(dxl_comm_result)}"
                )
            else:
                self.connected_motors.append(mid)

    def disengage(self, motor_ids: List[int]):
        """Relax torque (Safe Mode)."""
        if self.portHandler is None:
            return
        for mid in motor_ids:
            self.packetHandler.write1ByteTxRx(
                self.portHandler, mid, self.ADDR_TORQUE_ENABLE, 0
            )

    def move(self, motor_id: int, angle_deg: float):
        """
        Moves a specific motor to an angle.

        RCAN Spec: 0 degrees is center.
        Dynamixel: 0-4095 ticks. Center is ~2048.
        """
        if self.portHandler is None:
            logger.info(f"[MOCK] Motor {motor_id} -> {angle_deg} deg")
            return

        # Convert Degrees to Ticks (0.088 deg/tick for MX/X series)
        ticks = int(2048 + (angle_deg / 0.088))

        # Safety Clamping
        ticks = max(0, min(4095, ticks))

        dxl_comm_result, dxl_error = self.packetHandler.write4ByteTxRx(
            self.portHandler, motor_id, self.ADDR_GOAL_POSITION, ticks
        )
        if dxl_comm_result != COMM_SUCCESS:
            logger.error(f"Communication Error on ID {motor_id}")

    def get_position(self, motor_id: int) -> float:
        """Reads current angle. Critical for closed-loop agent reasoning."""
        if self.portHandler is None:
            return 0.0

        ticks, dxl_comm_result, dxl_error = self.packetHandler.read4ByteTxRx(
            self.portHandler, motor_id, self.ADDR_PRESENT_POSITION
        )
        if dxl_comm_result != COMM_SUCCESS:
            return 0.0

        return (ticks - 2048) * 0.088

    def stop(self):
        self.disengage(self.connected_motors)

    def close(self):
        self.stop()
        if self.portHandler is not None:
            self.portHandler.closePort()
