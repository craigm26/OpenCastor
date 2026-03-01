"""Tests for castor/action_validator.py — structured action validation (issue #271)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import castor.action_validator as _mod
from castor.action_validator import ActionValidator, ValidationResult, validate_action

# ── Helpers ───────────────────────────────────────────────────────────────────


def _reset_singleton():
    _mod._default_validator = None


# ── ValidationResult dataclass ────────────────────────────────────────────────


def test_validation_result_defaults():
    r = ValidationResult(valid=True, action_type="move")
    assert r.errors == []
    assert r.warnings == []


# ── Null / bad input ──────────────────────────────────────────────────────────


def test_validate_none_returns_invalid():
    r = validate_action(None)
    assert r.valid is False
    assert any("dict" in e for e in r.errors)


def test_validate_list_returns_invalid():
    r = validate_action([])
    assert r.valid is False


def test_validate_missing_type_returns_invalid():
    r = validate_action({"linear": 0.5})
    assert r.valid is False
    assert any("type" in e for e in r.errors)


def test_validate_empty_type_returns_invalid():
    r = validate_action({"type": ""})
    assert r.valid is False


# ── Known action types ────────────────────────────────────────────────────────


def test_validate_move_valid():
    r = validate_action({"type": "move", "linear": 0.5, "angular": 0.0})
    assert r.valid is True
    assert r.action_type == "move"
    assert r.errors == []


def test_validate_stop_valid():
    r = validate_action({"type": "stop"})
    assert r.valid is True


def test_validate_wait_valid():
    r = validate_action({"type": "wait", "duration_s": 2.0})
    assert r.valid is True


def test_validate_grip_valid():
    r = validate_action({"type": "grip", "position": 0.8, "force": 0.5})
    assert r.valid is True


def test_validate_nav_waypoint_valid():
    r = validate_action({"type": "nav_waypoint", "distance_m": 1.0, "heading_deg": 90.0})
    assert r.valid is True


# ── Schema violations ─────────────────────────────────────────────────────────


def test_validate_move_speed_out_of_range():
    r = validate_action({"type": "move", "speed": 5.0})
    assert r.valid is False
    assert r.errors


def test_validate_wait_negative_duration():
    r = validate_action({"type": "wait", "duration_s": -1.0})
    assert r.valid is False


def test_validate_grip_position_out_of_range():
    r = validate_action({"type": "grip", "position": 1.5})
    assert r.valid is False


def test_validate_nav_waypoint_negative_distance():
    r = validate_action({"type": "nav_waypoint", "distance_m": -0.5})
    assert r.valid is False


# ── Unknown action types ──────────────────────────────────────────────────────


def test_validate_unknown_type_returns_valid_with_warning():
    r = validate_action({"type": "fly"})
    assert r.valid is True
    assert any("unknown" in w for w in r.warnings)


# ── Unknown fields warn ───────────────────────────────────────────────────────


def test_validate_move_unknown_field_warns():
    r = validate_action({"type": "move", "linear": 0.3, "turbo": True})
    assert r.valid is True
    assert any("turbo" in w for w in r.warnings)


def test_validate_stop_extra_fields_allowed():
    # stop schema has additionalProperties: True — no warnings expected
    r = validate_action({"type": "stop", "extra": "yes"})
    assert r.valid is True
    assert not any("extra" in w for w in r.warnings)


# ── Custom schemas ────────────────────────────────────────────────────────────


def test_custom_schema_overrides_builtin():
    v = ActionValidator(
        custom_schemas={
            "move": {
                "type": "object",
                "required": ["type", "custom_field"],
                "properties": {
                    "type": {"type": "string"},
                    "custom_field": {"type": "string"},
                },
            }
        }
    )
    r = v.validate({"type": "move", "linear": 0.5})  # missing custom_field
    assert r.valid is False


def test_custom_schema_new_type():
    v = ActionValidator(
        custom_schemas={
            "spray": {
                "type": "object",
                "required": ["type"],
                "properties": {
                    "type": {"type": "string"},
                    "duration_s": {"type": "number", "minimum": 0},
                },
            }
        }
    )
    r = v.validate({"type": "spray", "duration_s": 3.0})
    assert r.valid is True
    assert r.action_type == "spray"


# ── known_types ───────────────────────────────────────────────────────────────


def test_known_types_includes_builtins():
    v = ActionValidator()
    types = v.known_types()
    assert "move" in types
    assert "stop" in types
    assert "wait" in types
    assert "grip" in types
    assert "nav_waypoint" in types


def test_known_types_sorted():
    v = ActionValidator()
    types = v.known_types()
    assert types == sorted(types)


# ── jsonschema not installed fallback ─────────────────────────────────────────


def test_validate_without_jsonschema_returns_valid_with_warning():
    import builtins

    real_import = builtins.__import__

    def _block_jsonschema(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("no jsonschema")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=_block_jsonschema):
        # Need a fresh validator that hasn't cached the import
        v = ActionValidator()
        r = v.validate({"type": "move", "linear": 0.5})

    assert r.valid is True
    assert any("jsonschema" in w for w in r.warnings)


# ── Singleton / get_validator ─────────────────────────────────────────────────


def test_get_validator_returns_same_instance():
    _reset_singleton()
    from castor.action_validator import get_validator

    a = get_validator()
    b = get_validator()
    assert a is b


def test_get_validator_with_custom_schemas_creates_new():
    _reset_singleton()
    from castor.action_validator import get_validator

    a = get_validator()
    b = get_validator(custom_schemas={"x": {"type": "object"}})
    assert a is not b


# ── API endpoints ─────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    import castor.api as _api

    return TestClient(_api.app)


def test_api_action_validate_valid(client):
    resp = client.post("/api/action/validate", json={"type": "move", "linear": 0.5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["action_type"] == "move"
    assert "errors" in data
    assert "warnings" in data


def test_api_action_validate_invalid(client):
    resp = client.post("/api/action/validate", json={"type": "grip", "position": 99.9})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert data["errors"]


def test_api_action_schemas(client):
    resp = client.get("/api/action/schemas")
    assert resp.status_code == 200
    data = resp.json()
    assert "types" in data
    assert "move" in data["types"]
