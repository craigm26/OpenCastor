"""tests/test_behavior_while_true.py — Tests for BehaviorRunner._step_while_true.

Covers:
- Registration in _step_handlers
- Empty inner_steps → warning, no execution
- max_iterations=3 → exactly 3 iterations
- max_iterations=0 + stop() → exits cleanly (threading)
- timeout_s positive → exits after timeout
- dwell_s between iterations is respected
- All inner steps executed per iteration (2 steps × 3 iterations = 6 events)
- Unknown inner step type doesn't raise
- stop() called from another thread breaks the loop
- max_iterations=1 → exactly 1 iteration
- After while_true completes, _running is still True (not cleared by step itself)
- max_iterations=0, timeout_s=0.05 → exits via timeout
- dwell_s=0 → no extra delay between iterations
- Nested inner steps execute in order
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch


def _make_runner():
    """Return a fresh BehaviorRunner with _running=True."""
    from castor.behaviors import BehaviorRunner

    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, brain=None, speaker=None, config={})
    runner._running = True  # critical — step methods check self._running
    return runner


# ---------------------------------------------------------------------------
# Test 1: "while_true" is registered in _step_handlers
# ---------------------------------------------------------------------------


def test_while_true_registered_in_step_handlers():
    runner = _make_runner()
    assert "while_true" in runner._step_handlers
    assert runner._step_handlers["while_true"] == runner._step_while_true


# ---------------------------------------------------------------------------
# Test 2: Empty inner_steps → warning logged, returns without running
# ---------------------------------------------------------------------------


def test_empty_inner_steps_logs_warning_and_returns():
    runner = _make_runner()

    with patch("castor.behaviors.logger") as mock_log:
        runner._step_while_true({"inner_steps": []})
        # Should have logged a warning
        assert mock_log.warning.called
        warning_args = mock_log.warning.call_args[0]
        assert "inner_steps" in warning_args[0] or "inner_steps" in str(warning_args)

    # _running should still be True — the step did not stop the runner
    assert runner._running is True


def test_missing_inner_steps_logs_warning_and_returns():
    runner = _make_runner()
    with patch("castor.behaviors.logger") as mock_log:
        runner._step_while_true({})
        assert mock_log.warning.called
    assert runner._running is True


# ---------------------------------------------------------------------------
# Test 3: max_iterations=3 → exactly 3 iterations
# ---------------------------------------------------------------------------


def test_max_iterations_3_runs_exactly_3_times():
    runner = _make_runner()
    call_count = []

    def _fake_wait(step):
        call_count.append(1)

    runner._step_handlers["wait"] = _fake_wait

    step = {
        "inner_steps": [{"type": "wait", "duration_s": 0}],
        "max_iterations": 3,
    }
    runner._step_while_true(step)

    assert len(call_count) == 3
    assert runner._running is True  # step does not clear _running


# ---------------------------------------------------------------------------
# Test 4: max_iterations=0 + stop() → exits cleanly (threading)
# ---------------------------------------------------------------------------


def test_stop_from_another_thread_exits_loop():
    """Loop runs until stop() is called from a background thread."""
    from castor.behaviors import BehaviorRunner

    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, brain=None, speaker=None, config={})
    runner._running = True

    iteration_count = []

    def _counting_wait(step):
        iteration_count.append(1)
        # Use a tiny real sleep so the background stopper has time to act
        time.sleep(0.01)

    runner._step_handlers["wait"] = _counting_wait

    # Schedule stop() after a short delay
    def _stopper():
        time.sleep(0.08)
        runner._running = False  # simulate stop without calling stop() to avoid driver.stop()

    t = threading.Thread(target=_stopper, daemon=True)
    t.start()

    step = {
        "inner_steps": [{"type": "wait", "duration_s": 0}],
        "max_iterations": 0,  # unlimited
        "timeout_s": 0,  # no timeout
    }
    runner._step_while_true(step)

    t.join(timeout=2.0)
    # Loop should have exited because _running became False
    assert not runner._running
    # At least one iteration should have run
    assert len(iteration_count) >= 1


# ---------------------------------------------------------------------------
# Test 5: timeout_s positive → exits after timeout
# ---------------------------------------------------------------------------


def test_timeout_exits_loop():
    runner = _make_runner()
    call_count = []

    def _fake_wait(step):
        call_count.append(1)
        time.sleep(0.005)  # small sleep per iteration

    runner._step_handlers["wait"] = _fake_wait

    step = {
        "inner_steps": [{"type": "wait"}],
        "timeout_s": 0.05,  # 50 ms timeout
        "max_iterations": 0,  # unlimited
    }

    t_start = time.monotonic()
    runner._step_while_true(step)
    elapsed = time.monotonic() - t_start

    # Should have exited in roughly timeout_s; give generous upper bound
    assert elapsed < 0.5, f"Loop took too long: {elapsed:.3f}s"
    assert runner._running is True  # step itself doesn't clear _running


# ---------------------------------------------------------------------------
# Test 6: dwell_s between iterations is respected (timestamps)
# ---------------------------------------------------------------------------


def test_dwell_s_adds_pause_between_iterations():
    runner = _make_runner()
    timestamps = []

    def _fake_wait(step):
        timestamps.append(time.monotonic())

    runner._step_handlers["wait"] = _fake_wait

    dwell = 0.08  # 80 ms dwell
    step = {
        "inner_steps": [{"type": "wait"}],
        "max_iterations": 3,
        "dwell_s": dwell,
    }

    runner._step_while_true(step)

    assert len(timestamps) == 3
    # Gap between first and second iteration should be >= dwell_s (minus small tolerance)
    gap = timestamps[1] - timestamps[0]
    assert gap >= dwell * 0.7, f"Expected dwell gap >= {dwell * 0.7:.3f}s, got {gap:.3f}s"


# ---------------------------------------------------------------------------
# Test 7: All inner steps executed per iteration (2 steps × 3 iters = 6 events)
# ---------------------------------------------------------------------------


def test_all_inner_steps_executed_per_iteration():
    runner = _make_runner()
    log = []

    def _fake_a(step):
        log.append("a")

    def _fake_b(step):
        log.append("b")

    runner._step_handlers["step_a"] = _fake_a
    runner._step_handlers["step_b"] = _fake_b

    step = {
        "inner_steps": [{"type": "step_a"}, {"type": "step_b"}],
        "max_iterations": 3,
    }
    runner._step_while_true(step)

    assert log == ["a", "b", "a", "b", "a", "b"]


# ---------------------------------------------------------------------------
# Test 8: Unknown inner step type doesn't raise
# ---------------------------------------------------------------------------


def test_unknown_inner_step_type_does_not_raise():
    runner = _make_runner()

    step = {
        "inner_steps": [{"type": "does_not_exist_xyz"}],
        "max_iterations": 2,
    }
    # Must not raise any exception
    runner._step_while_true(step)
    assert runner._running is True


# ---------------------------------------------------------------------------
# Test 9: stop() called from another thread breaks the loop (via _running=False)
# ---------------------------------------------------------------------------


def test_stop_method_breaks_infinite_loop():
    """stop() sets _running=False which should cause the loop to exit."""
    from castor.behaviors import BehaviorRunner

    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, brain=None, speaker=None, config={})
    runner._running = True

    call_count = []

    def _fake_wait(step):
        call_count.append(1)
        time.sleep(0.01)

    runner._step_handlers["wait"] = _fake_wait

    def _do_stop():
        time.sleep(0.07)
        # Directly set _running to False (stop() would also call driver.stop())
        runner._running = False

    t = threading.Thread(target=_do_stop, daemon=True)
    t.start()

    step = {
        "inner_steps": [{"type": "wait"}],
        "max_iterations": 0,  # unlimited
        "timeout_s": 0,
    }
    runner._step_while_true(step)
    t.join(timeout=2.0)

    assert not runner._running
    assert len(call_count) >= 1


# ---------------------------------------------------------------------------
# Test 10: max_iterations=1 → exactly 1 iteration
# ---------------------------------------------------------------------------


def test_max_iterations_1_runs_exactly_once():
    runner = _make_runner()
    call_count = []

    def _fake_wait(step):
        call_count.append(1)

    runner._step_handlers["wait"] = _fake_wait

    step = {
        "inner_steps": [{"type": "wait"}],
        "max_iterations": 1,
    }
    runner._step_while_true(step)

    assert len(call_count) == 1
    assert runner._running is True


# ---------------------------------------------------------------------------
# Test 11: After while_true completes, _running is still True
# ---------------------------------------------------------------------------


def test_running_flag_not_cleared_by_step():
    runner = _make_runner()

    step = {
        "inner_steps": [{"type": "wait"}],
        "max_iterations": 2,
    }
    runner._step_while_true(step)

    # The step itself must NOT clear _running
    assert runner._running is True


# ---------------------------------------------------------------------------
# Test 12: max_iterations=0, timeout_s=0.05 → exits via timeout
# ---------------------------------------------------------------------------


def test_timeout_with_unlimited_iterations():
    runner = _make_runner()
    call_count = []

    def _fast_noop(step):
        call_count.append(1)

    runner._step_handlers["wait"] = _fast_noop

    step = {
        "inner_steps": [{"type": "wait"}],
        "max_iterations": 0,  # unlimited
        "timeout_s": 0.05,  # 50 ms — exits via timeout
    }

    t_start = time.monotonic()
    runner._step_while_true(step)
    elapsed = time.monotonic() - t_start

    assert elapsed < 1.0, f"Should have exited quickly, took {elapsed:.3f}s"
    assert runner._running is True  # step does not clear _running
    # Must have run at least one iteration
    assert len(call_count) >= 1


# ---------------------------------------------------------------------------
# Test 13: dwell_s=0 → no significant extra delay between iterations
# ---------------------------------------------------------------------------


def test_dwell_zero_no_extra_delay():
    runner = _make_runner()
    call_count = []

    def _fast_noop(step):
        call_count.append(1)

    runner._step_handlers["wait"] = _fast_noop

    step = {
        "inner_steps": [{"type": "wait"}],
        "max_iterations": 5,
        "dwell_s": 0.0,
    }

    t_start = time.monotonic()
    runner._step_while_true(step)
    elapsed = time.monotonic() - t_start

    assert len(call_count) == 5
    # Without dwell, 5 trivial iterations should complete well under 1 second
    assert elapsed < 1.0


# ---------------------------------------------------------------------------
# Test 14: Inner steps execute in correct order each iteration
# ---------------------------------------------------------------------------


def test_inner_steps_execute_in_order_each_iteration():
    runner = _make_runner()
    order = []

    runner._step_handlers["step_x"] = lambda s: order.append("x")
    runner._step_handlers["step_y"] = lambda s: order.append("y")
    runner._step_handlers["step_z"] = lambda s: order.append("z")

    step = {
        "inner_steps": [
            {"type": "step_x"},
            {"type": "step_y"},
            {"type": "step_z"},
        ],
        "max_iterations": 2,
    }
    runner._step_while_true(step)

    assert order == ["x", "y", "z", "x", "y", "z"]
