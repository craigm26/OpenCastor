import logging
from typing import Dict

from .base import DriverBase

logger = logging.getLogger("OpenCastor.PCA9685")

# Try to import Adafruit libraries, but don't crash if missing
try:
    import busio
    from board import SCL, SDA
    from adafruit_pca9685 import PCA9685
    from adafruit_motor import motor

    HAS_PCA9685 = True
except ImportError:
    HAS_PCA9685 = False
    logger.warning("Adafruit PCA9685 libraries not found. Running in mock mode.")


class PCA9685Driver(DriverBase):
    """
    Driver for the generic 'Motor HAT' found in Amazon robot kits.
    Handles I2C communication to spin DC motors via PCA9685 PWM controller.
    """

    def __init__(self, config: Dict):
        self.config = config

        if not HAS_PCA9685:
            logger.warning("PCA9685 unavailable, driver in mock mode")
            self.pca = None
            self.motor_left = None
            self.motor_right = None
            return

        self.i2c = busio.I2C(SCL, SDA)

        try:
            self.pca = PCA9685(self.i2c, address=config.get("address", 0x40))
            self.pca.frequency = config.get("frequency", 50)
            logger.info(f"PCA9685 Connected at {hex(config.get('address', 0x40))}")
        except ValueError:
            logger.error("PCA9685 Not Found. Check wiring or I2C toggle in raspi-config.")
            self.pca = None
            return

        # Setup motors (standard Waveshare/Adafruit Motor HAT mapping)
        self.motor_left = motor.DCMotor(self.pca.channels[0], self.pca.channels[1])
        self.motor_right = motor.DCMotor(self.pca.channels[2], self.pca.channels[3])

        self.motor_left.decay_mode = motor.SLOW_DECAY
        self.motor_right.decay_mode = motor.SLOW_DECAY

    def move(self, linear_x: float = 0.0, angular_z: float = 0.0):
        """
        Differential Drive Mixing (Arcade Drive).

        Args:
            linear_x:  Forward/Back speed (-1.0 to 1.0)
            angular_z: Turn Left/Right speed (-1.0 to 1.0)
        """
        left_speed = max(-1.0, min(1.0, linear_x - angular_z))
        right_speed = max(-1.0, min(1.0, linear_x + angular_z))

        if self.motor_left is None:
            logger.info(f"[MOCK] L={left_speed:.2f} R={right_speed:.2f}")
            return

        self.motor_left.throttle = left_speed
        self.motor_right.throttle = right_speed

    def stop(self):
        if self.motor_left is not None:
            self.motor_left.throttle = 0
        if self.motor_right is not None:
            self.motor_right.throttle = 0

    def close(self):
        self.stop()
        if self.pca is not None:
            self.pca.deinit()
