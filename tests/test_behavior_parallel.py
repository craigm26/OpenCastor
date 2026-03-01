"""tests/test_behavior_parallel.py — Tests for the ``parallel`` step type in BehaviorRunner.

Covers:
- Registration in _step_handlers
- Empty / missing inner_steps → warning, no execution
- All steps execute (3 concurrent steps, all called)
- Steps run concurrently (two 0.1s waits finish in <0.2s total)
- timeout_s cancels long-running steps quickly
- Exception in one step does not abort others
- _running=False → returns immediately without launching threads
- Unknown step type in inner_steps is silently skipped (no crash)
- Single inner step runs correctly
- Log output contains step count
- _running flag is still True after step completes
"""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner():
    """Return a fresh BehaviorRunner with _running=True."""
    from castor.behaviors import BehaviorRunner

    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, brain=None, speaker=None, config={})
    runner._running = True  # simulate being inside run()
    return runner


# ---------------------------------------------------------------------------
# Test 1: "parallel" is registered in _step_handlers
# ---------------------------------------------------------------------------


def test_parallel_registered_in_step_handlers():
    """``parallel`` must appear in ``_step_handlers``."""
    runner = _make_runner()
    assert "parallel" in runner._step_handlers
    assert runner._step_handlers["parallel"] == runner._step_parallel


# ---------------------------------------------------------------------------
# Test 2: Empty inner_steps → warning logged, returns without running
# ---------------------------------------------------------------------------


def test_empty_inner_steps_logs_warning_and_returns(caplog):
    """An empty ``inner_steps`` list must log a warning and return without error."""
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_parallel({"type": "parallel", "inner_steps": []})
    assert any(
        "inner_steps" in r.message and ("missing" in r.message or "empty" in r.message)
        for r in caplog.records
    )
    assert runner._running is True


# ---------------------------------------------------------------------------
# Test 3: Missing inner_steps key → warning, no crash
# ---------------------------------------------------------------------------


def test_missing_inner_steps_no_crash(caplog):
    """A step dict with no ``inner_steps`` key must log a warning and return cleanly."""
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_parallel({"type": "parallel"})
    assert any("inner_steps" in r.message for r in caplog.records)
    assert runner._running is True


# ---------------------------------------------------------------------------
# Test 4: All steps execute (3 steps, all 3 called)
# ---------------------------------------------------------------------------


def test_all_steps_execute():
    """Each inner step must be dispatched exactly once."""
    runner = _make_runner()
    called: list = []
    lock = threading.Lock()

    def _fake_wait(step):
        with lock:
            called.append(step.get("_id"))

    runner._step_handlers["_fake"] = _fake_wait  # type: ignore[assignment]

    inner_steps = [
        {"type": "_fake", "_id": 0},
        {"type": "_fake", "_id": 1},
        {"type": "_fake", "_id": 2},
    ]
    runner._step_parallel({"type": "parallel", "inner_steps": inner_steps})

    assert sorted(called) == [0, 1, 2]


# ---------------------------------------------------------------------------
# Test 5: Steps run concurrently (two 0.1s waits finish in <0.2s total)
# ---------------------------------------------------------------------------


def test_steps_run_concurrently():
    """Two inner steps that each sleep 0.1 s must complete in under 0.18 s total."""
    runner = _make_runner()

    inner_steps = [
        {"type": "wait", "seconds": 0.1},
        {"type": "wait", "seconds": 0.1},
    ]

    start = time.monotonic()
    runner._step_parallel({"type": "parallel", "inner_steps": inner_steps})
    elapsed = time.monotonic() - start

    # Sequential would take ~0.2 s; concurrent should be well under 0.18 s
    assert elapsed < 0.18, f"Elapsed {elapsed:.3f}s — steps may not be running concurrently"


# ---------------------------------------------------------------------------
# Test 6: timeout_s cancels long-running steps
# ---------------------------------------------------------------------------


def test_timeout_cancels_long_steps():
    """A ``timeout_s=0.1`` step with a 2 s inner wait must return quickly."""
    runner = _make_runner()

    inner_steps = [{"type": "wait", "seconds": 2.0}]

    start = time.monotonic()
    runner._step_parallel({"type": "parallel", "inner_steps": inner_steps, "timeout_s": 0.1})
    elapsed = time.monotonic() - start

    # Should return close to timeout_s, not 2 s
    assert elapsed < 0.5, f"Elapsed {elapsed:.3f}s — timeout did not fire"


# ---------------------------------------------------------------------------
# Test 7: Exception in one step does not abort others
# ---------------------------------------------------------------------------


def test_exception_in_one_step_does_not_abort_others():
    """If one inner step raises, the other inner steps must still execute."""
    runner = _make_runner()
    executed: list = []
    lock = threading.Lock()

    def _raising_step(step):
        raise RuntimeError("intentional failure")

    def _good_step(step):
        with lock:
            executed.append("good")

    runner._step_handlers["_raising"] = _raising_step  # type: ignore[assignment]
    runner._step_handlers["_good"] = _good_step  # type: ignore[assignment]

    inner_steps = [
        {"type": "_raising"},
        {"type": "_good"},
        {"type": "_good"},
    ]

    # Must not raise itself
    runner._step_parallel({"type": "parallel", "inner_steps": inner_steps})

    assert len(executed) == 2, f"Expected 2 good steps, got {len(executed)}"


# ---------------------------------------------------------------------------
# Test 8: _running=False → returns immediately, no threads launched
# ---------------------------------------------------------------------------


def test_not_running_returns_immediately():
    """When ``_running`` is False the handler must return without submitting any futures."""
    runner = _make_runner()
    runner._running = False

    submitted: list = []

    original_run_step = runner._run_step

    def _spy_run_step(step):
        submitted.append(step)
        return original_run_step(step)

    runner._run_step = _spy_run_step  # type: ignore[method-assign]

    runner._step_parallel({"type": "parallel", "inner_steps": [{"type": "wait", "seconds": 0}]})
    assert submitted == [], "Threads should not be launched when _running is False"


# ---------------------------------------------------------------------------
# Test 9: Unknown step type in inner_steps is silently skipped (no crash)
# ---------------------------------------------------------------------------


def test_unknown_step_type_no_crash(caplog):
    """An unknown ``type`` inside ``inner_steps`` must log a warning and not raise."""
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_parallel(
            {"type": "parallel", "inner_steps": [{"type": "totally_unknown_xyz"}]}
        )
    # Should have logged a warning about the unknown type
    assert any("totally_unknown_xyz" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Test 10: Single inner step runs correctly
# ---------------------------------------------------------------------------


def test_parallel_with_single_step():
    """A single inner step must execute exactly once."""
    runner = _make_runner()
    called: list = []

    def _fake(step):
        called.append(True)

    runner._step_handlers["_single"] = _fake  # type: ignore[assignment]

    runner._step_parallel({"type": "parallel", "inner_steps": [{"type": "_single"}]})
    assert called == [True]


# ---------------------------------------------------------------------------
# Test 11: Log output contains step count
# ---------------------------------------------------------------------------


def test_steps_logged(caplog):
    """The log output must mention the count of inner steps being launched."""
    runner = _make_runner()

    with caplog.at_level(logging.INFO, logger="OpenCastor.Behaviors"):
        runner._step_parallel(
            {
                "type": "parallel",
                "inner_steps": [
                    {"type": "wait", "seconds": 0},
                    {"type": "wait", "seconds": 0},
                ],
            }
        )

    # Expect an INFO message that includes "2" (the step count)
    launch_msgs = [
        r.message
        for r in caplog.records
        if r.levelno == logging.INFO and "parallel step" in r.message
    ]
    assert any("2" in msg for msg in launch_msgs), (
        f"No log message containing '2' found. Messages: {launch_msgs}"
    )


# ---------------------------------------------------------------------------
# Test 12: _running flag is still True after step completes
# ---------------------------------------------------------------------------


def test_parallel_running_flag_not_cleared():
    """``_running`` must remain True after ``_step_parallel`` returns normally."""
    runner = _make_runner()

    runner._step_parallel({"type": "parallel", "inner_steps": [{"type": "wait", "seconds": 0}]})

    assert runner._running is True, "_step_parallel must not clear the _running flag"
