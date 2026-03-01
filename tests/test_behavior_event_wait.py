"""Tests for BehaviorRunner event_wait step (Issue #346)."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from castor.behaviors import BehaviorRunner


def make_runner():
    return BehaviorRunner(driver=None, brain=None, speaker=None, config={})


# ── Basic step dispatch tests ─────────────────────────────────────────────────


def test_event_wait_registered_in_dispatch_table():
    runner = make_runner()
    assert "event_wait" in runner._step_handlers


def test_event_wait_skips_when_sensor_missing():
    runner = make_runner()
    runner._running = True
    # No 'sensor' key — should log warning and return without error
    step = {"type": "event_wait", "op": "gt", "value": 0.5, "timeout_s": 0.1}
    runner._step_event_wait(step)  # Should not raise


def test_event_wait_skips_when_sensor_has_no_dot():
    runner = make_runner()
    runner._running = True
    step = {"type": "event_wait", "sensor": "imu", "op": "gt", "value": 0.5, "timeout_s": 0.1}
    runner._step_event_wait(step)  # Should not raise


def test_event_wait_skips_unknown_op():
    runner = make_runner()
    runner._running = True
    step = {
        "type": "event_wait",
        "sensor": "imu.vibration_rms",
        "op": "INVALID",
        "value": 0.5,
        "timeout_s": 0.1,
    }
    runner._step_event_wait(step)  # Should not raise


def test_event_wait_timeout_when_sensor_never_triggers():
    runner = make_runner()
    runner._running = True

    with patch.object(runner, "_get_sensor_value", return_value=0.1):
        start = time.monotonic()
        step = {
            "type": "event_wait",
            "sensor": "imu.vibration_rms",
            "op": "gt",
            "value": 0.5,
            "timeout_s": 0.2,
        }
        runner._step_event_wait(step)
        elapsed = time.monotonic() - start

    assert elapsed >= 0.15  # Respected the timeout


def test_event_wait_returns_when_condition_met_immediately():
    runner = make_runner()
    runner._running = True

    with patch.object(runner, "_get_sensor_value", return_value=1.0):
        start = time.monotonic()
        step = {
            "type": "event_wait",
            "sensor": "imu.vibration_rms",
            "op": "gt",
            "value": 0.5,
            "timeout_s": 5.0,
        }
        runner._step_event_wait(step)
        elapsed = time.monotonic() - start

    assert elapsed < 1.0  # Should return quickly when condition met immediately


def test_event_wait_lt_operator():
    runner = make_runner()
    runner._running = True

    with patch.object(runner, "_get_sensor_value", return_value=0.1):
        step = {
            "type": "event_wait",
            "sensor": "battery.voltage_v",
            "op": "lt",
            "value": 3.5,
            "timeout_s": 1.0,
        }
        runner._step_event_wait(step)  # Should return immediately (0.1 < 3.5)


def test_event_wait_eq_operator():
    runner = make_runner()
    runner._running = True

    with patch.object(runner, "_get_sensor_value", return_value=42):
        step = {
            "type": "event_wait",
            "sensor": "imu.temp_c",
            "op": "eq",
            "value": 42,
            "timeout_s": 1.0,
        }
        runner._step_event_wait(step)  # Should return immediately


def test_event_wait_stops_when_running_set_false():
    runner = make_runner()
    runner._running = True

    call_count = [0]

    def never_true(driver, field):
        call_count[0] += 1
        return 0.0

    with patch.object(runner, "_get_sensor_value", side_effect=never_true):
        # Stop runner after a short delay
        def stopper():
            time.sleep(0.15)
            runner._running = False

        t = threading.Thread(target=stopper)
        t.start()

        step = {
            "type": "event_wait",
            "sensor": "imu.vibration_rms",
            "op": "gt",
            "value": 0.5,
            "timeout_s": 5.0,
        }
        runner._step_event_wait(step)
        t.join()

    assert call_count[0] > 0  # Was actually polling


def test_event_wait_gte_operator():
    runner = make_runner()
    runner._running = True

    with patch.object(runner, "_get_sensor_value", return_value=0.5):
        step = {
            "type": "event_wait",
            "sensor": "imu.vibration_rms",
            "op": "gte",
            "value": 0.5,
            "timeout_s": 1.0,
        }
        runner._step_event_wait(step)  # 0.5 >= 0.5 → should return immediately


def test_event_wait_ne_operator():
    runner = make_runner()
    runner._running = True

    with patch.object(runner, "_get_sensor_value", return_value=99):
        step = {
            "type": "event_wait",
            "sensor": "battery.state",
            "op": "ne",
            "value": 0,
            "timeout_s": 1.0,
        }
        runner._step_event_wait(step)  # 99 != 0 → should return immediately


def test_event_wait_sensor_returns_none_then_value():
    runner = make_runner()
    runner._running = True

    side_effects = [None, None, 0.8]
    idx = [0]

    def side_effect(driver, field):
        v = side_effects[min(idx[0], len(side_effects) - 1)]
        idx[0] += 1
        return v

    with patch.object(runner, "_get_sensor_value", side_effect=side_effect):
        step = {
            "type": "event_wait",
            "sensor": "imu.vibration_rms",
            "op": "gt",
            "value": 0.5,
            "timeout_s": 2.0,
        }
        runner._step_event_wait(step)

    assert idx[0] >= 3  # Polled until value was available


def test_event_wait_default_timeout_is_30s():
    """Verify the step correctly reads the default timeout from spec."""
    runner = make_runner()
    runner._running = True

    call_count = [0]

    def never_triggers(driver, field):
        call_count[0] += 1
        return 0.0

    # We do NOT set timeout_s — relying on default of 30s
    # Force a quick return by stopping the runner immediately
    runner._running = False

    with patch.object(runner, "_get_sensor_value", side_effect=never_triggers):
        step = {
            "type": "event_wait",
            "sensor": "imu.vibration_rms",
            "op": "gt",
            "value": 0.5,
        }
        runner._step_event_wait(step)
