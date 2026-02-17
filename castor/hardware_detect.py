"""
OpenCastor Hardware Detection -- auto-detect connected hardware.

Scans I2C buses, USB serial ports, and camera devices to suggest
the most likely hardware preset for the wizard.

Usage (from wizard):
    from castor.hardware_detect import detect_hardware, suggest_preset
    results = detect_hardware()
    preset = suggest_preset(results)
"""

import logging
import os
import subprocess

logger = logging.getLogger("OpenCastor.HardwareDetect")


def scan_i2c() -> list:
    """Scan I2C buses for attached devices.

    Returns a list of dicts: ``{"bus": int, "address": "0xNN"}``.
    """
    devices = []

    # Find available I2C buses
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
                            devices.append({"bus": bus, "address": f"0x{part}"})
                        except ValueError:
                            pass
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return devices


def scan_usb_serial() -> list:
    """Find USB serial ports (common for Dynamixel, Arduino, etc.).

    Returns a list of port paths, e.g. ``["/dev/ttyUSB0", "/dev/ttyACM0"]``.
    """
    ports = []
    dev_dir = "/dev"
    if os.path.isdir(dev_dir):
        for entry in sorted(os.listdir(dev_dir)):
            if entry.startswith("ttyUSB") or entry.startswith("ttyACM"):
                ports.append(os.path.join(dev_dir, entry))
    return ports


def scan_cameras() -> list:
    """Detect available camera devices.

    Returns a list of dicts: ``{"type": "csi"|"usb", "device": str, "accessible": bool}``.
    """
    cameras = []

    # Check for CSI camera via video devices
    for entry in sorted(os.listdir("/dev")) if os.path.isdir("/dev") else []:
        if entry.startswith("video"):
            cameras.append(
                {
                    "type": "usb",
                    "device": f"/dev/{entry}",
                    "accessible": os.access(f"/dev/{entry}", os.R_OK),
                }
            )

    # Check for picamera2 availability (CSI)
    try:
        from picamera2 import Picamera2

        cam = Picamera2()
        cam.close()
        cameras.insert(0, {"type": "csi", "device": "CSI ribbon cable", "accessible": True})
    except Exception:
        pass

    return cameras


def detect_hardware() -> dict:
    """Run all hardware scans and return a combined result dict.

    Returns::

        {
            "i2c_devices": [...],
            "usb_serial": [...],
            "cameras": [...],
            "platform": "rpi"|"jetson"|"generic",
        }
    """
    result = {
        "i2c_devices": scan_i2c(),
        "usb_serial": scan_usb_serial(),
        "cameras": scan_cameras(),
        "platform": _detect_platform(),
    }
    return result


def _detect_platform() -> str:
    """Detect the current platform (Raspberry Pi, Jetson, or generic)."""
    # Check for Raspberry Pi
    try:
        with open("/proc/device-tree/model") as f:
            model = f.read().lower()
            if "raspberry pi" in model:
                return "rpi"
            if "jetson" in model:
                return "jetson"
    except (FileNotFoundError, PermissionError):
        pass

    return "generic"


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

    # PCA9685 at 0x40 + RPi -> rpi_rc_car
    if "0x40" in i2c_addrs and is_rpi:
        if has_camera:
            return "rpi_rc_car", "high", "PCA9685 at 0x40 + RPi + camera detected"
        return "rpi_rc_car", "medium", "PCA9685 at 0x40 + RPi detected (no camera)"

    # Serial port present -> likely Dynamixel
    if has_serial:
        return "dynamixel_arm", "medium", f"Serial port detected: {hw['usb_serial'][0]}"

    # RPi but no I2C -> generic Amazon kit
    if is_rpi:
        return "amazon_kit_generic", "low", "Raspberry Pi detected, no specific hardware found"

    # No specific hardware detected
    return "rpi_rc_car", "low", "No specific hardware detected, using default preset"


def print_scan_results(hw: dict, colors_class=None):
    """Print a human-readable scan report."""
    green = getattr(colors_class, "GREEN", "")
    warn = getattr(colors_class, "WARNING", "")
    blue = getattr(colors_class, "BLUE", "")
    bold = getattr(colors_class, "BOLD", "")
    end = getattr(colors_class, "ENDC", "")

    print(f"\n{bold}Hardware Scan Results{end}\n")

    # Platform
    print(f"  Platform: {blue}{hw['platform']}{end}")

    # I2C
    i2c = hw.get("i2c_devices", [])
    if i2c:
        print(f"\n  {green}I2C Devices ({len(i2c)}){end}")
        for d in i2c:
            print(f"    Bus {d['bus']}: {d['address']}")
    else:
        print(f"\n  {warn}No I2C devices found{end}")

    # USB Serial
    serial = hw.get("usb_serial", [])
    if serial:
        print(f"\n  {green}USB Serial Ports ({len(serial)}){end}")
        for p in serial:
            print(f"    {p}")
    else:
        print(f"\n  {warn}No USB serial ports found{end}")

    # Cameras
    cameras = hw.get("cameras", [])
    if cameras:
        print(f"\n  {green}Cameras ({len(cameras)}){end}")
        for c in cameras:
            status = "accessible" if c["accessible"] else "not accessible"
            print(f"    {c['type'].upper()}: {c['device']} ({status})")
    else:
        print(f"\n  {warn}No cameras found{end}")

    print()
