"""
OpenCastor Hardware Detection -- auto-detect connected hardware.

Scans I2C buses, USB serial ports, USB VID/PID descriptors, cameras, and
network-reachable robots to suggest the most likely hardware preset for the wizard.

Usage (from wizard)::

    from castor.hardware_detect import detect_hardware, suggest_preset
    results = detect_hardware()
    preset = suggest_preset(results)
"""

import logging
import os
import socket
import subprocess
import sys
import time as _time

# Suppress libcamera / picamera2 noise before any camera-related imports (#558)
os.environ.setdefault("LIBCAMERA_LOG_LEVELS", "*:FATAL")

try:
    import smbus2 as _smbus2_mod  # noqa: F401

    HAS_SMBUS = True
except ImportError:
    HAS_SMBUS = False

# Module-level cache for lsusb output — avoids running lsusb multiple times
# per detect_hardware() call. Reset by invalidate_usb_descriptors_cache().
_USB_DESCRIPTORS_CACHE: list | None = None

# Module-level TTL cache for detect_hardware() result (#553)
_HARDWARE_CACHE: dict | None = None
_HARDWARE_CACHE_TS: float = 0.0
_HARDWARE_CACHE_TTL: float = 30.0

logger = logging.getLogger("OpenCastor.HardwareDetect")


# ---------------------------------------------------------------------------
# VID/PID Tables
# ---------------------------------------------------------------------------

#: USB VID/PID table for known HLaboratories and compatible devices.
KNOWN_HLABS_DEVICES: dict = {
    "0483:df11": {
        "name": "STM32 DFU (firmware flash mode)",
        "warn": "Device is in DFU mode — flash firmware first before normal use.",
    },
    "0483:5740": {"name": "STM32 Virtual COM Port (ACB candidate)"},
    "0483:5720": {"name": "STM32 USB Serial (ACB candidate)"},
}

#: Intel RealSense depth cameras.
KNOWN_REALSENSE_DEVICES: dict = {
    "8086:0b07": "Intel RealSense D435",
    "8086:0b3a": "Intel RealSense D435i",
    "8086:0b5c": "Intel RealSense D455",
    "8086:0ad3": "Intel RealSense D415",
    "8086:0b64": "Intel RealSense L515",
    "8086:0b5b": "Intel RealSense D405",
    "8086:0b37": "Intel RealSense D430",
}

#: Luxonis OAK-D depth/AI cameras.
KNOWN_OAKD_DEVICES: dict = {
    "03e7:2485": "Luxonis OAK-D (bootloader/lite)",
    "03e7:2487": "Luxonis OAK-D (running)",
    "03e7:f63b": "Luxonis OAK-D SR",
}

#: Arduino family microcontroller boards.
KNOWN_ARDUINO_DEVICES: dict = {
    "2341:0043": "Arduino Uno R3",
    "2341:1002": "Arduino Uno R4 WiFi",
    "2341:0042": "Arduino Mega 2560",
    "2341:8036": "Arduino Leonardo",
    "2341:003d": "Arduino Due",
    "2341:0243": "Arduino Nano Every",
    "1a86:7523": "Arduino Nano (CH340 clone)",
    "1a86:55d4": "Arduino Nano (CH343 clone)",
    "0403:6001": "Arduino Pro Mini (FTDI)",
    "1b4f:9205": "SparkFun Pro Micro",
}

#: Feetech / Waveshare serial bus servo boards (SO-ARM101 candidate).
KNOWN_FEETECH_DEVICES: dict = {
    "1a86:7523": "Waveshare Serial Bus Servo Board (CH340G) — SO-ARM101 candidate",
    "0483:5740": "STM32 Servo Board variant — SO-ARM101 candidate",
    "10c4:ea60": "CP2102 USB-Serial — possible servo board",
}

#: Dynamixel U2D2 and OpenCR/OpenCM boards.
KNOWN_DYNAMIXEL_DEVICES: dict = {
    "0403:6014": "Dynamixel U2D2 (FT232R)",  # Standard U2D2
    "0403:6015": "Dynamixel U2D2-H (FT232H)",  # High-speed U2D2-H
    "16d0:0c17": "Robotis OpenCM 9.04",
    "0483:5740": "Robotis OpenCR 1.0",
}

#: ODrive brushless motor controllers.
KNOWN_ODRIVE_DEVICES: dict = {
    "1209:0d32": "ODrive v3.x",
    "1209:0d33": "ODrive Pro",
    "1209:0d34": "ODrive S1",
}

#: LiDAR USB adapters (RPLidar, YDLIDAR, Hokuyo, Sick).
KNOWN_LIDAR_DEVICES: dict = {
    "10c4:ea60": "RPLidar/YDLIDAR (CP2102) — probe baud to disambiguate",
    "0483:5740": "YDLIDAR T15 / RPLidar S3 (STM32 VCP)",  # shared VID/PID; see detect_rplidar_usb()
    "15d1:0000": "Hokuyo URG",
    "19a2:5343": "Sick TIM571",
}

#: VID/PID pairs associated with RPLidar / YDLIDAR USB adapters.
_LIDAR_CP2102_VID_PID = (0x10C4, 0xEA60)  # Slamtec / YDLIDAR CP2102
_LIDAR_STM32_VID_PID = (0x0483, 0x5740)  # STM32 VCP (newer RPLidar / YDLIDAR T15)
_LIDAR_VID_PIDS: frozenset = frozenset({_LIDAR_CP2102_VID_PID, _LIDAR_STM32_VID_PID})

#: I2C address → device name/type mapping for enriched scan output.
I2C_DEVICE_MAP: dict = {
    "0x28": {"name": "BNO055", "type": "imu"},
    "0x29": {"name": "VL53L1X or BNO055 (alt)", "type": "tof_or_imu"},
    "0x3c": {"name": "SSD1306/SH1106 OLED", "type": "display"},
    "0x3d": {"name": "SSD1306 OLED (alt addr)", "type": "display"},
    "0x40": {"name": "PCA9685 PWM Driver", "type": "servo_driver"},
    "0x48": {"name": "ADS1115 ADC", "type": "adc"},
    "0x49": {"name": "ADS1115 ADC (addr 1)", "type": "adc"},
    "0x4a": {"name": "ADS1115 ADC (addr 2)", "type": "adc"},
    "0x4b": {"name": "ADS1115 ADC (addr 3)", "type": "adc"},
    "0x68": {"name": "MPU-6050 / ICM-42688", "type": "imu"},
    "0x69": {"name": "MPU-6050 (alt) / ICM-42688 (alt)", "type": "imu"},
    "0x6a": {"name": "LSM6DSO / LSM6DSOX", "type": "imu"},
    "0x6b": {"name": "LSM6DSO (alt addr)", "type": "imu"},
    "0x76": {"name": "BME280 / BMP280", "type": "environmental"},
    "0x77": {"name": "BME280 / BMP280 (alt)", "type": "environmental"},
    "0x1e": {"name": "HMC5883L / QMC5883L", "type": "magnetometer"},
    "0x18": {"name": "LIS3DH", "type": "accelerometer"},
    "0x19": {"name": "LIS3DH (alt addr)", "type": "accelerometer"},
    "0x5d": {"name": "APDS-9960", "type": "gesture_proximity"},
}


# ---------------------------------------------------------------------------
# I2C / USB / Camera scanners
# ---------------------------------------------------------------------------


def scan_i2c() -> list:
    """Scan I2C buses for attached devices, enriching with :data:`I2C_DEVICE_MAP`.

    Returns:
        List of dicts: ``{"bus": int, "address": "0xNN", "device": str, "type": str}``.
        ``"device"`` and ``"type"`` are ``"unknown"`` when the address is not in the map.
    """
    devices = []

    if sys.platform != "linux":
        return devices

    i2c_buses = []
    dev_dir = "/dev"
    if os.path.isdir(dev_dir):
        for entry in os.listdir(dev_dir):
            if entry.startswith("i2c-"):
                try:
                    bus_num = int(entry.split("-")[1])
                    i2c_buses.append(bus_num)
                except (ValueError, IndexError):
                    pass

    for bus in sorted(i2c_buses):
        try:
            result = subprocess.run(
                ["i2cdetect", "-y", str(bus)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                continue
            for line in result.stdout.splitlines()[1:]:  # Skip header
                parts = line.split(":")[1].strip().split() if ":" in line else []
                for part in parts:
                    part = part.strip()
                    if part != "--" and len(part) == 2:
                        try:
                            int(part, 16)
                            addr = f"0x{part}"
                            info = I2C_DEVICE_MAP.get(addr, {})
                            devices.append(
                                {
                                    "bus": bus,
                                    "address": addr,
                                    "device": info.get("name", "unknown"),
                                    "type": info.get("type", "unknown"),
                                }
                            )
                        except ValueError:
                            pass
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return devices


def detect_i2c_devices() -> list:
    """Scan I2C buses for attached devices using smbus2 (preferred) or sysfs parsing.

    Uses :data:`I2C_DEVICE_MAP` to enrich found addresses with human-readable names.

    .. note::
        When using smbus2, only addresses in :data:`I2C_DEVICE_MAP` are probed.
        For a full 0x03–0x77 sweep, use :func:`scan_i2c` (requires ``i2cdetect``).

    Returns:
        List of dicts: ``{"bus": int, "address": str, "device_name": str}``.
        Returns ``[]`` on non-Linux platforms or when no I2C buses are found.
    """
    if sys.platform != "linux":
        return []

    devices: list = []

    # Collect available bus numbers from /dev/i2c-*
    i2c_buses: list = []
    dev_dir = "/dev"
    if os.path.isdir(dev_dir):
        for entry in os.listdir(dev_dir):
            if entry.startswith("i2c-"):
                try:
                    i2c_buses.append(int(entry.split("-")[1]))
                except (ValueError, IndexError):
                    pass

    if HAS_SMBUS:
        import smbus2  # noqa: PLC0415

        for bus_num in sorted(i2c_buses):
            for addr_hex, info in I2C_DEVICE_MAP.items():
                addr_int = int(addr_hex, 16)
                try:
                    with smbus2.SMBus(bus_num) as bus:
                        bus.read_byte(addr_int)
                    devices.append(
                        {
                            "bus": bus_num,
                            "address": addr_hex,
                            "device_name": info.get("name", "unknown"),
                        }
                    )
                    logger.debug(
                        "I2C device found: bus=%d addr=%s (%s)",
                        bus_num,
                        addr_hex,
                        info.get("name"),
                    )
                except OSError:
                    pass  # No ACK — device not present
    else:
        # Fallback: parse /sys/bus/i2c/devices/ directory names (format: "BUS-ADDR")
        sys_i2c = "/sys/bus/i2c/devices"
        if os.path.isdir(sys_i2c):
            for entry in os.listdir(sys_i2c):
                parts = entry.split("-")
                if len(parts) != 2:
                    continue
                try:
                    bus_num = int(parts[0])
                    addr_int = int(parts[1], 16)
                    addr_hex = f"0x{addr_int:02x}"
                    info = I2C_DEVICE_MAP.get(addr_hex, {})
                    devices.append(
                        {
                            "bus": bus_num,
                            "address": addr_hex,
                            "device_name": info.get("name", "unknown"),
                        }
                    )
                except (ValueError, IndexError):
                    pass

    return devices


def scan_usb_serial() -> list:
    """Find USB serial ports (common for Dynamixel, Arduino, etc.).

    Returns:
        List of port paths, e.g. ``["/dev/ttyUSB0", "/dev/ttyACM0"]``.
    """
    ports = []
    if sys.platform != "linux":
        return ports
    dev_dir = "/dev"
    if os.path.isdir(dev_dir):
        for entry in sorted(os.listdir(dev_dir)):
            if entry.startswith("ttyUSB") or entry.startswith("ttyACM"):
                ports.append(os.path.join(dev_dir, entry))
    return ports


def scan_usb_descriptors() -> list:
    """Return raw ``lsusb`` descriptor lines (lower-cased) when available.

    Result is cached for the lifetime of the process so that multiple
    detectors calling this within a single :func:`detect_hardware` scan
    do not invoke ``lsusb`` repeatedly.
    """
    return _scan_usb_descriptors_cached()


def _scan_usb_descriptors_cached() -> list:
    """Internal implementation — call once and cache in module-level var."""
    global _USB_DESCRIPTORS_CACHE
    if _USB_DESCRIPTORS_CACHE is not None:
        return _USB_DESCRIPTORS_CACHE
    try:
        proc = subprocess.run(
            ["lsusb"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _USB_DESCRIPTORS_CACHE = []
        return []
    if proc.returncode != 0:
        _USB_DESCRIPTORS_CACHE = []
        return []
    _USB_DESCRIPTORS_CACHE = [
        line.strip().lower() for line in proc.stdout.splitlines() if line.strip()
    ]
    return _USB_DESCRIPTORS_CACHE


def invalidate_usb_descriptors_cache() -> None:
    """Clear the ``scan_usb_descriptors`` cache (useful in tests or after hot-plug)."""
    global _USB_DESCRIPTORS_CACHE
    _USB_DESCRIPTORS_CACHE = None


def invalidate_hardware_cache() -> None:
    """Clear the detect_hardware() result cache and the lsusb sub-cache (#553)."""
    global _HARDWARE_CACHE, _HARDWARE_CACHE_TS
    _HARDWARE_CACHE = None
    _HARDWARE_CACHE_TS = 0.0
    invalidate_usb_descriptors_cache()


def _get_v4l2_device_name(dev_entry: str) -> str | None:
    """Read device name from /sys/class/video4linux/{entry}/name (#552).

    Args:
        dev_entry: Bare device name, e.g. ``"video0"``.

    Returns:
        Human-readable device name string, or ``None`` if not readable.
    """
    path = f"/sys/class/video4linux/{dev_entry}/name"
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def scan_cameras() -> list:
    """Detect available camera devices.

    Returns:
        List of dicts: ``{"type": "csi"|"usb", "device": str, "accessible": bool,
        "name": str|None}``.
    """
    cameras = []

    for entry in sorted(os.listdir("/dev")) if os.path.isdir("/dev") else []:
        if entry.startswith("video"):
            device_path = f"/dev/{entry}"
            cameras.append(
                {
                    "type": "usb",
                    "device": device_path,
                    "accessible": os.access(device_path, os.R_OK),
                    "name": _get_v4l2_device_name(entry),
                }
            )

    try:
        from picamera2 import Picamera2

        cam = Picamera2()
        cam.close()
        cameras.insert(0, {"type": "csi", "device": "CSI ribbon cable", "accessible": True})
    except Exception:
        pass

    return cameras


# ---------------------------------------------------------------------------
# USB VID/PID helpers
# ---------------------------------------------------------------------------


def _list_usb_ports_with_vidpid() -> list:
    """Return pyserial port info list; empty list if pyserial unavailable."""
    try:
        from serial.tools import list_ports

        return list(list_ports.comports())
    except ImportError:
        return []


def _match_vid_pid_table(table: dict) -> list:
    """Return list of dicts for USB serial ports matching *table*.

    Each entry: ``{"port": str, "vid_pid": str, "model": str}``.
    """
    matches = []
    for port_info in _list_usb_ports_with_vidpid():
        vid = getattr(port_info, "vid", None)
        pid = getattr(port_info, "pid", None)
        if vid is None or pid is None:
            continue
        key = f"{vid:04x}:{pid:04x}"
        if key in table:
            matches.append(
                {
                    "port": port_info.device,
                    "vid_pid": key,
                    "model": table[key]
                    if isinstance(table[key], str)
                    else table[key].get("name", key),
                }
            )
    return matches


def _scan_lsusb_for_vid(vid_hex: str) -> list:
    """Scan ``lsusb`` output for lines containing *vid_hex* (lower-case, no '0x').

    Returns list of raw lsusb lines.
    """
    lines = scan_usb_descriptors()
    return [ln for ln in lines if vid_hex.lower() in ln]


# ---------------------------------------------------------------------------
# New hardware detectors
# ---------------------------------------------------------------------------


def detect_realsense_usb() -> list:
    """Detect Intel RealSense cameras by USB VID/PID (VID 0x8086).

    Returns:
        List of dicts: ``{"port": str, "vid_pid": str, "model": str}``.
        Uses ``lsusb`` fallback when pyserial is not installed.
    """
    results = _match_vid_pid_table(KNOWN_REALSENSE_DEVICES)
    if results:
        return results
    # lsusb fallback — RealSense appears as non-serial USB device
    lines = _scan_lsusb_for_vid("8086")
    for ln in lines:
        for key, model in KNOWN_REALSENSE_DEVICES.items():
            if key.replace(":", " ") in ln or key in ln:
                results.append({"port": "usb", "vid_pid": key, "model": model})
    return results


def detect_oakd_usb() -> list:
    """Detect Luxonis OAK-D cameras by USB VID/PID (VID 0x03e7).

    Falls back to ``depthai`` Python API when installed.

    Returns:
        List of dicts: ``{"port": str, "vid_pid": str, "model": str}``.
    """
    results = _match_vid_pid_table(KNOWN_OAKD_DEVICES)
    if results:
        return results

    # lsusb fallback — normalise to lowercase so hex case doesn't matter (#546)
    lines = _scan_lsusb_for_vid("03e7")
    for ln in lines:
        ln_lower = ln.lower()
        for key, model in KNOWN_OAKD_DEVICES.items():
            vid_pid_spaced = key.replace(":", " ")
            if vid_pid_spaced in ln_lower or key in ln_lower:
                results.append({"port": "usb", "vid_pid": key, "model": model})

    if results:
        return results

    # depthai API fallback
    try:
        import depthai as dai  # type: ignore[import]

        devices = dai.Device.getAllAvailableDevices()
        for dev in devices:
            results.append({"port": "depthai", "vid_pid": "03e7:xxxx", "model": str(dev.name)})
    except Exception:
        pass

    return results


def detect_odrive_usb() -> list:
    """Detect ODrive motor controllers by USB VID/PID (pid.codes VID 0x1209).

    Returns:
        List of port path strings (e.g. ``["/dev/ttyACM0"]``).
    """
    matches = _match_vid_pid_table(KNOWN_ODRIVE_DEVICES)
    ports = [m["port"] for m in matches]
    if ports:
        logger.info("ODrive detected on: %s", ports)
    return ports


def detect_vesc_usb() -> list:
    """Detect VESC motor controllers; disambiguates from ACB by product string.

    Returns:
        List of port path strings.
    """
    ports: list = []
    for port_info in _list_usb_ports_with_vidpid():
        description = (getattr(port_info, "description", "") or "").upper()
        product = (getattr(port_info, "product", "") or "").upper()
        manufacturer = (getattr(port_info, "manufacturer", "") or "").upper()
        if "VESC" in description or "VESC" in product or "VESC" in manufacturer:
            ports.append(port_info.device)
            logger.info("VESC detected on %s (%s)", port_info.device, description)
    return ports


def detect_feetech_usb() -> list:
    """Detect Feetech/Waveshare serial bus servo boards (SO-ARM101 candidate).

    Matches by VID/PID; further disambiguation by product string when available.

    Returns:
        List of port path strings.
    """
    ports: list = []
    for port_info in _list_usb_ports_with_vidpid():
        vid = getattr(port_info, "vid", None)
        pid = getattr(port_info, "pid", None)
        if vid is None or pid is None:
            continue
        key = f"{vid:04x}:{pid:04x}"
        description = (getattr(port_info, "description", "") or "").upper()
        product = (getattr(port_info, "product", "") or "").upper()
        manufacturer = (getattr(port_info, "manufacturer", "") or "").upper()

        if key in KNOWN_FEETECH_DEVICES:
            combined = description + product + manufacturer
            # Skip if product string clearly identifies it as ACB, ODrive, or Robotis
            if any(tok in combined for tok in ("ACB", "ODRIVE", "ROBOTIS")):
                continue
            # Skip CH340 devices that are clearly Arduino boards (not servo boards)
            if key == "1a86:7523" and any(
                tok in combined for tok in ("ARDUINO", "UNO", "MEGA", "NANO", "LEONARDO", "DUE")
            ):
                continue
            ports.append(port_info.device)
            logger.info("Feetech servo board detected on %s (VID/PID %s)", port_info.device, key)

    return ports


def detect_arduino_usb() -> list:
    """Detect Arduino family boards by VID/PID table.

    Returns:
        List of dicts: ``{"port": str, "board": str, "vid_pid": str}``.
    """
    results: list = []
    for port_info in _list_usb_ports_with_vidpid():
        vid = getattr(port_info, "vid", None)
        pid = getattr(port_info, "pid", None)
        if vid is None or pid is None:
            continue
        key = f"{vid:04x}:{pid:04x}"
        if key in KNOWN_ARDUINO_DEVICES:
            board_name = KNOWN_ARDUINO_DEVICES[key]
            results.append({"port": port_info.device, "board": board_name, "vid_pid": key})
            logger.info("Arduino detected on %s: %s", port_info.device, board_name)
    return results


def detect_circuitpython_usb() -> list:
    """Detect Adafruit CircuitPython boards (VID 0x239A).

    Returns:
        List of dicts: ``{"port": str, "vid_pid": str, "description": str}``.
    """
    results: list = []
    for port_info in _list_usb_ports_with_vidpid():
        vid = getattr(port_info, "vid", None)
        if vid != 0x239A:
            continue
        pid = getattr(port_info, "pid", None)
        pid_str = f"{pid:04x}" if pid is not None else "xxxx"
        description = getattr(port_info, "description", "") or "CircuitPython board"
        results.append(
            {
                "port": port_info.device,
                "vid_pid": f"239a:{pid_str}",
                "description": description,
            }
        )
        logger.info("CircuitPython board detected on %s (%s)", port_info.device, description)
    return results


def detect_dynamixel_usb() -> list:
    """Detect Dynamixel U2D2 and OpenCR/OpenCM boards by VID/PID.

    Returns:
        List of dicts: ``{"port": str, "vid_pid": str, "model": str}``.
    """
    return _match_vid_pid_table(KNOWN_DYNAMIXEL_DEVICES)


def detect_lidar_usb() -> list:
    """Detect RPLidar and YDLIDAR USB adapters by VID/PID.

    Returns:
        List of dicts: ``{"port": str, "vid_pid": str, "model": str}``.
    """
    return _match_vid_pid_table(KNOWN_LIDAR_DEVICES)


def detect_rplidar_usb() -> dict:
    """Detect RPLidar and YDLIDAR USB adapters, distinguishing model by product string.

    Covers:
    - CP2102 (VID 0x10C4 / PID 0xEA60) — used by both Slamtec RPLidar and YDLIDAR.
    - STM32 VCP (VID 0x0483 / PID 0x5740) — used by newer RPLidar and YDLIDAR T15.

    Disambiguation heuristic: product/description string containing ``"YDLIDAR"`` →
    ydlidar; ``"RPLIDAR"`` or ``"SLAMTEC"`` → rplidar; anything else → unknown_lidar.

    Returns:
        Dict: ``{"detected": bool, "model": "rplidar"|"ydlidar"|"unknown_lidar"}``.
        ``detected`` is ``False`` and ``model`` is ``None`` when nothing matches.
    """
    for port_info in _list_usb_ports_with_vidpid():
        vid = getattr(port_info, "vid", None)
        pid = getattr(port_info, "pid", None)
        if (vid, pid) not in _LIDAR_VID_PIDS:
            continue
        combined = (
            (getattr(port_info, "description", "") or "")
            + (getattr(port_info, "product", "") or "")
            + (getattr(port_info, "manufacturer", "") or "")
        ).upper()
        if "YDLIDAR" in combined:
            model = "ydlidar"
        elif "RPLIDAR" in combined or "SLAMTEC" in combined:
            model = "rplidar"
        else:
            model = "unknown_lidar"
        logger.info("LiDAR detected: %s on %s", model, port_info.device)
        return {"detected": True, "model": model}

    # lsusb fallback — no product string available, classify as unknown_lidar
    lines = scan_usb_descriptors()
    for ln in lines:
        if "10c4:ea60" in ln or "0483:5740" in ln:
            logger.info("LiDAR detected via lsusb (model unknown): %s", ln)
            return {"detected": True, "model": "unknown_lidar"}

    return {"detected": False, "model": None}


def detect_hailo() -> list:
    """Detect Hailo-8 NPU via ``/dev/hailo0``, ``lspci``, or the ``hailo`` Python package.

    Returns:
        List of detection strings, e.g. ``["hailo8 via /dev/hailo0"]``.
    """
    found: list = []

    # /dev/hailo* character devices
    dev_dir = "/dev"
    if os.path.isdir(dev_dir):
        for entry in os.listdir(dev_dir):
            if entry.startswith("hailo"):
                found.append(f"hailo8 via /dev/{entry}")
                logger.info("Hailo NPU detected: /dev/%s", entry)

    if found:
        return found

    # lspci fallback
    try:
        proc = subprocess.run(
            ["lspci"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if "hailo" in line.lower():
                    found.append(f"hailo8 via lspci: {line.strip()}")
                    logger.info("Hailo NPU detected via lspci: %s", line.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if found:
        return found

    # hailo Python package
    try:
        import hailo  # type: ignore[import]

        devices = hailo.Device.scan()
        for dev in devices:
            found.append(f"hailo8 via sdk: {dev}")
    except Exception:
        pass

    return found


def detect_coral() -> list:
    """Detect Google Coral USB TPU and M.2/PCIe Edge TPU.

    Returns:
        List of detection strings.
    """
    found: list = []

    # USB TPU: VID 0x1a6e (Global Unichip), 0x18d1 (Google)
    for port_info in _list_usb_ports_with_vidpid():
        vid = getattr(port_info, "vid", None)
        if vid in (0x1A6E, 0x18D1):
            description = getattr(port_info, "description", "") or ""
            if "coral" in description.lower() or "edge tpu" in description.lower() or vid == 0x1A6E:
                found.append(f"coral_usb:{port_info.device}")
                logger.info("Coral USB TPU detected on %s", port_info.device)

    # lsusb fallback for USB TPU
    if not found:
        lines = scan_usb_descriptors()
        for ln in lines:
            if "1a6e" in ln or ("18d1" in ln and "coral" in ln):
                found.append("coral_usb:lsusb")

    # PCIe/M.2: check /dev/apex_*
    dev_dir = "/dev"
    if os.path.isdir(dev_dir):
        for entry in os.listdir(dev_dir):
            if entry.startswith("apex"):
                found.append(f"coral_pcie:/dev/{entry}")
                logger.info("Coral PCIe Edge TPU detected: /dev/%s", entry)

    return found


def detect_imx500_camera() -> list:
    """Detect Raspberry Pi AI Camera (IMX500) via ``picamera2.global_camera_info()``.

    Returns:
        List of detection strings, e.g. ``["imx500:cam0"]``.
    """
    found: list = []
    try:
        from picamera2 import Picamera2  # type: ignore[import]

        for cam_info in Picamera2.global_camera_info():
            model = str(cam_info.get("Model", "")).lower()
            if "imx500" in model:
                cam_id = cam_info.get("Id", "unknown")
                found.append(f"imx500:{cam_id}")
                logger.info("IMX500 AI Camera detected: %s", cam_id)
    except Exception:
        pass
    return found


def detect_rpi_ai_camera() -> dict:
    """Detect Raspberry Pi AI Camera (Sony IMX500) via libcamera and sysfs.

    Detection strategy (in priority order):

    1. Run ``libcamera-hello --list-cameras`` (timeout 3 s) — parse stdout for "imx500".
    2. Read ``/proc/device-tree/model`` — check for "imx500" mention.
    3. Scan ``/sys/class/video4linux/`` device name files for "imx500".

    NPU firmware is considered active when ``/lib/firmware/imx500/`` exists.

    Returns:
        Dict: ``{"detected": bool, "model": "imx500", "npu": bool}``.
    """
    detected = False
    npu = os.path.isdir("/lib/firmware/imx500")

    # 1. libcamera-hello
    try:
        proc = subprocess.run(
            ["libcamera-hello", "--list-cameras"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0 and "imx500" in proc.stdout.lower():
            detected = True
            logger.info("RPi AI Camera (IMX500) detected via libcamera-hello")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # 2. device-tree model
    if not detected:
        try:
            content = _read_device_tree_model().lower()
            if "imx500" in content:
                detected = True
                logger.info("RPi AI Camera (IMX500) detected via device-tree model")
        except (FileNotFoundError, PermissionError):
            pass

    # 3. /sys/class/video4linux/
    if not detected:
        v4l_dir = "/sys/class/video4linux"
        if os.path.isdir(v4l_dir):
            for entry in os.listdir(v4l_dir):
                name = _get_v4l2_device_name(entry) or ""
                if "imx500" in name.lower():
                    detected = True
                    logger.info(
                        "RPi AI Camera (IMX500) detected via /sys/class/video4linux/%s", entry
                    )
                    break

    return {"detected": detected, "model": "imx500", "npu": npu}


def detect_lerobot_hardware() -> dict:
    """Detect LeRobot-compatible hardware (Feetech SO-ARM101 / ALOHA).

    Heuristic:
    - Feetech servo board must be detected (via :func:`detect_feetech_usb`).
    - At least one ``/dev/ttyUSB*`` or ``/dev/ttyACM*`` port must exist.
    - Two or more serial ports → ALOHA profile (dual arm); one → SO-ARM101.

    Returns:
        Dict: ``{"compatible": bool, "profile": "so_arm101"|"aloha"|None}``.
    """
    feetech_ports = detect_feetech_usb()
    if not feetech_ports:
        return {"compatible": False, "profile": None}

    serial_ports = scan_usb_serial()
    if not serial_ports:
        return {"compatible": False, "profile": None}

    profile = "aloha" if len(serial_ports) >= 2 else "so_arm101"
    logger.info("LeRobot hardware detected: profile=%s, ports=%s", profile, serial_ports)
    return {"compatible": True, "profile": profile}


def detect_reachy_network(timeout: float = 2.0) -> list:
    """Detect Pollen Robotics Reachy 2 / Reachy Mini via mDNS or hostname resolution.

    Probes common Reachy hostnames concurrently (threaded, bounded by *timeout*)
    and the mDNS ``_reachy._tcp.local.`` service.

    Args:
        timeout: Maximum total wall-clock seconds to spend on discovery.

    Returns:
        List of host strings that responded (e.g. ``["reachy.local"]``).
    """
    import threading

    found: list = []
    candidates = ["reachy.local", "reachy2.local", "reachy-mini.local"]
    lock = threading.Lock()

    def _probe(host: str) -> None:
        try:
            addr = socket.getaddrinfo(
                host, 50055, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, socket.AI_ADDRCONFIG
            )
            if addr:
                with lock:
                    found.append(host)
                logger.info("Reachy detected at %s", host)
        except (socket.gaierror, OSError):
            pass

    threads = [threading.Thread(target=_probe, args=(h,), daemon=True) for h in candidates]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    # mDNS via zeroconf (optional) — only if hostname probes found nothing
    if not found:
        try:
            import time as _time

            from zeroconf import ServiceBrowser, Zeroconf  # type: ignore[import]

            zc = Zeroconf()
            discovered: list = []

            class _Handler:
                def add_service(self, zc_ref, stype, name):  # noqa: N802
                    info = zc_ref.get_service_info(stype, name)
                    if info:
                        addresses = info.parsed_addresses()
                        if addresses:
                            discovered.append(addresses[0])

                def remove_service(self, *_):
                    pass

                def update_service(self, *_):
                    pass

            ServiceBrowser(zc, "_reachy._tcp.local.", _Handler())
            _time.sleep(min(timeout, 1.0))
            zc.close()
            found.extend(discovered)
        except Exception:
            pass

    return found


# ---------------------------------------------------------------------------
# Top-level detect_hardware / detect_all_hlabs
# ---------------------------------------------------------------------------


def _run_all_detectors() -> dict:
    """Execute all hardware scans and return a combined result dict (uncached)."""
    return {
        "i2c_devices": scan_i2c(),
        "i2c": detect_i2c_devices(),
        "usb_serial": scan_usb_serial(),
        "usb_descriptors": scan_usb_descriptors(),
        "cameras": scan_cameras(),
        "platform": _detect_platform(),
        "realsense": detect_realsense_usb(),
        "oakd": detect_oakd_usb(),
        "odrive": detect_odrive_usb(),
        "vesc": detect_vesc_usb(),
        "feetech": detect_feetech_usb(),
        "arduino": detect_arduino_usb(),
        "circuitpython": detect_circuitpython_usb(),
        "dynamixel": detect_dynamixel_usb(),
        "lidar": detect_lidar_usb(),
        "rplidar": detect_rplidar_usb(),
        "hailo": detect_hailo(),
        "coral": detect_coral(),
        "imx500": detect_imx500_camera(),
        "rpi_ai_camera": detect_rpi_ai_camera(),
        "reachy": detect_reachy_network(),
        "lerobot": detect_lerobot_hardware(),
    }


def detect_hardware(refresh: bool = False) -> dict:
    """Run all hardware scans and return a combined result dict.

    Results are cached for :data:`_HARDWARE_CACHE_TTL` seconds (30 s by default).
    Pass ``refresh=True`` to force a fresh scan and reset both this cache and
    the lsusb sub-cache.

    Returns::

        {
            "i2c_devices": [...],
            "usb_serial": [...],
            "usb_descriptors": [...],
            "cameras": [...],
            "platform": "rpi"|"jetson"|"generic",
            "realsense": [...],
            "oakd": [...],
            ...
        }
    """
    global _HARDWARE_CACHE, _HARDWARE_CACHE_TS
    now = _time.monotonic()
    if (
        not refresh
        and _HARDWARE_CACHE is not None
        and (now - _HARDWARE_CACHE_TS) < _HARDWARE_CACHE_TTL
    ):
        return _HARDWARE_CACHE
    if refresh:
        invalidate_usb_descriptors_cache()
    result = _run_all_detectors()
    _HARDWARE_CACHE = result
    _HARDWARE_CACHE_TS = _time.monotonic()
    return result


def _read_device_tree_model(path: str = "/proc/device-tree/model") -> str:
    """Read device tree model file. Extracted for testability."""
    with open(path) as f:
        return f.read()


def _detect_platform() -> str:
    """Detect the current platform (Raspberry Pi, Jetson, or generic)."""
    try:
        model = _read_device_tree_model().lower()
        if "raspberry pi" in model:
            return "rpi"
        if "jetson" in model:
            return "jetson"
    except (FileNotFoundError, PermissionError):
        pass
    return "generic"


# ---------------------------------------------------------------------------
# HLaboratories ACB detection
# ---------------------------------------------------------------------------


def detect_acb_usb() -> list:
    """Scan USB serial ports for HLaboratories ACB v2.0 devices.

    Uses ``serial.tools.list_ports`` (pyserial) when available; falls back
    to the raw ``/dev/ttyACM*`` scan otherwise.

    Returns:
        List of port path strings (e.g. ``["/dev/ttyACM0"]``).
    """
    ports: list = []

    try:
        from serial.tools import list_ports

        for port_info in list_ports.comports():
            vid = getattr(port_info, "vid", None)
            pid = getattr(port_info, "pid", None)
            description = (getattr(port_info, "description", "") or "").upper()
            product = (getattr(port_info, "product", "") or "").upper()

            if vid is None:
                continue

            vid_pid_key = f"{vid:04x}:{pid:04x}" if pid is not None else ""

            if vid_pid_key == "0483:df11":
                logger.warning(
                    "ACB detected in DFU mode on %s — flash firmware first before normal use.",
                    port_info.device,
                )
                continue

            if vid == 0x0483 and (
                "ACB" in description
                or "STM32" in description
                or "ACB" in product
                or vid_pid_key in KNOWN_HLABS_DEVICES
            ):
                ports.append(port_info.device)
                logger.info("ACB device detected on %s (%s)", port_info.device, description)

    except ImportError:
        if sys.platform == "linux":
            dev_dir = "/dev"
            if os.path.isdir(dev_dir):
                for entry in sorted(os.listdir(dev_dir)):
                    if entry.startswith("ttyACM"):
                        ports.append(os.path.join(dev_dir, entry))

    return ports


def detect_all_hlabs() -> dict:
    """Run all HLabs hardware detectors.

    Returns:
        Dict mapping device class to list of port/node strings::

            {"acb": ["/dev/ttyACM0"]}
    """
    return {"acb": detect_acb_usb()}


# ---------------------------------------------------------------------------
# Preset suggestion
# ---------------------------------------------------------------------------


def suggest_preset(hw: dict) -> tuple:
    """Suggest a hardware preset based on scan results.

    Args:
        hw: Result from :func:`detect_hardware`.

    Returns:
        ``(preset_name, confidence, reason)`` where confidence is
        ``"high"``, ``"medium"``, or ``"low"``.
    """
    i2c_addrs = {d["address"] for d in hw.get("i2c_devices", [])}
    has_serial = len(hw.get("usb_serial", [])) > 0
    has_camera = len(hw.get("cameras", [])) > 0
    is_rpi = hw.get("platform") == "rpi"
    usb_desc = " ".join(hw.get("usb_descriptors", []))

    # ── Reachy humanoid ────────────────────────────────────────────────────
    if hw.get("reachy"):
        host = hw["reachy"][0]
        is_mini = "mini" in host.lower()
        profile = "pollen/reachy-mini" if is_mini else "pollen/reachy2"
        return profile, "high", f"Reachy {'Mini ' if is_mini else ''}detected at {host}"

    # ── Feetech servo board (SO-ARM101) ────────────────────────────────────
    if hw.get("feetech"):
        return (
            "lerobot/so-arm101-follower",
            "high",
            "Feetech servo board detected (SO-ARM101 candidate)",
        )

    # ── Dynamixel U2D2 ───────────────────────────────────────────────────
    if hw.get("dynamixel"):
        vp = hw["dynamixel"][0].get("vid_pid", "")
        port = hw["dynamixel"][0].get("port", "unknown")
        if vp in ("0403:6014", "0403:6015"):
            return "dynamixel_arm", "high", f"Dynamixel U2D2 on {port}"
        return "lerobot/koch-arm", "high", f"Dynamixel controller on {port}"

    # ── Luxonis OAK-D ─────────────────────────────────────────────────────
    if hw.get("oakd"):
        model = hw["oakd"][0].get("model", "OAK-D")
        preset = "rpi_oakd" if is_rpi else "jetson_oakd"
        return preset, "high", f"OAK-D detected: {model}"

    # ── Intel RealSense ───────────────────────────────────────────────────
    if hw.get("realsense"):
        model = hw["realsense"][0].get("model", "RealSense")
        preset = "rpi_realsense" if is_rpi else "generic_realsense"
        return preset, "high", f"RealSense detected: {model}"

    # ── ODrive motor controller ────────────────────────────────────────────
    if hw.get("odrive"):
        return "odrive/differential", "high", f"ODrive detected on {hw['odrive'][0]}"

    # ── Hailo NPU ─────────────────────────────────────────────────────────
    if hw.get("hailo"):
        return "hailo_vision", "high", "Hailo-8 NPU detected"

    # ── Coral TPU ─────────────────────────────────────────────────────────
    if hw.get("coral"):
        return "coral/tpu-inference", "high", "Coral Edge TPU detected"

    # ── LiDAR ─────────────────────────────────────────────────────────────
    rplidar_result = hw.get("rplidar")
    if isinstance(rplidar_result, dict) and rplidar_result.get("detected"):
        model = rplidar_result.get("model", "unknown_lidar")
        return "lidar_navigation", "high", f"LiDAR detected: {model}"

    # ── Arduino ───────────────────────────────────────────────────────────
    if hw.get("arduino"):
        board = hw["arduino"][0].get("board", "unknown")
        return "arduino/uno", "medium", f"Arduino detected: {board}"

    # ── LEGO EV3 ──────────────────────────────────────────────────────────
    if "ev3" in usb_desc or "mindstorms" in usb_desc:
        return "lego_mindstorms_ev3", "medium", "LEGO EV3 device hint detected over USB"

    # ── LEGO SPIKE Prime ──────────────────────────────────────────────────
    if "lego" in usb_desc or "0694:" in usb_desc:
        return "lego_spike_prime", "medium", "LEGO USB device detected (likely SPIKE Prime hub)"

    # ── PCA9685 + RPi → rover kit ─────────────────────────────────────────
    if "0x40" in i2c_addrs and is_rpi:
        if has_camera:
            return "rpi_rc_car", "high", "PCA9685 at 0x40 + RPi + camera detected"
        return "rpi_rc_car", "medium", "PCA9685 at 0x40 + RPi detected (no camera)"

    # ── ESP32 ─────────────────────────────────────────────────────────────
    if has_serial and any(token in usb_desc for token in ("esp32", "cp210", "ch340")):
        return "esp32_generic", "medium", f"Serial bridge detected ({hw['usb_serial'][0]})"

    # ── Generic serial → Dynamixel guess ─────────────────────────────────
    if has_serial:
        return "dynamixel_arm", "medium", f"Serial port detected: {hw['usb_serial'][0]}"

    # ── RPi fallback ──────────────────────────────────────────────────────
    if is_rpi:
        return "amazon_kit_generic", "low", "Raspberry Pi detected, no specific hardware found"

    return "rpi_rc_car", "low", "No specific hardware detected, using default preset"


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------


def print_scan_results(hw: dict, colors_class=None):
    """Print a human-readable scan report."""
    green = getattr(colors_class, "GREEN", "")
    warn = getattr(colors_class, "WARNING", "")
    blue = getattr(colors_class, "BLUE", "")
    bold = getattr(colors_class, "BOLD", "")
    end = getattr(colors_class, "ENDC", "")

    print(f"\n{bold}Hardware Scan Results{end}\n")
    print(f"  Platform: {blue}{hw['platform']}{end}")

    i2c = hw.get("i2c_devices", [])
    if i2c:
        print(f"\n  {green}I2C Devices ({len(i2c)}){end}")
        for d in i2c:
            name = d.get("device", "unknown")
            print(f"    Bus {d['bus']}: {d['address']}  [{name}]")
    else:
        print(f"\n  {warn}No I2C devices found{end}")

    serial = hw.get("usb_serial", [])
    if serial:
        print(f"\n  {green}USB Serial Ports ({len(serial)}){end}")
        for p in serial:
            print(f"    {p}")
    else:
        print(f"\n  {warn}No USB serial ports found{end}")

    cameras = hw.get("cameras", [])
    if cameras:
        print(f"\n  {green}Cameras ({len(cameras)}){end}")
        for c in cameras:
            status = "accessible" if c["accessible"] else "not accessible"
            print(f"    {c['type'].upper()}: {c['device']} ({status})")
    else:
        print(f"\n  {warn}No cameras found{end}")

    for category in (
        "realsense",
        "oakd",
        "odrive",
        "vesc",
        "dynamixel",
        "feetech",
        "arduino",
        "circuitpython",
        "lidar",
        "hailo",
        "coral",
        "imx500",
        "reachy",
    ):
        items = hw.get(category, [])
        if items:
            print(f"\n  {green}{category.upper()} ({len(items)}){end}")
            for item in items:
                print(f"    {item}")

    print()


# ---------------------------------------------------------------------------
# suggest_extras (#555)
# ---------------------------------------------------------------------------

#: Maps detected hardware keys to pip packages that may be needed.
_HARDWARE_EXTRAS: dict = {
    "oakd": ["depthai"],
    "reachy": ["reachy2-sdk", "zeroconf"],
    "feetech": ["feetech-servo-sdk"],
    "dynamixel": ["dynamixel-sdk"],
    "hailo": ["hailo-platform"],
    "coral": ["tflite-runtime"],
    "realsense": ["pyrealsense2"],
    "circuitpython": ["adafruit-circuitpython-bundle"],
    "arduino": [],  # no extra needed — standard serial
}


def suggest_extras(hw: dict) -> list[str]:
    """Return pip package suggestions for detected hardware that are not yet importable.

    Args:
        hw: Result dict from :func:`detect_hardware`.

    Returns:
        Ordered, deduplicated list of pip install strings, e.g. ``["depthai"]``.
    """
    suggestions: list[str] = []
    for hw_key, packages in _HARDWARE_EXTRAS.items():
        if hw.get(hw_key):
            for pkg in packages:
                import_name = pkg.replace("-", "_").split("[")[0]
                try:
                    __import__(import_name)
                except ImportError:
                    suggestions.append(pkg)

    # ── rplidar / ydlidar: package name depends on detected model ──────────
    rplidar_result = hw.get("rplidar")
    if isinstance(rplidar_result, dict) and rplidar_result.get("detected"):
        model = rplidar_result.get("model", "unknown_lidar")
        if model == "ydlidar":
            pkg = "ydlidar"
        elif model == "rplidar":
            pkg = "rplidar"
        else:
            pkg = None  # unknown_lidar — cannot determine package safely
        if pkg:
            try:
                __import__(pkg)
            except ImportError:
                if pkg not in suggestions:
                    suggestions.append(pkg)

    # ── i2c_devices: smbus2 ────────────────────────────────────────────────
    if hw.get("i2c"):
        if not HAS_SMBUS and "smbus2" not in suggestions:
            suggestions.append("smbus2")

    # ── rpi_ai_camera: picamera2 ───────────────────────────────────────────
    rpi_cam = hw.get("rpi_ai_camera")
    if isinstance(rpi_cam, dict) and rpi_cam.get("detected"):
        try:
            __import__("picamera2")
        except ImportError:
            if "picamera2" not in suggestions:
                suggestions.append("picamera2")

    # ── lerobot ────────────────────────────────────────────────────────────
    lerobot_result = hw.get("lerobot")
    if isinstance(lerobot_result, dict) and lerobot_result.get("compatible"):
        for pkg, import_name in [
            ("gym-pusht", "gym_pusht"),
            ("gym-aloha", "gym_aloha"),
            ("feetech-servo-sdk", "feetech_servo_sdk"),
        ]:
            try:
                __import__(import_name)
            except ImportError:
                if pkg not in suggestions:
                    suggestions.append(pkg)

    return list(dict.fromkeys(suggestions))  # deduplicate, preserve order
