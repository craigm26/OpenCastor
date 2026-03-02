"""Tests for BehaviorRunner._step_parallel_race (Issue #411)."""
import threading
import time
from unittest.mock import MagicMock

import pytest

from castor.behaviors import BehaviorRunner


@pytest.fixture
def runner():
    r = BehaviorRunner(config={}, driver=MagicMock(), brain=MagicMock())
    r._running = True
    return r


# ------------------------------------------------------------------
# Registration / structural checks
# ------------------------------------------------------------------


def test_parallel_race_registered_in_dispatch(runner):
    assert "parallel_race" in runner._step_handlers


def test_runner_has_step_parallel_race_method(runner):
    assert hasattr(runner, "_step_parallel_race")
    assert callable(runner._step_parallel_race)


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


def test_parallel_race_empty_inner_steps_skips(runner):
    """Empty inner_steps should log a warning and return without raising."""
    runner._step_parallel_race({"inner_steps": []})


def test_parallel_race_missing_inner_steps_skips(runner):
    """Missing inner_steps key should log a warning and return without raising."""
    runner._step_parallel_race({})


def test_parallel_race_not_running_returns_immediately(runner):
    """If _running is False, step should return without launching threads."""
    runner._running = False
    called = []

    def recording_step(step):
        called.append(True)

    runner._step_handlers["rec"] = recording_step
    runner._step_parallel_race({"inner_steps": [{"type": "rec"}]})
    assert called == []


# ------------------------------------------------------------------
# Concurrency behaviour
# ------------------------------------------------------------------


def test_parallel_race_runs_inner_steps_concurrently(runner):
    """Both threads should start concurrently (verified by overlapping execution)."""
    started = []
    start_lock = threading.Lock()
    all_started = threading.Event()

    def slow_step(step):
        with start_lock:
            started.append(step.get("label"))
            if len(started) == 2:
                all_started.set()
        time.sleep(0.3)

    runner._step_handlers["slow_step"] = slow_step

    runner._step_parallel_race({
        "inner_steps": [
            {"type": "slow_step", "label": "a"},
            {"type": "slow_step", "label": "b"},
        ],
        "timeout_s": 2.0,
    })

    # Both threads must have started (proven by both labels being in started list)
    # Give daemon threads a tiny window to record their start
    all_started.wait(timeout=1.0)
    assert set(started) == {"a", "b"}


def test_parallel_race_exits_on_first_completion(runner):
    """Race should return as soon as the fast step finishes, not wait for slow."""
    results = []

    def fast_step(step):
        results.append("fast")

    def slow_step(step):
        time.sleep(0.5)
        results.append("slow")

    runner._step_handlers["fast"] = fast_step
    runner._step_handlers["slow"] = slow_step

    start = time.monotonic()
    runner._step_parallel_race({
        "inner_steps": [{"type": "fast"}, {"type": "slow"}]
    })
    elapsed = time.monotonic() - start

    assert "fast" in results
    assert elapsed < 0.4  # returned quickly, did not wait for slow step


def test_parallel_race_at_least_one_thread_finishes(runner):
    """finished count reported should be >= 1."""
    import io
    import logging

    results = []

    def instant_step(step):
        results.append("done")

    runner._step_handlers["instant"] = instant_step

    runner._step_parallel_race({
        "inner_steps": [{"type": "instant"}, {"type": "wait", "duration_s": 0.3}]
    })

    assert len(results) >= 1


def test_parallel_race_never_raises(runner):
    """Even if an inner step raises, parallel_race must not propagate the exception."""

    def bad_step(step):
        raise RuntimeError("deliberate failure")

    runner._step_handlers["bad"] = bad_step

    # Should not raise
    runner._step_parallel_race({"inner_steps": [{"type": "bad"}]})


# ------------------------------------------------------------------
# Timeout
# ------------------------------------------------------------------


def test_parallel_race_timeout_s_respected(runner):
    """When timeout_s is set and all steps are slow, we return at roughly timeout_s."""
    started = []

    def very_slow_step(step):
        started.append(True)
        time.sleep(10)

    runner._step_handlers["very_slow"] = very_slow_step

    start = time.monotonic()
    runner._step_parallel_race({
        "inner_steps": [{"type": "very_slow"}, {"type": "very_slow"}],
        "timeout_s": 0.2,
    })
    elapsed = time.monotonic() - start

    # Should return within ~300 ms (timeout + small overhead), not 10 s
    assert elapsed < 1.0
    # Both threads were launched
    assert len(started) == 2


def test_parallel_race_zero_timeout_means_no_timeout(runner):
    """timeout_s=0 means no timeout — we wait for the first step to complete."""
    results = []

    def quick_step(step):
        results.append("quick")

    runner._step_handlers["quick"] = quick_step

    runner._step_parallel_race({
        "inner_steps": [{"type": "quick"}],
        "timeout_s": 0,
    })

    assert "quick" in results


def test_parallel_race_dispatch_via_handler(runner):
    """Calling the registered handler key should invoke _step_parallel_race."""
    results = []

    def marker_step(step):
        results.append("marker")

    runner._step_handlers["marker"] = marker_step

    handler = runner._step_handlers["parallel_race"]
    handler({"inner_steps": [{"type": "marker"}]})

    assert "marker" in results
