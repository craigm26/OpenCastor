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
import os
import shutil
import subprocess
import sys

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
        "desc": "6-pane: Brain, Eyes, Body, Safety, Comms, Logs",
        "panes": ["brain", "eyes", "body", "safety", "comms", "logs"],
    },
    "minimal": {
        "desc": "3-pane: Brain, Body, Logs",
        "panes": ["brain", "body", "logs"],
    },
    "debug": {
        "desc": "4-pane: Brain, Safety, Comms, Logs",
        "panes": ["brain", "safety", "comms", "logs"],
    },
}

PANE_TITLES = {
    "brain": "🧠 Brain (AI Reasoning)",
    "eyes": "👁️  Eyes (Camera/Vision)",
    "body": "🦾 Body (Drivers/Motors)",
    "safety": "🛡️  Safety (Health/Bounds)",
    "comms": "💬 Comms (Messaging)",
    "logs": "📋 Full Logs",
}

PANE_COLORS = {
    "brain": "cyan",
    "eyes": "green",
    "body": "yellow",
    "safety": "red",
    "comms": "magenta",
    "logs": "white",
}


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
    """
    if pane_name == "logs":
        # Full unfiltered log — tail the log file or run the robot
        return (
            "echo '📋 Full Logs — watching all OpenCastor output'; echo; "
            "tail -f /tmp/opencastor.log 2>/dev/null || "
            "echo 'Waiting for robot to start...'; sleep 999999"
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


def launch_dashboard(config_path, layout_name="full", simulate=False):
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
    robot_cmd = build_robot_command(config_path, simulate)
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
