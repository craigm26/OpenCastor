"""A motor velocity that is not a finite number is refused, and the motors are stopped.

Before this, SafetyLayer._clamp_motor_data clamped with min()/max(), and ``min(hi, nan)`` is
``hi``: a NaN linear velocity from a model was written to /dev/motor as full speed. ``True`` was
read as 1.0, a string as whatever it parsed to, and ``None`` raised TypeError out of write().
Found by the EV-03 hostile-model test (malformed commands).
"""

import math

import pytest

from castor.fs.namespace import Namespace
from castor.fs.permissions import PermissionTable
from castor.fs.safety import SafetyLayer


def _safety() -> SafetyLayer:
    sl = SafetyLayer(
        Namespace(),
        PermissionTable(),
        limits={"motor_rate_hz": 1000.0, "motor_linear_range": (-0.4, 0.4)},
    )
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.2, "angular": 0.1})
    return sl


def _events(sl: SafetyLayer) -> list[str]:
    rows = sl.ns.read("/var/log/safety")
    return [row.get("event") for row in rows] if isinstance(rows, list) else []


BAD = [
    ("linear", math.nan),
    ("angular", math.nan),
    ("linear", math.inf),
    ("linear", -math.inf),
    ("angular", math.inf),
    ("linear", True),
    ("linear", "0.2"),
    ("linear", None),
    ("angular", [0.1]),
]


@pytest.mark.parametrize("field,value", BAD)
def test_invalid_velocity_is_refused_and_stops_the_motors(field, value):
    sl = _safety()
    cmd = {"type": "move", "linear": 0.1, "angular": 0.0, field: value}
    assert sl.write("/dev/motor", cmd, principal="brain") is False
    assert sl.ns.read("/dev/motor") == {"type": "stop"}
    assert "Invalid motor command" in sl.last_write_denial
    assert "invalid_motor_command" in _events(sl)


def test_an_invalid_velocity_stops_every_registered_driver():
    sl = _safety()
    stops = []
    sl.add_motor_halt(lambda: stops.append("a"))
    sl.add_motor_halt(lambda: stops.append("b"))
    sl.write("/dev/motor", {"type": "move", "linear": math.nan}, principal="api")
    assert stops == ["a", "b"]


def test_a_driver_whose_stop_fails_does_not_undo_the_refusal():
    sl = _safety()
    stops = []

    def broken():
        raise RuntimeError("serial port gone")

    sl.add_motor_halt(broken)
    sl.add_motor_halt(lambda: stops.append("other"))
    assert sl.write("/dev/motor", {"type": "move", "linear": math.inf}, principal="api") is False
    assert sl.ns.read("/dev/motor") == {"type": "stop"}
    assert stops == ["other"]


def test_a_removed_driver_is_not_stopped():
    sl = _safety()
    stops = []

    def halt():
        stops.append("stop")

    sl.add_motor_halt(halt)
    sl.add_motor_halt(halt)  # registering twice is one registration
    sl.remove_motor_halt(halt)
    sl.write("/dev/motor", {"type": "move", "linear": math.nan}, principal="api")
    assert stops == []


def test_nan_is_no_longer_clamped_to_full_speed():
    sl = _safety()
    sl.write("/dev/motor", {"type": "move", "linear": math.nan, "angular": 0.0}, principal="brain")
    motor = sl.ns.read("/dev/motor")
    assert motor.get("linear", 0.0) == 0.0


@pytest.mark.parametrize("linear,expected", [(0.1, 0.1), (5, 0.4), (-2.5, -0.4), (0, 0.0)])
def test_finite_numbers_are_clamped_as_before(linear, expected):
    sl = _safety()
    assert sl.write(
        "/dev/motor", {"type": "move", "linear": linear, "angular": 0.0}, principal="brain"
    )
    assert sl.ns.read("/dev/motor")["linear"] == pytest.approx(expected)


def test_missing_velocity_fields_are_still_allowed():
    sl = _safety()
    assert sl.write("/dev/motor", {"type": "move", "angular": 0.3}, principal="brain")
    assert sl.write("/dev/motor", {"type": "stop"}, principal="brain")
