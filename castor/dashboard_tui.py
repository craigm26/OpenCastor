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
        "desc": "8-pane: Brain, Eyes, Body, Safety, Comms, Logs, Status, Embedding",
        "panes": ["brain", "eyes", "body", "safety", "comms", "logs", "status", "embedding"],
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
    "embedding": "🧠 Embedding Interpreter",
}

PANE_COLORS = {
    "brain": "cyan",
    "eyes": "green",
    "body": "yellow",
    "safety": "red",
    "comms": "magenta",
    "logs": "white",
    "status": "blue",
    "embedding": "cyan",
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


def _get_runtime_stats() -> dict:
    """Read runtime stats from file. Returns empty dict on failure."""
    try:
        import json as _json

        path = os.path.expanduser("~/.opencastor/runtime_stats.json")
        mtime = os.path.getmtime(path)
        if time.time() - mtime > 60:
            return {}  # stale — robot probably not running
        with open(path) as f:
            return _json.load(f)
    except Exception:
        return {}


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_bytes(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1_024:
        return f"{n / 1_024:.1f} KB"
    return f"{n} B"


def _fmt_uptime(secs: float) -> str:
    s = int(secs)
    if s >= 3600:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    if s >= 60:
        return f"{s // 60}m {s % 60}s"
    return f"{s}s"


def _render_stats_bar(stats: dict) -> str:
    """Build the bottom status bar string from runtime stats."""
    if not stats:
        return "  ── waiting for robot ──"

    uptime = time.time() - stats.get("session_start", time.time())
    model = stats.get("last_model", "—")
    if "/" in model:
        model = model.split("/")[-1]
    model = model.replace("claude-", "").replace("-instruct", "").replace("-preview", "")[:20]

    tok_in = stats.get("tokens_in", 0)
    tok_out = stats.get("tokens_out", 0)
    tok_cached = stats.get("tokens_cached", 0)
    calls = stats.get("api_calls", 0)
    data = stats.get("bytes_in", 0) + stats.get("bytes_out", 0)
    tick = stats.get("tick", 0)
    action = stats.get("last_action", "—")[:16]

    parts = [
        f"⏱ {_fmt_uptime(uptime)}",
        f"🧠 {model}",
        f"↓{_fmt_tokens(tok_in)} ↑{_fmt_tokens(tok_out)} tok",
    ]
    if tok_cached:
        parts.append(f"💾{_fmt_tokens(tok_cached)} cached")
    parts += [
        f"🔁 {calls} calls",
        f"↕ {_fmt_bytes(data)}",
        f"tick {tick}",
        f"act: {action}",
    ]
    return "   │   ".join(parts)


def _run_status_loop(interval: float = 2.0) -> None:
    """Continuously render agent/swarm/improvement/stats status to the terminal.

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

            # ── Runtime stats (token / data / exchange) ──────────────────
            stats = _get_runtime_stats()
            print(_DIVIDER)
            print("▶ Exchange Stats")
            if stats:
                uptime = time.time() - stats.get("session_start", time.time())
                print(f"  ⏱  Uptime      {_fmt_uptime(uptime)}")
                print(f"  🧠 Model       {stats.get('last_model', '—')}")
                print(
                    f"  📥 Tokens in   {_fmt_tokens(stats.get('tokens_in', 0))}"
                    f"   📤 out  {_fmt_tokens(stats.get('tokens_out', 0))}"
                )
                cached = stats.get("tokens_cached", 0)
                if cached:
                    print(f"  💾 Cached      {_fmt_tokens(cached)}")
                print(f"  🔁 API calls   {stats.get('api_calls', 0)}")
                print(
                    f"  ↕  Data vol    {_fmt_bytes(stats.get('bytes_in', 0) + stats.get('bytes_out', 0))}"
                    f"   (↓{_fmt_bytes(stats.get('bytes_in', 0))} ↑{_fmt_bytes(stats.get('bytes_out', 0))})"
                )
                print(f"  🎬 Tick        {stats.get('tick', 0)}")
                print(f"  ⚡ Last act    {stats.get('last_action', '—')}")
            else:
                print("  Robot not running or no data yet.")
            print(_DIVIDER)
            print(f"  Refreshes every {interval}s  |  Ctrl+C to quit")
            print()
            print(_render_stats_bar(stats))

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


def _run_embedding_loop() -> None:
    """Render embedding interpreter metrics to terminal. Runs in a tmux pane."""
    import time as _time

    try:
        import requests
    except ImportError:
        print("🧠 Embedding Interpreter: requests not installed")
        _time.sleep(999999)
        return

    BASE = "http://localhost:18789"
    while True:
        try:
            r = requests.get(f"{BASE}/api/interpreter/status", timeout=2)
            d = r.json()
        except Exception:
            d = {"enabled": False}

        # Clear + render
        print("\033[2J\033[H", end="")
        if not d.get("enabled"):
            print("🧠 Embedding Interpreter: disabled")
        else:
            sim = d.get("last_goal_similarity", 0) or 0
            bar = "█" * int(sim * 20) + "░" * (20 - int(sim * 20))
            print("🧠 EMBEDDING INTERPRETER")
            print(f"  Backend   : {d.get('backend', '?')}")
            print(f"  Episodes  : {d.get('episode_count', 0)}")
            print(f"  Goal sim  : {sim:.2f} [{bar}]")
            print(f"  Escalations: {d.get('escalations_session', 0)} (session)")
            print(f"  Latency   : {d.get('avg_latency_ms') or '—'}ms avg")
        _time.sleep(2)


def build_log_command(config_path, pane_name):
    """Build the command for a specific pane.

    Each pane runs the robot and filters logs to its subsystem.
    The 'logs' pane shows everything unfiltered.
    The 'status' pane runs the live status monitor (agents/swarm/improvements).
    The 'embedding' pane renders EmbeddingInterpreter metrics.
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

    if pane_name == "embedding":
        return (
            f"{sys.executable} -c "
            f"'from castor.dashboard_tui import _run_embedding_loop; _run_embedding_loop()'"
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

    # ── tmux options — status bar with live exchange stats ───────────────
    def _tset(*args):
        subprocess.run(["tmux", "set-option", "-t", SESSION_NAME, *args], capture_output=True)

    _tset("status-style", "bg=colour234,fg=colour250")
    _tset("status-interval", "2")  # refresh every 2 s
    _tset("status-left-length", "40")
    _tset("status-right-length", "180")
    _tset(
        "status-left",
        "#[bg=colour28,fg=colour255,bold] 🤖 OpenCastor  #[bg=colour234,fg=colour245] v2026 ",
    )
    # Right side: pulls live stats written by runtime_stats.py every tick
    _tset(
        "status-right",
        "#[fg=colour245]#(cat /tmp/opencastor_status_bar.txt 2>/dev/null"
        " || echo ' waiting for robot...')  "
        "#[fg=colour238]│#[fg=colour250]  %H:%M:%S ",
    )
    _tset("pane-border-style", "fg=colour238")
    _tset("pane-active-border-style", "fg=colour34")
    _tset("pane-border-status", "top")
    _tset("pane-border-format", " #[bold]#{pane_title}#[nobold] ")
    _tset("mouse", "on")

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
