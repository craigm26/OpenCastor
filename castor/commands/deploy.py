"""
castor/commands/deploy.py — SSH-push RCAN config and restart service on remote Pi.

``castor deploy <host> --config robot.rcan.yaml``   Push config, restart service
``castor deploy <host> --full``                      pip install + config + restart
``castor deploy <host> --status``                    Show remote service status
``castor deploy <host> --dry-run``                   Preview without executing

Known hosts are stored in ``~/.castor/hosts.json`` for tab-completion and re-use.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Deploy")

_HOSTS_FILE = Path.home() / ".castor" / "hosts.json"
_DEFAULT_SERVICE_NAMES = ["opencastor", "castor"]
_REMOTE_DIR = "~/OpenCastor"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def cmd_deploy(args) -> None:
    """Deploy OpenCastor config (and optionally the package) to a remote host.

    Flags
    -----
    host        ``user@hostname`` or ``hostname`` (default user: pi)
    --config    Local ``.rcan.yaml`` to push (default: robot.rcan.yaml)
    --full      Also run ``pip install -e .`` on remote before restart
    --status    Only show remote service status, no deployment
    --dry-run   Print commands without executing
    --port      SSH port (default: 22)
    --key       Path to SSH private key file
    --no-restart   Push config only, skip service restart
    """
    host_arg: str = getattr(args, "host", "")
    config_path: str = getattr(args, "config", "robot.rcan.yaml")
    full_install: bool = getattr(args, "full", False)
    status_only: bool = getattr(args, "status", False)
    dry_run: bool = getattr(args, "dry_run", False)
    ssh_port: int = int(getattr(args, "port", 22))
    key_file: Optional[str] = getattr(args, "key", None)
    no_restart: bool = getattr(args, "no_restart", False)

    if not host_arg:
        print("  Error: host is required. Usage: castor deploy pi@192.168.1.10 --config robot.rcan.yaml")
        sys.exit(1)

    user, host = _parse_host(host_arg)
    _save_host(host, user=user, port=ssh_port)

    if status_only:
        _remote_status(user, host, ssh_port, key_file, dry_run)
        return

    # Validate config file exists
    if not os.path.exists(config_path):
        print(f"  Error: config file not found: {config_path}")
        sys.exit(1)

    print(f"  Deploying to {user}@{host}:{ssh_port}")
    print(f"  Config: {config_path}")

    # 1. Push RCAN config
    _push_config(config_path, user, host, ssh_port, key_file, dry_run)

    # 2. Optional: pip install
    if full_install:
        _remote_pip_install(user, host, ssh_port, key_file, dry_run)

    # 3. Restart service
    if not no_restart:
        _restart_service(user, host, ssh_port, key_file, dry_run)
    else:
        print("  Skipping service restart (--no-restart).")

    if not dry_run:
        print("\n  Deployment complete.")
    else:
        print("\n  (dry-run — no changes made)")


# ---------------------------------------------------------------------------
# Deployment steps
# ---------------------------------------------------------------------------


def _push_config(
    local_path: str,
    user: str,
    host: str,
    port: int,
    key_file: Optional[str],
    dry_run: bool,
) -> None:
    """SCP the RCAN config to the remote host."""
    remote_dest = f"{user}@{host}:{_REMOTE_DIR}/"
    scp_cmd = _build_scp_cmd(key_file, port, local_path, remote_dest)

    print(f"  Pushing config: {local_path} → {remote_dest}")
    _run_or_print(scp_cmd, dry_run=dry_run, label="scp config")


def _remote_pip_install(
    user: str,
    host: str,
    port: int,
    key_file: Optional[str],
    dry_run: bool,
) -> None:
    """Run pip install -e . on the remote host."""
    remote_cmd = (
        f"cd {_REMOTE_DIR} && "
        "source ~/opencastor-env/bin/activate 2>/dev/null || true && "
        "pip install -e . -q"
    )
    print("  Running remote pip install...")
    _run_remote(user, host, port, key_file, remote_cmd, dry_run, label="pip install")


def _restart_service(
    user: str,
    host: str,
    port: int,
    key_file: Optional[str],
    dry_run: bool,
) -> None:
    """Detect and restart the OpenCastor systemd service on remote host."""
    # Try known service names in order
    detect_cmd = " || ".join(
        f"systemctl --user is-active {name}.service 2>/dev/null && echo {name}"
        for name in _DEFAULT_SERVICE_NAMES
    )
    restart_cmd = (
        f"SERVICE=$({detect_cmd}); "
        "if [ -n \"$SERVICE\" ]; then "
        "  systemctl --user restart $SERVICE && echo \"Restarted $SERVICE\"; "
        "else "
        "  echo 'No OpenCastor service found — skipping restart'; "
        "fi"
    )
    print("  Restarting remote service...")
    _run_remote(user, host, port, key_file, restart_cmd, dry_run, label="restart service")


def _remote_status(
    user: str,
    host: str,
    port: int,
    key_file: Optional[str],
    dry_run: bool,
) -> None:
    """Show the status of the OpenCastor service on remote host."""
    status_cmd = " ; ".join(
        f"systemctl --user status {name}.service 2>/dev/null && exit 0"
        for name in _DEFAULT_SERVICE_NAMES
    ) + "; echo 'No OpenCastor service found'"
    _run_remote(user, host, port, key_file, status_cmd, dry_run, label="service status")


# ---------------------------------------------------------------------------
# SSH/SCP helpers
# ---------------------------------------------------------------------------


def _build_ssh_cmd(
    key_file: Optional[str],
    port: int,
    user: str,
    host: str,
    remote_cmd: str,
) -> List[str]:
    cmd = ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
    if key_file:
        cmd += ["-i", key_file]
    cmd += [f"{user}@{host}", remote_cmd]
    return cmd


def _build_scp_cmd(
    key_file: Optional[str],
    port: int,
    local_src: str,
    remote_dest: str,
) -> List[str]:
    cmd = ["scp", "-P", str(port), "-o", "StrictHostKeyChecking=no"]
    if key_file:
        cmd += ["-i", key_file]
    cmd += [local_src, remote_dest]
    return cmd


def _run_remote(
    user: str,
    host: str,
    port: int,
    key_file: Optional[str],
    remote_cmd: str,
    dry_run: bool,
    label: str = "",
) -> None:
    ssh_cmd = _build_ssh_cmd(key_file, port, user, host, remote_cmd)
    _run_or_print(ssh_cmd, dry_run=dry_run, label=label)


def _run_or_print(cmd: List[str], dry_run: bool, label: str = "") -> None:
    print(f"  $ {' '.join(cmd)}")
    if dry_run:
        return
    try:
        result = subprocess.run(cmd, capture_output=False, timeout=60)
        if result.returncode != 0:
            logger.warning("Command failed (exit %d): %s", result.returncode, label)
    except subprocess.TimeoutExpired:
        print(f"  Warning: command timed out: {label}")
    except FileNotFoundError as exc:
        print(f"  Error: {exc.filename} not found. Install ssh/scp.")


# ---------------------------------------------------------------------------
# Host persistence
# ---------------------------------------------------------------------------


def _parse_host(host_arg: str) -> tuple[str, str]:
    """Parse user@host into (user, host). Default user is 'pi'."""
    if "@" in host_arg:
        user, host = host_arg.split("@", 1)
        return user, host
    return "pi", host_arg


def _save_host(host: str, user: str = "pi", port: int = 22) -> None:
    """Save host to ~/.castor/hosts.json for reuse."""
    try:
        _HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        hosts: Dict[str, Any] = {}
        if _HOSTS_FILE.exists():
            hosts = json.loads(_HOSTS_FILE.read_text())
        hosts[host] = {"user": user, "port": port}
        _HOSTS_FILE.write_text(json.dumps(hosts, indent=2))
    except Exception as exc:
        logger.debug("Could not save host: %s", exc)


def load_known_hosts() -> Dict[str, Any]:
    """Load known hosts from ~/.castor/hosts.json."""
    if not _HOSTS_FILE.exists():
        return {}
    try:
        return json.loads(_HOSTS_FILE.read_text())
    except Exception:
        return {}
