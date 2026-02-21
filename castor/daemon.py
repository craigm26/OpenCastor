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
from pathlib import Path
from typing import Optional

SERVICE_NAME = "castor-gateway"
SERVICE_PATH = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")


# ── Service file generation ────────────────────────────────────────────────────


def generate_service_file(
    config_path: str,
    user: Optional[str] = None,
    venv_path: Optional[str] = None,
    working_dir: Optional[str] = None,
) -> str:
    """Generate a systemd .service file for the OpenCastor gateway.

    Args:
        config_path: Path to the RCAN config file (e.g. ``bob.rcan.yaml``).
        user:        System user to run the service as. Defaults to current user.
        venv_path:   Path to the Python venv. Auto-detected from sys.prefix if omitted.
        working_dir: Working directory for the service. Defaults to config file's parent.
    """
    user = user or os.environ.get("USER", "pi")
    venv_path = venv_path or sys.prefix
    config_abs = str(Path(config_path).resolve())
    working_dir = working_dir or str(Path(config_abs).parent)
    castor_bin = str(Path(venv_path) / "bin" / "castor")

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
ExecStart={castor_bin} gateway --config {config_abs}
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
SyslogIdentifier={SERVICE_NAME}

# Limit memory so the robot doesn't OOM the Pi
MemoryMax=1G

[Install]
WantedBy=multi-user.target
"""


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
        "message": "Service enabled and started" if result.returncode == 0 else result.stderr.decode(),
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
        ["systemctl", "show", SERVICE_NAME, "--no-pager",
         "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp"],
        check=False,
    )

    props: dict[str, str] = {}
    for line in result.stdout.decode().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            props[k.strip()] = v.strip()

    enabled_result = _run(
        ["systemctl", "is-enabled", SERVICE_NAME], check=False
    )
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(cmd: list[str], check: bool = False, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, check=check, **kwargs)
