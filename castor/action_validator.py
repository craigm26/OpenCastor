"""
Structured action validation for OpenCastor (#271).

Validates parsed robot action dicts against built-in or custom JSON schemas.
Uses jsonschema (>=4.20.0, already a core dependency) for schema enforcement.

Usage::

    from castor.action_validator import validate_action, ActionValidator

    # Validate a move action
    result = validate_action({"type": "move", "linear": 0.5, "angular": 0.0})
    if not result.valid:
        for err in result.errors:
            print(f"ERROR: {err}")

    # Custom schemas merged with built-ins
    validator = ActionValidator(custom_schemas={"spray": {
        "type": "object",
        "required": ["type"],
        "properties": {
            "type": {"type": "string"},
            "duration_s": {"type": "number", "minimum": 0},
        },
    }})
    result = validator.validate({"type": "spray", "duration_s": 2.0})
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.ActionValidator")

# ---------------------------------------------------------------------------
# Built-in JSON schemas for the core action vocabulary
# ---------------------------------------------------------------------------

_ACTION_SCHEMAS: dict[str, dict] = {
    "move": {
        "type": "object",
        "required": ["type"],
        "properties": {
            "type": {"type": "string"},
            "linear": {"type": "number"},
            "angular": {"type": "number"},
            "linear_x": {"type": "number"},  # legacy alias
            "angular_z": {"type": "number"},  # legacy alias
            "speed": {"type": "number", "minimum": 0, "maximum": 1},
            "duration_s": {"type": "number", "minimum": 0},
        },
    },
    "stop": {
        "type": "object",
        "required": ["type"],
        "properties": {
            "type": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "wait": {
        "type": "object",
        "required": ["type"],
        "properties": {
            "type": {"type": "string"},
            "duration_s": {"type": "number", "minimum": 0},
        },
    },
    "grip": {
        "type": "object",
        "required": ["type"],
        "properties": {
            "type": {"type": "string"},
            "position": {"type": "number", "minimum": 0, "maximum": 1},
            "force": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
    "nav_waypoint": {
        "type": "object",
        "required": ["type"],
        "properties": {
            "type": {"type": "string"},
            "distance_m": {"type": "number", "minimum": 0},
            "heading_deg": {"type": "number"},
            "speed": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Result of validating a single action dict."""

    valid: bool
    action_type: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ActionValidator
# ---------------------------------------------------------------------------


class ActionValidator:
    """Validate robot action dicts against built-in or custom JSON schemas.

    Args:
        custom_schemas: Dict mapping action type name → JSON schema dict.
                        These are merged with the built-in schemas; custom
                        entries take precedence over built-ins of the same name.
    """

    def __init__(self, custom_schemas: Optional[dict[str, dict]] = None) -> None:
        self._schemas: dict[str, dict] = {**_ACTION_SCHEMAS}
        self._schema_sources: dict[str, str] = {t: "builtin" for t in _ACTION_SCHEMAS}
        if custom_schemas:
            self._schemas.update(custom_schemas)
            for t in custom_schemas:
                self._schema_sources[t] = "rcan_config"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, action: dict[str, Any]) -> ValidationResult:
        """Validate an action dict.

        Args:
            action: Parsed action dictionary (e.g. ``{"type": "move", "linear": 0.5}``).

        Returns:
            A :class:`ValidationResult` describing whether the action is valid,
            any hard errors, and any non-fatal warnings.
        """
        # Step 1 — must be a non-None dict
        if action is None or not isinstance(action, dict):
            return ValidationResult(
                valid=False,
                action_type="",
                errors=["action must be a dict"],
            )

        # Step 2 — must contain a "type" key with a non-None value
        action_type = action.get("type")
        if not action_type:
            return ValidationResult(
                valid=False,
                action_type="",
                errors=["missing 'type' field"],
            )

        action_type = str(action_type)

        # Step 3 — look up schema; unknown types pass with a warning
        schema = self._schemas.get(action_type)
        if schema is None:
            logger.debug("Unknown action type '%s' — skipping validation", action_type)
            return ValidationResult(
                valid=True,
                action_type=action_type,
                warnings=[f"unknown action type '{action_type}' — skipping validation"],
            )

        # Step 4 — attempt jsonschema validation (lazy import)
        try:
            import jsonschema  # noqa: PLC0415
        except ImportError:
            logger.warning(
                "jsonschema not installed — skipping schema validation for '%s'", action_type
            )
            return ValidationResult(
                valid=True,
                action_type=action_type,
                warnings=["jsonschema not installed — skipping schema validation"],
            )

        try:
            jsonschema.validate(action, schema)
        except jsonschema.ValidationError as exc:
            return ValidationResult(
                valid=False,
                action_type=action_type,
                errors=[exc.message],
            )

        # Step 5 — warn about unknown properties when additionalProperties is
        #           not explicitly set to True
        warnings: list[str] = []
        schema_props = schema.get("properties", {})
        allows_additional = schema.get("additionalProperties", False) is True
        if schema_props and not allows_additional:
            for key in action:
                if key not in schema_props:
                    warnings.append(f"unknown field '{key}' for action type '{action_type}'")
                    logger.debug("Action '%s' contains unknown field '%s'", action_type, key)

        return ValidationResult(valid=True, action_type=action_type, warnings=warnings)

    def known_types(self) -> list[str]:
        """Return a sorted list of known action type names."""
        return sorted(self._schemas.keys())

    def schema_source_for(self, action_type: str) -> str:
        """Return the source of the schema for *action_type*.

        Returns:
            ``"builtin"`` — schema is part of OpenCastor's built-in vocabulary.
            ``"rcan_config"`` — schema was loaded from the RCAN ``action_schemas`` block.
            ``"unknown"`` — no schema registered for this action type.
        """
        return self._schema_sources.get(action_type, "unknown")


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_default_validator: Optional[ActionValidator] = None
_validator_lock = threading.Lock()


def get_validator(custom_schemas: Optional[dict[str, dict]] = None) -> ActionValidator:
    """Return the singleton :class:`ActionValidator`.

    If *custom_schemas* is provided the singleton is (re-)created with those
    schemas merged in.  Subsequent calls without *custom_schemas* return the
    previously created instance.
    """
    global _default_validator
    with _validator_lock:
        if _default_validator is None or custom_schemas is not None:
            _default_validator = ActionValidator(custom_schemas=custom_schemas)
    return _default_validator


def validate_action(action: dict[str, Any]) -> ValidationResult:
    """Validate *action* using the default singleton :class:`ActionValidator`.

    This is the most convenient entry-point for one-off validation::

        from castor.action_validator import validate_action

        result = validate_action({"type": "move", "linear": 0.5})
        assert result.valid

    Args:
        action: Parsed action dictionary.

    Returns:
        :class:`ValidationResult`
    """
    return get_validator().validate(action)


def init_from_config(config: dict[str, Any]) -> ActionValidator:
    """Initialise the singleton validator with custom schemas from RCAN *config*.

    Reads the ``action_schemas`` mapping from the RCAN config dict and merges
    any entries with the built-in action schemas.  Custom entries take
    precedence over built-ins with the same action type name.

    Usage in RCAN config::

        action_schemas:
          spray:
            type: object
            required: [type]
            properties:
              type: {type: string}
              duration_s: {type: number, minimum: 0}

    Args:
        config: RCAN agent config dict (the top-level mapping).

    Returns:
        The (re-)initialised :class:`ActionValidator` singleton.
    """
    raw: dict[str, dict] = config.get("action_schemas") or {}
    if raw:
        logger.info(
            "ActionValidator: loading %d custom schema(s) from RCAN config: %s",
            len(raw),
            list(raw.keys()),
        )
    return get_validator(custom_schemas=raw if raw else None)
