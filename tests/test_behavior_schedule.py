"""Tests for BehaviorRunner._step_schedule — issue #360."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


def _make_runner():
    from castor.behaviors import BehaviorRunner

    runner = BehaviorRunner(driver=MagicMock(), brain=None, speaker=None)
    runner._running = True
    return runner


# ── Basic dispatch ────────────────────────────────────────────────────────────


def test_schedule_step_registered():
    runner = _make_runner()
    assert "schedule" in runner._step_handlers


def test_schedule_empty_steps_skips(caplog):
    runner = _make_runner()
    import logging

    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_schedule({"type": "schedule", "every": 1, "steps": []})
    assert any("empty" in r.message for r in caplog.records)


# ── _stop_event attribute ─────────────────────────────────────────────────────


def test_runner_has_stop_event():
    runner = _make_runner()
    assert hasattr(runner, "_stop_event")
    assert isinstance(runner._stop_event, threading.Event)


def test_stop_sets_and_clears_stop_event():
    runner = _make_runner()
    runner.stop()
    # After stop() the event should be in cleared state (set then immediately cleared)
    assert not runner._stop_event.is_set()
    assert not runner._running


# ── 'every' mode ──────────────────────────────────────────────────────────────


def test_schedule_every_with_count():
    runner = _make_runner()
    executed = []

    def _fake_run(steps, label):
        executed.append(label)

    runner._run_step_list = _fake_run
    runner._step_schedule({"every": 0.001, "count": 3, "steps": [{"type": "wait", "seconds": 0}]})
    assert len(executed) == 3


def test_schedule_every_count_zero_stops_on_running_false():
    runner = _make_runner()
    calls = []

    def _fake_run(steps, label):
        calls.append(1)
        runner._running = False  # stop after first iteration

    runner._run_step_list = _fake_run
    runner._step_schedule({"every": 0.001, "count": 0, "steps": [{"type": "wait"}]})
    assert len(calls) == 1


def test_schedule_every_respects_count_cap():
    runner = _make_runner()
    calls = []
    runner._run_step_list = lambda steps, label: calls.append(1)
    runner._step_schedule({"every": 0.001, "count": 2, "steps": [{"type": "wait"}]})
    assert len(calls) == 2


# ── 'at' mode ────────────────────────────────────────────────────────────────


def test_schedule_at_invalid_time_skips(caplog):
    runner = _make_runner()
    import logging

    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_schedule({"at": "INVALID", "steps": [{"type": "wait"}]})
    assert any("invalid" in r.message.lower() for r in caplog.records)


def test_schedule_at_executes_steps_after_wait():
    """'at' mode executes inner steps after the wait completes."""
    runner = _make_runner()
    calls = []
    runner._run_step_list = lambda steps, label: calls.append(1)
    # Patch _stop_event.wait to be instant so the test doesn't hang
    runner._stop_event.wait = lambda timeout=None: None
    runner._step_schedule({"at": "23:59", "steps": [{"type": "wait"}]})
    assert len(calls) == 1


def test_schedule_at_stops_if_not_running():
    runner = _make_runner()
    calls = []
    runner._run_step_list = lambda steps, label: calls.append(1)
    runner._stop_event.wait = lambda timeout=None: setattr(runner, "_running", False)
    runner._step_schedule({"at": "23:59", "steps": [{"type": "wait"}]})
    assert len(calls) == 0  # stopped before executing


# ── Neither at nor every ──────────────────────────────────────────────────────


def test_schedule_no_mode_warns(caplog):
    runner = _make_runner()
    import logging

    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_schedule({"steps": [{"type": "wait"}]})
    assert any("neither" in r.message.lower() or "skipping" in r.message.lower() for r in caplog.records)


# ── Integration: schedule via run_step_list (dispatch table) ─────────────────


def test_schedule_dispatched_via_handlers():
    runner = _make_runner()
    calls = []
    runner._run_step_list = lambda steps, label: calls.append(1)
    handler = runner._step_handlers["schedule"]
    handler({"every": 0.001, "count": 1, "steps": [{"type": "wait"}]})
    assert len(calls) == 1
