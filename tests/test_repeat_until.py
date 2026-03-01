"""Tests for BehaviorRunner repeat_until step (issue #292)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


def _make_runner():
    from castor.behaviors import BehaviorRunner

    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, brain=None, speaker=None, config={})
    runner._running = True  # simulate being inside run(); _step_* methods expect this
    return runner


# ── Registration ──────────────────────────────────────────────────────────────


def test_repeat_until_registered():
    runner = _make_runner()
    assert "repeat_until" in runner._step_handlers


# ── Empty inner_steps warns ───────────────────────────────────────────────────


def test_repeat_until_empty_steps_warns(caplog):
    import logging

    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_repeat_until({"inner_steps": []})
    assert caplog.records  # at least one warning logged


# ── max_count limits iterations ───────────────────────────────────────────────


def test_repeat_until_respects_max_count():
    runner = _make_runner()
    call_counts = []

    def record_wait(step):
        call_counts.append(1)

    runner._step_handlers["wait"] = record_wait

    runner._step_repeat_until(
        {
            "inner_steps": [{"type": "wait"}],
            "sensor": "none",
            "max_count": 5,
        }
    )
    assert len(call_counts) == 5


def test_repeat_until_max_count_zero_skips():
    runner = _make_runner()
    call_counts = []
    runner._step_handlers["wait"] = lambda s: call_counts.append(1)
    runner._step_repeat_until(
        {
            "inner_steps": [{"type": "wait"}],
            "sensor": "none",
            "max_count": 0,
        }
    )
    assert len(call_counts) == 0


def test_repeat_until_stops_when_condition_met():
    """When condition becomes True mid-loop, stop iterating."""
    runner = _make_runner()
    call_counts = []
    iteration = [0]

    def record_wait(step):
        call_counts.append(1)
        iteration[0] += 1

    runner._step_handlers["wait"] = record_wait

    with patch("castor.drivers.lidar_driver.get_lidar") as mock_lidar:
        # Return center_cm=500 for first 2 calls, then 50 (triggers lt 100 → True)
        side_effects = [{"center_cm": 500}, {"center_cm": 500}, {"center_cm": 50}]
        mock_lidar.return_value.obstacles.side_effect = side_effects

        runner._step_repeat_until(
            {
                "inner_steps": [{"type": "wait"}],
                "sensor": "lidar",
                "field": "center_cm",
                "op": "lt",
                "value": 100,
                "max_count": 10,
            }
        )

    # Should have stopped after 3 iterations (condition True on 3rd)
    assert len(call_counts) <= 3


# ── stop() terminates loop ────────────────────────────────────────────────────


def test_repeat_until_stop_breaks_loop():
    runner = _make_runner()
    call_counts = []

    def slow_wait(step):
        call_counts.append(1)
        time.sleep(0.03)

    runner._step_handlers["wait"] = slow_wait

    t = threading.Thread(
        target=runner._step_repeat_until,
        args=({"inner_steps": [{"type": "wait"}], "sensor": "none", "max_count": -1},),
        daemon=True,
    )
    t.start()
    time.sleep(0.1)
    runner.stop()
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert len(call_counts) < 20


# ── dwell_s between iterations ───────────────────────────────────────────────


def test_repeat_until_dwell_respected():
    runner = _make_runner()
    timestamps = []

    def record_wait(step):
        timestamps.append(time.monotonic())

    runner._step_handlers["wait"] = record_wait

    runner._step_repeat_until(
        {
            "inner_steps": [{"type": "wait"}],
            "sensor": "none",
            "max_count": 2,
            "dwell_s": 0.05,
        }
    )

    assert len(timestamps) == 2
    assert timestamps[1] - timestamps[0] >= 0.04  # at least dwell_s apart


# ── Runs inner steps each iteration ──────────────────────────────────────────


def test_repeat_until_runs_all_inner_steps_per_iteration():
    runner = _make_runner()
    events = []
    runner._step_handlers["wait"] = lambda s: events.append("wait")
    runner._step_handlers["stop"] = lambda s: events.append("stop")

    runner._step_repeat_until(
        {
            "inner_steps": [{"type": "wait"}, {"type": "stop"}],
            "sensor": "none",
            "max_count": 3,
        }
    )
    # 3 iterations × 2 steps each = 6 events
    assert len(events) == 6
    assert events == ["wait", "stop"] * 3


# ── Unknown inner step type doesn't raise ────────────────────────────────────


def test_repeat_until_unknown_inner_step_skipped():
    runner = _make_runner()
    runner._step_repeat_until(
        {
            "inner_steps": [{"type": "does_not_exist"}],
            "sensor": "none",
            "max_count": 2,
        }
    )  # must not raise


# ── All 6 operators ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "op,actual,threshold,expect_stop_on",
    [
        ("lt", 50, 100, True),
        ("gt", 150, 100, True),
        ("lte", 100, 100, True),
        ("gte", 100, 100, True),
        ("eq", 100, 100, True),
        ("neq", 50, 100, True),
    ],
)
def test_repeat_until_operator(op, actual, threshold, expect_stop_on):
    runner = _make_runner()
    call_counts = []
    runner._step_handlers["wait"] = lambda s: call_counts.append(1)

    with patch("castor.drivers.lidar_driver.get_lidar") as mock_lidar:
        mock_lidar.return_value.obstacles.return_value = {"val": actual}
        runner._step_repeat_until(
            {
                "inner_steps": [{"type": "wait"}],
                "sensor": "lidar",
                "field": "val",
                "op": op,
                "value": threshold,
                "max_count": 5,
            }
        )

    if expect_stop_on:
        assert len(call_counts) == 1  # stopped after first iteration when condition=True
