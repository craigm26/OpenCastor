"""RCAN config validation for OpenCastor.

Validates that a loaded ``.rcan.yaml`` config has all required top-level keys
and critical nested fields before the gateway or runtime tries to use them.
Call :func:`validate_rcan_config` early in startup to fail fast with a helpful
message rather than a cryptic KeyError deep in provider/driver initialisation.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("OpenCastor.ConfigValidation")

# Required top-level keys in a .rcan.yaml file
REQUIRED_TOP_LEVEL: list[str] = [
    "rcan_version",
    "metadata",
    "agent",
    "physics",
    "drivers",
    "network",
    "rcan_protocol",
]

# Optional top-level keys recognised by RCAN v1.3+
OPTIONAL_TOP_LEVEL: list[str] = [
    "security_level",  # v1.3: deployment security posture (e.g. "standard", "high")
    "profile",  # HLabs / hardware profile name (e.g. "hlabs/acb-single")
    "reactive",
    "tiered_brain",
    "agent_roster",
    "learner",
    "camera",
    "offline_fallback",
    "geofence",
    "hailo_vision",
    "hailo_confidence",
    "interpreter",
    "memory",  # v1.3+: memory.compaction for rolling context compression
]

# Required keys inside the 'agent' block
REQUIRED_AGENT_KEYS: list[str] = ["model"]

# Optional keys inside the 'agent' block (recognised by RCAN v1.3+)
OPTIONAL_AGENT_KEYS: list[str] = [
    "provider",
    "vision_enabled",
    "latency_budget_ms",
    "safety_stop",
    "confidence_gates",  # v1.3: list of confidence gate definitions
    "hitl_gates",  # v1.3: list of HiTL gate definitions
    "security_level",  # v1.3: agent-level security override
]

# Required keys inside the 'metadata' block
REQUIRED_METADATA_KEYS: list[str] = ["robot_name"]


def validate_rcan_config(config: dict) -> tuple[bool, list[str]]:
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

    errors: list[str] = []

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

        # ── RCAN v1.3: confidence_gates ───────────────────────────────────
        confidence_gates = agent.get("confidence_gates")
        if confidence_gates is not None:
            if not isinstance(confidence_gates, list):
                errors.append("'agent.confidence_gates' must be a list")
            else:
                _VALID_CONF_ON_FAIL = {"block", "escalate", "allow"}
                for i, gate in enumerate(confidence_gates):
                    if not isinstance(gate, dict):
                        errors.append(f"agent.confidence_gates[{i}] must be a mapping (dict)")
                        continue
                    for field in ("scope", "min_confidence", "on_fail"):
                        if field not in gate:
                            errors.append(
                                f"agent.confidence_gates[{i}] is missing required field '{field}'"
                            )
                    on_fail = gate.get("on_fail")
                    if on_fail is not None and on_fail not in _VALID_CONF_ON_FAIL:
                        errors.append(
                            f"agent.confidence_gates[{i}].on_fail must be one of "
                            f"{sorted(_VALID_CONF_ON_FAIL)}, got '{on_fail}'"
                        )

        # ── RCAN v1.3: hitl_gates ─────────────────────────────────────────
        hitl_gates = agent.get("hitl_gates")
        if hitl_gates is not None:
            if not isinstance(hitl_gates, list):
                errors.append("'agent.hitl_gates' must be a list")
            else:
                _VALID_HITL_ON_FAIL = {"block", "allow"}
                for i, gate in enumerate(hitl_gates):
                    if not isinstance(gate, dict):
                        errors.append(f"agent.hitl_gates[{i}] must be a mapping (dict)")
                        continue
                    for field in ("action_types", "require_auth"):
                        if field not in gate:
                            errors.append(
                                f"agent.hitl_gates[{i}] is missing required field '{field}'"
                            )
                    on_fail = gate.get("on_fail")
                    if on_fail is not None and on_fail not in _VALID_HITL_ON_FAIL:
                        errors.append(
                            f"agent.hitl_gates[{i}].on_fail must be one of "
                            f"{sorted(_VALID_HITL_ON_FAIL)}, got '{on_fail}'"
                        )

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

    # ── offline_fallback block (optional) ─────────────────────────────────────
    offline_fb = config.get("offline_fallback")
    if offline_fb is not None:
        if not isinstance(offline_fb, dict):
            errors.append("'offline_fallback' must be a mapping (dict), not a scalar")
        elif offline_fb.get("enabled"):
            provider = str(offline_fb.get("provider", "")).lower().strip()
            _VALID_FALLBACK_PROVIDERS = {"ollama", "llamacpp", "mlx", "apple"}
            if provider not in _VALID_FALLBACK_PROVIDERS:
                errors.append(
                    f"offline_fallback.provider must be one of: "
                    f"{', '.join(sorted(_VALID_FALLBACK_PROVIDERS))} "
                    f"(got '{provider or '<empty>'}')"
                )

    # ── interpreter block ────────────────────────────────────────────────────
    if "interpreter" in config:
        interp = config["interpreter"]
        if not isinstance(interp, dict):
            errors.append(f"interpreter must be a mapping (got {type(interp).__name__!r})")
            return len(errors) == 0, errors
        valid_backends = {"auto", "local", "local_extended", "gemini", "mock"}
        backend = str(interp.get("backend", "auto"))
        if backend not in valid_backends:
            errors.append(
                f"interpreter.backend must be one of {sorted(valid_backends)} (got '{backend}')"
            )
        gemini_cfg = interp.get("gemini", {})
        if isinstance(gemini_cfg, dict):
            dims = gemini_cfg.get("dimensions", 1536)
            if dims not in (768, 1536, 3072):
                errors.append(
                    f"interpreter.gemini.dimensions must be 768, 1536, or 3072 (got {dims})"
                )
        if backend == "gemini":
            import os as _os

            if not _os.getenv("GOOGLE_API_KEY"):
                logger.warning(
                    "interpreter.backend=gemini but GOOGLE_API_KEY not set — "
                    "will run in mock mode at runtime"
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
