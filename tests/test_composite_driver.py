"""Tests for castor/drivers/composite.py — Composite driver (issue #96)."""

import pytest

from castor.drivers.composite import CompositeDriver


def _make_config(subsystems=None, routing=None):
    """Return a minimal RCAN-like config dict for CompositeDriver."""
    return {
        "drivers": [
            {
                "id": "full_robot",
                "protocol": "composite",
                "subsystems": subsystems or [
                    {"id": "base", "protocol": "mock"},
                ],
                "routing": routing or {
                    "linear": "base",
                    "angular": "base",
                    "throttle": "base",
                },
            }
        ]
    }


def test_instantiation():
    """CompositeDriver should instantiate without raising."""
    drv = CompositeDriver(_make_config())
    assert drv is not None


def test_stop_does_not_raise():
    drv = CompositeDriver(_make_config())
    drv.stop()  # should not raise even with no real hardware


def test_close_does_not_raise():
    drv = CompositeDriver(_make_config())
    drv.close()


def test_move_float_pair():
    """move(linear, angular) form should not raise."""
    drv = CompositeDriver(_make_config())
    drv.move(0.5, 0.0)
    drv.move(-0.3, 0.1)
    drv.stop()


def test_move_action_dict_stop():
    drv = CompositeDriver(_make_config())
    drv.move({"type": "stop"})


def test_move_action_dict_move():
    drv = CompositeDriver(_make_config())
    drv.move({"type": "move", "linear": 0.4, "angular": -0.2})


def test_health_check_returns_dict():
    drv = CompositeDriver(_make_config())
    h = drv.health_check()
    assert isinstance(h, dict)
    assert "ok" in h
    assert "subsystems" in h


def test_missing_composite_driver_section():
    """If no composite driver in config, init should still succeed (empty subsystems)."""
    drv = CompositeDriver({"drivers": []})
    drv.stop()  # no-op, no crash


def test_unknown_subsystem_protocol():
    """Unknown protocol should fall back to NullDriver, not raise."""
    cfg = _make_config(subsystems=[
        {"id": "arm", "protocol": "totally_unknown_xyz"},
    ])
    drv = CompositeDriver(cfg)
    drv.move(0.0, 0.0)  # routes to NullDriver — should not raise
    drv.stop()
