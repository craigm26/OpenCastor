"""Tests for ActionValidator RCAN custom schema loading — issue #318."""

from __future__ import annotations

import pytest

from castor.action_validator import (
    _ACTION_SCHEMAS,
    ActionValidator,
    get_validator,
    init_from_config,
)

# ── schema_source_for ──────────────────────────────────────────────────────────


def test_schema_source_builtin_move():
    v = ActionValidator()
    assert v.schema_source_for("move") == "builtin"


def test_schema_source_builtin_stop():
    v = ActionValidator()
    assert v.schema_source_for("stop") == "builtin"


def test_schema_source_unknown_returns_unknown():
    v = ActionValidator()
    assert v.schema_source_for("nonexistent_type") == "unknown"


def test_schema_source_custom_returns_rcan_config():
    v = ActionValidator(custom_schemas={"spray": {"type": "object", "required": ["type"]}})
    assert v.schema_source_for("spray") == "rcan_config"


def test_schema_source_custom_overrides_builtin():
    """Custom schema that overrides a builtin is still labeled 'rcan_config'."""
    v = ActionValidator(custom_schemas={"move": {"type": "object", "required": ["type"]}})
    assert v.schema_source_for("move") == "rcan_config"


# ── _schema_sources dict ──────────────────────────────────────────────────────


def test_schema_sources_populated_for_builtins():
    v = ActionValidator()
    for t in _ACTION_SCHEMAS:
        assert v._schema_sources.get(t) == "builtin"


def test_schema_sources_populated_for_custom():
    v = ActionValidator(custom_schemas={"grip_custom": {"type": "object"}})
    assert v._schema_sources.get("grip_custom") == "rcan_config"


# ── init_from_config ──────────────────────────────────────────────────────────


def test_init_from_config_no_action_schemas():
    """Config without action_schemas key returns validator with only built-ins."""
    import threading

    import castor.action_validator as _av

    _av._default_validator = None
    _av._validator_lock = threading.Lock()

    result = init_from_config({"metadata": {"robot_name": "test"}})
    assert isinstance(result, ActionValidator)
    assert "move" in result.known_types()


def test_init_from_config_with_custom_schemas():
    import threading

    import castor.action_validator as _av

    _av._default_validator = None
    _av._validator_lock = threading.Lock()

    config = {
        "action_schemas": {
            "spray": {
                "type": "object",
                "required": ["type"],
                "properties": {
                    "type": {"type": "string"},
                    "duration_s": {"type": "number", "minimum": 0},
                },
            }
        }
    }
    v = init_from_config(config)
    assert "spray" in v.known_types()


def test_init_from_config_custom_schema_source():
    import threading

    import castor.action_validator as _av

    _av._default_validator = None
    _av._validator_lock = threading.Lock()

    v = init_from_config({"action_schemas": {"pour": {"type": "object"}}})
    assert v.schema_source_for("pour") == "rcan_config"


def test_init_from_config_empty_action_schemas():
    import threading

    import castor.action_validator as _av

    _av._default_validator = None
    _av._validator_lock = threading.Lock()

    v = init_from_config({"action_schemas": {}})
    assert "move" in v.known_types()


def test_init_from_config_null_action_schemas():
    """action_schemas: null in YAML → None in Python."""
    import threading

    import castor.action_validator as _av

    _av._default_validator = None
    _av._validator_lock = threading.Lock()

    v = init_from_config({"action_schemas": None})
    assert "move" in v.known_types()


def test_init_from_config_updates_singleton():
    import threading

    import castor.action_validator as _av

    _av._default_validator = None
    _av._validator_lock = threading.Lock()

    v1 = init_from_config({"action_schemas": {"a_type": {"type": "object"}}})
    # Subsequent call without custom_schemas should return the already-init singleton
    v2 = get_validator()
    assert v1 is v2


# ── validate result includes schema_source ─────────────────────────────────────


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    import castor.api as _api

    return TestClient(_api.app)


def test_api_validate_builtin_schema_source(client):
    resp = client.post("/api/action/validate", json={"type": "move", "linear": 0.5})
    assert resp.status_code == 200
    data = resp.json()
    assert "schema_source" in data
    assert data["schema_source"] == "builtin"


def test_api_validate_unknown_schema_source(client):
    resp = client.post("/api/action/validate", json={"type": "totally_custom_action"})
    assert resp.status_code == 200
    data = resp.json()
    assert "schema_source" in data
    assert data["schema_source"] == "unknown"


def test_api_action_schemas_lists_known_types(client):
    resp = client.get("/api/action/schemas")
    assert resp.status_code == 200
    types = resp.json().get("types", [])
    assert "move" in types
    assert "stop" in types
