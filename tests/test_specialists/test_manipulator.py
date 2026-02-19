"""Tests for ManipulatorSpecialist."""

from __future__ import annotations

import asyncio
import math

import pytest

from castor.specialists.base_specialist import Task, TaskStatus
from castor.specialists.manipulator import ManipulatorSpecialist


def run(coro):
    return asyncio.run(coro)


class TestManipulatorSpecialist:
    def setup_method(self):
        self.spec = ManipulatorSpecialist()

    # ------------------------------------------------------------------ #
    # Basic attributes
    # ------------------------------------------------------------------ #

    def test_name(self):
        assert self.spec.name == "manipulator"

    def test_capabilities(self):
        assert set(self.spec.capabilities) == {"grasp", "place", "push", "home"}

    def test_can_handle_grasp(self):
        task = Task(type="grasp", goal="pick up cup")
        assert self.spec.can_handle(task) is True

    def test_can_handle_home(self):
        task = Task(type="home", goal="go home")
        assert self.spec.can_handle(task) is True

    def test_cannot_handle_dock(self):
        task = Task(type="dock", goal="dock")
        assert self.spec.can_handle(task) is False

    # ------------------------------------------------------------------ #
    # Grasp — valid params
    # ------------------------------------------------------------------ #

    def test_grasp_valid_params(self):
        task = Task(
            type="grasp",
            goal="grasp cup",
            params={"object_position": [1.0, 0.5, 0.3]},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert result.error is None
        assert "joint_angles" in result.output
        assert "gripper_state" in result.output
        assert "approach_vector" in result.output

    def test_grasp_joint_angles_format(self):
        task = Task(
            type="grasp",
            goal="grasp",
            params={"object_position": [1.0, 0.0, 0.5]},
        )
        result = run(self.spec.execute(task))
        angles = result.output["joint_angles"]
        assert isinstance(angles, list), "joint_angles must be a list"
        assert len(angles) == 6, "6-DOF arm requires 6 joint angles"
        for a in angles:
            assert isinstance(a, float), f"Each angle must be a float, got {type(a)}"

    def test_grasp_gripper_closed(self):
        task = Task(
            type="grasp",
            goal="grasp",
            params={"object_position": [0.5, 0.5, 0.2]},
        )
        result = run(self.spec.execute(task))
        assert result.output["gripper_state"] == "closed"

    def test_grasp_approach_vector_normalised(self):
        task = Task(
            type="grasp",
            goal="grasp",
            params={"object_position": [3.0, 4.0, 0.0]},
        )
        result = run(self.spec.execute(task))
        av = result.output["approach_vector"]
        mag = math.sqrt(sum(v**2 for v in av))
        assert abs(mag - 1.0) < 0.01, f"Approach vector should be normalised, magnitude={mag}"

    def test_grasp_object_position_in_output(self):
        pos = [1.0, 2.0, 0.5]
        task = Task(type="grasp", goal="g", params={"object_position": pos})
        result = run(self.spec.execute(task))
        assert result.output["object_position"] == pos

    # ------------------------------------------------------------------ #
    # Grasp — missing / invalid params
    # ------------------------------------------------------------------ #

    def test_grasp_missing_object_position(self):
        task = Task(type="grasp", goal="grasp cup", params={})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED
        assert result.error is not None
        assert "object_position" in result.error.lower()

    def test_grasp_invalid_object_position_short(self):
        task = Task(type="grasp", goal="grasp", params={"object_position": [1.0]})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED

    def test_grasp_invalid_object_position_type(self):
        task = Task(type="grasp", goal="grasp", params={"object_position": "invalid"})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED

    # ------------------------------------------------------------------ #
    # Home position
    # ------------------------------------------------------------------ #

    def test_home_succeeds(self):
        task = Task(type="home", goal="go home", params={})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS

    def test_home_joint_angles(self):
        task = Task(type="home", goal="go home", params={})
        result = run(self.spec.execute(task))
        angles = result.output["joint_angles"]
        assert len(angles) == 6
        assert all(isinstance(a, float) for a in angles)

    def test_home_gripper_open(self):
        task = Task(type="home", goal="go home", params={})
        result = run(self.spec.execute(task))
        assert result.output["gripper_state"] == "open"

    def test_home_position_label(self):
        task = Task(type="home", goal="go home", params={})
        result = run(self.spec.execute(task))
        assert result.output.get("position") == "home"

    # ------------------------------------------------------------------ #
    # Duration estimation
    # ------------------------------------------------------------------ #

    def test_estimate_home_duration(self):
        task = Task(type="home", goal="go home")
        d = self.spec.estimate_duration_s(task)
        assert d == 2.0

    def test_estimate_grasp_with_position(self):
        # Object at distance 3m → duration = 3/0.3 = 10s
        task = Task(
            type="grasp",
            goal="g",
            params={"object_position": [3.0, 0.0, 0.0]},
        )
        d = self.spec.estimate_duration_s(task)
        assert d == pytest.approx(10.0, rel=0.1)

    def test_estimate_grasp_without_position(self):
        task = Task(type="grasp", goal="g", params={})
        d = self.spec.estimate_duration_s(task)
        assert d >= 1.0  # default fallback

    def test_estimate_close_object_minimum(self):
        task = Task(
            type="grasp",
            goal="g",
            params={"object_position": [0.01, 0.0, 0.0]},
        )
        d = self.spec.estimate_duration_s(task)
        assert d >= 1.0  # minimum enforced

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    def test_health_keys(self):
        h = self.spec.health()
        assert h["name"] == "manipulator"
        assert "arm_dof" in h
        assert h["arm_dof"] == 6

    # ------------------------------------------------------------------ #
    # Place & Push (coverage)
    # ------------------------------------------------------------------ #

    def test_place_succeeds(self):
        task = Task(
            type="place",
            goal="place object",
            params={"place_position": [1.0, 0.0, 0.5]},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert result.output["gripper_state"] == "open"

    def test_push_succeeds(self):
        task = Task(
            type="push",
            goal="push object",
            params={"target_position": [2.0, 1.0, 0.0]},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS

    def test_place_missing_params(self):
        task = Task(type="place", goal="place", params={})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED

    def test_push_missing_params(self):
        task = Task(type="push", goal="push", params={})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED
