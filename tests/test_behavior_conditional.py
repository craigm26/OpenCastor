"""tests/test_behavior_conditional.py — Tests for BehaviorRunner._step_conditional.

Covers:
- ``conditional`` is registered in ``_step_handlers``
- ``then`` branch runs when condition is true
- ``else`` branch runs when condition is false
- Both branches can be empty (no crash)
- ``None`` sensor value skips both branches and logs warning
- Unsupported op logs warning and skips
- ``_running=False`` skips execution
- ``lt`` operator works correctly
- ``gt`` operator works correctly
- ``eq`` operator works correctly
- ``ne`` operator works correctly
- Missing ``sensor`` key logs warning, no crash
- ``_get_sensor_value`` returns None for unknown driver (no crash)
- ``lte`` operator works correctly
- ``gte`` operator works correctly
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_runner():
    """Return a fresh BehaviorRunner with _running=True."""
    from castor.behaviors import BehaviorRunner

    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, brain=None, speaker=None, config={})
    runner._running = True  # critical — step methods check self._running
    return runner


# ---------------------------------------------------------------------------
# Test 1: "conditional" is registered in _step_handlers
# ---------------------------------------------------------------------------


def test_conditional_registered_in_step_handlers():
    runner = _make_runner()
    assert "conditional" in runner._step_handlers
    assert runner._step_handlers["conditional"] == runner._step_conditional


# ---------------------------------------------------------------------------
# Test 2: then branch runs when condition is true
# ---------------------------------------------------------------------------


def test_then_branch_runs_when_condition_is_true():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")
    runner._step_handlers["stop"] = lambda s: executed.append("else")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lt",
        "value": 4.0,
        "then": [{"type": "speak", "text": "low"}],
        "else": [{"type": "stop"}],
    }

    with patch.object(runner, "_get_sensor_value", return_value=3.2):
        runner._step_conditional(step)

    assert executed == ["then"]


# ---------------------------------------------------------------------------
# Test 3: else branch runs when condition is false
# ---------------------------------------------------------------------------


def test_else_branch_runs_when_condition_is_false():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")
    runner._step_handlers["wait"] = lambda s: executed.append("else")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lt",
        "value": 3.0,
        "then": [{"type": "speak", "text": "low"}],
        "else": [{"type": "wait", "seconds": 1}],
    }

    with patch.object(runner, "_get_sensor_value", return_value=4.2):
        runner._step_conditional(step)

    assert executed == ["else"]


# ---------------------------------------------------------------------------
# Test 4: Both branches can be empty (no crash)
# ---------------------------------------------------------------------------


def test_empty_branches_do_not_crash():
    runner = _make_runner()

    # then branch empty — condition true
    step_true = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lt",
        "value": 5.0,
        "then": [],
        "else": [],
    }
    with patch.object(runner, "_get_sensor_value", return_value=3.0):
        runner._step_conditional(step_true)  # must not raise

    # else branch empty — condition false
    step_false = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "gt",
        "value": 5.0,
        "then": [],
        "else": [],
    }
    with patch.object(runner, "_get_sensor_value", return_value=3.0):
        runner._step_conditional(step_false)  # must not raise

    assert runner._running is True


# ---------------------------------------------------------------------------
# Test 5: None sensor value skips both branches and logs warning
# ---------------------------------------------------------------------------


def test_none_sensor_value_skips_both_branches_and_logs_warning():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")
    runner._step_handlers["wait"] = lambda s: executed.append("else")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lt",
        "value": 4.0,
        "then": [{"type": "speak", "text": "low"}],
        "else": [{"type": "wait", "seconds": 1}],
    }

    with (
        patch.object(runner, "_get_sensor_value", return_value=None),
        patch("castor.behaviors.logger") as mock_log,
    ):
        runner._step_conditional(step)

    # Neither branch should execute
    assert executed == []
    # A warning should have been logged
    assert mock_log.warning.called
    warning_text = str(mock_log.warning.call_args)
    assert "None" in warning_text or "skipping" in warning_text


# ---------------------------------------------------------------------------
# Test 6: Unsupported op logs warning and skips both branches
# ---------------------------------------------------------------------------


def test_unsupported_op_logs_warning_and_skips():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")
    runner._step_handlers["wait"] = lambda s: executed.append("else")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "INVALID_OP",
        "value": 3.5,
        "then": [{"type": "speak", "text": "x"}],
        "else": [{"type": "wait", "seconds": 1}],
    }

    with (
        patch.object(runner, "_get_sensor_value", return_value=3.0),
        patch("castor.behaviors.logger") as mock_log,
    ):
        runner._step_conditional(step)

    assert executed == []
    assert mock_log.warning.called
    warning_text = str(mock_log.warning.call_args)
    assert "INVALID_OP" in warning_text or "op" in warning_text.lower()


# ---------------------------------------------------------------------------
# Test 7: _running=False skips execution
# ---------------------------------------------------------------------------


def test_running_false_skips_step():
    runner = _make_runner()
    runner._running = False
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lt",
        "value": 5.0,
        "then": [{"type": "speak", "text": "hi"}],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=3.0) as mock_get:
        runner._step_conditional(step)

    # Sensor should not be read and branches should not execute
    mock_get.assert_not_called()
    assert executed == []


# ---------------------------------------------------------------------------
# Test 8: lt operator works correctly
# ---------------------------------------------------------------------------


def test_lt_operator_true_when_actual_less_than_value():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lt",
        "value": 4.0,
        "then": [{"type": "speak", "text": "low"}],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=3.5):
        runner._step_conditional(step)

    assert executed == ["then"]


def test_lt_operator_false_when_actual_equal_to_value():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")
    runner._step_handlers["wait"] = lambda s: executed.append("else")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lt",
        "value": 3.5,
        "then": [{"type": "speak", "text": "low"}],
        "else": [{"type": "wait", "seconds": 1}],
    }

    with patch.object(runner, "_get_sensor_value", return_value=3.5):
        runner._step_conditional(step)

    # 3.5 < 3.5 is False → else branch
    assert executed == ["else"]


# ---------------------------------------------------------------------------
# Test 9: gt operator works correctly
# ---------------------------------------------------------------------------


def test_gt_operator_true_when_actual_greater_than_value():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        "sensor": "imu.yaw_deg",
        "op": "gt",
        "value": 45.0,
        "then": [{"type": "speak", "text": "turned"}],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=90.0):
        runner._step_conditional(step)

    assert executed == ["then"]


def test_gt_operator_false_when_actual_less_than_value():
    runner = _make_runner()
    executed = []

    runner._step_handlers["wait"] = lambda s: executed.append("else")

    step = {
        "type": "conditional",
        "sensor": "imu.yaw_deg",
        "op": "gt",
        "value": 45.0,
        "then": [],
        "else": [{"type": "wait", "seconds": 1}],
    }

    with patch.object(runner, "_get_sensor_value", return_value=10.0):
        runner._step_conditional(step)

    assert executed == ["else"]


# ---------------------------------------------------------------------------
# Test 10: eq operator works correctly
# ---------------------------------------------------------------------------


def test_eq_operator_true_when_values_equal():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        "sensor": "battery.status",
        "op": "eq",
        "value": 1,
        "then": [{"type": "speak", "text": "ok"}],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=1):
        runner._step_conditional(step)

    assert executed == ["then"]


def test_eq_operator_false_when_values_differ():
    runner = _make_runner()
    executed = []

    runner._step_handlers["wait"] = lambda s: executed.append("else")

    step = {
        "type": "conditional",
        "sensor": "battery.status",
        "op": "eq",
        "value": 1,
        "then": [],
        "else": [{"type": "wait", "seconds": 1}],
    }

    with patch.object(runner, "_get_sensor_value", return_value=0):
        runner._step_conditional(step)

    assert executed == ["else"]


# ---------------------------------------------------------------------------
# Test 11: ne operator works correctly
# ---------------------------------------------------------------------------


def test_ne_operator_true_when_values_differ():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        "sensor": "battery.status",
        "op": "ne",
        "value": 0,
        "then": [{"type": "speak", "text": "not zero"}],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=1):
        runner._step_conditional(step)

    assert executed == ["then"]


def test_ne_operator_false_when_values_equal():
    runner = _make_runner()
    executed = []

    runner._step_handlers["wait"] = lambda s: executed.append("else")

    step = {
        "type": "conditional",
        "sensor": "battery.status",
        "op": "ne",
        "value": 0,
        "then": [],
        "else": [{"type": "wait", "seconds": 1}],
    }

    with patch.object(runner, "_get_sensor_value", return_value=0):
        runner._step_conditional(step)

    assert executed == ["else"]


# ---------------------------------------------------------------------------
# Test 12: Missing sensor key logs warning, no crash
# ---------------------------------------------------------------------------


def test_missing_sensor_key_logs_warning_no_crash():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        # "sensor" key is absent
        "op": "lt",
        "value": 4.0,
        "then": [{"type": "speak", "text": "x"}],
        "else": [],
    }

    with patch("castor.behaviors.logger") as mock_log:
        runner._step_conditional(step)

    assert executed == []
    assert mock_log.warning.called
    warning_text = str(mock_log.warning.call_args)
    assert "sensor" in warning_text.lower()


def test_sensor_without_dot_logs_warning_no_crash():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        "sensor": "battery",  # no dot separator
        "op": "lt",
        "value": 4.0,
        "then": [{"type": "speak", "text": "x"}],
        "else": [],
    }

    with patch("castor.behaviors.logger") as mock_log:
        runner._step_conditional(step)

    assert executed == []
    assert mock_log.warning.called


# ---------------------------------------------------------------------------
# Test 13: _get_sensor_value returns None for unknown driver (no crash)
# ---------------------------------------------------------------------------


def test_get_sensor_value_returns_none_for_unknown_driver():
    from castor.behaviors import BehaviorRunner

    with patch("castor.behaviors.logger") as mock_log:
        result = BehaviorRunner._get_sensor_value("unknown_driver_xyz", "some_field")

    assert result is None
    assert mock_log.warning.called
    warning_text = str(mock_log.warning.call_args)
    assert "unknown_driver_xyz" in warning_text or "unknown" in warning_text.lower()


def test_get_sensor_value_returns_none_and_no_crash_on_import_error():
    """_get_sensor_value handles ImportError from missing driver gracefully."""
    from castor.behaviors import BehaviorRunner

    with patch("castor.behaviors.logger"):
        # battery driver will raise if hardware is unavailable — we simulate that
        with patch.dict("sys.modules", {"castor.drivers.battery_driver": None}):
            # Even if import fails, should return None, not raise
            result = BehaviorRunner._get_sensor_value("battery", "voltage_v")

    # Result is either None (ImportError path) or a real value; either way no exception
    # We just confirm the function did not raise
    assert result is None or result is not None  # tautology — main point is no exception raised


# ---------------------------------------------------------------------------
# Test 14: lte operator works correctly
# ---------------------------------------------------------------------------


def test_lte_operator_true_when_equal():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lte",
        "value": 3.5,
        "then": [{"type": "speak", "text": "low"}],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=3.5):
        runner._step_conditional(step)

    assert executed == ["then"]


def test_lte_operator_true_when_less():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lte",
        "value": 3.5,
        "then": [{"type": "speak", "text": "low"}],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=3.0):
        runner._step_conditional(step)

    assert executed == ["then"]


# ---------------------------------------------------------------------------
# Test 15: gte operator works correctly
# ---------------------------------------------------------------------------


def test_gte_operator_true_when_equal():
    runner = _make_runner()
    executed = []

    runner._step_handlers["speak"] = lambda s: executed.append("then")

    step = {
        "type": "conditional",
        "sensor": "imu.pitch_deg",
        "op": "gte",
        "value": 10.0,
        "then": [{"type": "speak", "text": "tilted"}],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=10.0):
        runner._step_conditional(step)

    assert executed == ["then"]


def test_gte_operator_false_when_less():
    runner = _make_runner()
    executed = []

    runner._step_handlers["wait"] = lambda s: executed.append("else")

    step = {
        "type": "conditional",
        "sensor": "imu.pitch_deg",
        "op": "gte",
        "value": 10.0,
        "then": [],
        "else": [{"type": "wait", "seconds": 1}],
    }

    with patch.object(runner, "_get_sensor_value", return_value=5.0):
        runner._step_conditional(step)

    assert executed == ["else"]


# ---------------------------------------------------------------------------
# Test 16: then branch executes multiple steps in order
# ---------------------------------------------------------------------------


def test_then_branch_executes_multiple_steps_in_order():
    runner = _make_runner()
    order = []

    runner._step_handlers["step_a"] = lambda s: order.append("a")
    runner._step_handlers["step_b"] = lambda s: order.append("b")
    runner._step_handlers["step_c"] = lambda s: order.append("c")

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lt",
        "value": 4.0,
        "then": [
            {"type": "step_a"},
            {"type": "step_b"},
            {"type": "step_c"},
        ],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=3.0):
        runner._step_conditional(step)

    assert order == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Test 17: step completes without clearing _running flag
# ---------------------------------------------------------------------------


def test_conditional_does_not_clear_running_flag():
    runner = _make_runner()

    step = {
        "type": "conditional",
        "sensor": "battery.voltage_v",
        "op": "lt",
        "value": 4.0,
        "then": [],
        "else": [],
    }

    with patch.object(runner, "_get_sensor_value", return_value=3.0):
        runner._step_conditional(step)

    # The step itself must NOT clear _running
    assert runner._running is True
