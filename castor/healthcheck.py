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
    if simulate:
        checks.append({"name": "Hardware", "status": "skip", "detail": "Simulation mode"})
    else:
        checks.append(_check_camera(config))
        checks.append(_check_gpio())
        checks.append(_check_i2c())
        checks.append(_check_speaker(config))

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
