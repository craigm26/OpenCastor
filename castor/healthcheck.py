"""
Startup health check for OpenCastor runtime.

Performs hardware and software checks at boot, reports a system health card.
"""

import importlib
import logging
import os
import platform
import shutil
import time

logger = logging.getLogger("OpenCastor.HealthCheck")


def run_startup_checks(config: dict, simulate: bool = False) -> dict:
    """Run all startup health checks and return a results dict.

    Returns:
        {
            "status": "healthy" | "degraded" | "critical",
            "checks": [
                {"name": str, "status": "ok"|"warn"|"fail"|"skip", "detail": str},
                ...
            ],
            "summary": str,
            "duration_ms": float,
        }
    """
    start = time.time()
    checks = []

    # --- Software checks ---
    checks.append(_check_python_version())
    checks.append(_check_package_version())
    checks.append(_check_dependencies())
    checks.append(_check_config_valid(config))
    checks.append(_check_provider_auth(config))

    # --- Hardware checks (skip in simulation) ---
    # --- Hardware checks (skip in simulation) ---
    if simulate:
        checks.append({"name": "Hardware", "status": "skip", "detail": "Simulation mode"})
    else:
        checks.append(_check_camera(config))
        checks.append(_check_gpio())
        checks.append(_check_i2c())
        checks.append(_check_i2c_devices())
        checks.append(_check_spi())
        checks.append(_check_serial_ports())
        checks.append(_check_usb_devices())
        checks.append(_check_speaker(config))
        checks.append(_check_drivers())

    # --- System resource checks ---
    checks.append(_check_disk_space())
    checks.append(_check_memory())
    checks.append(_check_cpu_temp())

    # Determine overall status
    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        overall = "critical"
    elif "warn" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    duration_ms = (time.time() - start) * 1000

    ok_count = statuses.count("ok")
    warn_count = statuses.count("warn")
    fail_count = statuses.count("fail")
    skip_count = statuses.count("skip")

    summary = f"{ok_count} passed, {warn_count} warnings, {fail_count} failed, {skip_count} skipped"

    return {
        "status": overall,
        "checks": checks,
        "summary": summary,
        "duration_ms": round(duration_ms, 1),
    }


def print_health_report(result: dict) -> None:
    """Print a formatted health report to the console."""
    status_icons = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}
    overall_icons = {"healthy": "🟢", "degraded": "🟡", "critical": "🔴"}

    icon = overall_icons.get(result["status"], "❓")
    print(f"\n{'=' * 56}")
    print(f"  {icon} SYSTEM HEALTH CHECK — {result['status'].upper()}")
    print(f"{'=' * 56}")

    for check in result["checks"]:
        si = status_icons.get(check["status"], "?")
        print(f"  {si} {check['name']:<24s} {check['detail']}")

    print(f"{'─' * 56}")
    print(f"  {result['summary']} ({result['duration_ms']}ms)")
    print(f"{'=' * 56}\n")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_python_version():
    v = platform.python_version()
    major, minor = int(v.split(".")[0]), int(v.split(".")[1])
    if major >= 3 and minor >= 10:
        return {"name": "Python version", "status": "ok", "detail": f"v{v}"}
    return {"name": "Python version", "status": "warn", "detail": f"v{v} (3.10+ recommended)"}


def _check_package_version():
    try:
        from castor import __version__

        return {"name": "OpenCastor version", "status": "ok", "detail": f"v{__version__}"}
    except Exception:
        return {"name": "OpenCastor version", "status": "warn", "detail": "Unknown"}


def _check_dependencies():
    """Check that critical dependencies are importable."""
    required = ["yaml", "fastapi", "uvicorn"]
    missing = []
    for mod in required:
        try:
            importlib.import_module(mod)
        except ImportError:
            # yaml is imported as pyyaml
            if mod == "yaml":
                missing.append("pyyaml")
            else:
                missing.append(mod)

    if missing:
        return {
            "name": "Dependencies",
            "status": "warn",
            "detail": f"Missing: {', '.join(missing)}",
        }
    return {"name": "Dependencies", "status": "ok", "detail": "All core packages found"}


def _check_config_valid(config):
    """Basic config structure validation."""
    if not config:
        return {"name": "Config", "status": "fail", "detail": "Empty config"}
    metadata = config.get("metadata", {})
    agent = config.get("agent", {})
    if not metadata.get("robot_name"):
        return {"name": "Config", "status": "warn", "detail": "Missing robot_name"}
    if not agent.get("provider") and not agent.get("model"):
        return {"name": "Config", "status": "warn", "detail": "No AI provider configured"}
    name = metadata.get("robot_name", "?")
    return {"name": "Config", "status": "ok", "detail": f"'{name}' loaded"}


def _check_provider_auth(config):
    """Check if the AI provider credentials are available."""
    agent = config.get("agent", {})
    provider = agent.get("provider", "google")

    env_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
        "huggingface": "HF_TOKEN",
    }

    env_var = env_map.get(provider)
    if not env_var:
        return {"name": "AI Provider auth", "status": "ok", "detail": f"{provider} (no key needed)"}

    # Check OpenCastor token store first for Anthropic (takes priority over env)
    if provider == "anthropic":
        token_path = os.path.expanduser("~/.opencastor/anthropic-token")
        if os.path.exists(token_path):
            return {
                "name": "AI Provider auth",
                "status": "ok",
                "detail": "anthropic (setup-token stored)",
            }

    # Check env var
    if os.getenv(env_var):
        return {"name": "AI Provider auth", "status": "ok", "detail": f"{provider} ({env_var} set)"}

    # Check .env file
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if line.startswith(f"{env_var}="):
                    return {
                        "name": "AI Provider auth",
                        "status": "ok",
                        "detail": f"{provider} (in .env)",
                    }

    return {
        "name": "AI Provider auth",
        "status": "fail",
        "detail": f"{provider} — {env_var} not found",
    }


def _check_camera(config):
    """Check if a camera device is accessible."""
    try:
        # Check for CSI camera (Raspberry Pi)
        if os.path.exists("/dev/video0"):
            return {"name": "Camera", "status": "ok", "detail": "/dev/video0 detected"}
        # Check for any video device
        import glob

        devs = glob.glob("/dev/video*")
        if devs:
            return {"name": "Camera", "status": "ok", "detail": f"{devs[0]} detected"}
        return {"name": "Camera", "status": "warn", "detail": "No video device found"}
    except Exception as e:
        return {"name": "Camera", "status": "warn", "detail": str(e)}


def _check_gpio():
    """Check if GPIO is accessible (Raspberry Pi)."""
    if not os.path.exists("/sys/class/gpio"):
        return {"name": "GPIO", "status": "skip", "detail": "Not a GPIO-capable system"}
    try:
        # Check if we can access GPIO
        if os.access("/sys/class/gpio/export", os.W_OK):
            return {"name": "GPIO", "status": "ok", "detail": "Accessible"}
        return {"name": "GPIO", "status": "warn", "detail": "No write access (run as root?)"}
    except Exception as e:
        return {"name": "GPIO", "status": "warn", "detail": str(e)}


def _check_i2c():
    """Check if I2C bus is available (for PCA9685, sensors, etc.)."""
    if not os.path.exists("/dev/i2c-1"):
        if platform.machine().startswith("x86"):
            return {"name": "I2C", "status": "skip", "detail": "x86 system (no I2C expected)"}
        return {"name": "I2C", "status": "warn", "detail": "Not found — enable with raspi-config"}
    return {"name": "I2C", "status": "ok", "detail": "/dev/i2c-1 available"}


def _check_speaker(config):
    """Check if audio output is available."""
    if shutil.which("aplay"):
        return {"name": "Speaker/Audio", "status": "ok", "detail": "aplay available"}
    if shutil.which("paplay"):
        return {"name": "Speaker/Audio", "status": "ok", "detail": "PulseAudio available"}
    if platform.system() == "Darwin":
        return {"name": "Speaker/Audio", "status": "ok", "detail": "macOS audio"}
    return {"name": "Speaker/Audio", "status": "warn", "detail": "No audio output detected"}


def _check_disk_space():
    """Check available disk space."""
    try:
        stat = os.statvfs("/")
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        if free_gb < 0.5:
            return {"name": "Disk space", "status": "fail", "detail": f"{free_gb:.1f} GB free"}
        if free_gb < 2:
            return {"name": "Disk space", "status": "warn", "detail": f"{free_gb:.1f} GB free"}
        return {"name": "Disk space", "status": "ok", "detail": f"{free_gb:.1f} GB free"}
    except Exception:
        return {"name": "Disk space", "status": "ok", "detail": "Check skipped"}


def _check_memory():
    """Check available system memory."""
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        meminfo = {}
        for line in lines[:5]:
            parts = line.split(":")
            meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])
        total_mb = meminfo.get("MemTotal", 0) / 1024
        avail_mb = meminfo.get("MemAvailable", 0) / 1024
        if avail_mb < 100:
            return {
                "name": "Memory",
                "status": "fail",
                "detail": f"{avail_mb:.0f} MB free / {total_mb:.0f} MB total",
            }
        if avail_mb < 256:
            return {
                "name": "Memory",
                "status": "warn",
                "detail": f"{avail_mb:.0f} MB free / {total_mb:.0f} MB total",
            }
        return {
            "name": "Memory",
            "status": "ok",
            "detail": f"{avail_mb:.0f} MB free / {total_mb:.0f} MB total",
        }
    except FileNotFoundError:
        return {"name": "Memory", "status": "ok", "detail": "Check skipped (non-Linux)"}


def _check_cpu_temp():
    """Check CPU temperature (Raspberry Pi and Linux)."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp_c = int(f.read().strip()) / 1000
        if temp_c > 80:
            return {"name": "CPU temperature", "status": "fail", "detail": f"{temp_c:.1f}°C"}
        if temp_c > 70:
            return {"name": "CPU temperature", "status": "warn", "detail": f"{temp_c:.1f}°C"}
        return {"name": "CPU temperature", "status": "ok", "detail": f"{temp_c:.1f}°C"}
    except FileNotFoundError:
        return {"name": "CPU temperature", "status": "ok", "detail": "Sensor not available"}


def _check_usb_devices():
    """Enumerate USB devices connected to the system."""
    import subprocess

    try:
        result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return {"name": "USB devices", "status": "warn", "detail": "lsusb failed"}

        lines = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip()]
        # Filter out root hubs, show meaningful devices
        devices = []
        for line in lines:
            # Extract device description (after "ID xxxx:xxxx")
            parts = line.split("ID ")
            if len(parts) >= 2:
                desc = parts[1].split(" ", 1)
                name = desc[1] if len(desc) > 1 else desc[0]
                # Skip generic root hubs
                if "root hub" in name.lower():
                    continue
                devices.append(name.strip())

        if not devices:
            return {"name": "USB devices", "status": "ok", "detail": "No external USB devices"}

        # Show first 4, count rest
        shown = devices[:4]
        detail = "; ".join(shown)
        if len(devices) > 4:
            detail += f" (+{len(devices) - 4} more)"
        return {"name": "USB devices", "status": "ok", "detail": detail}
    except FileNotFoundError:
        return {"name": "USB devices", "status": "ok", "detail": "lsusb not available"}
    except Exception as e:
        return {"name": "USB devices", "status": "warn", "detail": str(e)[:60]}


def _check_i2c_devices():
    """Scan I2C bus for connected devices (sensors, PCA9685, etc.)."""
    import subprocess

    if not os.path.exists("/dev/i2c-1"):
        return {"name": "I2C devices", "status": "skip", "detail": "No I2C bus"}

    try:
        result = subprocess.run(["i2cdetect", "-y", "1"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return {"name": "I2C devices", "status": "warn", "detail": "i2cdetect failed"}

        # Parse addresses from i2cdetect output
        addresses = []
        known_devices = {
            "40": "PCA9685 (servo driver)",
            "41": "PCA9685 #2",
            "48": "ADS1115 (ADC)",
            "53": "BNO055 (IMU)",
            "68": "MPU6050 (IMU)",
            "69": "MPU6050 #2",
            "76": "BME280 (env sensor)",
            "77": "BME280/BMP280",
            "3c": "SSD1306 (OLED)",
            "27": "LCD (I2C)",
            "29": "VL53L0X (ToF)",
            "1a": "WM8960 (audio codec)",
            "50": "EEPROM/HAT ID",
        }
        for line in result.stdout.strip().split("\n")[1:]:
            # Each line: "00: -- -- -- 03 ..."
            parts = line.split(":")[1].strip().split() if ":" in line else []
            for part in parts:
                part = part.strip()
                if part != "--" and part != "UU" and len(part) == 2:
                    try:
                        int(part, 16)
                        addresses.append(part)
                    except ValueError:
                        pass

        if not addresses:
            return {"name": "I2C devices", "status": "ok", "detail": "No devices on bus 1"}

        # Map addresses to known devices
        found = []
        for addr in addresses:
            name = known_devices.get(addr, f"0x{addr}")
            found.append(name)

        detail = "; ".join(found[:4])
        if len(found) > 4:
            detail += f" (+{len(found) - 4} more)"
        return {"name": "I2C devices", "status": "ok", "detail": detail}
    except FileNotFoundError:
        return {"name": "I2C devices", "status": "ok", "detail": "i2cdetect not installed"}
    except Exception as e:
        return {"name": "I2C devices", "status": "warn", "detail": str(e)[:60]}


def _check_spi():
    """Check if SPI bus is available."""
    import glob

    spi_devs = glob.glob("/dev/spidev*")
    if spi_devs:
        names = ", ".join(os.path.basename(d) for d in spi_devs)
        return {"name": "SPI", "status": "ok", "detail": names}
    if platform.machine().startswith("x86"):
        return {"name": "SPI", "status": "skip", "detail": "x86 system"}
    return {"name": "SPI", "status": "warn", "detail": "Not enabled (raspi-config → Interfaces)"}


def _check_serial_ports():
    """Check for serial ports (UART, USB-serial adapters)."""
    import glob

    ports = []
    # Standard Pi UART
    for p in ["/dev/ttyAMA0", "/dev/ttyS0", "/dev/serial0"]:
        if os.path.exists(p):
            ports.append(os.path.basename(p))
    # USB serial adapters
    usb_serial = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
    for p in usb_serial:
        ports.append(os.path.basename(p))

    if not ports:
        if platform.machine().startswith("x86"):
            return {"name": "Serial ports", "status": "skip", "detail": "x86 system"}
        return {"name": "Serial ports", "status": "ok", "detail": "None detected"}
    return {"name": "Serial ports", "status": "ok", "detail": ", ".join(ports)}


def _check_drivers():
    """Check for loaded kernel modules relevant to robotics."""
    relevant_modules = {
        "i2c_dev": "I2C userspace",
        "i2c_bcm2835": "I2C (Pi)",
        "spi_bcm2835": "SPI (Pi)",
        "pwm_bcm2835": "Hardware PWM",
        "v4l2_common": "Video4Linux",
        "videobuf2_common": "Video buffers",
        "bcm2835_codec": "Pi camera codec",
        "bcm2835_isp": "Pi camera ISP",
        "gpio_cdev": "GPIO chardev",
        "snd_bcm2835": "Pi audio",
        "uvcvideo": "USB camera",
        "ch341": "CH341 USB-serial",
        "cp210x": "CP210x USB-serial",
        "ftdi_sio": "FTDI USB-serial",
        "cdc_acm": "USB ACM (Arduino)",
    }

    try:
        with open("/proc/modules") as f:
            loaded_names = {line.split()[0] for line in f}
    except FileNotFoundError:
        return {"name": "Kernel drivers", "status": "ok", "detail": "Check skipped (non-Linux)"}

    found = []
    for mod, label in relevant_modules.items():
        if mod in loaded_names:
            found.append(label)

    if not found:
        return {"name": "Kernel drivers", "status": "warn", "detail": "No robotics drivers loaded"}

    detail = "; ".join(found[:5])
    if len(found) > 5:
        detail += f" (+{len(found) - 5} more)"
    return {"name": "Kernel drivers", "status": "ok", "detail": detail}
