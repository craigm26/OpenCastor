"""Unit tests for deploy/fleet-ops/fleet_metrics.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parents[1] / "deploy" / "fleet-ops" / "fleet_metrics.py"
_SPEC = importlib.util.spec_from_file_location("fleet_metrics", _MOD_PATH)
assert _SPEC and _SPEC.loader
fleet_metrics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fleet_metrics)


def test_parse_nodes():
    parsed = fleet_metrics.parse_nodes("bob=http://127.0.0.1:8001, ethan=http://ethan.local:8001")
    assert parsed == [("bob", "http://127.0.0.1:8001"), ("ethan", "http://ethan.local:8001")]


def test_render_prometheus_healthy_and_faulted():
    snaps = [
        {
            "node": "bob",
            "reachable": True,
            "status": {
                "estopped": False,
                "motor_voltages_v": {"shoulder_pan": 12.0, "gripper": 11.9},
                "motor_temps_c": {"shoulder_pan": 26.0},
                "motor_loads": {"shoulder_lift": 52},
            },
        },
        {
            "node": "ethan",
            "reachable": False,
            "status": {},
        },
    ]
    text = fleet_metrics.render_prometheus(snaps)
    assert 'castor_node_reachable{node="bob"} 1' in text
    assert 'castor_node_reachable{node="ethan"} 0' in text
    assert 'castor_node_estopped{node="bob"} 0' in text
    assert 'castor_servo_voltage_volts{node="bob",joint="shoulder_pan"} 12.0' in text
    assert 'castor_servo_temp_celsius{node="bob",joint="shoulder_pan"} 26.0' in text
    assert 'castor_servo_load{node="bob",joint="shoulder_lift"} 52.0' in text
