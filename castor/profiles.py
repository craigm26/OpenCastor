"""
OpenCastor Profiles -- manage named config profiles.

Switch between pre-saved .rcan.yaml configs without remembering file paths.
Profiles are symlinks or copies in ``~/.opencastor/profiles/``.

Usage:
    castor profile list                           # Show saved profiles
    castor profile save indoor --config robot.rcan.yaml  # Save a profile
    castor profile use indoor                     # Activate a profile
    castor profile remove indoor                  # Delete a profile
"""

import logging
import os
import shutil

logger = logging.getLogger("OpenCastor.Profiles")

_PROFILES_DIR = os.path.expanduser("~/.opencastor/profiles")
_ACTIVE_FILE = os.path.expanduser("~/.opencastor/active-profile")


def _ensure_dir():
    os.makedirs(_PROFILES_DIR, exist_ok=True)


def list_profiles() -> list:
    """List all saved profiles.

    Returns list of ``{"name": str, "path": str, "active": bool}`` dicts.
    """
    _ensure_dir()
    active = get_active_profile()
    profiles = []

    for f in sorted(os.listdir(_PROFILES_DIR)):
        if f.endswith(".rcan.yaml"):
            name = f.replace(".rcan.yaml", "")
            profiles.append(
                {
                    "name": name,
                    "path": os.path.join(_PROFILES_DIR, f),
                    "active": name == active,
                }
            )

    return profiles


def save_profile(name: str, config_path: str) -> str:
    """Save a config file as a named profile.

    Returns the profile path.
    """
    _ensure_dir()

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")

    dest = os.path.join(_PROFILES_DIR, f"{name}.rcan.yaml")
    shutil.copy2(config_path, dest)
    logger.info(f"Profile saved: {name} <- {config_path}")
    return dest


def use_profile(name: str) -> str:
    """Activate a profile by copying it to ``robot.rcan.yaml`` in cwd.

    Returns the source profile path.
    """
    profile_path = os.path.join(_PROFILES_DIR, f"{name}.rcan.yaml")
    if not os.path.exists(profile_path):
        raise FileNotFoundError(f"Profile not found: {name}")

    dest = os.path.join(os.getcwd(), "robot.rcan.yaml")

    # Backup existing config
    if os.path.exists(dest):
        backup = dest + ".bak"
        shutil.copy2(dest, backup)

    shutil.copy2(profile_path, dest)

    # Record active profile
    try:
        os.makedirs(os.path.dirname(_ACTIVE_FILE), exist_ok=True)
        with open(_ACTIVE_FILE, "w") as f:
            f.write(name)
    except Exception:
        pass

    logger.info(f"Profile activated: {name}")
    return profile_path


def remove_profile(name: str) -> bool:
    """Remove a saved profile."""
    profile_path = os.path.join(_PROFILES_DIR, f"{name}.rcan.yaml")
    if os.path.exists(profile_path):
        os.remove(profile_path)
        # Clear active if this was active
        if get_active_profile() == name:
            try:
                os.remove(_ACTIVE_FILE)
            except Exception:
                pass
        logger.info(f"Profile removed: {name}")
        return True
    return False


def get_active_profile() -> str:
    """Get the name of the currently active profile, or None."""
    try:
        if os.path.exists(_ACTIVE_FILE):
            with open(_ACTIVE_FILE) as f:
                return f.read().strip()
    except Exception:
        pass
    return None


def print_profiles(profiles: list):
    """Print profile list."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if not profiles:
        msg = "  No saved profiles. Save one with: castor profile save NAME --config FILE"
        if has_rich:
            console.print(f"\n[dim]{msg}[/]\n")
        else:
            print(f"\n{msg}\n")
        return

    if has_rich:
        table = Table(title=f"Config Profiles ({len(profiles)})", show_header=True)
        table.add_column("", width=2)
        table.add_column("Name", style="bold")
        table.add_column("Path", style="dim")

        for p in profiles:
            marker = "[green]*[/]" if p["active"] else " "
            table.add_row(marker, p["name"], p["path"])

        console.print()
        console.print(table)
        active = get_active_profile()
        if active:
            console.print(f"\n  Active: [bold]{active}[/]")
        console.print()
    else:
        print(f"\n  Config Profiles ({len(profiles)}):\n")
        for p in profiles:
            marker = " *" if p["active"] else "  "
            print(f"  {marker} {p['name']:20s} {p['path']}")
        active = get_active_profile()
        if active:
            print(f"\n  Active: {active}")
        print()
