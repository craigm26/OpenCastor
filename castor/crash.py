"""
OpenCastor Crash Recovery -- save and restore state after unexpected exits.

Writes a ``.opencastor-crash.json`` file when the runtime crashes,
containing the last known state. On next start, detects the crash
file and offers to review or resume.

Usage (automatic -- integrated into main.py):
    # Crash file written automatically on unhandled exception
    # On next start:
    castor run --config robot.rcan.yaml
    #   -> "Crash detected. Review? [Y/n]"
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger("OpenCastor.Crash")

CRASH_FILE = ".opencastor-crash.json"


def save_crash_report(
    config_path: str,
    error: str,
    last_thought: str = None,
    last_action: dict = None,
    loop_count: int = 0,
    uptime_seconds: float = 0,
):
    """Write a crash report to the working directory.

    Args:
        config_path: Path to the RCAN config that was running.
        error: The error message / traceback.
        last_thought: Last AI thought text.
        last_action: Last action dict.
        loop_count: Number of loop iterations completed.
        uptime_seconds: Time the runtime was running.
    """
    report = {
        "timestamp": datetime.now().isoformat(),
        "config_path": config_path,
        "error": error,
        "last_thought": last_thought,
        "last_action": last_action,
        "loop_count": loop_count,
        "uptime_seconds": round(uptime_seconds, 2),
        "python_version": f"{__import__('sys').version}",
    }

    try:
        with open(CRASH_FILE, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"Crash report saved to {CRASH_FILE}")
    except Exception as exc:
        logger.error(f"Failed to write crash report: {exc}")


def check_crash_report() -> dict:
    """Check for a crash report from a previous run.

    Returns the crash report dict if found, None otherwise.
    """
    if not os.path.exists(CRASH_FILE):
        return None

    try:
        with open(CRASH_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def clear_crash_report():
    """Remove the crash report file."""
    try:
        os.remove(CRASH_FILE)
    except FileNotFoundError:
        pass


def display_crash_report(report: dict):
    """Display a crash report to the user."""
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        console.print(
            Panel(
                f"[bold yellow]Previous crash detected[/]\n\n"
                f"  Time:      {report.get('timestamp', '?')}\n"
                f"  Config:    {report.get('config_path', '?')}\n"
                f"  Uptime:    {report.get('uptime_seconds', 0):.0f}s\n"
                f"  Loops:     {report.get('loop_count', 0)}\n"
                f"  Error:     {report.get('error', '?')[:200]}\n"
                f"  Last:      {(report.get('last_thought') or 'none')[:100]}",
                border_style="yellow",
                title="[bold]Crash Report[/]",
            )
        )
    except ImportError:
        print("\n  --- Previous Crash Detected ---")
        print(f"  Time:    {report.get('timestamp', '?')}")
        print(f"  Config:  {report.get('config_path', '?')}")
        print(f"  Uptime:  {report.get('uptime_seconds', 0):.0f}s")
        print(f"  Loops:   {report.get('loop_count', 0)}")
        print(f"  Error:   {report.get('error', '?')[:200]}")
        print()


def handle_crash_on_startup() -> bool:
    """Check for and handle a previous crash report.

    Returns True if the user wants to continue, False to abort.
    """
    report = check_crash_report()
    if report is None:
        return True

    display_crash_report(report)

    try:
        answer = input("  Continue running? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    clear_crash_report()

    if answer == "n":
        print("  Exiting. Review the crash details above.\n")
        return False

    return True
