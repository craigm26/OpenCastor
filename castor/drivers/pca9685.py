"""
PCA9685 PWM driver supporting two modes:

  1. **Differential drive** (pca9685_i2c) -- DC motors via Adafruit MotorKit.
     Original mode for Amazon-kit 4WD rovers.

  2. **RC car** (pca9685_rc) -- Steering servo + ESC via raw PWM pulse widths.
     For hobby-grade RC cars wired through PCA9685.

The mode is selected by the ``protocol`` field in your RCAN driver config:
  - ``pca9685_i2c``  -> PCA9685Driver  (differential drive)
  - ``pca9685_rc``   -> PCA9685RCDriver (servo + ESC)
"""

import logging
import time
from typing import Dict

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.PCA9685")

# ---------------------------------------------------------------------------
# Hardware imports (graceful degradation)
# ---------------------------------------------------------------------------
try:
    import busio
    from adafruit_pca9685 import PCA9685
    from board import SCL, SDA

    HAS_PCA9685 = True
except ImportError:
    HAS_PCA9685 = False
    logger.warning("Adafruit PCA9685 libraries not found. Running in mock mode.")

try:
    from adafruit_motor import motor

    HAS_MOTOR = True
except ImportError:
    HAS_MOTOR = False


# ---------------------------------------------------------------------------
# Pulse-width safety bounds (microseconds)
# ---------------------------------------------------------------------------
PULSE_MIN_US = 500   # absolute minimum -- below this risks damaging servos/ESCs
PULSE_MAX_US = 2500  # absolute maximum -- above this risks damaging servos/ESCs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _us_to_duty(pca_freq: int, pulse_us: float) -> int:
    """Convert a pulse width in microseconds to a 16-bit duty-cycle value."""
    period_us = 1_000_000 / pca_freq
    return int(pulse_us / period_us * 0xFFFF)


# ===========================================================================
# RC Car Driver (steering servo + ESC)
# ===========================================================================
class PCA9685RCDriver(DriverBase):
    """
    Controls an RC car through a PCA9685 breakout board.

    Wiring (typical):
      - PCA9685 channel 0 -> Steering servo signal wire
      - PCA9685 channel 1 -> ESC signal wire
      - PCA9685 powered by 5 V regulator or BEC
      - I2C SDA/SCL -> Raspberry Pi GPIO 2/3

    Config keys (from RCAN ``drivers[0]``):
      - steering_channel   (default 0)
      - steering_center_us (default 1500)
      - steering_range_us  (default 500)  -- +/- from center
      - steering_invert    (default false)
      - throttle_channel   (default 1)
      - throttle_neutral_us(default 1500)
      - throttle_max_us    (default 2000)
      - throttle_min_us    (default 1000)
      - throttle_deadzone  (default 0.05)
    """

    def __init__(self, config: Dict):
        self.config = config

        # Read channel mappings from RCAN config
        self.steer_ch = config.get("steering_channel", 0)
        self.steer_center = config.get("steering_center_us", 1500)
        self.steer_range = config.get("steering_range_us", 500)
        self.steer_invert = config.get("steering_invert", False)

        self.thr_ch = config.get("throttle_channel", 1)
        self.thr_neutral = config.get("throttle_neutral_us", 1500)
        self.thr_max = config.get("throttle_max_us", 2000)
        self.thr_min = config.get("throttle_min_us", 1000)
        self.thr_deadzone = config.get("throttle_deadzone", 0.05)

        self.freq = config.get("frequency", 50)

        # Validate configured pulse widths against safety bounds
        for label, val in [
            ("throttle_neutral_us", self.thr_neutral),
            ("throttle_max_us", self.thr_max),
            ("throttle_min_us", self.thr_min),
            ("steering_center_us", self.steer_center),
        ]:
            if not isinstance(val, (int, float)):
                logger.warning(
                    f"{label}={val!r} is not numeric; "
                    f"skipping range validation (will be coerced at runtime)"
                )
                continue
            if val < PULSE_MIN_US or val > PULSE_MAX_US:
                logger.warning(
                    f"{label}={val} is outside safe range "
                    f"[{PULSE_MIN_US}, {PULSE_MAX_US}]; "
                    f"it will be clamped at runtime"
                )

        if not HAS_PCA9685:
            logger.warning("PCA9685 unavailable -- RC driver in mock mode")
            self.pca = None
            return

        try:
            i2c = busio.I2C(SCL, SDA)
            addr = config.get("address", 0x40)
            if isinstance(addr, str):
                addr = int(addr, 16)
            self.pca = PCA9685(i2c, address=addr)
            self.pca.frequency = self.freq
            logger.info(f"PCA9685 RC driver online at {hex(addr)}, {self.freq} Hz")
        except (ValueError, OSError) as exc:
            logger.error(f"PCA9685 init failed: {exc}. Falling back to mock mode.")
            self.pca = None
            return

        # Arm the ESC: send neutral throttle so the ESC recognises the signal.
        # Pulses are clamped inside _set_pulse() so out-of-range config values
        # cannot reach the hardware.
        self._set_pulse(self.thr_ch, self.thr_neutral)
        self._set_pulse(self.steer_ch, self.steer_center)
        time.sleep(0.5)  # give the ESC time to recognise the neutral signal
        logger.info("ESC armed (neutral throttle sent)")

    def move(self, linear_x: float = 0.0, angular_z: float = 0.0):
        """
        Drive the RC car.

        Args:
            linear_x:  Throttle, -1.0 (full reverse) to 1.0 (full forward).
            angular_z: Steering, -1.0 (full left) to 1.0 (full right).
        """
        linear_x = max(-1.0, min(1.0, linear_x))
        angular_z = max(-1.0, min(1.0, angular_z))

        # --- Throttle ---
        if abs(linear_x) < self.thr_deadzone:
            thr_us = self.thr_neutral
        elif linear_x > 0:
            thr_us = self.thr_neutral + linear_x * (self.thr_max - self.thr_neutral)
        else:
            thr_us = self.thr_neutral + linear_x * (self.thr_neutral - self.thr_min)

        # --- Steering ---
        direction = -1.0 if self.steer_invert else 1.0
        steer_us = self.steer_center + angular_z * self.steer_range * direction

        if self.pca is None:
            logger.info(f"[MOCK RC] throttle={thr_us:.0f}us  steer={steer_us:.0f}us")
            return

        self._set_pulse(self.thr_ch, thr_us)
        self._set_pulse(self.steer_ch, steer_us)

    def stop(self):
        """Neutral throttle + center steering."""
        if self.pca is None:
            logger.info("[MOCK RC] stop")
            return
        self._set_pulse(self.thr_ch, self.thr_neutral)
        self._set_pulse(self.steer_ch, self.steer_center)

    def close(self):
        self.stop()
        if self.pca is not None:
            self.pca.deinit()

    def _set_pulse(self, channel: int, pulse_us: float):
        """Write a pulse width (microseconds) to a PCA9685 channel.

        The value is clamped to [PULSE_MIN_US, PULSE_MAX_US] so that no
        caller -- arming, move(), stop(), or future code -- can send an
        out-of-range signal to the hardware.
        """
        pulse_us = max(PULSE_MIN_US, min(PULSE_MAX_US, pulse_us))
        duty = _us_to_duty(self.freq, pulse_us)
        self.pca.channels[channel].duty_cycle = duty


# ===========================================================================
# Differential Drive Driver (original 4WD rovers)
# ===========================================================================
class PCA9685Driver(DriverBase):
    """
    Driver for the generic 'Motor HAT' found in Amazon robot kits.
    Handles I2C communication to spin DC motors via PCA9685 PWM controller.
    """

    def __init__(self, config: Dict):
        self.config = config

        if not HAS_PCA9685 or not HAS_MOTOR:
            logger.warning("PCA9685/motor libs unavailable, driver in mock mode")
            self.pca = None
            self.motor_left = None
            self.motor_right = None
            return

        try:
            i2c = busio.I2C(SCL, SDA)
            addr = config.get("address", 0x40)
            if isinstance(addr, str):
                addr = int(addr, 16)
            self.pca = PCA9685(i2c, address=addr)
            self.pca.frequency = config.get("frequency", 50)
            logger.info(f"PCA9685 Connected at {hex(addr)}")
        except (ValueError, OSError):
            logger.error("PCA9685 Not Found. Check wiring or I2C toggle in raspi-config.")
            self.pca = None
            self.motor_left = None
            self.motor_right = None
            return

        self.motor_left = motor.DCMotor(self.pca.channels[0], self.pca.channels[1])
        self.motor_right = motor.DCMotor(self.pca.channels[2], self.pca.channels[3])
        self.motor_left.decay_mode = motor.SLOW_DECAY
        self.motor_right.decay_mode = motor.SLOW_DECAY

    def move(self, linear_x: float = 0.0, angular_z: float = 0.0):
        """Arcade-drive mixing."""
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
