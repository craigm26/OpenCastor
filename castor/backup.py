"""
OpenCastor Backup & Restore -- config and credential backup.

Tars up .env, RCAN configs, and systemd unit files for easy
recovery after SD card re-flash or migration.

Usage:
    castor backup                           # Creates opencastor-backup-YYYYMMDD.tar.gz
    castor backup --output my-backup.tar.gz
    castor restore opencastor-backup-YYYYMMDD.tar.gz
"""

import logging
import os
import tarfile
import time
from datetime import datetime

logger = logging.getLogger("OpenCastor.Backup")


def create_backup(output_path: str = None, work_dir: str = None) -> str:
    """Create a backup archive of OpenCastor configuration files.

    Includes:
      - ``.env`` file (API keys, channel credentials)
      - All ``*.rcan.yaml`` config files in the working directory
      - ``config/presets/`` directory
      - Systemd service file (if it exists)

    Args:
        output_path: Path for the output archive. Defaults to
            ``opencastor-backup-YYYYMMDD-HHMMSS.tar.gz``.
        work_dir: Working directory to back up from. Defaults to cwd.

    Returns:
        The path to the created archive.
    """
    if work_dir is None:
        work_dir = os.getcwd()

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = os.path.join(work_dir, f"opencastor-backup-{timestamp}.tar.gz")

    files_to_backup = []

    # .env file
    env_path = os.path.join(work_dir, ".env")
    if os.path.exists(env_path):
        files_to_backup.append((".env", env_path))

    # RCAN config files in working dir
    for entry in os.listdir(work_dir):
        if entry.endswith(".rcan.yaml"):
            files_to_backup.append((entry, os.path.join(work_dir, entry)))

    # Config presets directory
    presets_dir = os.path.join(work_dir, "config", "presets")
    if os.path.isdir(presets_dir):
        for entry in os.listdir(presets_dir):
            if entry.endswith(".rcan.yaml"):
                arcname = os.path.join("config", "presets", entry)
                files_to_backup.append((arcname, os.path.join(presets_dir, entry)))

    # Systemd service file
    for svc_path in [
        "/etc/systemd/system/opencastor.service",
        "/tmp/opencastor.service",
    ]:
        if os.path.exists(svc_path):
            files_to_backup.append(
                (os.path.basename(svc_path), svc_path)
            )
            break

    if not files_to_backup:
        print("  No files to back up.")
        return ""

    with tarfile.open(output_path, "w:gz") as tar:
        for arcname, filepath in files_to_backup:
            tar.add(filepath, arcname=arcname)

    return output_path


def restore_backup(archive_path: str, target_dir: str = None, dry_run: bool = False) -> list:
    """Restore an OpenCastor backup archive.

    Args:
        archive_path: Path to the backup ``.tar.gz`` file.
        target_dir: Directory to restore files to. Defaults to cwd.
        dry_run: If True, list files without extracting.

    Returns:
        List of restored file paths.
    """
    if target_dir is None:
        target_dir = os.getcwd()

    if not os.path.exists(archive_path):
        print(f"  Backup file not found: {archive_path}")
        return []

    restored = []

    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getmembers()

        if dry_run:
            print(f"\n  Contents of {archive_path}:\n")
            for member in members:
                size = member.size
                print(f"    {member.name} ({size:,} bytes)")
            print(f"\n  Total: {len(members)} file(s)\n")
            return [m.name for m in members]

        # Safety: check for path traversal
        for member in members:
            member_path = os.path.join(target_dir, member.name)
            abs_target = os.path.abspath(target_dir)
            abs_member = os.path.abspath(member_path)
            if not abs_member.startswith(abs_target):
                print(f"  Skipping unsafe path: {member.name}")
                continue

            # Check for overwrites
            if os.path.exists(member_path):
                backup_existing = member_path + f".bak.{int(time.time())}"
                os.rename(member_path, backup_existing)
                print(f"  Backed up existing: {member.name} -> {os.path.basename(backup_existing)}")

            tar.extract(member, path=target_dir)
            restored.append(member.name)

    return restored


def print_backup_summary(archive_path: str, files_backed_up: list):
    """Print a summary of the backup operation."""
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    if has_rich:
        file_list = "\n".join(f"  [green]+[/] {f}" for f in files_backed_up) if files_backed_up else "  (none)"
        size = os.path.getsize(archive_path) if os.path.exists(archive_path) else 0
        console.print(Panel.fit(
            f"[bold green]Backup created[/]\n\n"
            f"  Archive: [cyan]{archive_path}[/]\n"
            f"  Size:    {size:,} bytes\n"
            f"  Files:   {len(files_backed_up)}\n\n"
            f"{file_list}",
            border_style="green",
        ))
    else:
        print(f"\n  Backup created: {archive_path}")
        print(f"  Files: {len(files_backed_up)}")
        for f in files_backed_up:
            print(f"    + {f}")
        print()


def print_restore_summary(restored: list):
    """Print a summary of the restore operation."""
    try:
        from rich.console import Console

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    if has_rich:
        console.print(f"\n  [bold green]Restored {len(restored)} file(s)[/]\n")
        for f in restored:
            console.print(f"    [green]+[/] {f}")
        console.print()
    else:
        print(f"\n  Restored {len(restored)} file(s)")
        for f in restored:
            print(f"    + {f}")
        print()
