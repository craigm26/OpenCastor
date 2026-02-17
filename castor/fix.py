"""
OpenCastor Fix -- automated issue resolver.

Reads the output of ``castor doctor`` and attempts to fix common issues:
  - Missing .env file -> copy .env.example
  - Missing SDKs -> pip install suggestions
  - I2C not enabled -> raspi-config hint

Usage:
    castor fix
    castor fix --config robot.rcan.yaml
"""

import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger("OpenCastor.Fix")


def run_fix(config_path: str = None):
    """Run doctor, then attempt to fix each failure.

    Creates a backup of affected files before modifying them.
    """
    from castor.doctor import run_all_checks

    try:
        from rich.console import Console
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    if has_rich:
        console.print("\n[bold cyan]  OpenCastor Fix[/]")
        console.print("  Running diagnostics and attempting fixes...\n")
    else:
        print("\n  OpenCastor Fix")
        print("  Running diagnostics and attempting fixes...\n")

    # Backup existing files before any repairs
    _backup_before_repair(config_path)

    results = run_all_checks(config_path=config_path)
    fixed = 0
    skipped = 0

    for ok, name, detail in results:
        if ok:
            continue

        # Try to fix each failure
        fix_result = _attempt_fix(name, detail)

        if fix_result == "fixed":
            fixed += 1
            if has_rich:
                console.print(f"  [green]FIXED[/]  {name}")
            else:
                print(f"  [FIXED]  {name}")
        elif fix_result == "hint":
            skipped += 1
            # Hint already printed by _attempt_fix
        else:
            skipped += 1
            if has_rich:
                console.print(f"  [yellow]SKIP[/]   {name}: {detail}")
            else:
                print(f"  [SKIP]   {name}: {detail}")

    if has_rich:
        console.print(f"\n  [bold]{fixed} fixed, {skipped} need manual action[/]\n")
    else:
        print(f"\n  {fixed} fixed, {skipped} need manual action\n")


def _attempt_fix(name: str, detail: str) -> str:
    """Attempt to fix a single issue.

    Returns ``"fixed"``, ``"hint"``, or ``"skip"``.
    """
    name_lower = name.lower()

    # Fix: Missing .env file
    if ".env" in name_lower and "missing" in detail:
        return _fix_env_file()

    # Fix: Missing SDK
    if "sdk:" in name_lower and "not installed" in detail:
        return _fix_missing_sdk(name)

    # Fix: Provider key missing
    if "provider key" in name_lower and "no key" in detail:
        return _hint_provider_key(name)

    # Fix: Camera not accessible
    if "camera" in name_lower and "not accessible" in detail:
        return _hint_camera()

    return "skip"


def _fix_env_file() -> str:
    """Copy .env.example to .env if it exists."""
    example = ".env.example"
    if os.path.exists(example):
        shutil.copy2(example, ".env")
        print(f"    Copied {example} -> .env")
        return "fixed"
    else:
        # Create a minimal .env
        with open(".env", "w") as f:
            f.write("# OpenCastor Environment Variables\n")
            f.write("# See .env.example for all options\n\n")
        print("    Created empty .env file")
        return "fixed"


def _fix_missing_sdk(name: str) -> str:
    """Suggest pip install for missing SDK."""
    sdk_map = {
        "dynamixel": ("dynamixel-sdk", "pip install dynamixel-sdk"),
        "pca9685": ("adafruit-circuitpython-pca9685", "pip install adafruit-circuitpython-pca9685"),
        "picamera": ("picamera2", "pip install picamera2"),
        "opencv": ("opencv-python-headless", "pip install opencv-python-headless"),
    }

    for key, (pkg, cmd) in sdk_map.items():
        if key in name.lower():
            print(f"    Install with: {cmd}")
            return "hint"

    return "skip"


def _hint_provider_key(name: str) -> str:
    """Suggest how to set a provider API key."""
    provider_hints = {
        "google": "Get key at https://aistudio.google.com/apikey -> set GOOGLE_API_KEY in .env",
        "openai": "Get key at https://platform.openai.com/api-keys -> set OPENAI_API_KEY in .env",
        "anthropic": "Get key at https://console.anthropic.com/ -> set ANTHROPIC_API_KEY in .env",
        "openrouter": "Get key at https://openrouter.ai/keys -> set OPENROUTER_API_KEY in .env",
    }

    for provider, hint in provider_hints.items():
        if provider in name.lower():
            print(f"    {hint}")
            return "hint"

    return "skip"


def _hint_camera() -> str:
    """Provide camera troubleshooting hints."""
    print("    Troubleshooting:")
    print("      - USB camera: check it's plugged in, try: ls /dev/video*")
    print("      - CSI camera: enable in raspi-config -> Interface Options -> Camera")
    print("      - Set CAMERA_INDEX=0 (or 1, 2) in .env to select device")
    return "hint"


def _backup_before_repair(config_path: str = None):
    """Create a snapshot of key files before attempting any repairs.

    This ensures all repairs are reversible.
    """
    import glob
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = f".opencastor-fix-backup-{timestamp}"

    files_to_backup = []

    # Always back up .env if it exists
    if os.path.exists(".env"):
        files_to_backup.append(".env")

    # Back up the config file if provided
    if config_path and os.path.exists(config_path):
        files_to_backup.append(config_path)

    # Back up any .rcan.yaml files in the current directory
    for f in glob.glob("*.rcan.yaml"):
        if f not in files_to_backup:
            files_to_backup.append(f)

    if not files_to_backup:
        return

    try:
        os.makedirs(backup_dir, exist_ok=True)
        for filepath in files_to_backup:
            dest = os.path.join(backup_dir, os.path.basename(filepath))
            shutil.copy2(filepath, dest)
        print(f"  Pre-repair backup: {backup_dir}/ ({len(files_to_backup)} file(s))\n")
    except Exception as exc:
        logger.debug(f"Backup before repair failed: {exc}")
