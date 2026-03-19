"""
castor.daemon — systemd service management for auto-starting the OpenCastor gateway.

Usage via CLI:
    castor daemon enable [--config bob.rcan.yaml]
    castor daemon disable
    castor daemon status
    castor daemon logs [--lines 50]
    castor daemon restart
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Optional

import yaml

SERVICE_NAME = "castor-gateway"
SERVICE_PATH = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
DASHBOARD_SERVICE_NAME = "castor-dashboard"
DASHBOARD_SERVICE_PATH = Path(f"/etc/systemd/system/{DASHBOARD_SERVICE_NAME}.service")
ATTESTATION_SERVICE_NAME = "opencastor-attestation"
ATTESTATION_SERVICE_PATH = Path(f"/etc/systemd/system/{ATTESTATION_SERVICE_NAME}.service")
SECURITY_INSTALL_PATH = Path("/etc/opencastor/security")


def _systemd_path(path_value: str) -> str:
    """Render a path in POSIX form for systemd unit files."""
    raw = str(path_value or "")
    if raw.startswith("/"):
        return raw.replace("\\", "/")
    return str(Path(raw).resolve()).replace("\\", "/")


# ── Service file generation ────────────────────────────────────────────────────


def generate_service_file(
    config_path: str,
    user: Optional[str] = None,
    venv_path: Optional[str] = None,
    working_dir: Optional[str] = None,
    security_profile: Optional[str] = None,
) -> str:
    """Generate a systemd .service file for the OpenCastor gateway.

    Args:
        config_path: Path to the RCAN config file (e.g. ``bob.rcan.yaml``).
        user:        System user to run the service as. Defaults to current user.
        venv_path:   Path to the Python venv. Auto-detected from sys.prefix if omitted.
        working_dir: Working directory for the service. Defaults to config file's parent.
        security_profile: Service security profile. Supports ``hardened`` or
                          ``permissive``. If omitted, reads
                          ``service.security_profile`` from config.
    """
    user = user or os.environ.get("USER", "pi")
    venv_path = venv_path or sys.prefix
    config_abs = _systemd_path(config_path)
    if working_dir is None:
        working_dir = str(PurePosixPath(config_abs).parent)
    else:
        working_dir = _systemd_path(working_dir)
    venv_root = _systemd_path(venv_path).rstrip("/")
    security_profile = (security_profile or _get_security_profile(config_abs)).strip().lower()

    if security_profile not in {"hardened", "permissive"}:
        security_profile = "hardened"

    hardened_block = ""
    if security_profile == "hardened":
        runtime_dir = f"{working_dir.rstrip('/')}/.castor"
        hardened_block = f"""

# Hardened baseline (set service.security_profile: permissive to opt out)
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
DevicePolicy=closed
# Allow only explicitly required robot devices.
DeviceAllow=/dev/null rw
DeviceAllow=/dev/zero rw
DeviceAllow=/dev/full rw
DeviceAllow=/dev/random rw
DeviceAllow=/dev/urandom rw
DeviceAllow=/dev/tty rw
DeviceAllow=/dev/ttyAMA0 rw
DeviceAllow=/dev/ttyS0 rw
DeviceAllow=/dev/ttyUSB0 rw
DeviceAllow=/dev/ttyACM0 rw
DeviceAllow=/dev/i2c-1 rw
DeviceAllow=/dev/spidev0.0 rw
DeviceAllow=/dev/spidev0.1 rw
DeviceAllow=/dev/gpiochip0 rw
DeviceAllow=/dev/video0 rw
ReadWritePaths={runtime_dir}
AppArmorProfile=opencastor-gateway
SystemCallFilter=@system-service @network-io @file-system @io-event
SystemCallFilter=~@mount @swap @clock @cpu-emulation @obsolete
SystemCallErrorNumber=EPERM
"""

    # Use python -m castor.cli so the venv path is not hardcoded (#549)
    python_bin = f"{venv_root}/bin/python"

    return f"""\
[Unit]
Description=OpenCastor Gateway — {Path(config_abs).stem}
Documentation=https://docs.opencastor.com
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={working_dir}
Environment=PYTHONUNBUFFERED=1
Environment=OPENCASTOR_VENV={venv_root}
ExecStartPre=/bin/sh -c 'fuser -k 8000/tcp 2>/dev/null || true'
ExecStart={python_bin} -m castor.cli gateway --config {config_abs}
Restart=on-failure
RestartSec=5s
KillMode=control-group
KillSignal=SIGTERM
TimeoutStopSec=15
SendSIGKILL=yes
StandardOutput=journal
StandardError=journal
SyslogIdentifier={SERVICE_NAME}

# Limit memory so the robot doesn't OOM the Pi
MemoryMax=1G{hardened_block}

[Install]
WantedBy=multi-user.target
"""


def _get_security_profile(config_path: str) -> str:
    """Read service.security_profile from config, defaulting to hardened."""
    try:
        with open(config_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return "hardened"

    service = data.get("service") if isinstance(data, dict) else None
    profile = service.get("security_profile") if isinstance(service, dict) else None
    return str(profile) if profile else "hardened"


def generate_driver_worker_units(
    config_path: str, working_dir: Optional[str] = None
) -> dict[str, str]:
    """Generate hardened per-driver worker unit files.

    Each driver receives a dedicated ``User=``/``Group=`` identity suggestion,
    ``DevicePolicy=closed``, and a minimal ``DeviceAllow=`` set inferred from
    protocol + explicit config (e.g. serial ``port``).
    """
    with open(config_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    drivers = data.get("drivers", []) if isinstance(data, dict) else []
    workdir = working_dir or str(Path(config_path).resolve().parent)
    config_abs = str(Path(config_path).resolve())
    units: dict[str, str] = {}

    for index, drv in enumerate(drivers):
        if not isinstance(drv, dict):
            continue
        drv_id = str(drv.get("id") or f"driver{index}")
        protocol = str(drv.get("protocol") or "unknown")
        user = f"castor-drv-{drv_id}"
        group = user
        service_name = f"castor-driver@{drv_id}.service"
        device_allows = "\n".join(
            f"DeviceAllow={node} rw" for node in _device_nodes_for_driver(protocol, drv)
        )

        units[service_name] = f"""\
[Unit]
Description=OpenCastor isolated driver worker ({drv_id} / {protocol})
After=network.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={workdir}
Environment=PYTHONUNBUFFERED=1
ExecStart={sys.prefix}/bin/python -m castor.drivers.worker --config {config_abs} --driver-id {drv_id}
Restart=on-failure
RestartSec=2s
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX
PrivateNetwork=true
DevicePolicy=closed
AppArmorProfile=opencastor-driver
SystemCallFilter=@basic-io @file-system @signal
SystemCallFilter=~@mount @network-io @privileged @resources @raw-io @debug
SystemCallErrorNumber=EPERM
{device_allows}

[Install]
WantedBy=multi-user.target
"""

    return units


def _device_nodes_for_driver(protocol: str, drv_cfg: dict) -> list[str]:
    nodes = {"/dev/null", "/dev/zero", "/dev/random", "/dev/urandom"}
    proto = protocol.lower()
    if "pca9685" in proto:
        nodes.add(str(drv_cfg.get("port") or "/dev/i2c-1"))
    if proto in {"gpio", "stepper"}:
        nodes.add("/dev/gpiochip0")
    if "dynamixel" in proto or proto in {"odrive", "vesc", "lidar"}:
        port = drv_cfg.get("port")
        if port:
            nodes.add(str(port))
        else:
            nodes.update({"/dev/ttyUSB0", "/dev/ttyACM0"})
    if proto == "imu":
        nodes.add("/dev/i2c-1")
    return sorted(nodes)


# ── Install / remove ──────────────────────────────────────────────────────────


def enable_daemon(
    config_path: str,
    user: Optional[str] = None,
    venv_path: Optional[str] = None,
    working_dir: Optional[str] = None,
) -> dict:
    """Install and enable the systemd service.

    Returns a status dict with ``ok``, ``message``, and ``service_path``.
    """
    service_content = generate_service_file(config_path, user, venv_path, working_dir)
    _install_security_profiles()

    try:
        SERVICE_PATH.write_text(service_content)
    except PermissionError:
        # Fall back to sudo write
        proc = subprocess.run(
            ["sudo", "tee", str(SERVICE_PATH)],
            input=service_content.encode(),
            capture_output=True,
        )
        if proc.returncode != 0:
            return {"ok": False, "message": f"Could not write service file: {proc.stderr.decode()}"}

    _run(["sudo", "systemctl", "daemon-reload"])
    _run(["sudo", "systemctl", "enable", SERVICE_NAME])
    result = _run(["sudo", "systemctl", "start", SERVICE_NAME])

    return {
        "ok": result.returncode == 0,
        "message": "Service enabled and started"
        if result.returncode == 0
        else result.stderr.decode(),
        "service_path": str(SERVICE_PATH),
    }


def disable_daemon() -> dict:
    """Stop and disable the systemd service, and remove the service file."""
    _run(["sudo", "systemctl", "stop", SERVICE_NAME])
    _run(["sudo", "systemctl", "disable", SERVICE_NAME])

    removed = False
    if SERVICE_PATH.exists():
        try:
            SERVICE_PATH.unlink()
            removed = True
        except PermissionError:
            result = _run(["sudo", "rm", "-f", str(SERVICE_PATH)])
            removed = result.returncode == 0

    _run(["sudo", "systemctl", "daemon-reload"])
    return {"ok": True, "removed": removed, "message": "Service stopped and disabled"}


# ── Status / logs ─────────────────────────────────────────────────────────────


def daemon_status() -> dict:
    """Return a status dict describing the service state."""
    if not shutil.which("systemctl"):
        return {"available": False, "message": "systemctl not found — not a systemd system"}

    installed = SERVICE_PATH.exists()

    result = _run(
        [
            "systemctl",
            "show",
            SERVICE_NAME,
            "--no-pager",
            "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp",
        ],
        check=False,
    )

    props: dict[str, str] = {}
    for line in result.stdout.decode().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            props[k.strip()] = v.strip()

    enabled_result = _run(["systemctl", "is-enabled", SERVICE_NAME], check=False)
    enabled = enabled_result.stdout.decode().strip() == "enabled"

    return {
        "available": True,
        "installed": installed,
        "enabled": enabled,
        "running": props.get("ActiveState") == "active" and props.get("SubState") == "running",
        "pid": props.get("MainPID", "0") if props.get("MainPID", "0") != "0" else None,
        "started": props.get("ExecMainStartTimestamp", ""),
        "service_path": str(SERVICE_PATH) if installed else None,
    }


def daemon_logs(lines: int = 50) -> str:
    """Return recent journal logs for the service."""
    if not shutil.which("journalctl"):
        return "(journalctl not available)"
    result = _run(
        ["journalctl", "-u", SERVICE_NAME, f"-n{lines}", "--no-pager"],
        check=False,
    )
    return result.stdout.decode()


def daemon_security_status() -> dict:
    """Return whether MAC/seccomp protections are present and active."""
    status = {
        "profiles_installed": SECURITY_INSTALL_PATH.exists(),
        "apparmor_profile": None,
        "seccomp_mode": None,
        "enabled_in_unit": False,
    }

    if SERVICE_PATH.exists():
        unit_text = SERVICE_PATH.read_text(encoding="utf-8")
        status["enabled_in_unit"] = (
            "AppArmorProfile=opencastor-gateway" in unit_text and "SystemCallFilter=" in unit_text
        )

    service = daemon_status()
    pid = service.get("pid") if isinstance(service, dict) else None
    if not pid:
        return status

    attr_current = Path(f"/proc/{pid}/attr/current")
    proc_status = Path(f"/proc/{pid}/status")

    if attr_current.exists():
        status["apparmor_profile"] = attr_current.read_text(encoding="utf-8").strip()

    if proc_status.exists():
        for line in proc_status.read_text(encoding="utf-8").splitlines():
            if line.startswith("Seccomp:"):
                status["seccomp_mode"] = line.split(":", 1)[1].strip()
                break

    return status


# ── Dashboard service ─────────────────────────────────────────────────────────


def generate_dashboard_service_file(
    user: Optional[str] = None,
    venv_path: Optional[str] = None,
    working_dir: Optional[str] = None,
    port: int = 8501,
) -> str:
    """Generate a systemd .service file for the CastorDash Streamlit dashboard."""
    user = user or os.environ.get("USER", "pi")
    venv_root = _systemd_path(venv_path or sys.prefix).rstrip("/")
    castor_pkg = Path(__file__).resolve().parent
    dashboard_py = str(castor_pkg / "dashboard.py")
    workdir = _systemd_path(working_dir or str(castor_pkg.parent))

    # Use python -m streamlit so the venv path is not hardcoded (#549, #550)
    python_bin = f"{venv_root}/bin/python"

    return f"""\
[Unit]
Description=OpenCastor Dashboard (CastorDash)
Documentation=https://docs.opencastor.com
After=network-online.target {SERVICE_NAME}.service
Wants=network-online.target
PartOf={SERVICE_NAME}.service

[Service]
Type=simple
User={user}
WorkingDirectory={workdir}
Environment=PYTHONUNBUFFERED=1
Environment=OPENCASTOR_VENV={venv_root}
ExecStartPre=/bin/sh -c 'fuser -k {port}/tcp 2>/dev/null || true'
ExecStart={python_bin} -m streamlit run {dashboard_py} \\
    --server.port {port} \\
    --server.address 0.0.0.0 \\
    --server.headless true \\
    --server.fileWatcherType none
Restart=always
RestartSec=5s
KillMode=control-group
KillSignal=SIGTERM
TimeoutStopSec=15
SendSIGKILL=yes
StandardOutput=journal
StandardError=journal
SyslogIdentifier={DASHBOARD_SERVICE_NAME}

[Install]
WantedBy=multi-user.target
"""


def enable_dashboard(
    user: Optional[str] = None,
    venv_path: Optional[str] = None,
    working_dir: Optional[str] = None,
    port: int = 8501,
) -> dict:
    """Install and enable the CastorDash systemd service.

    Returns a status dict with ``ok``, ``message``, and ``service_path``.
    """
    service_content = generate_dashboard_service_file(user, venv_path, working_dir, port)

    try:
        DASHBOARD_SERVICE_PATH.write_text(service_content)
    except PermissionError:
        proc = subprocess.run(
            ["sudo", "tee", str(DASHBOARD_SERVICE_PATH)],
            input=service_content.encode(),
            capture_output=True,
        )
        if proc.returncode != 0:
            return {
                "ok": False,
                "message": f"Could not write dashboard service file: {proc.stderr.decode()}",
            }

    _run(["sudo", "systemctl", "daemon-reload"])
    _run(["sudo", "systemctl", "enable", DASHBOARD_SERVICE_NAME])
    result = _run(["sudo", "systemctl", "start", DASHBOARD_SERVICE_NAME])

    return {
        "ok": result.returncode == 0,
        "message": "Dashboard service enabled and started"
        if result.returncode == 0
        else result.stderr.decode(),
        "service_path": str(DASHBOARD_SERVICE_PATH),
    }


def disable_dashboard() -> dict:
    """Stop and disable the dashboard service, and remove the service file."""
    _run(["sudo", "systemctl", "stop", DASHBOARD_SERVICE_NAME])
    _run(["sudo", "systemctl", "disable", DASHBOARD_SERVICE_NAME])

    removed = False
    if DASHBOARD_SERVICE_PATH.exists():
        try:
            DASHBOARD_SERVICE_PATH.unlink()
            removed = True
        except PermissionError:
            result = _run(["sudo", "rm", "-f", str(DASHBOARD_SERVICE_PATH)])
            removed = result.returncode == 0

    _run(["sudo", "systemctl", "daemon-reload"])
    return {"ok": True, "removed": removed, "message": "Dashboard service stopped and disabled"}


# ── Attestation service ──────────────────────────────────────────────────


def generate_attestation_service_file(
    config_path: str,
    user: Optional[str] = None,
    python_path: Optional[str] = None,
    working_dir: Optional[str] = None,
) -> str:
    """Generate the opencastor-attestation.service content.

    Args:
        config_path: Path to the RCAN config file.
        user:        System user. Defaults to current user.
        python_path: Python interpreter path. Auto-detected if omitted.
        working_dir: Working directory. Defaults to config file's parent.
    """
    user = user or os.environ.get("USER", "pi")
    python_bin = python_path or f"{_systemd_path(sys.prefix).rstrip('/')}/bin/python"
    config_abs = _systemd_path(config_path)
    if working_dir is None:
        working_dir = str(PurePosixPath(config_abs).parent)
    else:
        working_dir = _systemd_path(working_dir)

    # Try loading from template file first
    try:
        from castor.templates import load_template

        tmpl = load_template("systemd/opencastor-attestation.service.tmpl")
        return tmpl.format(
            user=user,
            python=python_bin,
            config=config_abs,
            working_dir=working_dir,
        )
    except Exception:
        pass

    # Inline fallback
    return f"""\
[Unit]
Description=OpenCastor Software Attestation
Before=castor-gateway.service opencastor.service
After=network.target

[Service]
Type=oneshot
User={user}
WorkingDirectory={working_dir}
ExecStartPre=/bin/mkdir -p /run/opencastor
ExecStart={python_bin} -m castor.attestation_generator --config {config_abs} --out /run/opencastor/attestation.json
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal
SyslogIdentifier={ATTESTATION_SERVICE_NAME}

[Install]
WantedBy=multi-user.target
"""


def enable_attestation_service(
    config_path: str,
    user: Optional[str] = None,
    python_path: Optional[str] = None,
    working_dir: Optional[str] = None,
) -> dict:
    """Install, enable, and start the attestation service.

    Returns a status dict with ``ok``, ``message``, and ``service_path``.
    """
    service_content = generate_attestation_service_file(
        config_path, user, python_path, working_dir
    )

    try:
        ATTESTATION_SERVICE_PATH.write_text(service_content)
    except PermissionError:
        proc = subprocess.run(
            ["sudo", "tee", str(ATTESTATION_SERVICE_PATH)],
            input=service_content.encode(),
            capture_output=True,
        )
        if proc.returncode != 0:
            return {
                "ok": False,
                "message": f"Could not write attestation service file: {proc.stderr.decode()}",
            }

    _run(["sudo", "systemctl", "daemon-reload"])
    _run(["sudo", "systemctl", "enable", ATTESTATION_SERVICE_NAME])
    result = _run(["sudo", "systemctl", "start", ATTESTATION_SERVICE_NAME])

    return {
        "ok": result.returncode == 0,
        "message": "Attestation service enabled and started"
        if result.returncode == 0
        else result.stderr.decode(),
        "service_path": str(ATTESTATION_SERVICE_PATH),
    }


def status_attestation_service() -> dict:
    """Return status of the attestation service."""
    if not shutil.which("systemctl"):
        return {"available": False, "message": "systemctl not found"}

    installed = ATTESTATION_SERVICE_PATH.exists()

    result = _run(
        [
            "systemctl",
            "show",
            ATTESTATION_SERVICE_NAME,
            "--no-pager",
            "--property=ActiveState,SubState,Result",
        ],
        check=False,
    )

    props: dict[str, str] = {}
    for line in result.stdout.decode().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            props[k.strip()] = v.strip()

    return {
        "available": True,
        "installed": installed,
        "active_state": props.get("ActiveState", "unknown"),
        "result": props.get("Result", "unknown"),
        "service_path": str(ATTESTATION_SERVICE_PATH) if installed else None,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(cmd: list[str], check: bool = False, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, check=check, **kwargs)


def _install_security_profiles() -> None:
    script = Path(__file__).resolve().parent.parent / "deploy" / "security" / "install_profiles.sh"
    if not script.exists():
        return
    runner = ["bash", str(script)]
    if os.geteuid() != 0:
        runner = ["sudo", *runner]
    _run(runner, check=False)
