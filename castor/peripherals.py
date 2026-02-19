"""
castor/peripherals.py — Plug-and-play peripheral auto-detection.

Scans USB, V4L2, I²C, serial, and NPU devices at startup or on-demand.
Returns detected peripherals with suggested RCAN config snippets.

No external dependencies required for basic scanning — uses stdlib + subprocess.
Optional: depthai, pyserial, smbus2 for richer detection.

Usage:
    from castor.peripherals import scan_all, print_scan_table
    peripherals = scan_all()
    print_scan_table(peripherals)

CLI:
    castor scan
    castor scan --json
    castor scan --i2c-bus 1
"""

from __future__ import annotations

import glob
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PeripheralInfo dataclass
# ---------------------------------------------------------------------------


@dataclass
class PeripheralInfo:
    name: str                # "OAK-D Lite", "Logitech C920", "RPLiDAR A1"
    category: str            # camera | depth | npu | lidar | imu | motor | serial | display | sensor | unknown
    interface: str           # usb | i2c | serial | pcie | csi
    device_path: str | None  # /dev/video0, /dev/ttyUSB0, None
    usb_id: str | None       # "03e7:2485" or None
    i2c_address: int | None  # 0x40, 0x68, or None
    driver_hint: str         # "depthai", "v4l2", "rplidar", "hailo", "pca9685"
    rcan_snippet: str        # suggested RCAN yaml block (multiline string)
    confidence: str          # "identified" | "probable" | "unknown"


# ---------------------------------------------------------------------------
# Known USB device database (VID:PID → info dict)
# ---------------------------------------------------------------------------

_USB_DEVICES: dict[str, dict] = {
    # OAK-D / DepthAI
    "03e7:2485": {
        "name": "OAK-D / OAK-D Lite / OAK-D Pro",
        "category": "depth",
        "driver_hint": "depthai",
        "rcan_snippet": (
            "camera:\n"
            '  type: "oakd"\n'
            "  depth_enabled: true\n"
            "  fps: 30"
        ),
    },
    "03e7:f63b": {
        "name": "OAK-D (Myriad X)",
        "category": "depth",
        "driver_hint": "depthai",
        "rcan_snippet": (
            "camera:\n"
            '  type: "oakd"\n'
            "  depth_enabled: true\n"
            "  fps: 30"
        ),
    },
    "03e7:2150": {
        "name": "OAK-1 / OAK-1 Lite",
        "category": "camera",
        "driver_hint": "depthai",
        "rcan_snippet": (
            "camera:\n"
            '  type: "oakd"\n'
            "  depth_enabled: false\n"
            "  fps: 30"
        ),
    },
    # Intel RealSense
    "8086:0b3a": {
        "name": "Intel RealSense D435",
        "category": "depth",
        "driver_hint": "pyrealsense2",
        "rcan_snippet": (
            "camera:\n"
            '  type: "realsense"\n'
            '  serial: ""\n'
            "  depth_enabled: true\n"
            "  fps: 30"
        ),
    },
    "8086:0b07": {
        "name": "Intel RealSense D415",
        "category": "depth",
        "driver_hint": "pyrealsense2",
        "rcan_snippet": (
            "camera:\n"
            '  type: "realsense"\n'
            '  serial: ""\n'
            "  depth_enabled: true\n"
            "  fps: 30"
        ),
    },
    "8086:0b64": {
        "name": "Intel RealSense D435i",
        "category": "depth",
        "driver_hint": "pyrealsense2",
        "rcan_snippet": (
            "camera:\n"
            '  type: "realsense"\n'
            '  serial: ""\n'
            "  depth_enabled: true\n"
            "  fps: 30"
        ),
    },
    # Motor controllers / serial
    "0483:5740": {
        "name": "STM32 Virtual COM / motor controller",
        "category": "motor",
        "driver_hint": "serial",
        "rcan_snippet": (
            "driver:\n"
            '  type: "serial"\n'
            '  port: "/dev/ttyACM0"\n'
            "  baud: 115200"
        ),
    },
    "0483:df11": {
        "name": "STM32 DFU",
        "category": "serial",
        "driver_hint": "serial",
        "rcan_snippet": (
            "driver:\n"
            '  type: "serial"\n'
            '  port: "/dev/ttyACM0"\n'
            "  baud: 115200"
        ),
    },
    # CH340 USB-Serial (cheap Arduino clones)
    "1a86:7523": {
        "name": "CH340 USB-Serial (Arduino clone)",
        "category": "serial",
        "driver_hint": "serial",
        "rcan_snippet": (
            "driver:\n"
            '  type: "arduino"\n'
            '  port: "/dev/ttyUSB0"\n'
            "  baud: 115200"
        ),
    },
    "1a86:55d4": {
        "name": "CH341 USB-Serial",
        "category": "serial",
        "driver_hint": "serial",
        "rcan_snippet": (
            "driver:\n"
            '  type: "serial"\n'
            '  port: "/dev/ttyUSB0"\n'
            "  baud: 115200"
        ),
    },
    # Arduino
    "2341:0043": {
        "name": "Arduino Uno",
        "category": "serial",
        "driver_hint": "arduino",
        "rcan_snippet": (
            "driver:\n"
            '  type: "arduino"\n'
            '  port: "/dev/ttyACM0"\n'
            "  baud: 115200"
        ),
    },
    "2341:0001": {
        "name": "Arduino Uno (older)",
        "category": "serial",
        "driver_hint": "arduino",
        "rcan_snippet": (
            "driver:\n"
            '  type: "arduino"\n'
            '  port: "/dev/ttyACM0"\n'
            "  baud: 115200"
        ),
    },
    "2341:8036": {
        "name": "Arduino Leonardo",
        "category": "serial",
        "driver_hint": "arduino",
        "rcan_snippet": (
            "driver:\n"
            '  type: "arduino"\n'
            '  port: "/dev/ttyACM0"\n'
            "  baud: 115200"
        ),
    },
    "2341:0042": {
        "name": "Arduino Mega 2560",
        "category": "serial",
        "driver_hint": "arduino",
        "rcan_snippet": (
            "driver:\n"
            '  type: "arduino"\n'
            '  port: "/dev/ttyACM0"\n'
            "  baud: 115200"
        ),
    },
    # Pololu
    "1ffb:0089": {
        "name": "Pololu USB Servo Controller",
        "category": "motor",
        "driver_hint": "serial",
        "rcan_snippet": (
            "driver:\n"
            '  type: "pololu"\n'
            '  port: "/dev/ttyACM0"\n'
            "  baud: 9600"
        ),
    },
    # USB cameras
    "0c45:636b": {
        "name": "Microdia USB Camera",
        "category": "camera",
        "driver_hint": "v4l2",
        "rcan_snippet": (
            "camera:\n"
            '  type: "usb"\n'
            '  device: "/dev/video0"\n'
            "  fps: 30"
        ),
    },
    "046d:082d": {
        "name": "Logitech HD Pro Webcam C920",
        "category": "camera",
        "driver_hint": "v4l2",
        "rcan_snippet": (
            "camera:\n"
            '  type: "usb"\n'
            '  device: "/dev/video0"\n'
            "  fps: 30"
        ),
    },
    "046d:085e": {
        "name": "Logitech BRIO Ultra HD",
        "category": "camera",
        "driver_hint": "v4l2",
        "rcan_snippet": (
            "camera:\n"
            '  type: "usb"\n'
            '  device: "/dev/video0"\n'
            "  fps: 30"
        ),
    },
    "046d:0825": {
        "name": "Logitech HD C270",
        "category": "camera",
        "driver_hint": "v4l2",
        "rcan_snippet": (
            "camera:\n"
            '  type: "usb"\n'
            '  device: "/dev/video0"\n'
            "  fps: 30"
        ),
    },
    "045e:097d": {
        "name": "Microsoft Modern Webcam",
        "category": "camera",
        "driver_hint": "v4l2",
        "rcan_snippet": (
            "camera:\n"
            '  type: "usb"\n'
            '  device: "/dev/video0"\n'
            "  fps: 30"
        ),
    },
    # LiDAR / serial adapters
    "10c4:ea60": {
        "name": "Silicon Labs CP2102 (LiDAR/GPS/serial)",
        "category": "lidar",
        "driver_hint": "serial",
        "rcan_snippet": (
            "lidar:\n"
            '  type: "rplidar"\n'
            '  port: "/dev/ttyUSB0"'
        ),
    },
    "0403:6001": {
        "name": "FTDI FT232R (Hokuyo/Arduino/serial)",
        "category": "serial",
        "driver_hint": "serial",
        "rcan_snippet": (
            "driver:\n"
            '  type: "serial"\n'
            '  port: "/dev/ttyUSB0"\n'
            "  baud: 115200"
        ),
    },
    "0403:6015": {
        "name": "FTDI FT231X (RPLiDAR A1/A2)",
        "category": "lidar",
        "driver_hint": "rplidar",
        "rcan_snippet": (
            "lidar:\n"
            '  type: "rplidar"\n'
            '  port: "/dev/ttyUSB0"'
        ),
    },
    "0403:6010": {
        "name": "FTDI FT2232 Dual USB-Serial",
        "category": "serial",
        "driver_hint": "serial",
        "rcan_snippet": (
            "driver:\n"
            '  type: "serial"\n'
            '  port: "/dev/ttyUSB0"\n'
            "  baud: 115200"
        ),
    },
    "067b:2303": {
        "name": "Prolific PL2303 USB-Serial",
        "category": "serial",
        "driver_hint": "serial",
        "rcan_snippet": (
            "driver:\n"
            '  type: "serial"\n'
            '  port: "/dev/ttyUSB0"\n'
            "  baud: 115200"
        ),
    },
    # Google Coral USB accelerator
    "18d1:9302": {
        "name": "Google Coral USB Accelerator (Edge TPU)",
        "category": "npu",
        "driver_hint": "coral",
        "rcan_snippet": (
            "npu:\n"
            '  type: "coral"\n'
            '  device: "usb"'
        ),
    },
    "18d1:9303": {
        "name": "Google Coral USB Accelerator v2",
        "category": "npu",
        "driver_hint": "coral",
        "rcan_snippet": (
            "npu:\n"
            '  type: "coral"\n'
            '  device: "usb"'
        ),
    },
    # Stereolabs ZED
    "2b03:f580": {
        "name": "Stereolabs ZED 2",
        "category": "depth",
        "driver_hint": "pyzed",
        "rcan_snippet": (
            "camera:\n"
            '  type: "zed"\n'
            "  depth_enabled: true\n"
            "  fps: 30"
        ),
    },
    "2b03:f881": {
        "name": "Stereolabs ZED Mini",
        "category": "depth",
        "driver_hint": "pyzed",
        "rcan_snippet": (
            "camera:\n"
            '  type: "zed"\n'
            "  depth_enabled: true\n"
            "  fps: 30"
        ),
    },
}

# ---------------------------------------------------------------------------
# Known I²C address database
# ---------------------------------------------------------------------------

_I2C_DEVICES: dict[int, dict] = {
    # PCA9685 PWM servo controllers
    0x40: {
        "name": "PCA9685 PWM servo controller",
        "category": "motor",
        "driver_hint": "pca9685",
        "rcan_type": "pca9685",
        "rcan_snippet": (
            "driver:\n"
            '  type: "pca9685"\n'
            "  i2c_bus: 1\n"
            "  address: 0x40"
        ),
    },
    0x41: {
        "name": "PCA9685 (alt addr 0x41)",
        "category": "motor",
        "driver_hint": "pca9685",
        "rcan_type": "pca9685",
        "rcan_snippet": (
            "driver:\n"
            '  type: "pca9685"\n'
            "  i2c_bus: 1\n"
            "  address: 0x41"
        ),
    },
    0x42: {
        "name": "PCA9685 (alt addr 0x42)",
        "category": "motor",
        "driver_hint": "pca9685",
        "rcan_type": "pca9685",
        "rcan_snippet": (
            "driver:\n"
            '  type: "pca9685"\n'
            "  i2c_bus: 1\n"
            "  address: 0x42"
        ),
    },
    # ADC
    0x48: {
        "name": "ADS1115 ADC",
        "category": "sensor",
        "driver_hint": "ads1115",
        "rcan_type": "ads1115",
        "rcan_snippet": (
            "sensor:\n"
            '  type: "ads1115"\n'
            "  i2c_bus: 1\n"
            "  address: 0x48"
        ),
    },
    0x49: {
        "name": "ADS1115 ADC (alt addr 0x49)",
        "category": "sensor",
        "driver_hint": "ads1115",
        "rcan_type": "ads1115",
        "rcan_snippet": (
            "sensor:\n"
            '  type: "ads1115"\n'
            "  i2c_bus: 1\n"
            "  address: 0x49"
        ),
    },
    # IMU — MPU-6050
    0x68: {
        "name": "MPU-6050 IMU (or DS1307 RTC)",
        "category": "imu",
        "driver_hint": "mpu6050",
        "rcan_type": "mpu6050",
        "rcan_snippet": (
            "imu:\n"
            '  type: "mpu6050"\n'
            "  i2c_bus: 1"
        ),
    },
    0x69: {
        "name": "MPU-6050 IMU (alt addr 0x69)",
        "category": "imu",
        "driver_hint": "mpu6050",
        "rcan_type": "mpu6050",
        "rcan_snippet": (
            "imu:\n"
            '  type: "mpu6050"\n'
            "  i2c_bus: 1\n"
            "  address: 0x69"
        ),
    },
    # IMU — BNO055
    0x28: {
        "name": "BNO055 IMU",
        "category": "imu",
        "driver_hint": "bno055",
        "rcan_type": "bno055",
        "rcan_snippet": (
            "imu:\n"
            '  type: "bno055"\n'
            "  i2c_bus: 1"
        ),
    },
    0x29: {
        "name": "VL53L0X ToF distance sensor",
        "category": "sensor",
        "driver_hint": "vl53l0x",
        "rcan_type": "vl53l0x",
        "rcan_snippet": (
            "sensor:\n"
            '  type: "vl53l0x"\n'
            "  i2c_bus: 1"
        ),
    },
    # OLED display
    0x3C: {
        "name": "SSD1306 OLED display (0x3C)",
        "category": "display",
        "driver_hint": "ssd1306",
        "rcan_type": "ssd1306",
        "rcan_snippet": (
            "display:\n"
            '  type: "ssd1306"\n'
            "  i2c_bus: 1\n"
            "  address: 0x3C"
        ),
    },
    0x3D: {
        "name": "SSD1306 OLED display (alt addr 0x3D)",
        "category": "display",
        "driver_hint": "ssd1306",
        "rcan_type": "ssd1306",
        "rcan_snippet": (
            "display:\n"
            '  type: "ssd1306"\n'
            "  i2c_bus: 1\n"
            "  address: 0x3D"
        ),
    },
    # Magnetometer
    0x1E: {
        "name": "HMC5883L magnetometer",
        "category": "imu",
        "driver_hint": "hmc5883l",
        "rcan_type": "hmc5883l",
        "rcan_snippet": (
            "imu:\n"
            '  type: "hmc5883l"\n'
            "  i2c_bus: 1"
        ),
    },
    # ICM-42688 IMU
    0x6A: {
        "name": "ICM-42688 IMU (0x6A)",
        "category": "imu",
        "driver_hint": "icm42688",
        "rcan_type": "icm42688",
        "rcan_snippet": (
            "imu:\n"
            '  type: "icm42688"\n'
            "  i2c_bus: 1"
        ),
    },
    0x6B: {
        "name": "ICM-42688 IMU (alt addr 0x6B)",
        "category": "imu",
        "driver_hint": "icm42688",
        "rcan_type": "icm42688",
        "rcan_snippet": (
            "imu:\n"
            '  type: "icm42688"\n'
            "  i2c_bus: 1\n"
            "  address: 0x6B"
        ),
    },
    # BMP/BME environment sensors
    0x77: {
        "name": "BMP280/BME280 environment sensor (0x77)",
        "category": "sensor",
        "driver_hint": "bme280",
        "rcan_type": "bme280",
        "rcan_snippet": (
            "sensor:\n"
            '  type: "bme280"\n'
            "  i2c_bus: 1"
        ),
    },
    0x76: {
        "name": "BME280 environment sensor (alt addr 0x76)",
        "category": "sensor",
        "driver_hint": "bme280",
        "rcan_type": "bme280",
        "rcan_snippet": (
            "sensor:\n"
            '  type: "bme280"\n'
            "  i2c_bus: 1\n"
            "  address: 0x76"
        ),
    },
}

# ---------------------------------------------------------------------------
# RCAN snippet helpers
# ---------------------------------------------------------------------------

_CATEGORY_ORDER = ["depth", "camera", "npu", "lidar", "imu", "motor", "sensor", "display", "serial", "unknown"]


def _make_usb_camera_snippet(device_path: str) -> str:
    return (
        "camera:\n"
        f'  type: "usb"\n'
        f'  device: "{device_path}"\n'
        "  fps: 30"
    )


def _make_serial_snippet(device_path: str) -> str:
    return (
        "driver:\n"
        f'  type: "serial"\n'
        f'  port: "{device_path}"\n'
        "  baud: 115200"
    )


# ---------------------------------------------------------------------------
# Individual scanners
# ---------------------------------------------------------------------------


def scan_usb() -> list[PeripheralInfo]:
    """Run lsusb, parse VID:PID, match against _USB_DEVICES.

    For unknown devices, returns a PeripheralInfo with confidence='unknown'.
    Hub-only / root controller entries are skipped.
    """
    results: list[PeripheralInfo] = []

    try:
        proc = subprocess.run(
            ["lsusb"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("lsusb not available or timed out")
        return results

    # Pattern: "Bus 001 Device 005: ID 03e7:2485 Intel Corp. ..."
    pattern = re.compile(r"ID\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+(.*)")

    for line in proc.stdout.splitlines():
        m = pattern.search(line)
        if not m:
            continue

        vid_pid = m.group(1).lower()
        usb_label = m.group(2).strip()

        # Skip USB hubs and root controllers (common noise)
        skip_keywords = ("hub", "root hub", "xHCI", "EHCI", "OHCI", "UHCI")
        if any(kw.lower() in usb_label.lower() for kw in skip_keywords):
            continue

        if vid_pid in _USB_DEVICES:
            info = _USB_DEVICES[vid_pid]
            peripheral = PeripheralInfo(
                name=info["name"],
                category=info["category"],
                interface="usb",
                device_path=None,
                usb_id=vid_pid,
                i2c_address=None,
                driver_hint=info.get("driver_hint", ""),
                rcan_snippet=info.get("rcan_snippet", ""),
                confidence="identified",
            )
            results.append(peripheral)
            logger.info("USB peripheral identified: %s (%s)", info["name"], vid_pid)
        else:
            # Unknown device — include it anyway with lower confidence
            peripheral = PeripheralInfo(
                name=f"Unknown USB device ({usb_label[:50]})" if usb_label else f"Unknown USB ({vid_pid})",
                category="unknown",
                interface="usb",
                device_path=None,
                usb_id=vid_pid,
                i2c_address=None,
                driver_hint="",
                rcan_snippet="",
                confidence="unknown",
            )
            results.append(peripheral)
            logger.debug("Unknown USB device: %s %s", vid_pid, usb_label)

    return results


def scan_v4l2() -> list[PeripheralInfo]:
    """Find /dev/video* devices via glob; enrich with v4l2-ctl if available."""
    results: list[PeripheralInfo] = []

    # Only even-numbered minor devices on Linux (odd numbers are metadata/subdev nodes)
    video_nodes = sorted(glob.glob("/dev/video*"))

    for dev_path in video_nodes:
        # Filter out metadata nodes (v4l2 creates /dev/video0, /dev/video1 pairs on some kernels)
        # We keep all that can be described, skipping clearly non-capture nodes
        card_name = ""
        driver_name = ""
        is_csi = False

        # Try v4l2-ctl for richer info
        try:
            info_proc = subprocess.run(
                ["v4l2-ctl", f"--device={dev_path}", "--info"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            for info_line in info_proc.stdout.splitlines():
                if "Card type" in info_line:
                    card_name = info_line.split(":", 1)[-1].strip()
                elif "Driver name" in info_line:
                    driver_name = info_line.split(":", 1)[-1].strip()
            if "bm2835" in driver_name.lower() or "unicam" in driver_name.lower():
                is_csi = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        name = card_name if card_name else f"Video device {dev_path}"
        interface = "csi" if is_csi else "usb"
        category = "camera"
        driver_hint = "libcamera" if is_csi else "v4l2"
        rcan_snippet = _make_usb_camera_snippet(dev_path)
        confidence = "probable"

        peripheral = PeripheralInfo(
            name=name,
            category=category,
            interface=interface,
            device_path=dev_path,
            usb_id=None,
            i2c_address=None,
            driver_hint=driver_hint,
            rcan_snippet=rcan_snippet,
            confidence=confidence,
        )
        results.append(peripheral)
        logger.info("V4L2 device: %s at %s", name, dev_path)

    return results


def scan_i2c(bus: int = 1) -> list[PeripheralInfo]:
    """Run i2cdetect on *bus*, parse addresses, match against _I2C_DEVICES.

    Graceful failure if i2cdetect is not available or bus does not exist.
    """
    results: list[PeripheralInfo] = []

    try:
        proc = subprocess.run(
            ["i2cdetect", "-y", str(bus)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("i2cdetect not available or timed out for bus %d", bus)
        return results

    if proc.returncode != 0:
        logger.debug("i2cdetect failed for bus %d: %s", bus, proc.stderr.strip())
        return results

    # Parse the hex grid output; addresses shown as hex values (not '--' or 'UU')
    addr_pattern = re.compile(r"\b([0-9a-fA-F]{2})\b")
    # i2cdetect output looks like:
    #      0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
    # 00:          -- -- -- -- -- -- -- -- -- -- -- -- --
    # 10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
    # ...
    # 40: 40 -- -- -- -- -- -- -- -- -- -- -- -- -- -- --

    found_addresses: list[int] = []
    for line in proc.stdout.splitlines():
        if not line or line.startswith("  "):
            # Skip header / address column lines
            continue
        # The row header is like "40:" — skip those
        parts = line.split(":")
        if len(parts) < 2:
            continue
        data_part = parts[1]
        for token in data_part.split():
            if token in ("--", "UU"):
                continue
            try:
                addr = int(token, 16)
                if 0x03 <= addr <= 0x77:  # valid I2C address range
                    found_addresses.append(addr)
            except ValueError:
                pass

    for addr in found_addresses:
        if addr in _I2C_DEVICES:
            info = _I2C_DEVICES[addr]
            peripheral = PeripheralInfo(
                name=info["name"],
                category=info["category"],
                interface="i2c",
                device_path=f"/dev/i2c-{bus}",
                usb_id=None,
                i2c_address=addr,
                driver_hint=info.get("driver_hint", ""),
                rcan_snippet=info.get("rcan_snippet", ""),
                confidence="identified",
            )
        else:
            peripheral = PeripheralInfo(
                name=f"Unknown I2C device at 0x{addr:02X}",
                category="unknown",
                interface="i2c",
                device_path=f"/dev/i2c-{bus}",
                usb_id=None,
                i2c_address=addr,
                driver_hint="",
                rcan_snippet="",
                confidence="unknown",
            )
        results.append(peripheral)
        logger.info("I2C device at 0x%02X on bus %d: %s", addr, bus, peripheral.name)

    return results


def scan_serial() -> list[PeripheralInfo]:
    """Find /dev/ttyUSB* and /dev/ttyACM* devices."""
    results: list[PeripheralInfo] = []

    # Build a friendly name map from /dev/serial/by-id symlinks
    id_map: dict[str, str] = {}
    for symlink in glob.glob("/dev/serial/by-id/*"):
        try:
            import os
            target = os.path.realpath(symlink)
            id_map[target] = symlink.split("/")[-1]
        except OSError:
            pass

    serial_nodes = sorted(
        glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
    )

    for dev_path in serial_nodes:
        friendly = id_map.get(dev_path, "")
        name = f"Serial device {dev_path}"
        if friendly:
            name = f"Serial: {friendly[:60]}"

        peripheral = PeripheralInfo(
            name=name,
            category="serial",
            interface="serial",
            device_path=dev_path,
            usb_id=None,
            i2c_address=None,
            driver_hint="serial",
            rcan_snippet=_make_serial_snippet(dev_path),
            confidence="probable",
        )
        results.append(peripheral)
        logger.info("Serial device: %s", dev_path)

    return results


def scan_npu() -> list[PeripheralInfo]:
    """Detect NPU accelerators: Hailo-8, Google Coral (USB+PCIe)."""
    results: list[PeripheralInfo] = []

    # --- Hailo ---
    hailo_detected = False

    # Method 1: check for /dev/hailo* devices
    hailo_devs = glob.glob("/dev/hailo*")
    if hailo_devs:
        hailo_detected = True
        logger.info("Hailo NPU detected via /dev/hailo*: %s", hailo_devs)

    # Method 2: try importing hailo Python module
    if not hailo_detected:
        try:
            import importlib
            importlib.import_module("hailo_platform")
            hailo_detected = True
            logger.info("Hailo NPU detected via hailo_platform module")
        except ImportError:
            pass

    if hailo_detected:
        results.append(PeripheralInfo(
            name="Hailo-8 / Hailo-8L NPU",
            category="npu",
            interface="pcie",
            device_path=hailo_devs[0] if hailo_devs else None,
            usb_id=None,
            i2c_address=None,
            driver_hint="hailo",
            rcan_snippet=(
                "npu:\n"
                '  type: "hailo"\n'
                '  device: "/dev/hailo0"'
            ),
            confidence="identified",
        ))

    # --- Google Coral USB ---
    coral_usb_detected = any(
        p.usb_id in ("18d1:9302", "18d1:9303")
        for p in scan_usb()
        # Already captured in scan_usb; avoid double-adding, just flag
    )
    # Check lsusb directly to avoid calling scan_usb recursively if already called
    try:
        proc = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
        if "18d1:9302" in proc.stdout or "18d1:9303" in proc.stdout:
            coral_usb_detected = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if coral_usb_detected and not any(p.driver_hint == "coral" for p in results):
        results.append(PeripheralInfo(
            name="Google Coral USB Accelerator",
            category="npu",
            interface="usb",
            device_path=None,
            usb_id="18d1:9302",
            i2c_address=None,
            driver_hint="coral",
            rcan_snippet=(
                "npu:\n"
                '  type: "coral"\n'
                '  device: "usb"'
            ),
            confidence="identified",
        ))

    # --- Google Coral PCIe ---
    coral_pcie_devs = glob.glob("/dev/apex_*")
    if coral_pcie_devs:
        results.append(PeripheralInfo(
            name="Google Coral PCIe Accelerator",
            category="npu",
            interface="pcie",
            device_path=coral_pcie_devs[0],
            usb_id=None,
            i2c_address=None,
            driver_hint="coral",
            rcan_snippet=(
                "npu:\n"
                '  type: "coral"\n'
                f'  device: "{coral_pcie_devs[0]}"'
            ),
            confidence="identified",
        ))
        logger.info("Google Coral PCIe detected: %s", coral_pcie_devs)

    return results


def scan_csi() -> list[PeripheralInfo]:
    """Detect CSI cameras (Raspberry Pi camera modules)."""
    results: list[PeripheralInfo] = []

    # Method 1: libcamera-hello --list-cameras
    try:
        proc = subprocess.run(
            ["libcamera-hello", "--list-cameras"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = proc.stdout + proc.stderr
        if "Available cameras" in output or "camera" in output.lower():
            # Parse camera entries
            for line in output.splitlines():
                if line.strip().startswith(("0 :", "1 :", "0:", "1:")):
                    cam_name = line.split(":")[-1].strip() or "Raspberry Pi Camera"
                    results.append(PeripheralInfo(
                        name=f"CSI Camera: {cam_name}",
                        category="camera",
                        interface="csi",
                        device_path="/dev/video0",
                        usb_id=None,
                        i2c_address=None,
                        driver_hint="libcamera",
                        rcan_snippet=(
                            "camera:\n"
                            '  type: "csi"\n'
                            '  device: "/dev/video0"\n'
                            "  fps: 30"
                        ),
                        confidence="identified",
                    ))
                    logger.info("CSI camera found: %s", cam_name)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Method 2: check for bm2835_v4l2 via /dev/video0
    if not results:
        try:
            proc = subprocess.run(
                ["v4l2-ctl", "--device=/dev/video0", "--info"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            output = proc.stdout.lower()
            if "bm2835" in output or "unicam" in output:
                results.append(PeripheralInfo(
                    name="Raspberry Pi Camera (CSI)",
                    category="camera",
                    interface="csi",
                    device_path="/dev/video0",
                    usb_id=None,
                    i2c_address=None,
                    driver_hint="v4l2",
                    rcan_snippet=(
                        "camera:\n"
                        '  type: "csi"\n'
                        '  device: "/dev/video0"\n'
                        "  fps: 30"
                    ),
                    confidence="identified",
                ))
                logger.info("Raspberry Pi CSI camera detected via v4l2-ctl")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return results


def scan_all(i2c_buses: list[int] | None = None) -> list[PeripheralInfo]:
    """Run all scans. Returns a deduplicated list sorted by category.

    Args:
        i2c_buses: List of I2C bus numbers to scan. Defaults to [1].

    Returns:
        Deduplicated list of PeripheralInfo sorted by category priority.
    """
    if i2c_buses is None:
        i2c_buses = [1]

    all_peripherals: list[PeripheralInfo] = []

    # Run each scanner
    logger.debug("Starting peripheral scan...")

    usb_results = scan_usb()
    all_peripherals.extend(usb_results)

    v4l2_results = scan_v4l2()
    all_peripherals.extend(v4l2_results)

    for bus in i2c_buses:
        i2c_results = scan_i2c(bus=bus)
        all_peripherals.extend(i2c_results)

    serial_results = scan_serial()
    all_peripherals.extend(serial_results)

    npu_results = scan_npu()
    all_peripherals.extend(npu_results)

    csi_results = scan_csi()
    all_peripherals.extend(csi_results)

    # Deduplicate:
    # 1. If USB scan already identified an OAK-D (depth), remove any v4l2 entry
    #    for the same device_path (OAK-D exposes /dev/video* but it's not a plain camera).
    # 2. Remove serial duplicates (same device_path).
    # 3. Remove NPU duplicates (same driver_hint + interface).

    seen_device_paths: set[str] = set()
    seen_usb_ids: set[str] = set()
    seen_npu_hints: set[tuple] = set()

    # Collect USB-identified depth/npu devices to suppress v4l2 duplicates
    usb_depth_ids = {p.usb_id for p in usb_results if p.category in ("depth", "npu") and p.usb_id}

    deduplicated: list[PeripheralInfo] = []

    for p in all_peripherals:
        # Skip duplicate USB ids
        if p.usb_id and p.usb_id in seen_usb_ids:
            continue

        # Skip v4l2 duplicate if USB already identified the same physical device
        if p.interface in ("usb", "csi") and p.device_path:
            if p.device_path in seen_device_paths:
                continue

        # If USB scan identified a depth camera (OAK-D, RealSense) skip v4l2 entries
        # for the same interface unless the v4l2 device has a real card name
        if p.interface in ("usb", "csi") and p.category == "camera":
            # Check if this is a generic v4l2 probe that duplicates a USB depth device
            if usb_depth_ids and p.driver_hint == "v4l2" and "/dev/video" in (p.device_path or ""):
                # If we already have a depth peripheral from USB scan, skip bare v4l2 probes
                # but keep ones with a real card name
                if not p.name or p.name.startswith("Video device"):
                    continue

        # Deduplicate serial devices
        if p.device_path and p.category in ("serial", "lidar", "motor"):
            if p.device_path in seen_device_paths:
                continue

        # Deduplicate NPU
        npu_key = (p.driver_hint, p.interface)
        if p.category == "npu":
            if npu_key in seen_npu_hints:
                continue
            seen_npu_hints.add(npu_key)

        # Track seen
        if p.usb_id:
            seen_usb_ids.add(p.usb_id)
        if p.device_path:
            seen_device_paths.add(p.device_path)

        deduplicated.append(p)

    # Sort by category priority
    def _sort_key(p: PeripheralInfo) -> tuple:
        try:
            cat_idx = _CATEGORY_ORDER.index(p.category)
        except ValueError:
            cat_idx = len(_CATEGORY_ORDER)
        return (cat_idx, p.name)

    deduplicated.sort(key=_sort_key)

    for p in deduplicated:
        logger.info(
            "Peripheral: [%s] %s — %s via %s (confidence: %s)",
            p.category, p.name, p.device_path or p.usb_id or f"0x{p.i2c_address:02X}" if p.i2c_address else "?",
            p.interface, p.confidence,
        )

    return deduplicated


# ---------------------------------------------------------------------------
# Public API helpers
# ---------------------------------------------------------------------------


def to_rcan_snippet(peripheral: PeripheralInfo) -> str:
    """Return a ready-to-paste RCAN yaml block for the peripheral."""
    return peripheral.rcan_snippet


def print_scan_table(peripherals: list[PeripheralInfo], color: bool = True) -> None:
    """Pretty-print scan results to stdout.

    Shows a table with: Status, Name, Category, Interface, Path/Address, RCAN type.
    Followed by unique RCAN snippets.
    """
    if not peripherals:
        print("\n  No peripherals detected.\n")
        print("  Tips:")
        print("    • Connect hardware and re-run: castor scan")
        print("    • On Raspberry Pi, enable I2C: sudo raspi-config > Interface Options")
        print("    • For serial devices: ls /dev/ttyUSB* /dev/ttyACM*")
        print()
        return

    # Try Rich first
    try:
        _print_scan_table_rich(peripherals, color)
    except ImportError:
        _print_scan_table_plain(peripherals)


def _confidence_icon(confidence: str, use_color: bool = True) -> str:
    """Return a colored icon for confidence level."""
    if confidence == "identified":
        return "✓" if not use_color else "[green]✓[/green]"
    elif confidence == "probable":
        return "~" if not use_color else "[yellow]~[/yellow]"
    else:
        return "?" if not use_color else "[dim]?[/dim]"


def _print_scan_table_rich(peripherals: list[PeripheralInfo], color: bool) -> None:
    """Rich-formatted scan table."""
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console(no_color=not color)

    console.print("\n  [bold]OpenCastor Peripheral Scanner[/bold]\n")

    table = Table(
        show_header=True,
        header_style="bold",
        box=box.SIMPLE,
        padding=(0, 1),
    )
    table.add_column("", width=3)
    table.add_column("Name", min_width=30)
    table.add_column("Category", width=10)
    table.add_column("Interface", width=9)
    table.add_column("Path / Address", width=20)

    for p in peripherals:
        if p.confidence == "identified":
            icon = "[green]✓[/green]"
        elif p.confidence == "probable":
            icon = "[yellow]~[/yellow]"
        else:
            icon = "[dim]?[/dim]"

        path_or_addr = (
            p.device_path
            or (f"0x{p.i2c_address:02X}" if p.i2c_address is not None else "")
            or (p.usb_id or "")
        )

        table.add_row(
            icon,
            p.name,
            p.category,
            p.interface,
            path_or_addr,
        )

    console.print(table)

    # Print unique non-empty RCAN snippets
    seen_snippets: set[str] = set()
    unique_snippets = []
    for p in peripherals:
        if p.rcan_snippet and p.rcan_snippet not in seen_snippets:
            seen_snippets.add(p.rcan_snippet)
            unique_snippets.append((p.name, p.rcan_snippet))

    if unique_snippets:
        console.print("  [bold]Suggested rcan.yaml additions:[/bold]\n")
        for name, snippet in unique_snippets:
            console.print(f"  # {name}")
            for line in snippet.splitlines():
                console.print(f"  [cyan]{line}[/cyan]")
            console.print()

    # Legend
    console.print(
        "  [green]✓[/green] identified  "
        "[yellow]~[/yellow] probable  "
        "[dim]?[/dim] unknown\n"
    )


def _print_scan_table_plain(peripherals: list[PeripheralInfo]) -> None:
    """Fallback plain-text scan table (no Rich)."""
    ICONS = {"identified": "[OK]", "probable": "[~~ ]", "unknown": "[?? ]"}

    print("\n  OpenCastor Peripheral Scanner\n")
    print(f"  {'':3} {'Name':<35} {'Cat':<10} {'Iface':<8} {'Path/Addr'}")
    print(f"  {'-' * 70}")

    for p in peripherals:
        icon = ICONS.get(p.confidence, "[?]")
        path_or_addr = (
            p.device_path
            or (f"0x{p.i2c_address:02X}" if p.i2c_address is not None else "")
            or (p.usb_id or "")
        )
        name_trunc = p.name[:33] + ".." if len(p.name) > 35 else p.name
        print(f"  {icon:3} {name_trunc:<35} {p.category:<10} {p.interface:<8} {path_or_addr}")

    print()

    seen_snippets: set[str] = set()
    unique_snippets = []
    for p in peripherals:
        if p.rcan_snippet and p.rcan_snippet not in seen_snippets:
            seen_snippets.add(p.rcan_snippet)
            unique_snippets.append((p.name, p.rcan_snippet))

    if unique_snippets:
        print("  Suggested rcan.yaml additions:\n")
        for name, snippet in unique_snippets:
            print(f"  # {name}")
            for line in snippet.splitlines():
                print(f"  {line}")
            print()

    print("  [OK] = identified  [~~ ] = probable  [?? ] = unknown\n")
