"""Tests for DockSpecialist."""

from __future__ import annotations

import asyncio

import pytest

from castor.specialists.base_specialist import Task, TaskStatus
from castor.specialists.dock import (
    DockSpecialist,
    _BATTERY_DOCK_THRESHOLD,
    _DECEL_STEPS,
    _HOME_POSITION,
    _generate_approach_path,
)


def run(coro):
    return asyncio.run(coro)


class TestGenerateApproachPath:
    def test_returns_correct_step_count(self):
        path = _generate_approach_path((0.0, 0.0), (5.0, 0.0))
        assert len(path) == _DECEL_STEPS

    def test_final_waypoint_at_target(self):
        path = _generate_approach_path((0.0, 0.0), (3.0, 4.0))
        last = path[-1]
        assert last["x"] == pytest.approx(3.0, abs=0.01)
        assert last["y"] == pytest.approx(4.0, abs=0.01)

    def test_waypoints_have_required_keys(self):
        path = _generate_approach_path((1.0, 1.0), (2.0, 2.0))
        for wp in path:
            assert "x" in wp
            assert "y" in wp
            assert "speed" in wp
            assert "step" in wp

    def test_speed_decreases(self):
        path = _generate_approach_path((0.0, 0.0), (10.0, 0.0))
        speeds = [wp["speed"] for wp in path]
        # Speed should generally decrease (deceleration)
        assert speeds[0] >= speeds[-1]

    def test_final_speed_low(self):
        path = _generate_approach_path((0.0, 0.0), (5.0, 0.0))
        assert path[-1]["speed"] <= 0.1

    def test_monotonic_x_approach(self):
        path = _generate_approach_path((0.0, 0.0), (5.0, 0.0))
        xs = [wp["x"] for wp in path]
        # x should increase monotonically toward target
        for i in range(len(xs) - 1):
            assert xs[i] <= xs[i + 1]


class TestDockSpecialist:
    def setup_method(self):
        self.spec = DockSpecialist()

    def test_name(self):
        assert self.spec.name == "dock"

    def test_capabilities(self):
        assert set(self.spec.capabilities) == {"dock", "undock", "charge", "return_home"}

    def test_can_handle_dock(self):
        task = Task(type="dock", goal="dock")
        assert self.spec.can_handle(task) is True

    def test_cannot_handle_grasp(self):
        task = Task(type="grasp", goal="grasp")
        assert self.spec.can_handle(task) is False

    # ------------------------------------------------------------------ #
    # Dock — with known position
    # ------------------------------------------------------------------ #

    def test_dock_with_known_position(self):
        task = Task(
            type="dock",
            goal="dock",
            params={
                "dock_position": [5.0, 3.0],
                "current_position": [0.0, 0.0],
                "battery_level": 10.0,
            },
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert result.output["action"] == "dock"
        assert "waypoints" in result.output
        assert len(result.output["waypoints"]) > 0

    def test_dock_waypoints_reach_target(self):
        dock_pos = [4.0, 0.0]
        task = Task(
            type="dock",
            goal="dock",
            params={
                "dock_position": dock_pos,
                "current_position": [0.0, 0.0],
                "battery_level": 5.0,
            },
        )
        result = run(self.spec.execute(task))
        last_wp = result.output["waypoints"][-1]
        assert last_wp["x"] == pytest.approx(dock_pos[0], abs=0.01)
        assert last_wp["y"] == pytest.approx(dock_pos[1], abs=0.01)

    def test_dock_deceleration_waypoints(self):
        task = Task(
            type="dock",
            goal="dock",
            params={"dock_position": [5.0, 0.0], "battery_level": 20.0},
        )
        result = run(self.spec.execute(task))
        assert len(result.output["waypoints"]) == _DECEL_STEPS

    # ------------------------------------------------------------------ #
    # Dock — without known position
    # ------------------------------------------------------------------ #

    def test_dock_without_position_returns_search(self):
        task = Task(
            type="dock",
            goal="dock",
            params={"battery_level": 5.0},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert result.output["action"] == "search_for_dock"
        assert "instructions" in result.output
        assert len(result.output["instructions"]) > 0

    # ------------------------------------------------------------------ #
    # Battery check
    # ------------------------------------------------------------------ #

    def test_dock_refuses_high_battery(self):
        task = Task(
            type="dock",
            goal="dock",
            params={"battery_level": 90.0, "dock_position": [1.0, 0.0]},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED
        assert result.error is not None

    def test_dock_accepts_low_battery(self):
        task = Task(
            type="dock",
            goal="dock",
            params={"battery_level": 15.0, "dock_position": [1.0, 0.0]},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS

    def test_dock_at_threshold_boundary(self):
        # Exactly at threshold — should fail (> not >=)
        task = Task(
            type="dock",
            goal="dock",
            params={"battery_level": _BATTERY_DOCK_THRESHOLD + 0.1, "dock_position": [1.0, 0.0]},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED

    def test_dock_no_battery_param(self):
        """No battery_level param → no check, should succeed."""
        task = Task(
            type="dock",
            goal="dock",
            params={"dock_position": [1.0, 0.0]},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS

    def test_dock_invalid_battery_value(self):
        task = Task(
            type="dock",
            goal="dock",
            params={"battery_level": "not_a_number", "dock_position": [1.0, 0.0]},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED

    # ------------------------------------------------------------------ #
    # Return home
    # ------------------------------------------------------------------ #

    def test_return_home_returns_waypoints(self):
        task = Task(
            type="return_home",
            goal="go home",
            params={"current_position": [3.0, 4.0]},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert "waypoints" in result.output
        assert len(result.output["waypoints"]) > 0

    def test_return_home_final_waypoint_at_home(self):
        task = Task(
            type="return_home",
            goal="go home",
            params={"current_position": [5.0, 5.0]},
        )
        result = run(self.spec.execute(task))
        last_wp = result.output["waypoints"][-1]
        assert last_wp["x"] == pytest.approx(_HOME_POSITION[0], abs=0.01)
        assert last_wp["y"] == pytest.approx(_HOME_POSITION[1], abs=0.01)

    def test_return_home_includes_time_estimate(self):
        task = Task(
            type="return_home",
            goal="go home",
            params={"current_position": [5.0, 0.0]},
        )
        result = run(self.spec.execute(task))
        assert "estimated_time_s" in result.output
        assert result.output["estimated_time_s"] > 0

    def test_return_home_deceleration(self):
        task = Task(
            type="return_home",
            goal="go home",
            params={"current_position": [10.0, 0.0]},
        )
        result = run(self.spec.execute(task))
        speeds = [wp["speed"] for wp in result.output["waypoints"]]
        assert speeds[0] >= speeds[-1]

    def test_return_home_no_current_position(self):
        """Defaults to (0,0) when not provided."""
        task = Task(type="return_home", goal="go home", params={})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS

    # ------------------------------------------------------------------ #
    # Duration estimation
    # ------------------------------------------------------------------ #

    def test_estimate_dock_duration(self):
        task = Task(
            type="dock",
            goal="dock",
            params={"current_position": [0.0, 0.0], "dock_position": [5.0, 0.0]},
        )
        d = self.spec.estimate_duration_s(task)
        assert d == pytest.approx(10.0, rel=0.1)  # 5m / 0.5m/s

    def test_estimate_return_home_duration(self):
        task = Task(
            type="return_home",
            goal="home",
            params={"current_position": [5.0, 0.0]},
        )
        d = self.spec.estimate_duration_s(task)
        assert d == pytest.approx(10.0, rel=0.1)

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    def test_health_keys(self):
        h = self.spec.health()
        assert h["name"] == "dock"
        assert "dock_threshold_pct" in h
        assert h["dock_threshold_pct"] == _BATTERY_DOCK_THRESHOLD
        assert "home_position" in h

    # ------------------------------------------------------------------ #
    # Undock
    # ------------------------------------------------------------------ #

    def test_undock_succeeds(self):
        task = Task(type="undock", goal="leave dock", params={"current_position": [0.0, 0.0]})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert result.output["action"] == "undock"
        assert len(result.output["waypoints"]) > 0
