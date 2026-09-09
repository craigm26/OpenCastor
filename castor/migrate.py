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
CURRENT_VERSION = "3.0"

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
        logger.warning("RCAN v2.1 migration: deprecated aliases found in config: %s", found_aliases)

    return config


@_register_migration("2.1", "2.2")
def _migrate_2_1_to_2_2(config: dict) -> dict:
    """Migrate from 2.1 to 2.2 — RCAN v2.2 additions.

    v2.2 highlights:
      - ML-DSA-65 becomes primary signing algorithm (Ed25519 deprecated but tolerated)
      - Dual-brain pattern: ``brain_reactive`` (VLA) + ``brain_planning`` (LLM)
      - M2M_TRUSTED trust mode with explicit ``fleet_rrns``
      - ISO 42001 + EU AI Act Art. 11/12 audit blocks

    Migration:
      - Bump ``rcan_version`` to "2.2"
      - If ``signing_alg`` absent, warn (operator should declare explicitly — ``ml-dsa-65``
        recommended; ``ed25519`` tolerated in 2.2 but rejected in 3.0)
    """
    config["rcan_version"] = "2.2"

    if not config.get("signing_alg") and not config.get("network", {}).get("signing_alg"):
        config.setdefault("_migration_warnings", []).append(
            "signing_alg not set — v2.2 recommends 'ml-dsa-65' (post-quantum). "
            "Leaving implicit; operator should set 'signing_alg: ml-dsa-65' explicitly "
            "because v3.0 rejects Ed25519-only profiles at L2+."
        )

    return config


@_register_migration("2.2", "3.0")
def _migrate_2_2_to_3_0(config: dict) -> dict:
    """Migrate from 2.2 to 3.0 — RCAN v3.0 EU AI Act compliance bump.

    v3.0 breaking changes:
      - Signatures mandatory at L2+ conformance; Ed25519-only rejected under
        the pqc-hybrid-v1 profile (§9).
      - ``fria_ref`` required in /.well-known/rcan-node.json for all Annex III
        high-risk systems (§22, §27).
      - New sections §23 (Safety Benchmark), §24 (Instructions for Use),
        §25 (Post-Market Monitoring), §26 (EU Register Submission),
        §27 (FRIA Protocol).

    Migration does NOT fabricate FRIA documents. Operators must run
    ``castor fria generate`` and sign the result separately. This migrator
    adds a ``fria_ref: None`` placeholder and emits a loud warning.
    """
    config["rcan_version"] = "3.0"

    # fria_ref placeholder — operator must populate via `castor fria generate`
    if "fria_ref" not in config:
        config["fria_ref"] = None
        config.setdefault("_migration_warnings", []).append(
            "fria_ref not set — v3.0 REQUIRES fria_ref for Annex III high-risk "
            "systems (RCAN §22, §27). Run `castor fria generate` to produce a "
            "Fundamental Rights Impact Assessment document, then set "
            "fria_ref to its URI. Without this, your node will be rejected "
            "at registration under L2+ conformance."
        )

    # Ed25519-only sunset check
    signing_alg = config.get("signing_alg") or config.get("network", {}).get("signing_alg")
    if signing_alg == "ed25519":
        config.setdefault("_migration_warnings", []).append(
            "signing_alg='ed25519' — v3.0 REJECTS Ed25519-only profiles at L2+. "
            "Switch to 'ml-dsa-65' or 'pqc-hybrid-v1' (recommended). The ed25519 "
            "key may remain as a secondary key inside a pqc-hybrid-v1 keyset, "
            "but must not be the primary."
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


# ---------------------------------------------------------------------------
# v3.0 one-shot converter: .rcan.yaml → ROBOT.md
# Deprecated at ship (v3.0.0). Scheduled for removal in v3.1.0.
# ---------------------------------------------------------------------------

_ROBOT_MD_BODY = """\
# {robot_name}

Migrated from legacy .rcan.yaml. This is a one-shot conversion; the
legacy format is deprecated and will be removed in opencastor 3.1.0.
"""


#: Blocks that describe the *body* rather than the brain, and that a migration must
#: therefore carry across untouched.
#:
#: ``drivers`` is the load-bearing one.  ``ComponentRegistry.get_driver`` returns
#: ``None`` the moment it is missing (``castor/registry.py:201-202``), so a migration
#: that drops it converts a configured robot into a brain with no body — and does it
#: silently, which is the worst possible way to fail.  The others are here because a
#: robot that keeps its driver and loses its velocity envelope or its camera is still
#: a different robot from the one that was migrated.
_CARRIED_BLOCKS = (
    "drivers",
    "connection",
    "physics",
    "sensors",
    "camera",
    "task_routing",
    "tiered_brain",
    "rcan_protocol",
)


def _convert_to_v32(old: dict) -> dict:
    """Translate a legacy .rcan.yaml dict to v3.2 ROBOT.md frontmatter.

    Everything in :data:`_CARRIED_BLOCKS` is copied across verbatim.  The agent block
    is restructured into v3.2 ``runtimes``; the body blocks are not restructured at
    all, because nothing about them changed between the formats and re-shaping a
    ``drivers`` entry is how you lose one.
    """
    agent = old.get("agent") or {}
    provider = agent.get("provider", "anthropic")
    model = agent.get("model", "claude-sonnet-4-6")
    out = {
        "rcan_version": "3.2",
        "metadata": old.get("metadata") or {},
        "network": old.get("network")
        or {
            "rrf_endpoint": "https://rcan.dev",
            "signing_alg": "pqc-hybrid-v1",
        },
        "agent": {
            "provider": provider,
            "model": model,
            "runtimes": [
                {
                    "id": "opencastor",
                    "harness": "castor-default",
                    "default": True,
                    "models": [
                        {"provider": provider, "model": model, "role": "primary"},
                    ],
                },
            ],
        },
        "safety": old.get("safety") or {},
    }
    for key in _CARRIED_BLOCKS:
        value = old.get(key)
        if value:
            out[key] = copy.deepcopy(value)
    return out


def dropped_blocks(old: dict, new: dict) -> list[str]:
    """Blocks present in *old* that did not survive into *new*. Empty means clean.

    A migration is allowed to restructure. It is not allowed to lose a robot's body
    without saying so, which is what this exists to detect.
    """
    return [key for key in _CARRIED_BLOCKS if old.get(key) and not new.get(key)]


def migrate_to_robot_md(src, dst) -> int:
    """Convert ``src`` (.rcan.yaml) to ``dst`` (ROBOT.md v3.2). Returns exit code.

    Deprecated at ship (opencastor 3.0.0). Scheduled for removal in 3.1.0.
    """
    import sys
    from pathlib import Path

    sys.stderr.write(
        "[castor migrate] legacy .rcan.yaml is deprecated. Output ROBOT.md is "
        "one-shot; future opencastor releases will remove this command.\n"
    )
    old = yaml.safe_load(Path(src).read_text()) or {}
    fm = _convert_to_v32(old)

    dropped = dropped_blocks(old, fm)
    if dropped:
        # Fail loudly. A migration that silently drops `drivers` produces a manifest
        # that loads, answers, and never moves a motor.
        sys.stderr.write(
            "[castor migrate] refusing to write a manifest that loses "
            + ", ".join(dropped)
            + f" from {src}.\n"
            "  Nothing was written. This is a bug in the migration, not in your "
            "config — please report it with the block name above.\n"
        )
        return 1

    robot_name = (fm["metadata"] or {}).get("robot_name", "robot")
    body = _ROBOT_MD_BODY.format(robot_name=robot_name)
    if fm.get("drivers"):
        protocols = ", ".join(
            str(d.get("protocol") or d.get("class") or "?") for d in fm["drivers"]
        )
        body += f"\nDrivers carried across: {protocols}.\n"
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    Path(dst).write_text(f"---\n{serialized}---\n\n{body}")
    return 0
