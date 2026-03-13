"""Tests for task-aware routing in TieredBrain (issue #612)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from castor.providers.base import Thought
from castor.providers.task_router import TaskCategory, TaskRouter
from castor.tiered_brain import TieredBrain


def _make_provider(action=None, text="ok"):
    """Return a mock provider whose think() returns a Thought."""
    p = MagicMock()
    p.think.return_value = Thought(text, action or {"type": "move", "direction": "forward"})
    return p


def _brain(fast=None, planner=None, config=None):
    """Helper: build a TieredBrain with reactive layer disabled."""
    fast = fast or _make_provider()
    cfg = config or {}
    # Disable reactive layer interference
    cfg.setdefault("reactive", {"min_obstacle_m": 0.0})
    brain = TieredBrain(fast_provider=fast, planner_provider=planner, config=cfg)
    # Ensure reactive layer never triggers
    brain.reactive.evaluate = lambda *_: None
    return brain, fast


class TestSensorPollSkipsPlanner:
    """SENSOR_POLL should never call the planner, saving tokens."""

    def test_sensor_poll_does_not_call_planner(self):
        planner = _make_provider()
        brain, fast = _brain(planner=planner)

        brain.think(b"", "check battery level", task_category="sensor_poll")

        planner.think.assert_not_called()
        fast.think.assert_called_once()

    def test_sensor_poll_skips_planner_even_at_interval_tick(self):
        """Even on a tick that would normally trigger the planner, SENSOR_POLL bypasses it."""
        planner = _make_provider()
        brain, fast = _brain(planner=planner, config={"tiered_brain": {"planner_interval": 1}})

        # Tick 1 would normally trigger planner (interval=1)
        brain.think(b"", "poll range sensor", task_category="sensor_poll")

        planner.think.assert_not_called()


class TestReasoningForcesPlanner:
    """High-complexity task categories should force planner execution."""

    @pytest.mark.parametrize(
        "category",
        [
            "reasoning",
            "code",
            "safety",
            "vision",
            "search",
        ],
    )
    def test_prefers_planner_when_available(self, category):
        planner = _make_provider(text="planner response")
        brain, fast = _brain(planner=planner)
        # Ensure we're NOT on a periodic interval tick
        brain.tick_count = 999  # won't hit any interval

        brain.think(b"", "complex task", task_category=category)

        planner.think.assert_called_once()

    def test_safety_forces_planner(self):
        """SAFETY must never be downgraded — always uses planner if available."""
        planner = _make_provider(text="safety check result")
        brain, fast = _brain(planner=planner)
        brain.tick_count = 997  # non-interval tick

        brain.think(b"", "check safe to proceed", task_category="safety")

        planner.think.assert_called_once()


class TestFallbackWhenPlannerNone:
    """When no planner is configured, all categories fall back to fast provider."""

    @pytest.mark.parametrize(
        "category",
        [
            "sensor_poll",
            "navigation",
            "reasoning",
            "code",
            "safety",
            "vision",
            "search",
            None,
        ],
    )
    def test_falls_back_to_fast_without_planner(self, category):
        brain, fast = _brain(planner=None)

        result = brain.think(b"", "any task", task_category=category)

        fast.think.assert_called_once()
        assert result is not None


class TestTaskRoutingConfig:
    """task_routing config block is passed through to TaskRouter."""

    def test_task_routing_config_builds_router(self):
        """TieredBrain reads task_routing from RCAN config and builds a TaskRouter."""
        config = {
            "task_routing": {
                "sensor_poll": ["custom_fast", "ollama"],
            }
        }
        fast = _make_provider()
        brain = TieredBrain(fast_provider=fast, config=config)
        brain.reactive.evaluate = lambda *_: None

        # Verify the router was built with the custom routing table
        result = brain.task_router.select(TaskCategory.SENSOR_POLL, ["custom_fast", "ollama"])
        assert result == "custom_fast"

    def test_custom_task_router_injection(self):
        """A pre-built TaskRouter can be injected directly."""
        custom_router = TaskRouter(routing_table={"reasoning": ["my_provider"]})
        fast = _make_provider()
        brain = TieredBrain(fast_provider=fast, task_router=custom_router)
        brain.reactive.evaluate = lambda *_: None

        assert brain.task_router is custom_router
        assert brain.task_router.select(TaskCategory.REASONING, ["my_provider"]) == "my_provider"

    def test_unknown_category_does_not_crash(self):
        """An unknown category string should be ignored gracefully."""
        planner = _make_provider()
        brain, fast = _brain(planner=planner)

        # Should not raise — logs a warning and falls back to default behaviour
        result = brain.think(b"", "something", task_category="totally_unknown_category")
        assert result is not None


class TestNavigationDefaultBehaviour:
    """NAVIGATION uses default interval-based planner logic."""

    def test_navigation_uses_planner_on_interval(self):
        planner = _make_provider(text="nav plan")
        brain, fast = _brain(planner=planner, config={"tiered_brain": {"planner_interval": 5}})

        # Run 5 ticks, only tick 5 should trigger planner
        for _ in range(4):
            brain.think(b"", "navigate", task_category="navigation")
        assert planner.think.call_count == 0

        brain.think(b"", "navigate", task_category="navigation")
        assert planner.think.call_count == 1
