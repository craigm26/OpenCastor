"""
OpenCastor Config Migration -- upgrade RCAN configs between schema versions.

Detects the RCAN version in a config file and applies migration steps
to bring it up to the current schema version.

Usage:
    castor migrate --config robot.rcan.yaml
    castor migrate --config robot.rcan.yaml --dry-run
"""

import copy
import logging
import os

import yaml

logger = logging.getLogger("OpenCastor.Migrate")

# Current RCAN schema version
CURRENT_VERSION = "1.0.0-alpha"

# Ordered list of migrations: (from_version, to_version, migration_fn)
# Each migration_fn takes a config dict and returns the modified config.
_MIGRATIONS = []


def _register_migration(from_ver, to_ver):
    """Decorator to register a migration function."""

    def wrapper(fn):
        _MIGRATIONS.append((from_ver, to_ver, fn))
        return fn

    return wrapper


# ---------------------------------------------------------------------------
# Migration definitions
# ---------------------------------------------------------------------------
@_register_migration("0.9.0", "1.0.0-alpha")
def _migrate_0_9_to_1_0(config):
    """Migrate from hypothetical 0.9.0 to 1.0.0-alpha.

    Changes:
      - Rename ``brain`` to ``agent`` if present.
      - Add ``rcan_protocol`` section if missing.
      - Add ``network`` section if missing.
    """
    # Rename brain -> agent
    if "brain" in config and "agent" not in config:
        config["agent"] = config.pop("brain")

    # Ensure rcan_protocol exists
    if "rcan_protocol" not in config:
        config["rcan_protocol"] = {
            "port": 8000,
            "capabilities": ["status"],
            "enable_mdns": False,
            "enable_jwt": False,
        }

    # Ensure network exists
    if "network" not in config:
        config["network"] = {
            "telemetry_stream": True,
            "sim_to_real_sync": False,
            "allow_remote_override": False,
        }

    config["rcan_version"] = "1.0.0-alpha"
    return config


# ---------------------------------------------------------------------------
# Future migration placeholder
# ---------------------------------------------------------------------------
# @_register_migration("1.0.0-alpha", "1.1.0")
# def _migrate_1_0_to_1_1(config):
#     """Migrate from 1.0.0-alpha to 1.1.0."""
#     config["rcan_version"] = "1.1.0"
#     return config


def get_version(config: dict) -> str:
    """Extract the RCAN version from a config dict."""
    return config.get("rcan_version", "unknown")


def needs_migration(config: dict) -> bool:
    """Check if a config needs migration to the current version."""
    version = get_version(config)
    return version != CURRENT_VERSION and version != "unknown"


def get_migration_path(from_version: str) -> list:
    """Determine the ordered sequence of migrations needed.

    Returns a list of ``(from_ver, to_ver, fn)`` tuples.
    """
    path = []
    current = from_version

    for from_ver, to_ver, fn in _MIGRATIONS:
        if from_ver == current:
            path.append((from_ver, to_ver, fn))
            current = to_ver

    return path


def migrate_config(config: dict, dry_run: bool = False) -> tuple:
    """Apply all necessary migrations to bring a config to the current version.

    Args:
        config: The RCAN config dict.
        dry_run: If True, return the migrated config without modifying the original.

    Returns:
        ``(migrated_config, changes_list)`` where ``changes_list`` is a list
        of human-readable change descriptions.
    """
    from_version = get_version(config)
    if from_version == CURRENT_VERSION:
        return config, []

    if dry_run:
        config = copy.deepcopy(config)

    path = get_migration_path(from_version)
    changes = []

    if not path:
        # No registered migrations, but version differs
        # Try to apply structural fixes anyway
        fixed_config, fix_changes = _apply_structural_fixes(config)
        if fix_changes:
            return fixed_config, fix_changes
        return config, [f"No migration path from {from_version} to {CURRENT_VERSION}"]

    for from_ver, to_ver, fn in path:
        config = fn(config)
        changes.append(f"Migrated {from_ver} -> {to_ver}")

    return config, changes


def _apply_structural_fixes(config: dict) -> tuple:
    """Apply common structural fixes regardless of version.

    This handles configs that are mostly valid but missing some
    newer fields.
    """
    changes = []
    copy.deepcopy(config)

    # Ensure required top-level keys exist
    if "metadata" not in config:
        config["metadata"] = {
            "robot_name": "UnnamedRobot",
            "robot_uuid": "00000000-0000-0000-0000-000000000000",
            "author": "OpenCastor Migration",
        }
        changes.append("Added missing 'metadata' section")

    if "agent" not in config and "brain" in config:
        config["agent"] = config.pop("brain")
        changes.append("Renamed 'brain' to 'agent'")

    if "rcan_protocol" not in config:
        config["rcan_protocol"] = {
            "port": 8000,
            "capabilities": ["status"],
            "enable_mdns": False,
            "enable_jwt": False,
        }
        changes.append("Added missing 'rcan_protocol' section")

    if "network" not in config:
        config["network"] = {
            "telemetry_stream": True,
            "sim_to_real_sync": False,
            "allow_remote_override": False,
        }
        changes.append("Added missing 'network' section")

    # Update version if changes were made
    if changes:
        config["rcan_version"] = CURRENT_VERSION
        changes.append(f"Updated rcan_version to {CURRENT_VERSION}")

    return config, changes


def migrate_file(config_path: str, dry_run: bool = False, backup: bool = True) -> bool:
    """Migrate an RCAN config file in-place.

    Args:
        config_path: Path to the ``.rcan.yaml`` file.
        dry_run: If True, show changes without modifying the file.
        backup: If True, create a ``.bak`` copy before modifying.

    Returns:
        True if migration was applied, False if no changes were needed.
    """
    if not os.path.exists(config_path):
        print(f"  File not found: {config_path}")
        return False

    with open(config_path) as f:
        config = yaml.safe_load(f)

    from_version = get_version(config)
    migrated, changes = migrate_config(config, dry_run=dry_run)

    if not changes:
        print(f"  {config_path}: already at {CURRENT_VERSION}, no migration needed.")
        return False

    # Print diff
    try:
        from rich.console import Console

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    if has_rich:
        console.print(f"\n  [bold]Migration: {config_path}[/]")
        console.print(f"  From: [yellow]{from_version}[/] -> To: [green]{CURRENT_VERSION}[/]\n")
        console.print("  Changes:")
        for change in changes:
            console.print(f"    [cyan]+[/] {change}")
    else:
        print(f"\n  Migration: {config_path}")
        print(f"  From: {from_version} -> To: {CURRENT_VERSION}\n")
        print("  Changes:")
        for change in changes:
            print(f"    + {change}")

    if dry_run:
        if has_rich:
            console.print("\n  [dim](dry run -- no changes written)[/]\n")
        else:
            print("\n  (dry run -- no changes written)\n")
        return False

    # Create backup
    if backup:
        backup_path = config_path + ".bak"
        import shutil

        shutil.copy2(config_path, backup_path)
        print(f"\n  Backup: {backup_path}")

    # Write migrated config
    with open(config_path, "w") as f:
        yaml.dump(migrated, f, sort_keys=False, default_flow_style=False)

    print(f"  Updated: {config_path}\n")
    return True
