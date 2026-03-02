"""Tests for BehaviorRunner event_trigger step + set_event / clear_event (#379)."""

import threading
import time
from unittest.mock import MagicMock

from castor.behaviors import BehaviorRunner


def _make_runner():
    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, config={})
    runner._running = True
    return runner


# ── dispatch table ─────────────────────────────────────────────────────────────

def test_event_trigger_in_dispatch_table():
    runner = _make_runner()
    assert "event_trigger" in runner._step_handlers


def test_event_trigger_handler_callable():
    runner = _make_runner()
    assert callable(runner._step_handlers["event_trigger"])


# ── missing event name ────────────────────────────────────────────────────────

def test_event_trigger_no_event_key_skips(caplog):
    import logging
    runner = _make_runner()
    with caplog.at_level(logging.WARNING):
        runner._step_event_trigger({})
    assert any("event" in r.message.lower() for r in caplog.records)


# ── set_event / clear_event ───────────────────────────────────────────────────

def test_set_event_creates_event():
    runner = _make_runner()
    runner.set_event("foo")
    with runner._events_lock:
        assert "foo" in runner._events
        assert runner._events["foo"].is_set()


def test_clear_event_unsets():
    runner = _make_runner()
    runner.set_event("bar")
    runner.clear_event("bar")
    with runner._events_lock:
        assert "bar" in runner._events
        assert not runner._events["bar"].is_set()


def test_clear_event_nonexistent_does_not_raise():
    runner = _make_runner()
    runner.clear_event("nonexistent_xyz")  # should not raise


def test_set_event_idempotent():
    runner = _make_runner()
    runner.set_event("alpha")
    runner.set_event("alpha")
    with runner._events_lock:
        assert runner._events["alpha"].is_set()


# ── event_trigger fires when pre-set ─────────────────────────────────────────

def test_event_trigger_fires_immediately_when_pre_set():
    runner = _make_runner()
    runner.set_event("go")
    start = time.monotonic()
    runner._step_event_trigger({"event": "go", "timeout_s": 5.0})
    elapsed = time.monotonic() - start
    assert elapsed < 1.0  # should return quickly


def test_event_trigger_does_not_stop_running_on_success():
    runner = _make_runner()
    runner.set_event("signal")
    runner._step_event_trigger({"event": "signal", "timeout_s": 5.0})
    assert runner._running is True


# ── timeout behaviour ─────────────────────────────────────────────────────────

def test_event_trigger_timeout_stop_sets_running_false():
    runner = _make_runner()
    runner._step_event_trigger({"event": "never", "timeout_s": 0.01, "on_timeout": "stop"})
    assert runner._running is False


def test_event_trigger_timeout_warn_keeps_running():
    runner = _make_runner()
    runner._step_event_trigger({"event": "never", "timeout_s": 0.01, "on_timeout": "warn"})
    assert runner._running is True


def test_event_trigger_default_on_timeout_is_stop():
    runner = _make_runner()
    runner._step_event_trigger({"event": "never", "timeout_s": 0.01})
    assert runner._running is False


# ── trigger from separate thread ──────────────────────────────────────────────

def test_event_trigger_unblocked_by_thread():
    runner = _make_runner()

    def trigger_later():
        time.sleep(0.05)
        runner.set_event("signal2")

    t = threading.Thread(target=trigger_later, daemon=True)
    t.start()
    runner._step_event_trigger({"event": "signal2", "timeout_s": 2.0})
    t.join(timeout=1.0)
    assert runner._running is True


# ── stop() clears events ──────────────────────────────────────────────────────

def test_stop_unblocks_waiting_events():
    runner = _make_runner()
    # Pre-add an event that is NOT set
    with runner._events_lock:
        ev = threading.Event()
        runner._events["pending"] = ev

    runner._running = False
    runner.stop()  # should set all events + clear dict

    assert ev.is_set()


def test_stop_clears_events_dict():
    runner = _make_runner()
    runner.set_event("x")
    runner._running = False
    runner.stop()
    with runner._events_lock:
        assert len(runner._events) == 0
