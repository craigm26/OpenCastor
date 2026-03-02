"""
IMU driver for OpenCastor.

Supports MPU6050, BNO055, ICM-42688-P via I2C (smbus2).

Env:
  IMU_I2C_BUS      — I2C bus number (default 1)
  IMU_I2C_ADDRESS  — hex address (default 0x68 for MPU6050, 0x28 for BNO055)
  IMU_MODEL        — "mpu6050" | "bno055" | "icm42688" | "auto" (default auto)
  IMU_VIBRATION_THRESHOLD_G — RMS acceleration threshold for vibration alert (default 0.5)

REST API:
  GET /api/imu/latest      — {accel_g, gyro_dps, mag_uT, temp_c, mode}
  GET /api/imu/calibrate   — trigger BNO055 calibration (others: no-op)

Install: pip install smbus2
"""

import logging
import math
import os
import threading
import time
from typing import Optional

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

logger = logging.getLogger("OpenCastor.IMU")


# ── Madgwick complementary filter (Issue #343) ────────────────────────────────


class MadgwickFilter:
    """Madgwick AHRS complementary filter that fuses accelerometer + gyroscope.

    Reduces heading drift compared to pure gyroscope integration by blending
    in a gravity-based error correction term at each step.

    Reference: Sebastian O.H. Madgwick, "An efficient orientation filter for
    inertial and inertial/magnetic sensor arrays", 2010.

    Only the IMU (no magnetometer) variant is implemented here.  The
    quaternion is stored internally and exposed as Euler angles (yaw/pitch/roll)
    via :meth:`get_euler`.

    Args:
        beta:       Filter gain (0.0–1.0).  Higher values trust the
                    accelerometer more; lower values follow the gyroscope.
                    Typical value: 0.1.
        sample_period_s: Expected time between :meth:`update` calls in seconds.
                    Used when ``dt`` is not supplied to ``update()``.
    """

    def __init__(self, beta: float = 0.1, sample_period_s: float = 0.01) -> None:
        self.beta = float(beta)
        self.sample_period_s = float(sample_period_s)
        # Quaternion [w, x, y, z] — identity orientation
        self.q = [1.0, 0.0, 0.0, 0.0]

    def update(
        self,
        gx: float,
        gy: float,
        gz: float,
        ax: float,
        ay: float,
        az: float,
        dt: Optional[float] = None,
    ) -> None:
        """Update the orientation estimate with a new IMU sample.

        Args:
            gx, gy, gz: Gyroscope readings in **radians per second**.
            ax, ay, az: Accelerometer readings in any consistent unit (will
                        be normalised internally).
            dt:         Time step in seconds.  Falls back to
                        :attr:`sample_period_s` when ``None``.
        """
        dt = dt if dt is not None else self.sample_period_s
        q0, q1, q2, q3 = self.q

        # Normalise accelerometer vector; skip gradient step if magnitude is zero
        norm_a = math.sqrt(ax * ax + ay * ay + az * az)
        if norm_a < 1e-10:
            # Only gyro integration
            self._integrate_gyro(gx, gy, gz, dt)
            return

        ax /= norm_a
        ay /= norm_a
        az /= norm_a

        # Gradient descent error function for gravity (no magnetometer)
        f1 = 2.0 * (q1 * q3 - q0 * q2) - ax
        f2 = 2.0 * (q0 * q1 + q2 * q3) - ay
        f3 = 1.0 - 2.0 * (q1 * q1 + q2 * q2) - az

        # Jacobian transpose * f
        j11 = 2.0 * q2
        j12 = 2.0 * q3
        j13 = 2.0 * q0
        j14 = 2.0 * q1
        j32 = 4.0 * q2
        j33 = 4.0 * q3

        step0 = -j11 * f1 + j13 * f2
        step1 = j12 * f1 + j14 * f2 - j32 * f3
        step2 = -j11 * f1 + j14 * f2 - j33 * f3  # sign correction on j11 per paper
        step3 = j12 * f1 + j13 * f2

        # Normalise step
        norm_s = math.sqrt(step0 * step0 + step1 * step1 + step2 * step2 + step3 * step3)
        if norm_s > 1e-10:
            step0 /= norm_s
            step1 /= norm_s
            step2 /= norm_s
            step3 /= norm_s

        # Rate of change of quaternion — gyro integration + gradient correction
        qdot0 = 0.5 * (-q1 * gx - q2 * gy - q3 * gz) - self.beta * step0
        qdot1 = 0.5 * (q0 * gx + q2 * gz - q3 * gy) - self.beta * step1
        qdot2 = 0.5 * (q0 * gy - q1 * gz + q3 * gx) - self.beta * step2
        qdot3 = 0.5 * (q0 * gz + q1 * gy - q2 * gx) - self.beta * step3

        q0 += qdot0 * dt
        q1 += qdot1 * dt
        q2 += qdot2 * dt
        q3 += qdot3 * dt

        # Normalise quaternion
        norm_q = math.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3)
        if norm_q > 1e-10:
            q0 /= norm_q
            q1 /= norm_q
            q2 /= norm_q
            q3 /= norm_q

        self.q = [q0, q1, q2, q3]

    def _integrate_gyro(self, gx: float, gy: float, gz: float, dt: float) -> None:
        """Integrate gyro only (no accelerometer correction)."""
        q0, q1, q2, q3 = self.q
        qdot0 = 0.5 * (-q1 * gx - q2 * gy - q3 * gz)
        qdot1 = 0.5 * (q0 * gx + q2 * gz - q3 * gy)
        qdot2 = 0.5 * (q0 * gy - q1 * gz + q3 * gx)
        qdot3 = 0.5 * (q0 * gz + q1 * gy - q2 * gx)
        q0 += qdot0 * dt
        q1 += qdot1 * dt
        q2 += qdot2 * dt
        q3 += qdot3 * dt
        norm_q = math.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3)
        if norm_q > 1e-10:
            self.q = [q0 / norm_q, q1 / norm_q, q2 / norm_q, q3 / norm_q]

    def get_euler(self) -> dict:
        """Convert current quaternion to Euler angles (degrees).

        Returns:
            Dict with keys ``"yaw_deg"``, ``"pitch_deg"``, ``"roll_deg"``.
        """
        q0, q1, q2, q3 = self.q
        roll = math.atan2(2.0 * (q0 * q1 + q2 * q3), 1.0 - 2.0 * (q1 * q1 + q2 * q2))
        pitch_sin = 2.0 * (q0 * q2 - q3 * q1)
        pitch_sin = max(-1.0, min(1.0, pitch_sin))
        pitch = math.asin(pitch_sin)
        yaw = math.atan2(2.0 * (q0 * q3 + q1 * q2), 1.0 - 2.0 * (q2 * q2 + q3 * q3))
        return {
            "yaw_deg": round(math.degrees(yaw), 4),
            "pitch_deg": round(math.degrees(pitch), 4),
            "roll_deg": round(math.degrees(roll), 4),
        }

    def reset(self) -> None:
        """Reset the filter to identity orientation."""
        self.q = [1.0, 0.0, 0.0, 0.0]


try:
    import smbus2

    HAS_SMBUS2 = True
except ImportError:
    HAS_SMBUS2 = False

# ── Register maps ─────────────────────────────────────────────────────────────

# MPU6050 / ICM-42688-P shared registers
_MPU_ADDR_PWR_MGMT_1 = 0x6B
_MPU_ADDR_ACCEL_XOUT_H = 0x3B
_MPU_ADDR_TEMP_OUT_H = 0x41
_MPU_ADDR_GYRO_XOUT_H = 0x43
_MPU_ADDR_WHO_AM_I = 0x75

# MPU6050 WHO_AM_I value
_MPU6050_WHO_AM_I = 0x68

# ICM-42688-P WHO_AM_I value
_ICM42688_WHO_AM_I = 0x47

# ICM-42688-P specific registers
_ICM_ADDR_PWR_MGMT0 = 0x4E
_ICM_ADDR_ACCEL_DATA_X1 = 0x1F
_ICM_ADDR_GYRO_DATA_X1 = 0x25
_ICM_ADDR_TEMP_DATA1 = 0x1D

# BNO055 registers
_BNO_ADDR_CHIP_ID = 0x00
_BNO_CHIP_ID = 0xA0
_BNO_ADDR_OPR_MODE = 0x3D
_BNO_ADDR_CALIB_STAT = 0x35
_BNO_ADDR_ACC_DATA_X_LSB = 0x28
_BNO_ADDR_GYR_DATA_X_LSB = 0x14
_BNO_ADDR_MAG_DATA_X_LSB = 0x0E
_BNO_ADDR_TEMP = 0x34
_BNO_OPR_MODE_NDOF = 0x0C  # 9-DOF fusion mode

# Scale factors
_MPU6050_ACCEL_SCALE = 16384.0  # LSB/g  (±2g default)
_MPU6050_GYRO_SCALE = 131.0  # LSB/dps (±250dps default)
_ICM42688_ACCEL_SCALE = 2048.0  # LSB/g  (±16g default FS_SEL=0 → use 16g range)
_ICM42688_GYRO_SCALE = 16.4  # LSB/dps (±2000dps default)
_BNO055_ACCEL_SCALE = 100.0  # LSB/m·s⁻² → convert to g by dividing by 9.80665*100
_BNO055_GYRO_SCALE = 16.0  # LSB/dps

# Singleton
_singleton: Optional["IMUDriver"] = None
_singleton_lock = threading.Lock()


def _s16(high: int, low: int) -> int:
    """Combine two bytes into a signed 16-bit integer."""
    val = (high << 8) | low
    return val if val < 32768 else val - 65536


class IMUDriver:
    """IMU driver supporting MPU6050, BNO055, and ICM-42688-P sensors.

    Auto-detects the connected sensor by probing known I2C addresses and
    reading WHO_AM_I / CHIP_ID registers. Falls back to a mock mode when
    smbus2 is not available or no sensor is found.
    """

    # Known probe targets: (address, model_hint)
    _PROBE_TARGETS = [
        (0x68, "mpu_or_icm"),  # MPU6050 or ICM-42688-P (AD0 low)
        (0x69, "mpu_or_icm"),  # MPU6050 or ICM-42688-P (AD0 high)
        (0x28, "bno055"),  # BNO055 (COM3 low)
        (0x29, "bno055"),  # BNO055 (COM3 high)
    ]

    def __init__(
        self,
        bus: int = 1,
        address: Optional[int] = None,
        model: str = "auto",
        imu_filter: str = "complementary",
        imu_beta: float = 0.1,
    ):
        self._bus_num = int(os.getenv("IMU_I2C_BUS", str(bus)))
        self._model = os.getenv("IMU_MODEL", model).lower()
        self._mode = "mock"
        self._bus: Optional[smbus2.SMBus] = None  # type: ignore[name-defined]
        self._address: int = 0x68
        self._detected_model: str = "none"
        self._lock = threading.Lock()
        self._orientation: dict = {"yaw_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0}
        self._last_orient_ts: float = 0.0

        # Issue #343 — Madgwick filter support
        self._imu_filter: str = os.getenv("IMU_FILTER", imu_filter).lower()
        self._imu_beta: float = float(os.getenv("IMU_BETA", str(imu_beta)))
        self._madgwick: Optional[MadgwickFilter] = None
        if self._imu_filter == "madgwick":
            self._madgwick = MadgwickFilter(beta=self._imu_beta)
            logger.info("IMU: Madgwick filter enabled (beta=%.3f)", self._imu_beta)

        # ── Pose state ────────────────────────────────────────────────────────
        self._pose_x_m: float = 0.0
        self._pose_y_m: float = 0.0
        self._pose_heading_deg: float = 0.0
        self._pose_last_ts: Optional[float] = None

        # ── Step counter ──────────────────────────────────────────────────────
        self._step_count: int = 0
        self._step_last_mag: float = 0.0
        self._step_threshold: float = float(os.getenv("IMU_STEP_THRESHOLD", "1.2"))
        self._step_in_peak: bool = False

        # ── Tap detection (#357) ──────────────────────────────────────────────
        self._last_tap_time: Optional[float] = None
        self._tap_count: int = 0
        self._tap_accel_threshold_g: float = float(os.getenv("IMU_TAP_THRESHOLD_G", "2.0"))
        self._double_tap_window_s: float = float(os.getenv("IMU_DOUBLE_TAP_WINDOW_S", "0.5"))

        # Issue #369 — shake detection state
        self._shake_history: list = []  # list of (timestamp, axis, sign) tuples
        self._shake_window_s: float = float(os.getenv("IMU_SHAKE_WINDOW_S", "0.5"))
        self._shake_threshold_g: float = float(os.getenv("IMU_SHAKE_THRESHOLD_G", "1.5"))
        self._shake_min_reversals: int = int(os.getenv("IMU_SHAKE_MIN_REVERSALS", "3"))

        # Resolve explicit address from env or constructor argument
        env_addr = os.getenv("IMU_I2C_ADDRESS", "")
        if env_addr:
            address = int(env_addr, 16)

        if not HAS_SMBUS2:
            logger.info(
                "IMU driver: smbus2 not installed — mock mode (install: pip install smbus2)"
            )
            self._detected_model = "mock"
            return

        try:
            self._bus = smbus2.SMBus(self._bus_num)
            if address is not None and self._model != "auto":
                # Explicit address + model: trust the caller
                self._address = address
                self._detected_model = self._model
                self._init_sensor()
            else:
                self._autodetect(address)
        except Exception as exc:
            logger.warning("IMU init failed: %s — mock mode", exc)
            self._detected_model = "mock"

    # ── Auto-detect ───────────────────────────────────────────────────────────

    def _autodetect(self, hint_address: Optional[int]):
        """Probe I2C bus to detect sensor model and address."""
        targets = [(hint_address, "auto")] if hint_address is not None else self._PROBE_TARGETS
        for addr, hint in targets:
            try:
                if hint in ("auto", "mpu_or_icm"):
                    who = self._bus.read_byte_data(addr, _MPU_ADDR_WHO_AM_I)
                    if who == _MPU6050_WHO_AM_I or who == addr:
                        self._address = addr
                        self._detected_model = "mpu6050"
                        self._init_sensor()
                        logger.info("IMU autodetected MPU6050 at 0x%02x", addr)
                        return
                    if who == _ICM42688_WHO_AM_I:
                        self._address = addr
                        self._detected_model = "icm42688"
                        self._init_sensor()
                        logger.info("IMU autodetected ICM-42688-P at 0x%02x", addr)
                        return
                if hint in ("auto", "bno055"):
                    chip = self._bus.read_byte_data(addr, _BNO_ADDR_CHIP_ID)
                    if chip == _BNO_CHIP_ID:
                        self._address = addr
                        self._detected_model = "bno055"
                        self._init_sensor()
                        logger.info("IMU autodetected BNO055 at 0x%02x", addr)
                        return
            except Exception:
                continue  # Address not responding — try next

        logger.warning("IMU: no sensor found on bus %d — mock mode", self._bus_num)
        self._detected_model = "mock"

    def _init_sensor(self):
        """Wake up / configure the detected sensor."""
        try:
            if self._detected_model == "mpu6050":
                # Clear sleep bit (bit 6 of PWR_MGMT_1)
                self._bus.write_byte_data(self._address, _MPU_ADDR_PWR_MGMT_1, 0x00)
                time.sleep(0.1)
                self._mode = "hardware"
                logger.info("MPU6050 initialized at 0x%02x on bus %d", self._address, self._bus_num)

            elif self._detected_model == "icm42688":
                # PWR_MGMT0: accel+gyro in low-noise mode (0x0F)
                self._bus.write_byte_data(self._address, _ICM_ADDR_PWR_MGMT0, 0x0F)
                time.sleep(0.05)
                self._mode = "hardware"
                logger.info(
                    "ICM-42688-P initialized at 0x%02x on bus %d", self._address, self._bus_num
                )

            elif self._detected_model == "bno055":
                time.sleep(0.65)  # BNO055 boot time
                # Set NDOF fusion mode
                self._bus.write_byte_data(self._address, _BNO_ADDR_OPR_MODE, _BNO_OPR_MODE_NDOF)
                time.sleep(0.02)
                self._mode = "hardware"
                logger.info("BNO055 initialized at 0x%02x on bus %d", self._address, self._bus_num)

        except Exception as exc:
            logger.warning("IMU sensor init failed: %s — mock mode", exc)
            self._detected_model = "mock"

    # ── Low-level reads ───────────────────────────────────────────────────────

    def _read_mpu6050(self) -> dict:
        """Read raw data from MPU6050."""
        raw = self._bus.read_i2c_block_data(self._address, _MPU_ADDR_ACCEL_XOUT_H, 14)
        ax = _s16(raw[0], raw[1]) / _MPU6050_ACCEL_SCALE
        ay = _s16(raw[2], raw[3]) / _MPU6050_ACCEL_SCALE
        az = _s16(raw[4], raw[5]) / _MPU6050_ACCEL_SCALE
        temp_c = _s16(raw[6], raw[7]) / 340.0 + 36.53
        gx = _s16(raw[8], raw[9]) / _MPU6050_GYRO_SCALE
        gy = _s16(raw[10], raw[11]) / _MPU6050_GYRO_SCALE
        gz = _s16(raw[12], raw[13]) / _MPU6050_GYRO_SCALE
        return {
            "accel_g": {"x": round(ax, 4), "y": round(ay, 4), "z": round(az, 4)},
            "gyro_dps": {"x": round(gx, 4), "y": round(gy, 4), "z": round(gz, 4)},
            "mag_uT": None,
            "temp_c": round(temp_c, 2),
            "mode": "hardware",
            "model": "mpu6050",
        }

    def _read_icm42688(self) -> dict:
        """Read raw data from ICM-42688-P.

        Register layout is similar to MPU6050 but starting at different base
        addresses and using different scale factors.
        """
        # Temperature (2 bytes)
        temp_raw = self._bus.read_i2c_block_data(self._address, _ICM_ADDR_TEMP_DATA1, 2)
        temp_c = _s16(temp_raw[0], temp_raw[1]) / 132.48 + 25.0

        # Accel (6 bytes: X_H, X_L, Y_H, Y_L, Z_H, Z_L)
        acc_raw = self._bus.read_i2c_block_data(self._address, _ICM_ADDR_ACCEL_DATA_X1, 6)
        ax = _s16(acc_raw[0], acc_raw[1]) / _ICM42688_ACCEL_SCALE
        ay = _s16(acc_raw[2], acc_raw[3]) / _ICM42688_ACCEL_SCALE
        az = _s16(acc_raw[4], acc_raw[5]) / _ICM42688_ACCEL_SCALE

        # Gyro (6 bytes)
        gyr_raw = self._bus.read_i2c_block_data(self._address, _ICM_ADDR_GYRO_DATA_X1, 6)
        gx = _s16(gyr_raw[0], gyr_raw[1]) / _ICM42688_GYRO_SCALE
        gy = _s16(gyr_raw[2], gyr_raw[3]) / _ICM42688_GYRO_SCALE
        gz = _s16(gyr_raw[4], gyr_raw[5]) / _ICM42688_GYRO_SCALE

        return {
            "accel_g": {"x": round(ax, 4), "y": round(ay, 4), "z": round(az, 4)},
            "gyro_dps": {"x": round(gx, 4), "y": round(gy, 4), "z": round(gz, 4)},
            "mag_uT": None,
            "temp_c": round(temp_c, 2),
            "mode": "hardware",
            "model": "icm42688",
        }

    def _read_bno055(self) -> dict:
        """Read fused data from BNO055.

        BNO055 in NDOF mode outputs 16-bit little-endian values.
        """
        # Accelerometer (LSB first, 6 bytes)
        acc_raw = self._bus.read_i2c_block_data(self._address, _BNO_ADDR_ACC_DATA_X_LSB, 6)
        ax = _s16(acc_raw[1], acc_raw[0]) / (_BNO055_ACCEL_SCALE * 9.80665)
        ay = _s16(acc_raw[3], acc_raw[2]) / (_BNO055_ACCEL_SCALE * 9.80665)
        az = _s16(acc_raw[5], acc_raw[4]) / (_BNO055_ACCEL_SCALE * 9.80665)

        # Magnetometer (LSB first, 6 bytes)
        mag_raw = self._bus.read_i2c_block_data(self._address, _BNO_ADDR_MAG_DATA_X_LSB, 6)
        mx = _s16(mag_raw[1], mag_raw[0]) / 16.0  # 1/16 µT per LSB
        my = _s16(mag_raw[3], mag_raw[2]) / 16.0
        mz = _s16(mag_raw[5], mag_raw[4]) / 16.0

        # Gyroscope (LSB first, 6 bytes)
        gyr_raw = self._bus.read_i2c_block_data(self._address, _BNO_ADDR_GYR_DATA_X_LSB, 6)
        gx = _s16(gyr_raw[1], gyr_raw[0]) / _BNO055_GYRO_SCALE
        gy = _s16(gyr_raw[3], gyr_raw[2]) / _BNO055_GYRO_SCALE
        gz = _s16(gyr_raw[5], gyr_raw[4]) / _BNO055_GYRO_SCALE

        # Temperature (signed 8-bit, 1°C per LSB in NDOF mode)
        temp_c = self._bus.read_byte_data(self._address, _BNO_ADDR_TEMP)
        if temp_c > 127:
            temp_c -= 256

        return {
            "accel_g": {"x": round(ax, 4), "y": round(ay, 4), "z": round(az, 4)},
            "gyro_dps": {"x": round(gx, 4), "y": round(gy, 4), "z": round(gz, 4)},
            "mag_uT": {"x": round(mx, 2), "y": round(my, 2), "z": round(mz, 2)},
            "temp_c": float(temp_c),
            "mode": "hardware",
            "model": "bno055",
        }

    def _mock_read(self) -> dict:
        """Return static near-zero mock values with a tiny sinusoidal wobble."""
        t = time.monotonic()
        wobble = math.sin(t * 0.5) * 0.002
        return {
            "accel_g": {
                "x": round(wobble, 4),
                "y": round(wobble * 0.5, 4),
                "z": round(1.0 + wobble, 4),
            },
            "gyro_dps": {"x": round(wobble * 10, 4), "y": 0.0, "z": 0.0},
            "mag_uT": None,
            "temp_c": 25.0,
            "mode": "mock",
            "model": self._detected_model,
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def read(self) -> dict:
        """Read current IMU data.

        Returns a dict with keys: accel_g {x,y,z}, gyro_dps {x,y,z},
        mag_uT {x,y,z} or None, temp_c, mode, model.
        """
        if self._mode != "hardware" or self._bus is None:
            return self._mock_read()

        with self._lock:
            try:
                if self._detected_model == "mpu6050":
                    return self._read_mpu6050()
                if self._detected_model == "icm42688":
                    return self._read_icm42688()
                if self._detected_model == "bno055":
                    return self._read_bno055()
            except Exception as exc:
                logger.error("IMU read error: %s", exc)
                return {
                    "accel_g": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "gyro_dps": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "mag_uT": None,
                    "temp_c": 0.0,
                    "mode": "error",
                    "model": self._detected_model,
                    "error": str(exc),
                }
        return self._mock_read()

    def calibrate(self) -> dict:
        """Trigger calibration.

        BNO055: returns the current calibration status (sys/gyro/accel/mag 0–3).
        MPU6050 / ICM-42688-P: calibration not supported by hardware — returns a note.
        """
        if self._detected_model == "bno055" and self._mode == "hardware":
            with self._lock:
                try:
                    calib = self._bus.read_byte_data(self._address, _BNO_ADDR_CALIB_STAT)
                    sys_cal = (calib >> 6) & 0x03
                    gyro_cal = (calib >> 4) & 0x03
                    accel_cal = (calib >> 2) & 0x03
                    mag_cal = calib & 0x03
                    fully_calibrated = all(v == 3 for v in [sys_cal, gyro_cal, accel_cal, mag_cal])
                    return {
                        "ok": True,
                        "model": "bno055",
                        "sys": sys_cal,
                        "gyro": gyro_cal,
                        "accel": accel_cal,
                        "mag": mag_cal,
                        "fully_calibrated": fully_calibrated,
                    }
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "model": self._detected_model,
            "note": "calibration not supported for this sensor",
        }

    def orientation(self) -> dict:
        """Return current orientation estimate with confidence.

        In mock mode returns zeros with confidence 0.5.

        In hardware mode integrates gyroscope readings over ``dt`` (seconds
        since the last call) to update ``_orientation`` via dead-reckoning.
        When a magnetometer is available (BNO055) a simple complementary
        filter blends the gyro integration with the mag-derived yaw, giving
        confidence 0.9.  Gyro-only gives confidence 0.7.

        Returns:
            {
                "yaw_deg":   float,
                "pitch_deg": float,
                "roll_deg":  float,
                "confidence": float,  # 0.5 mock | 0.7 gyro-only | 0.9 with mag
                "mode":      str,
            }
        """
        if self._mode != "hardware" or self._bus is None:
            return {
                "yaw_deg": 0.0,
                "pitch_deg": 0.0,
                "roll_deg": 0.0,
                "confidence": 0.5,
                "mode": "mock",
            }

        try:
            data = self.read()
            now = time.monotonic()
            dt = now - self._last_orient_ts if self._last_orient_ts > 0.0 else 0.0
            self._last_orient_ts = now

            gyro = data.get("gyro_dps", {})
            gx = float(gyro.get("x", 0.0))
            gy = float(gyro.get("y", 0.0))
            gz = float(gyro.get("z", 0.0))

            accel = data.get("accel_g", {})
            ax = float(accel.get("x", 0.0))
            ay = float(accel.get("y", 0.0))
            az = float(accel.get("z", 0.0))

            # Issue #343: Use Madgwick filter when configured
            if self._madgwick is not None and dt > 0.0:
                # Convert gyro from dps to rad/s for Madgwick filter
                gx_rad = math.radians(gx)
                gy_rad = math.radians(gy)
                gz_rad = math.radians(gz)
                self._madgwick.update(gx_rad, gy_rad, gz_rad, ax, ay, az, dt=dt)
                euler = self._madgwick.get_euler()
                self._orientation["yaw_deg"] = euler["yaw_deg"]
                self._orientation["pitch_deg"] = euler["pitch_deg"]
                self._orientation["roll_deg"] = euler["roll_deg"]
                return {
                    "yaw_deg": euler["yaw_deg"],
                    "pitch_deg": euler["pitch_deg"],
                    "roll_deg": euler["roll_deg"],
                    "confidence": 0.92,
                    "mode": self._mode,
                    "filter": "madgwick",
                }

            # Default: complementary filter (gyro integration + optional mag correction)
            if dt > 0.0:
                self._orientation["roll_deg"] += gx * dt
                self._orientation["pitch_deg"] += gy * dt
                self._orientation["yaw_deg"] += gz * dt

            mag = data.get("mag_uT")
            if mag is not None:
                # Complementary filter: trust mag 10 % per step for yaw correction
                mx = float(mag.get("x", 0.0))
                my = float(mag.get("y", 0.0))
                if mx != 0.0 or my != 0.0:
                    mag_yaw = math.degrees(math.atan2(my, mx))
                    alpha = 0.10
                    self._orientation["yaw_deg"] = (1.0 - alpha) * self._orientation[
                        "yaw_deg"
                    ] + alpha * mag_yaw
                confidence = 0.9
            else:
                confidence = 0.7

            return {
                "yaw_deg": round(self._orientation["yaw_deg"], 4),
                "pitch_deg": round(self._orientation["pitch_deg"], 4),
                "roll_deg": round(self._orientation["roll_deg"], 4),
                "confidence": confidence,
                "mode": self._mode,
            }
        except Exception as exc:
            logger.warning("IMUDriver.orientation error: %s", exc)
            return {
                "yaw_deg": self._orientation.get("yaw_deg", 0.0),
                "pitch_deg": self._orientation.get("pitch_deg", 0.0),
                "roll_deg": self._orientation.get("roll_deg", 0.0),
                "confidence": 0.5,
                "mode": "error",
            }

    def reset_orientation(self) -> None:
        """Zero out the accumulated orientation estimate.

        Also resets the Madgwick filter quaternion to identity when the
        Madgwick filter is active (Issue #343).
        """
        self._orientation = {"yaw_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0}
        self._last_orient_ts = 0.0
        if self._madgwick is not None:
            self._madgwick.reset()

    def step_count(self, reset: bool = False) -> int:
        """Return the accumulated step count, optionally resetting it.

        Calls ``read()`` to obtain a fresh accelerometer reading, computes the
        magnitude of the acceleration vector, and applies a peak-detection
        algorithm with hysteresis to count steps.

        Args:
            reset: When True, zero the counter and return the count *before*
                   the reset.

        Returns:
            Current (pre-reset when ``reset=True``) step count as an int.
            Never raises.
        """
        try:
            data = self.read()
            accel = data.get("accel_g", {})
            ax = float(accel.get("x", 0.0))
            ay = float(accel.get("y", 0.0))
            az = float(accel.get("z", 0.0))
            mag = math.sqrt(ax * ax + ay * ay + az * az)
            self._step_last_mag = mag

            if mag > self._step_threshold and not self._step_in_peak:
                self._step_count += 1
                self._step_in_peak = True
            elif mag <= self._step_threshold * 0.8:
                self._step_in_peak = False
        except Exception as exc:
            logger.warning("IMUDriver.step_count error: %s", exc)

        if reset:
            count = self._step_count
            self._step_count = 0
            return count
        return self._step_count

    def reset_steps(self) -> int:
        """Reset the step counter and return the count before the reset.

        Convenience wrapper around ``step_count(reset=True)``.
        """
        return self.step_count(reset=True)

    # ── Tap detection (#357) ──────────────────────────────────────────────────

    def tap_detection(
        self,
        accel_threshold_g: Optional[float] = None,
        double_tap_window_s: Optional[float] = None,
    ) -> dict:
        """Detect single and double tap events from accelerometer spikes.

        Returns mock (all-False) values immediately when in mock mode.  In
        hardware mode reads the current accelerometer vector and checks
        whether any axis exceeds *accel_threshold_g*, tracking ``_last_tap_time``
        and ``_tap_count`` to distinguish single from double taps.

        Args:
            accel_threshold_g:   Acceleration threshold in g (default 2.0 g).
            double_tap_window_s: Max seconds between taps to count as double
                                 (default 0.5 s).

        Returns:
            ``{"single_tap": bool, "double_tap": bool, "axis": str|None,
            "timestamp": float|None}``.  Falls back to mock result on any
            hardware error.  Never raises.
        """
        _mock: dict = {"single_tap": False, "double_tap": False, "axis": None, "timestamp": None}

        if self._mode != "hardware" or self._bus is None:
            return _mock

        threshold_g = (
            accel_threshold_g if accel_threshold_g is not None else self._tap_accel_threshold_g
        )
        window_s = (
            double_tap_window_s if double_tap_window_s is not None else self._double_tap_window_s
        )

        try:
            data = self.read()
            accel = data.get("accel_g", {})
            ax = abs(float(accel.get("x", 0.0)))
            ay = abs(float(accel.get("y", 0.0)))
            az = abs(float(accel.get("z", 0.0)))
            axis_vals = {"x": ax, "y": ay, "z": az}
            dominant_axis = max(axis_vals, key=lambda k: axis_vals[k])
            if axis_vals[dominant_axis] < threshold_g:
                return _mock

            now = time.time()
            if self._last_tap_time is not None:
                elapsed = now - self._last_tap_time
                if elapsed <= window_s:
                    self._last_tap_time = None
                    self._tap_count = 0
                    return {
                        "single_tap": False,
                        "double_tap": True,
                        "axis": dominant_axis,
                        "timestamp": now,
                    }
                # Too slow — start fresh single-tap sequence
                self._last_tap_time = now
                self._tap_count = 1
                return {
                    "single_tap": True,
                    "double_tap": False,
                    "axis": dominant_axis,
                    "timestamp": now,
                }

            # First tap in a new sequence
            self._last_tap_time = now
            self._tap_count = 1
            return {
                "single_tap": True,
                "double_tap": False,
                "axis": dominant_axis,
                "timestamp": now,
            }
        except Exception as exc:
            logger.warning("IMUDriver.tap_detection error: %s", exc)
            return _mock

    def reset_taps(self) -> None:
        """Zero tap detection state for a fresh single/double-tap sequence."""
        self._last_tap_time = None
        self._tap_count = 0

    # ------------------------------------------------------------------
    # Issue #369 — shake detection
    # ------------------------------------------------------------------

    def shake_detection(
        self,
        threshold_g: Optional[float] = None,
        min_reversals: Optional[int] = None,
        window_s: Optional[float] = None,
    ) -> dict:
        """Detect a shake gesture via rapid high-magnitude acceleration reversals.

        Each call reads the current acceleration.  If the magnitude on any
        axis exceeds *threshold_g* the event is appended to a rolling history
        window.  A shake is reported when at least *min_reversals* sign-change
        transitions are detected within the *window_s* time window.

        In mock mode always returns ``{shaking: False, ...}``.

        Args:
            threshold_g:   Acceleration magnitude threshold in g.  Defaults to
                           ``IMU_SHAKE_THRESHOLD_G`` env var (1.5 g).
            min_reversals: Minimum sign-change count to classify as a shake.
                           Defaults to ``IMU_SHAKE_MIN_REVERSALS`` env var (3).
            window_s:      Rolling time window in seconds.  Defaults to
                           ``IMU_SHAKE_WINDOW_S`` env var (0.5 s).

        Returns:
            ``{shaking: bool, reversals: int, axis: str|None, timestamp: float|None}``
        """
        _mock = {"shaking": False, "reversals": 0, "axis": None, "timestamp": None}
        if self._mode != "hardware" or self._bus is None:
            return _mock

        _threshold = threshold_g if threshold_g is not None else self._shake_threshold_g
        _min_rev = min_reversals if min_reversals is not None else self._shake_min_reversals
        _window = window_s if window_s is not None else self._shake_window_s

        try:
            data = self.read()
            accel = data.get("accel_g", {})
            now = time.time()

            # Prune history outside window
            self._shake_history = [e for e in self._shake_history if now - e[0] <= _window]

            # Determine dominant axis and check threshold
            ax = float(accel.get("x", 0.0))
            ay = float(accel.get("y", 0.0))
            az = float(accel.get("z", 0.0))
            magnitudes = {"x": abs(ax), "y": abs(ay), "z": abs(az)}
            dominant_axis = max(magnitudes, key=lambda k: magnitudes[k])
            dominant_val = {"x": ax, "y": ay, "z": az}[dominant_axis]

            if magnitudes[dominant_axis] >= _threshold:
                sign = 1 if dominant_val >= 0 else -1
                self._shake_history.append((now, dominant_axis, sign))

            # Count sign reversals on the dominant axis within the window
            axis_events = [(t, s) for t, a, s in self._shake_history if a == dominant_axis]
            reversals = 0
            for i in range(1, len(axis_events)):
                if axis_events[i][1] != axis_events[i - 1][1]:
                    reversals += 1

            shaking = reversals >= _min_rev
            return {
                "shaking": shaking,
                "reversals": reversals,
                "axis": dominant_axis if shaking else None,
                "timestamp": now if shaking else None,
            }
        except Exception as exc:
            logger.warning("IMUDriver.shake_detection error: %s", exc)
            return _mock

    def reset_shake(self) -> None:
        """Clear shake detection history."""
        self._shake_history = []

    # ── Issue #381 — step_counter (dict-returning variant) ────────────────────

    def step_counter(
        self,
        threshold_g: Optional[float] = None,
        min_interval_s: float = 0.3,
    ) -> dict:
        """Count motion steps via accelerometer magnitude peaks.

        Each call reads the IMU, applies a peak-detection algorithm using
        *threshold_g* as the detection threshold, and returns the accumulated
        step count together with configuration metadata.

        In mock mode the step count remains 0 (no hardware to integrate).

        Args:
            threshold_g:    Acceleration magnitude threshold in g-units above
                            which a step peak is detected.  Defaults to the
                            ``_step_threshold`` value set at construction.
            min_interval_s: Minimum seconds between consecutive steps (used for
                            debounce on hardware; not enforced in mock mode).

        Returns:
            ``{"steps": int, "threshold_g": float, "min_interval_s": float, "mode": str}``.
            Never raises.
        """
        _thr = float(threshold_g) if threshold_g is not None else self._step_threshold
        try:
            if self._mode == "hardware":
                data = self.read()
                accel = data.get("accel_g", {})
                ax = float(accel.get("x", 0.0))
                ay = float(accel.get("y", 0.0))
                az = float(accel.get("z", 0.0))
                mag = math.sqrt(ax * ax + ay * ay + az * az)
                if mag > _thr and not self._step_in_peak:
                    self._step_count += 1
                    self._step_in_peak = True
                elif mag <= _thr * 0.8:
                    self._step_in_peak = False
        except Exception as exc:
            logger.warning("IMUDriver.step_counter error: %s", exc)

        return {
            "steps": self._step_count,
            "threshold_g": _thr,
            "min_interval_s": min_interval_s,
            "mode": self._mode,
        }

    def reset_step_counter(self) -> None:
        """Reset the accumulated step counter to zero."""
        self._step_count = 0
        self._step_in_peak = False

    def reset_pose(self) -> None:
        """Zero all accumulated pose state.

        After calling this method the next call to :meth:`pose` will treat
        itself as the *first* call and return zeros without integrating any
        displacement.
        """
        self._pose_x_m = 0.0
        self._pose_y_m = 0.0
        self._pose_heading_deg = 0.0
        self._pose_last_ts = None

    def pose(self) -> dict:
        """Estimate the robot's 2-D pose by dead-reckoning from IMU data.

        On the first call the method records the current timestamp and returns
        all-zero pose (no ``dt`` to integrate yet).  Subsequent calls integrate
        heading from gyro Z and position from body-frame accelerometer readings
        rotated into the world frame.

        Integration equations (dt = elapsed seconds since last call):

        * heading:  ``_pose_heading_deg += gyro_z_dps * dt`` (wrapped ±180°)
        * body vel: ``vx = accel_x_ms2 * dt``,  ``vy = accel_y_ms2 * dt``
        * world dx: ``vx * cos(h) - vy * sin(h)``
        * world dy: ``vx * sin(h) + vy * cos(h)``

        Returns:
            {
                "x_m":         float,
                "y_m":         float,
                "heading_deg": float,
                "confidence":  float,   # 0.5 in mock mode
                "mode":        str,
            }

        On any internal error returns
        ``{"x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "confidence": 0.0, "error": str}``.

        Never raises.
        """
        try:
            now = time.time()

            # First call — record timestamp and return zeros
            if self._pose_last_ts is None:
                self._pose_last_ts = now
                return {
                    "x_m": 0.0,
                    "y_m": 0.0,
                    "heading_deg": 0.0,
                    "confidence": 0.5,
                    "mode": self._mode,
                }

            dt = now - self._pose_last_ts
            self._pose_last_ts = now

            data = self.read()
            accel = data.get("accel_g", {})
            gyro = data.get("gyro_dps", {})
            mode = data.get("mode", self._mode)

            # Convert g → m/s²
            accel_x_ms2 = float(accel.get("x", 0.0)) * 9.80665
            accel_y_ms2 = float(accel.get("y", 0.0)) * 9.80665
            gyro_z_dps = float(gyro.get("z", 0.0))

            # Integrate heading
            self._pose_heading_deg += gyro_z_dps * dt
            # Wrap to -180..180
            while self._pose_heading_deg > 180.0:
                self._pose_heading_deg -= 360.0
            while self._pose_heading_deg < -180.0:
                self._pose_heading_deg += 360.0

            heading_rad = math.radians(self._pose_heading_deg)

            # Body-frame velocity estimate from acceleration
            vx = accel_x_ms2 * dt
            vy = accel_y_ms2 * dt

            # Rotate to world frame
            dx = vx * math.cos(heading_rad) - vy * math.sin(heading_rad)
            dy = vx * math.sin(heading_rad) + vy * math.cos(heading_rad)

            self._pose_x_m += dx
            self._pose_y_m += dy

            confidence = 0.5  # mock mode; hardware would give 0.8

            return {
                "x_m": float(self._pose_x_m),
                "y_m": float(self._pose_y_m),
                "heading_deg": float(self._pose_heading_deg),
                "confidence": confidence,
                "mode": mode,
            }

        except Exception as exc:
            logger.warning("IMUDriver.pose error: %s", exc)
            return {
                "x_m": 0.0,
                "y_m": 0.0,
                "heading_deg": 0.0,
                "confidence": 0.0,
                "error": str(exc),
            }

    def vibration_bands(self, window_n: int = 64) -> dict:
        """Classify motor vibration using FFT on accelerometer magnitude.

        Collects window_n accelerometer samples rapidly, computes FFT on the
        magnitude signal, and returns dominant frequency + per-band power.

        Args:
            window_n: Number of samples to collect for FFT analysis (default 64).

        Returns:
            {
                "dominant_hz": float,   # frequency with highest power
                "bands": {
                    "low": float,       # 0-20 Hz (structural/chassis)
                    "mid": float,       # 20-100 Hz (motor)
                    "high": float,      # 100+ Hz (gear mesh / high-freq)
                },
                "rms_g": float,         # RMS of accel magnitude across window
                "samples": int,         # actual samples collected
                "alert": bool,          # True if rms_g > IMU_VIBRATION_THRESHOLD_G
            }

        Never raises.
        """
        _zero: dict = {
            "dominant_hz": 0.0,
            "bands": {"low": 0.0, "mid": 0.0, "high": 0.0},
            "rms_g": 0.0,
            "samples": 0,
            "alert": False,
        }
        threshold_g = float(os.getenv("IMU_VIBRATION_THRESHOLD_G", "0.5"))
        sample_rate_hz = 50.0

        try:
            magnitudes = []
            for _ in range(window_n):
                try:
                    data = self.read()
                    accel = data.get("accel_g", {})
                    ax = float(accel.get("x", 0.0))
                    ay = float(accel.get("y", 0.0))
                    az = float(accel.get("z", 0.0))
                    magnitudes.append(math.sqrt(ax * ax + ay * ay + az * az))
                except Exception:
                    continue

            n_samples = len(magnitudes)
            if n_samples == 0:
                return _zero

            if not HAS_NUMPY:
                # numpy unavailable — return zeros with sample count
                rms_g = math.sqrt(sum(m * m for m in magnitudes) / n_samples)
                alert = rms_g > threshold_g
                return {
                    "dominant_hz": 0.0,
                    "bands": {"low": 0.0, "mid": 0.0, "high": 0.0},
                    "rms_g": round(rms_g, 6),
                    "samples": n_samples,
                    "alert": alert,
                }

            mag_arr = np.array(magnitudes, dtype=float)
            rms_g = float(np.sqrt(np.mean(mag_arr**2)))

            # Remove DC offset before FFT
            mag_dc = mag_arr - np.mean(mag_arr)

            fft_vals = np.fft.rfft(mag_dc)
            freqs = np.fft.rfftfreq(n_samples, d=1.0 / sample_rate_hz)
            amplitudes = np.abs(fft_vals)

            # Dominant frequency (skip DC bin at index 0)
            if len(amplitudes) > 1:
                dominant_idx = int(np.argmax(amplitudes[1:]) + 1)
                dominant_hz = float(freqs[dominant_idx])
            else:
                dominant_hz = 0.0

            # Per-band power sums of amplitudes
            low_mask = freqs < 20.0
            mid_mask = (freqs >= 20.0) & (freqs < 100.0)
            high_mask = freqs >= 100.0

            band_low = float(np.sum(amplitudes[low_mask]))
            band_mid = float(np.sum(amplitudes[mid_mask]))
            band_high = float(np.sum(amplitudes[high_mask]))

            alert = rms_g > threshold_g

            return {
                "dominant_hz": round(dominant_hz, 4),
                "bands": {
                    "low": round(band_low, 6),
                    "mid": round(band_mid, 6),
                    "high": round(band_high, 6),
                },
                "rms_g": round(rms_g, 6),
                "samples": n_samples,
                "alert": bool(alert),
            }
        except Exception as exc:
            logger.warning("IMUDriver.vibration_bands error: %s", exc)
            return _zero

    def health_check(self) -> dict:
        """Return driver health information."""
        return {
            "ok": self._mode in ("hardware", "mock"),
            "mode": self._mode,
            "model": self._detected_model,
            "address": hex(self._address) if self._mode == "hardware" else None,
            "bus": self._bus_num,
            "filter": self._imu_filter,
            "madgwick_beta": self._imu_beta if self._madgwick is not None else None,
            "error": None,
        }

    def close(self):
        """Release the I2C bus handle."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._mode = "mock"


# ── Singleton factory ─────────────────────────────────────────────────────────


def get_imu(
    bus: Optional[int] = None,
    address: Optional[int] = None,
    model: Optional[str] = None,
) -> IMUDriver:
    """Return the process-wide IMUDriver singleton."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _bus = bus if bus is not None else int(os.getenv("IMU_I2C_BUS", "1"))
            _model = model if model is not None else os.getenv("IMU_MODEL", "auto")
            env_addr = os.getenv("IMU_I2C_ADDRESS", "")
            _address = address
            if env_addr and _address is None:
                _address = int(env_addr, 16)
            _singleton = IMUDriver(bus=_bus, address=_address, model=_model)
    return _singleton
