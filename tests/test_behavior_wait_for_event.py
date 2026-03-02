"""Tests for BehaviorRunner._step_wait_for_event (Issue #419)."""
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


def test_wait_for_event_registered_in_dispatch(runner):
    assert "wait_for_event" in runner._step_handlers


def test_runner_has_step_wait_for_event_method(runner):
    assert hasattr(runner, "_step_wait_for_event")
    assert callable(runner._step_wait_for_event)


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


def test_wait_for_event_missing_event_key_skips(runner):
    """Missing 'event' key should log a warning and return without blocking."""
    runner._step_wait_for_event({})


def test_wait_for_event_empty_event_key_skips(runner):
    """Empty string event name should log a warning and return without blocking."""
    runner._step_wait_for_event({"event": ""})


# ------------------------------------------------------------------
# Event creation
# ------------------------------------------------------------------


def test_wait_for_event_creates_event_if_not_exists(runner):
    """The step should create a threading.Event for unknown names."""
    assert "brand_new_event" not in runner._events

    # Fire the event from another thread after a tiny delay so we don't block forever
    def fire():
        time.sleep(0.05)
        runner.set_event("brand_new_event")

    threading.Thread(target=fire, daemon=True).start()
    runner._step_wait_for_event({"event": "brand_new_event", "timeout_s": 2.0})

    assert "brand_new_event" in runner._events


def test_wait_for_event_uses_existing_event(runner):
    """If the event already exists in _events, the step should use it."""
    existing = threading.Event()
    runner._events["existing_ev"] = existing
    existing.set()  # pre-set so we don't block

    runner._step_wait_for_event({"event": "existing_ev", "timeout_s": 1.0, "clear_after": False})

    # The same object should still be in _events
    assert runner._events["existing_ev"] is existing


# ------------------------------------------------------------------
# Waiting behaviour
# ------------------------------------------------------------------


def test_wait_for_event_fires_when_set(runner):
    """Step should unblock when set_event() is called from another thread."""

    def fire_later():
        time.sleep(0.1)
        runner.set_event("test_ev")

    threading.Thread(target=fire_later, daemon=True).start()
    start = time.monotonic()
    runner._step_wait_for_event({"event": "test_ev", "timeout_s": 2.0})
    assert time.monotonic() - start < 0.5  # fired within 500 ms


def test_wait_for_event_times_out_if_not_set(runner):
    """Step should return after timeout_s even if the event is never set."""
    start = time.monotonic()
    runner._step_wait_for_event({"event": "never_fires", "timeout_s": 0.15})
    elapsed = time.monotonic() - start
    assert 0.1 <= elapsed < 1.0  # returned around 150 ms, not immediately


def test_wait_for_event_zero_timeout_means_no_timeout(runner):
    """timeout_s=0 means no timeout — fires quickly if event is pre-set."""
    runner.set_event("instant_ev")
    start = time.monotonic()
    runner._step_wait_for_event({"event": "instant_ev", "timeout_s": 0, "clear_after": False})
    assert time.monotonic() - start < 0.5


# ------------------------------------------------------------------
# clear_after behaviour
# ------------------------------------------------------------------


def test_wait_for_event_clear_after_true_clears_event(runner):
    """With clear_after=True (default), event should be cleared after firing."""
    runner.set_event("clearme")
    runner._step_wait_for_event({"event": "clearme", "timeout_s": 1.0, "clear_after": True})

    # Event should now be cleared
    assert not runner._events["clearme"].is_set()


def test_wait_for_event_clear_after_false_leaves_event_set(runner):
    """With clear_after=False, event should remain set after firing."""
    runner.set_event("keepme")
    runner._step_wait_for_event({"event": "keepme", "timeout_s": 1.0, "clear_after": False})

    # Event should still be set
    assert runner._events["keepme"].is_set()


def test_wait_for_event_default_clear_after_is_true(runner):
    """Default clear_after behaviour should clear the event."""
    runner.set_event("default_clear_ev")
    runner._step_wait_for_event({"event": "default_clear_ev", "timeout_s": 1.0})

    assert not runner._events["default_clear_ev"].is_set()


# ------------------------------------------------------------------
# Pre-set event (fires immediately)
# ------------------------------------------------------------------


def test_wait_for_event_pre_set_event_fires_immediately(runner):
    """If the event is already set, step should return almost instantly."""
    runner.set_event("already_set")
    start = time.monotonic()
    runner._step_wait_for_event({"event": "already_set", "timeout_s": 5.0})
    assert time.monotonic() - start < 0.3


# ------------------------------------------------------------------
# Safety
# ------------------------------------------------------------------


def test_wait_for_event_never_raises(runner):
    """Even on timeout, the step must not raise."""
    # Use a very short timeout so the test stays fast
    runner._step_wait_for_event({"event": "no_raise_ev", "timeout_s": 0.05})


def test_wait_for_event_dispatch_via_handler(runner):
    """Calling via the registered dispatch key should invoke _step_wait_for_event."""
    runner.set_event("dispatch_ev")
    handler = runner._step_handlers["wait_for_event"]
    # Should not block (event already set)
    handler({"event": "dispatch_ev", "timeout_s": 1.0, "clear_after": False})
