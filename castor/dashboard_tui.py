"""
OpenCastor Terminal Dashboard — tmux-based multi-pane robot monitor.

Launches a tmux session with panes for each robot subsystem:
  - Brain    : AI reasoning, model calls, action decisions
  - Eyes     : Camera frames, object detection, scene analysis
  - Body     : Driver commands, motor/servo state, actuator feedback
  - Safety   : Health score, e-stop status, bounds, thermal
  - Comms    : Messaging channel (WhatsApp/Telegram), incoming/outgoing
  - Logs     : Full combined log stream

Usage:
    castor dashboard-tui --config robot.rcan.yaml
    castor dashboard-tui --config robot.rcan.yaml --layout minimal
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

SESSION_NAME = "opencastor"

# Log filter patterns for each pane
PANE_FILTERS = {
    "brain": "Anthropic|OpenAI|Google|Ollama|HuggingFace|Brain|Thought|Action|provider",
    "eyes": "Camera|camera|Vision|vision|frame|Frame|detect|object",
    "body": "Driver|driver|Motor|motor|Servo|servo|PCA9685|Hardware|actuator|PWM",
    "safety": "Safety|safety|E-stop|estop|Bound|bound|Thermal|thermal|Monitor|Health|audit",
    "comms": "WhatsApp|Telegram|Discord|Slack|Channel|channel|Message|message|neonize",
}

LAYOUTS = {
    "full": {
        "desc": "7-pane: Brain, Eyes, Body, Safety, Comms, Logs, Status",
        "panes": ["brain", "eyes", "body", "safety", "comms", "logs", "status"],
    },
    "minimal": {
        "desc": "4-pane: Brain, Body, Logs, Status",
        "panes": ["brain", "body", "logs", "status"],
    },
    "debug": {
        "desc": "5-pane: Brain, Safety, Comms, Logs, Status",
        "panes": ["brain", "safety", "comms", "logs", "status"],
    },
}

PANE_TITLES = {
    "brain": "🧠 Brain (AI Reasoning)",
    "eyes": "👁️  Eyes (Camera/Vision)",
    "body": "🦾 Body (Drivers/Motors)",
    "safety": "🛡️  Safety (Health/Bounds)",
    "comms": "💬 Comms (Messaging)",
    "logs": "📋 Full Logs",
    "status": "📊 Status (Agents/Swarm/Improvements)",
}

PANE_COLORS = {
    "brain": "cyan",
    "eyes": "green",
    "body": "yellow",
    "safety": "red",
    "comms": "magenta",
    "logs": "white",
    "status": "blue",
}


# ---------------------------------------------------------------------------
# File-based status helpers (agents, swarm, improvements, episodes)
# ---------------------------------------------------------------------------

_AGENT_STATUS_PATH = os.path.expanduser("~/.opencastor/agent_status.json")
_SWARM_MEMORY_PATH = os.path.expanduser("~/.opencastor/swarm_memory.json")
_IMPROVEMENT_HISTORY_PATH = os.path.expanduser("~/.opencastor/improvement_history.json")
_EPISODES_DIR = os.path.expanduser("~/.opencastor/episodes/")


def _read_json_file(path: str, max_age_s: float = 30) -> object:
    """Read and parse a JSON file if it exists and is not stale.

    Args:
        path: Absolute path to the JSON file.
        max_age_s: Maximum file age in seconds before it is considered stale.

    Returns:
        Parsed JSON data, or ``None`` if the file is missing, stale, or invalid.
    """
    try:
        if not os.path.exists(path):
            return None
        age = time.time() - os.path.getmtime(path)
        if age > max_age_s:
            return None
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _get_agents_lines() -> list:
    """Return display lines for the Agents status panel.

    Reads ``~/.opencastor/agent_status.json`` (max 10 s old).

    Returns:
        List of formatted strings, one per agent.
    """
    data = _read_json_file(_AGENT_STATUS_PATH, max_age_s=10)
    if data is None:
        return ["[no agent data]"]
    agents = data.get("agents", {})
    if not agents:
        return ["[no agents running]"]
    lines = []
    for name, health in agents.items():
        status = health.get("status", "?")
        uptime = health.get("uptime_s", 0.0)
        lines.append(f"{name:<14} {status:<10} uptime={uptime}s")
    return lines


def _get_swarm_lines() -> list:
    """Return display lines for the Swarm panel.

    Reads ``~/.opencastor/swarm_memory.json``.

    Returns:
        List of formatted strings describing fleet and patch state.
    """
    data = _read_json_file(_SWARM_MEMORY_PATH)
    if data is None:
        return ["[solo mode]"]
    peers = sum(1 for k in data if "consensus" in str(k))
    patches = sum(1 for k in data if str(k).startswith("swarm_patch:"))
    return [f"Fleet: {peers} peers | Patches: {patches} synced"]


def _get_improvements_lines() -> list:
    """Return display lines for the Sisyphus Improvements panel.

    Reads ``~/.opencastor/improvement_history.json`` and shows the last 5
    patches.

    Returns:
        List of formatted strings, one per patch entry.
    """
    data = _read_json_file(_IMPROVEMENT_HISTORY_PATH)
    if data is None:
        return ["[no improvements yet]"]
    patches = data if isinstance(data, list) else data.get("patches", [])
    if not patches:
        return ["[no improvements yet]"]
    lines = []
    for patch in list(patches)[-5:]:
        icon = "✅" if patch.get("status") == "success" else "❌"
        kind = str(patch.get("kind", "?"))
        name = str(patch.get("name", "?"))
        date = str(patch.get("date", "?"))
        status_tag = "" if patch.get("status") == "success" else " (failed)"
        lines.append(f"{icon} {kind:<12} {name:<32}{status_tag:10} {date}")
    return lines


def _get_episode_count() -> int:
    """Count recorded episode JSON files in ``~/.opencastor/episodes/``.

    Returns:
        Number of ``.json`` files found; 0 if directory is missing.
    """
    try:
        return sum(1 for f in os.listdir(_EPISODES_DIR) if f.endswith(".json"))
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Curses-style render functions (stdscr can be a mock for testing)
# ---------------------------------------------------------------------------


def _render_agents_panel(stdscr, y: int, x: int, width: int) -> None:
    """Draw the Agents status panel onto a curses window.

    Args:
        stdscr: A curses window (or mock object for testing).
        y: Top-left row offset.
        x: Left column offset.
        width: Maximum display width in characters.
    """
    for i, line in enumerate(_get_agents_lines()):
        try:
            stdscr.addstr(y + i, x, line[:width])
        except Exception:
            pass


def _render_swarm_panel(stdscr, y: int, x: int, width: int) -> None:
    """Draw the Swarm status panel onto a curses window.

    Args:
        stdscr: A curses window (or mock object for testing).
        y: Top-left row offset.
        x: Left column offset.
        width: Maximum display width in characters.
    """
    for i, line in enumerate(_get_swarm_lines()):
        try:
            stdscr.addstr(y + i, x, line[:width])
        except Exception:
            pass


def _render_improvements_panel(stdscr, y: int, x: int, width: int) -> None:
    """Draw the Sisyphus Improvements panel onto a curses window.

    Args:
        stdscr: A curses window (or mock object for testing).
        y: Top-left row offset.
        x: Left column offset.
        width: Maximum display width in characters.
    """
    for i, line in enumerate(_get_improvements_lines()):
        try:
            stdscr.addstr(y + i, x, line[:width])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Terminal status loop — used by the 'status' tmux pane
# ---------------------------------------------------------------------------

_DIVIDER = "─" * 60


def _run_status_loop(interval: float = 2.0) -> None:
    """Continuously render agent/swarm/improvement status to the terminal.

    Designed to run inside a dedicated tmux pane as the status monitor.

    Args:
        interval: Refresh interval in seconds.
    """
    try:
        while True:
            # Clear terminal
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"📊 OpenCastor Status Monitor  [{now}]")
            print(_DIVIDER)

            # Agents section
            print("▶ Agents")
            for line in _get_agents_lines():
                print(f"  {line}")
            print()

            # Swarm section
            print("▶ Swarm")
            for line in _get_swarm_lines():
                print(f"  {line}")
            print()

            # Improvements section
            print("▶ Improvements (last 5)")
            for line in _get_improvements_lines():
                print(f"  {line}")
            print()

            # Episode counter
            ep_count = _get_episode_count()
            print(f"▶ Episodes: {ep_count} recorded")
            print()
            print(_DIVIDER)
            print(f"  Refreshes every {interval}s  |  Ctrl+C to quit")

            time.sleep(interval)
    except KeyboardInterrupt:
        pass


def check_tmux():
    """Verify tmux is installed."""
    if not shutil.which("tmux"):
        print("  ❌ tmux is not installed.")
        print()
        if shutil.which("apt"):
            print("  Install with: sudo apt install tmux")
        elif shutil.which("brew"):
            print("  Install with: brew install tmux")
        elif shutil.which("dnf"):
            print("  Install with: sudo dnf install tmux")
        else:
            print("  Install tmux for your platform and try again.")
        return False
    return True


def kill_existing_session():
    """Kill any existing OpenCastor tmux session."""
    subprocess.run(
        ["tmux", "kill-session", "-t", SESSION_NAME],
        capture_output=True,
    )


def build_log_command(config_path, pane_name):
    """Build the command for a specific pane.

    Each pane runs the robot and filters logs to its subsystem.
    The 'logs' pane shows everything unfiltered.
    The 'status' pane runs the live status monitor (agents/swarm/improvements).
    """
    if pane_name == "logs":
        # Full unfiltered log — tail the log file or run the robot
        return (
            "echo '📋 Full Logs — watching all OpenCastor output'; echo; "
            "tail -f /tmp/opencastor.log 2>/dev/null || "
            "echo 'Waiting for robot to start...'; sleep 999999"
        )

    if pane_name == "status":
        # Live status monitor reads JSON status files periodically
        return (
            f"{sys.executable} -c "
            f"'from castor.dashboard_tui import _run_status_loop; _run_status_loop()'"
        )

    pattern = PANE_FILTERS.get(pane_name, "")
    title = PANE_TITLES.get(pane_name, pane_name)

    return (
        f"echo '{title}'; echo '{'─' * 40}'; echo; "
        f"tail -f /tmp/opencastor.log 2>/dev/null | "
        f"grep --line-buffered -iE '{pattern}' || "
        f"echo 'Waiting for robot to start...'; sleep 999999"
    )


def build_robot_command(config_path, simulate=False):
    """Build the main robot run command that tees to log file."""
    cmd = f"{sys.executable} -m castor.cli run --config {config_path}"
    if simulate:
        cmd += " --simulate"
    return f"{cmd} 2>&1 | tee /tmp/opencastor.log"


def launch_dashboard(config_path, layout_name="full", simulate=False, run_command=None):
    """Launch the tmux dashboard."""
    if not check_tmux():
        return False

    layout = LAYOUTS.get(layout_name, LAYOUTS["full"])
    panes = layout["panes"]

    print("\n  🖥️  OpenCastor Terminal Dashboard")
    print(f"  Layout: {layout_name} — {layout['desc']}")
    print(f"  Config: {config_path}")
    print()

    # Kill any existing session
    kill_existing_session()

    # Ensure log file exists
    open("/tmp/opencastor.log", "a").close()

    # Create new session with the first pane (robot runner)
    robot_cmd = run_command or build_robot_command(config_path, simulate)
    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            SESSION_NAME,
            "-n",
            "dashboard",
            robot_cmd,
        ],
    )

    # Set tmux options for nice display
    subprocess.run(["tmux", "set-option", "-t", SESSION_NAME, "status-style", "bg=black,fg=green"])
    subprocess.run(
        [
            "tmux",
            "set-option",
            "-t",
            SESSION_NAME,
            "status-left",
            "#[bold] 🤖 OpenCastor Dashboard ",
        ]
    )
    subprocess.run(
        [
            "tmux",
            "set-option",
            "-t",
            SESSION_NAME,
            "status-right",
            " %H:%M | #{pane_title} ",
        ]
    )
    subprocess.run(["tmux", "set-option", "-t", SESSION_NAME, "pane-border-style", "fg=colour240"])
    subprocess.run(
        ["tmux", "set-option", "-t", SESSION_NAME, "pane-active-border-style", "fg=green"]
    )
    subprocess.run(["tmux", "set-option", "-t", SESSION_NAME, "mouse", "on"])

    # Rename first pane
    subprocess.run(["tmux", "select-pane", "-t", f"{SESSION_NAME}:0.0", "-T", "🤖 Robot Runtime"])

    # Create panes for each subsystem
    for i, pane_name in enumerate(panes):
        cmd = build_log_command(config_path, pane_name)
        title = PANE_TITLES.get(pane_name, pane_name)

        # Split: alternate between horizontal and vertical for good layout
        if i % 2 == 0:
            subprocess.run(["tmux", "split-window", "-t", SESSION_NAME, "-v", cmd])
        else:
            subprocess.run(["tmux", "split-window", "-t", SESSION_NAME, "-h", cmd])

        # Set pane title
        subprocess.run(
            [
                "tmux",
                "select-pane",
                "-t",
                f"{SESSION_NAME}:0.{i + 1}",
                "-T",
                title,
            ]
        )

    # Apply a tiled layout for even distribution
    subprocess.run(["tmux", "select-layout", "-t", SESSION_NAME, "tiled"])

    # Select the first pane (robot runtime)
    subprocess.run(["tmux", "select-pane", "-t", f"{SESSION_NAME}:0.0"])

    print("  Dashboard ready! Attaching...")
    print("  Controls:")
    print("    Ctrl+B then arrow keys — switch panes")
    print("    Ctrl+B then z          — zoom a pane (toggle)")
    print("    Ctrl+B then d          — detach (dashboard keeps running)")
    print("    Ctrl+C in robot pane   — stop the robot")
    print()

    # Attach to the session
    os.execvp("tmux", ["tmux", "attach-session", "-t", SESSION_NAME])
    return True


def main():
    parser = argparse.ArgumentParser(
        description="OpenCastor Terminal Dashboard (tmux)",
        epilog=(
            "Layouts:\n"
            "  full    — 6 panes: Brain, Eyes, Body, Safety, Comms, Logs\n"
            "  minimal — 3 panes: Brain, Body, Logs\n"
            "  debug   — 4 panes: Brain, Safety, Comms, Logs\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="robot.rcan.yaml",
        help="Path to RCAN config file",
    )
    parser.add_argument(
        "--layout",
        default="full",
        choices=list(LAYOUTS.keys()),
        help="Dashboard layout (default: full)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run in simulation mode (no hardware)",
    )
    parser.add_argument(
        "--kill",
        action="store_true",
        help="Kill existing dashboard session and exit",
    )
    args = parser.parse_args()

    if args.kill:
        kill_existing_session()
        print("  Dashboard session killed.")
        return

    launch_dashboard(args.config, args.layout, args.simulate)


if __name__ == "__main__":
    main()
