"""Tests for RCAN §20 telemetry field registry constants."""

from castor.rcan import telemetry_fields


def test_standard_fields_exist():
    assert telemetry_fields.JOINT_POSITION == "joint_position"
    assert telemetry_fields.LINEAR_VELOCITY == "linear_velocity"
    assert telemetry_fields.BATTERY_PERCENT == "battery_percent"
    assert telemetry_fields.ESTOP_ACTIVE == "estop_active"
    assert telemetry_fields.CPU_PERCENT == "cpu_percent"


def test_field_names_are_snake_case():
    """All field names should be snake_case strings."""
    import re

    snake = re.compile(r"^[a-z][a-z0-9_]*$")
    for attr in dir(telemetry_fields):
        if attr.startswith("_"):
            continue
        val = getattr(telemetry_fields, attr)
        if isinstance(val, str):
            assert snake.match(val), f"{attr} = '{val}' is not snake_case"
