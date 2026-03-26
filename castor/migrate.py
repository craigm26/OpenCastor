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
CURRENT_VERSION = "2.1"

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
# Migration chain: 1.0.0-alpha → 1.1 → 1.2 → 1.3 → 1.4
# ---------------------------------------------------------------------------


@_register_migration("1.0.0-alpha", "1.1")
def _migrate_1_0_alpha_to_1_1(config: dict) -> dict:
    """Migrate from 1.0.0-alpha to 1.1.

    v1.1 introduced the AI Accountability Layer (§16).  No structural changes
    are required for existing configs — the new section is optional.
    """
    config["rcan_version"] = "1.1"
    return config


@_register_migration("1.1", "1.2")
def _migrate_1_1_to_1_2(config: dict) -> dict:
    """Migrate from 1.1 to 1.2.

    v1.2 added §17–§20 (Distributed Registry, Capability Advertisement,
    INVOKE, Telemetry Fields) and Appendix B (WebSocket Transport).
    All new sections are optional; no structural changes needed.
    """
    config["rcan_version"] = "1.2"
    return config


@_register_migration("1.2", "1.3")
def _migrate_1_2_to_1_3(config: dict) -> dict:
    """Migrate from 1.2 to 1.3.

    v1.3 stabilises §18–20 + Appendix B and adds §21 (Registry Integration,
    REGISTRY_REGISTER MessageType=13, REGISTRY_RESOLVE MessageType=14,
    INVOKE_CANCEL MessageType=15).  No structural changes required.
    """
    config["rcan_version"] = "1.3"
    return config


@_register_migration("1.3", "1.4")
def _migrate_1_3_to_1_4(config: dict) -> dict:
    """Migrate from 1.3 to 1.4.

    v1.4 adds §22 (Capability Advertisement), extends §17 node manifest with
    ``hw_uid`` and ``trust_level`` fields, and stabilises all L4 registry
    tests.  No structural changes are required for existing configs — the new
    fields are optional.
    """
    config["rcan_version"] = "1.4"
    return config


@_register_migration("1.4", "1.5")
def _migrate_1_4_to_1_5(config: dict) -> dict:
    """Migrate from 1.4 to 1.5 (no structural changes)."""
    config["rcan_version"] = "1.5"
    return config


@_register_migration("1.5", "1.6")
def _migrate_1_5_to_1_6(config: dict) -> dict:
    """Migrate from 1.5 to 1.6 (no structural changes)."""
    config["rcan_version"] = "1.6"
    return config


@_register_migration("1.6", "1.7")
def _migrate_1_6_to_1_7(config: dict) -> dict:
    """Migrate from 1.6 to 1.7 (no structural changes)."""
    config["rcan_version"] = "1.7"
    return config


@_register_migration("1.7", "1.8")
def _migrate_1_7_to_1_8(config: dict) -> dict:
    """Migrate from 1.7 to 1.8 (no structural changes)."""
    config["rcan_version"] = "1.8"
    return config


@_register_migration("1.8", "1.9")
def _migrate_1_8_to_1_9(config: dict) -> dict:
    """Migrate from 1.8 to 1.9 (no structural changes)."""
    config["rcan_version"] = "1.9"
    return config


@_register_migration("1.9", "1.10")
def _migrate_1_9_to_1_10(config: dict) -> dict:
    """Migrate from 1.9 to 1.10 (no structural changes)."""
    config["rcan_version"] = "1.10"
    return config


@_register_migration("1.10", "2.1")
def _migrate_1_10_to_2_1(config: dict) -> dict:
    """Migrate from 1.10 to 2.1 — RCAN v2.1 breaking changes.

    RCAN v2.1 is a clean break from v1.x:
    - rcan_version bumped to "2.1"
    - firmware_hash placeholder added (SHA-256 of firmware manifest)
    - attestation_ref placeholder added (SBOM well-known URL)
    - pending_signatures list populated with any "pending" signature blocks
      (signature:"pending" is hard-rejected in v2.1; callers must sign or remove)
    - deprecated_aliases list populated with any deprecated alias usages found
      (FEDERATION_SYNC, ALERT, AUDIT removed in v2.1)
    """
    config["rcan_version"] = "2.1"

    # Add firmware attestation stubs so operators know they need to run
    # `castor attest generate && castor attest sign`
    if not config.get("firmware_hash"):
        config["firmware_hash"] = None  # populate via `castor attest generate`
        logger.warning(
            "firmware_hash not set — run `castor attest generate && castor attest sign` "
            "to satisfy RCAN v2.1 §11 and EU AI Act Art. 16(d)."
        )

    if not config.get("attestation_ref"):
        config["attestation_ref"] = None  # populate via `castor sbom generate`
        logger.warning(
            "attestation_ref not set — run `castor sbom generate && castor sbom publish` "
            "to satisfy RCAN v2.1 §12 and EU AI Act Art. 16(a)."
        )

    # Flag any deprecated alias usages in harness_yaml keys
    deprecated_aliases = {"FEDERATION_SYNC", "ALERT", "AUDIT"}
    found_aliases = []
    for key in config:
        if isinstance(key, str) and key.upper() in deprecated_aliases:
            found_aliases.append(key)
    if found_aliases:
        config.setdefault("_migration_warnings", []).append(
            f"Deprecated RCAN v1.x message type aliases in config: {found_aliases}. "
            "These are removed in v2.1 — update to canonical type names."
        )
        logger.warning(
            "RCAN v2.1 migration: deprecated aliases found in config: %s", found_aliases
        )

    return config


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
