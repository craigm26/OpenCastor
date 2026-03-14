"""
LeRobot bridge for SO-ARM101 setup.

When LeRobot is installed (e.g. ~/lerobot/.venv on alex.local),
we delegate to its CLI tools rather than reimplementing the protocol:

  lerobot-find-port       → port detection
  lerobot-setup-motors    → motor ID + baudrate setup
  lerobot-calibrate       → joint calibration

Falls back to native castor implementation if LeRobot is not found.

Detection order:
  1. Active venv (sys.executable sibling)
  2. ~/lerobot/.venv/bin/
  3. ~/.venv/bin/
  4. /usr/local/bin/ (system install)
  5. PATH
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ── Locate LeRobot venv ───────────────────────────────────────────────────────

LEROBOT_VENV_CANDIDATES = [
    Path.home() / "lerobot" / ".venv",
    Path.home() / ".venv",
    Path("/opt/lerobot/.venv"),
]


def find_lerobot_bin(tool: str) -> Optional[Path]:
    """
    Locate a LeRobot CLI tool binary.

    Returns the full path if found, else None.
    """
    # 1. Current active venv
    current_venv = os.environ.get("VIRTUAL_ENV")
    if current_venv:
        candidate = Path(current_venv) / "bin" / tool
        if candidate.exists():
            return candidate

    # 2. Known candidate venvs
    for venv in LEROBOT_VENV_CANDIDATES:
        candidate = venv / "bin" / tool
        if candidate.exists():
            return candidate

    # 3. PATH
    found = shutil.which(tool)
    if found:
        return Path(found)

    return None


def lerobot_available() -> bool:
    """Return True if lerobot-find-port is discoverable."""
    return find_lerobot_bin("lerobot-find-port") is not None


def lerobot_venv_path() -> Optional[Path]:
    """Return the LeRobot venv directory if found."""
    for venv in LEROBOT_VENV_CANDIDATES:
        if (venv / "bin" / "python").exists():
            return venv
    current = os.environ.get("VIRTUAL_ENV")
    if current:
        return Path(current)
    return None


# ── LeRobot tool wrappers ─────────────────────────────────────────────────────

def run_find_port(print_fn=print, input_fn=input) -> Optional[str]:
    """
    Run lerobot-find-port interactively to identify a port.

    Returns the detected port string, or None.
    """
    tool = find_lerobot_bin("lerobot-find-port")
    if not tool:
        return None

    print_fn(f"\n[LeRobot] Running: {tool}")
    print_fn("  Follow the prompts: disconnect USB when asked, reconnect when done.\n")

    try:
        result = subprocess.run(
            [str(tool)],
            text=True,
            capture_output=False,  # let output go to terminal
        )
        # lerobot-find-port prints "The port of this MotorsBus is /dev/ttyXXX"
        # We can't capture it here since we let it run interactively.
        # User should note the port and enter it manually.
        return None  # caller should prompt user to confirm/enter the port
    except Exception as e:
        print_fn(f"  Error running lerobot-find-port: {e}")
        return None


def run_setup_motors(
    port: str,
    arm: str = "follower",
    print_fn=print,
) -> bool:
    """
    Run lerobot-setup-motors for one arm.

    Returns True if the command exited successfully.
    """
    tool = find_lerobot_bin("lerobot-setup-motors")
    if not tool:
        return False

    # Map arm type to lerobot robot/teleop type
    if arm == "follower":
        type_flag = "--robot.type=so101_follower"
        port_flag = f"--robot.port={port}"
    else:
        type_flag = "--teleop.type=so101_leader"
        port_flag = f"--teleop.port={port}"

    cmd = [str(tool), type_flag, port_flag]
    print_fn(f"\n[LeRobot] Running: {' '.join(cmd)}")
    print_fn("  Follow the prompts to connect each motor individually.\n")

    try:
        result = subprocess.run(cmd)
        return result.returncode == 0
    except Exception as e:
        print_fn(f"  Error: {e}")
        return False


def run_calibrate(
    port: str,
    arm: str = "follower",
    print_fn=print,
) -> bool:
    """
    Run lerobot-calibrate for one arm.

    Returns True if the command exited successfully.
    """
    tool = find_lerobot_bin("lerobot-calibrate")
    if not tool:
        return False

    if arm == "follower":
        type_flag = "--robot.type=so101_follower"
        port_flag = f"--robot.port={port}"
    else:
        type_flag = "--teleop.type=so101_leader"
        port_flag = f"--teleop.port={port}"

    cmd = [str(tool), type_flag, port_flag]
    print_fn(f"\n[LeRobot] Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd)
        return result.returncode == 0
    except Exception as e:
        print_fn(f"  Error: {e}")
        return False


# ── LeRobotBridge class ───────────────────────────────────────────────────────

class LeRobotBridge:
    """
    Thin wrapper around the LeRobot CLI tools for SO-ARM101.

    Usage::

        bridge = LeRobotBridge()
        if bridge.available:
            result = subprocess.run(bridge._prefix_cmd(["lerobot-record", ...]))
    """

    def __init__(self) -> None:
        self._venv = lerobot_venv_path()

    @property
    def available(self) -> bool:
        """True when lerobot-record (or at minimum lerobot-find-port) is discoverable."""
        return find_lerobot_bin("lerobot-find-port") is not None

    def _prefix_cmd(self, cmd: list[str]) -> list[str]:
        """
        Resolve the first element of *cmd* to its full binary path.

        If the tool lives inside a specific venv, that path is used so the
        command runs with the correct Python environment even when called from
        a different venv or the system Python.

        If the tool is not found on disk (e.g. not yet installed), the command
        is returned unchanged so the caller gets a descriptive OSError.
        """
        if not cmd:
            return cmd
        tool_name = cmd[0]
        resolved = find_lerobot_bin(tool_name)
        if resolved:
            return [str(resolved)] + list(cmd[1:])
        return list(cmd)


# ── Status summary ────────────────────────────────────────────────────────────

def status() -> dict:
    """Return a dict summarising LeRobot tool availability."""
    venv = lerobot_venv_path()
    tools = ["lerobot-find-port", "lerobot-setup-motors", "lerobot-calibrate"]
    return {
        "available": lerobot_available(),
        "venv": str(venv) if venv else None,
        "tools": {t: str(find_lerobot_bin(t)) if find_lerobot_bin(t) else None for t in tools},
    }
