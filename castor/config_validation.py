"""RCAN config validation for OpenCastor.

Validates that a loaded ``.rcan.yaml`` config has all required top-level keys
and critical nested fields before the gateway or runtime tries to use them.
Call :func:`validate_rcan_config` early in startup to fail fast with a helpful
message rather than a cryptic KeyError deep in provider/driver initialisation.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

logger = logging.getLogger("OpenCastor.ConfigValidation")

# Required top-level keys in a .rcan.yaml file
REQUIRED_TOP_LEVEL: List[str] = [
    "rcan_version",
    "metadata",
    "agent",
    "physics",
    "drivers",
    "network",
    "rcan_protocol",
]

# Required keys inside the 'agent' block
REQUIRED_AGENT_KEYS: List[str] = ["model"]

# Required keys inside the 'metadata' block
REQUIRED_METADATA_KEYS: List[str] = ["robot_name"]


def validate_rcan_config(config: dict) -> Tuple[bool, List[str]]:
    """Validate a loaded RCAN config dict.

    Checks for required top-level keys, required nested keys, and that the
    ``drivers`` list is non-empty.

    Returns:
        A ``(is_valid, errors)`` tuple.  ``is_valid`` is ``True`` only when
        ``errors`` is empty.  Each entry in ``errors`` is a human-readable
        description of what is missing or wrong.

    Example::

        ok, errors = validate_rcan_config(config)
        if not ok:
            for msg in errors:
                logger.error("Config error: %s", msg)
    """
    if not isinstance(config, dict):
        return False, ["Config must be a dict (check YAML syntax)"]

    errors: List[str] = []

    # ── Top-level keys ────────────────────────────────────────────────────────
    for key in REQUIRED_TOP_LEVEL:
        if key not in config:
            errors.append(f"Missing required top-level key: '{key}'")

    # ── agent block ───────────────────────────────────────────────────────────
    agent = config.get("agent")
    if isinstance(agent, dict):
        for key in REQUIRED_AGENT_KEYS:
            if not agent.get(key):
                errors.append(f"Missing or empty required key: 'agent.{key}'")
    elif "agent" in config:
        errors.append("'agent' must be a mapping (dict), not a scalar")

    # ── metadata block ────────────────────────────────────────────────────────
    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        for key in REQUIRED_METADATA_KEYS:
            if not metadata.get(key):
                errors.append(f"Missing or empty required key: 'metadata.{key}'")
    elif "metadata" in config:
        errors.append("'metadata' must be a mapping (dict), not a scalar")

    # ── drivers list ──────────────────────────────────────────────────────────
    drivers = config.get("drivers")
    if drivers is not None:
        if not isinstance(drivers, list):
            errors.append("'drivers' must be a list")
        elif len(drivers) == 0:
            errors.append(
                "'drivers' is an empty list — add at least one driver entry "
                "(or use --simulate to skip hardware)"
            )

    return len(errors) == 0, errors


def log_validation_result(config: dict, label: str = "RCAN config") -> bool:
    """Validate *config* and log each error.  Returns True if valid."""
    ok, errors = validate_rcan_config(config)
    if ok:
        logger.debug("%s validation passed", label)
    else:
        for msg in errors:
            logger.error("%s validation error: %s", label, msg)
    return ok
