"""
OpenCastor Log Viewer -- structured, colored log viewer.

Reads OpenCastor log output and presents it with filtering and
color-coded severity levels.

Usage:
    castor logs
    castor logs --follow
    castor logs --level ERROR
    castor logs --module providers
"""

import logging
import os
import re
import time

logger = logging.getLogger("OpenCastor.Logs")

# ANSI color codes for log levels
LEVEL_COLORS = {
    "DEBUG": "\033[36m",  # Cyan
    "INFO": "\033[32m",  # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "CRITICAL": "\033[91m",  # Bright red
}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Pattern to parse standard OpenCastor log lines
# Format: 2026-02-16 12:00:00,000 - OpenCastor.Module - LEVEL - message
LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3})"  # timestamp
    r"\s+-\s+"
    r"([\w.]+)"  # logger name
    r"\s+-\s+"
    r"(\w+)"  # level
    r"\s+-\s+"
    r"(.+)$"  # message
)

LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def _matches_filter(line: str, level: str = None, module: str = None) -> bool:
    """Check if a log line matches the given filters."""
    match = LOG_PATTERN.match(line)
    if not match:
        return level is None  # Show non-log lines only if no level filter

    _, log_module, log_level, _ = match.groups()

    if level:
        min_order = LEVEL_ORDER.get(level.upper(), 0)
        line_order = LEVEL_ORDER.get(log_level.upper(), 0)
        if line_order < min_order:
            return False

    if module:
        if module.lower() not in log_module.lower():
            return False

    return True


def _colorize_line(line: str) -> str:
    """Add ANSI colors to a log line based on its level."""
    match = LOG_PATTERN.match(line)
    if not match:
        return line

    timestamp, log_module, log_level, message = match.groups()
    color = LEVEL_COLORS.get(log_level.upper(), "")

    return (
        f"{DIM}{timestamp}{RESET} {BOLD}{log_module}{RESET} {color}{log_level:8s}{RESET} {message}"
    )


def _get_log_file() -> str:
    """Determine the log file path.

    OpenCastor uses stderr logging by default. If a log file is configured
    via LOG_FILE env var, use that. Otherwise fall back to journalctl.
    """
    return os.getenv("OPENCASTOR_LOG_FILE", "")


def view_logs(
    follow: bool = False,
    level: str = None,
    module: str = None,
    lines: int = 50,
    no_color: bool = False,
):
    """Display OpenCastor logs with optional filtering.

    Args:
        follow: Continuously tail new log lines.
        level: Minimum log level to display (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        module: Filter to logs from a specific module (e.g., "providers", "Gateway").
        lines: Number of recent lines to show initially.
        no_color: Disable ANSI color output.
    """
    log_file = _get_log_file()

    if log_file and os.path.exists(log_file):
        _tail_file(log_file, follow, level, module, lines, no_color)
    else:
        # Try journalctl for systemd service logs
        _tail_journalctl(follow, level, module, lines, no_color)


def _tail_file(path: str, follow: bool, level: str, module: str, lines: int, no_color: bool):
    """Tail a log file."""
    try:
        with open(path) as f:
            # Read all lines and show last N
            all_lines = f.readlines()
            recent = all_lines[-lines:] if len(all_lines) > lines else all_lines

            for line in recent:
                line = line.rstrip()
                if _matches_filter(line, level, module):
                    if no_color:
                        print(line)
                    else:
                        print(_colorize_line(line))

            if not follow:
                return

            # Follow mode: watch for new lines
            print(f"\n{DIM}--- Following {path} (Ctrl+C to stop) ---{RESET}\n")
            while True:
                line = f.readline()
                if line:
                    line = line.rstrip()
                    if _matches_filter(line, level, module):
                        if no_color:
                            print(line)
                        else:
                            print(_colorize_line(line))
                else:
                    time.sleep(0.2)

    except KeyboardInterrupt:
        print()
    except FileNotFoundError:
        print(f"  Log file not found: {path}")


def _tail_journalctl(follow: bool, level: str, module: str, lines: int, no_color: bool):
    """Read logs from systemd journal."""
    import subprocess

    cmd = ["journalctl", "-u", "opencastor", "--no-pager", f"-n{lines}"]
    if follow:
        cmd.append("-f")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        for line in proc.stdout:
            line = line.rstrip()
            if _matches_filter(line, level, module):
                if no_color:
                    print(line)
                else:
                    print(_colorize_line(line))

    except FileNotFoundError:
        # journalctl not available
        print("  No log source available.")
        print()
        print("  Options:")
        print("    1. Set OPENCASTOR_LOG_FILE to a file path")
        print("    2. Install as a systemd service: castor install-service")
        print("    3. Run castor gateway and check terminal output")
        print()
    except KeyboardInterrupt:
        print()
