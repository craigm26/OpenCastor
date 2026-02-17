"""
OpenCastor Schedule -- cron-like task scheduling for robots.

Define recurring tasks like "patrol every 30 minutes" or
"run diagnostics at midnight" in RCAN config or via CLI.

RCAN config format::

    schedule:
      - name: patrol
        command: "castor run --config robot.rcan.yaml"
        cron: "*/30 * * * *"
      - name: health_check
        command: "castor doctor"
        cron: "0 0 * * *"

Usage:
    castor schedule list                   # Show scheduled tasks
    castor schedule add --name patrol \\
        --command "castor run ..." \\
        --cron "*/30 * * * *"              # Add a task
    castor schedule remove --name patrol   # Remove a task
    castor schedule install                # Install to system crontab
"""

import json
import logging
import os
import subprocess
import sys

logger = logging.getLogger("OpenCastor.Schedule")

_SCHEDULE_FILE = ".opencastor-schedule.json"


def _load_schedule() -> list:
    """Load schedule from disk."""
    if not os.path.exists(_SCHEDULE_FILE):
        return []
    try:
        with open(_SCHEDULE_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_schedule(tasks: list):
    """Save schedule to disk."""
    with open(_SCHEDULE_FILE, "w") as f:
        json.dump(tasks, f, indent=2)


def list_tasks(config_path: str = None) -> list:
    """List all scheduled tasks from file and optionally from RCAN config."""
    tasks = _load_schedule()

    # Also read from RCAN config if available
    if config_path and os.path.exists(config_path):
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
            config_tasks = config.get("schedule", [])
            for ct in config_tasks:
                ct["source"] = "rcan_config"
                # Avoid duplicates by name
                if not any(t.get("name") == ct.get("name") for t in tasks):
                    tasks.append(ct)
        except Exception:
            pass

    return tasks


def add_task(name: str, command: str, cron: str) -> dict:
    """Add a scheduled task."""
    tasks = _load_schedule()

    # Remove existing with same name
    tasks = [t for t in tasks if t.get("name") != name]

    task = {
        "name": name,
        "command": command,
        "cron": cron,
        "source": "cli",
    }
    tasks.append(task)
    _save_schedule(tasks)
    logger.info(f"Scheduled task added: {name} ({cron})")
    return task


def remove_task(name: str) -> bool:
    """Remove a scheduled task by name."""
    tasks = _load_schedule()
    before = len(tasks)
    tasks = [t for t in tasks if t.get("name") != name]
    if len(tasks) < before:
        _save_schedule(tasks)
        logger.info(f"Scheduled task removed: {name}")
        return True
    return False


def install_crontab(config_path: str = None):
    """Install scheduled tasks to the system crontab."""
    tasks = list_tasks(config_path)
    if not tasks:
        print("  No tasks to install.\n")
        return

    # Build crontab entries
    work_dir = os.getcwd()
    entries = []
    for task in tasks:
        cron = task.get("cron", "")
        command = task.get("command", "")
        name = task.get("name", "unknown")
        if not cron or not command:
            continue
        entry = f"{cron} cd {work_dir} && {command}  # opencastor:{name}"
        entries.append(entry)

    if not entries:
        print("  No valid cron entries to install.\n")
        return

    # Read existing crontab
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True
        )
        existing = result.stdout if result.returncode == 0 else ""
    except Exception:
        existing = ""

    # Remove old opencastor entries
    lines = [
        line for line in existing.splitlines()
        if "# opencastor:" not in line
    ]

    # Add new entries
    lines.extend(entries)
    new_crontab = "\n".join(lines) + "\n"

    # Write to crontab
    try:
        proc = subprocess.run(
            ["crontab", "-"], input=new_crontab, text=True,
            capture_output=True,
        )
        if proc.returncode == 0:
            print(f"  Installed {len(entries)} task(s) to crontab.\n")
            for entry in entries:
                print(f"    {entry}")
            print()
        else:
            print(f"  Failed to install crontab: {proc.stderr}\n")
    except Exception as exc:
        print(f"  Failed to install crontab: {exc}\n")


def print_schedule(tasks: list):
    """Print scheduled tasks."""
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if not tasks:
        msg = "  No scheduled tasks."
        if has_rich:
            console.print(f"\n[dim]{msg}[/]\n")
        else:
            print(f"\n{msg}\n")
        return

    if has_rich:
        table = Table(title=f"Scheduled Tasks ({len(tasks)})", show_header=True)
        table.add_column("Name", style="bold")
        table.add_column("Cron")
        table.add_column("Command", style="dim")
        table.add_column("Source")

        for task in tasks:
            table.add_row(
                task.get("name", "?"),
                task.get("cron", "?"),
                task.get("command", "?")[:50],
                task.get("source", "file"),
            )

        console.print()
        console.print(table)
        console.print()
    else:
        print(f"\n  Scheduled Tasks ({len(tasks)}):\n")
        for task in tasks:
            print(f"  {task.get('name', '?')}:")
            print(f"    Cron:    {task.get('cron', '?')}")
            print(f"    Command: {task.get('command', '?')}")
            print(f"    Source:  {task.get('source', 'file')}")
            print()
