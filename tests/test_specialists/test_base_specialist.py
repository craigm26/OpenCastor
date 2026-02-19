"""Tests for base_specialist: Task, TaskResult, TaskStatus, BaseSpecialist."""

from __future__ import annotations

import asyncio
import time

import pytest

from castor.specialists.base_specialist import (
    BaseSpecialist,
    Task,
    TaskResult,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# Concrete stub specialist for testing ABC
# ---------------------------------------------------------------------------


class _StubSpecialist(BaseSpecialist):
    name = "stub"
    capabilities = ["foo", "bar"]

    async def execute(self, task: Task) -> TaskResult:
        return TaskResult(
            task_id=task.id,
            status=TaskStatus.SUCCESS,
            output={"done": True},
            duration_s=0.01,
        )

    def estimate_duration_s(self, task: Task) -> float:
        return 2.5


class _EmptySpecialist(BaseSpecialist):
    name = "empty"
    capabilities = []

    async def execute(self, task: Task) -> TaskResult:
        return TaskResult(task_id=task.id, status=TaskStatus.FAILED)


# ---------------------------------------------------------------------------
# TaskStatus tests
# ---------------------------------------------------------------------------


class TestTaskStatus:
    def test_all_statuses_exist(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.SUCCESS.value == "success"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.CANCELLED.value == "cancelled"

    def test_status_count(self):
        assert len(TaskStatus) == 5

    def test_status_from_value(self):
        assert TaskStatus("running") is TaskStatus.RUNNING


# ---------------------------------------------------------------------------
# Task dataclass tests
# ---------------------------------------------------------------------------


class TestTask:
    def test_minimal_creation(self):
        task = Task(type="grasp", goal="pick up cup")
        assert task.type == "grasp"
        assert task.goal == "pick up cup"
        assert task.id  # non-empty uuid
        assert isinstance(task.params, dict)
        assert task.params == {}
        assert task.priority == 3
        assert task.deadline_s is None

    def test_id_is_unique(self):
        t1 = Task(type="dock", goal="go home")
        t2 = Task(type="dock", goal="go home")
        assert t1.id != t2.id

    def test_custom_params(self):
        params = {"object_position": [1.0, 2.0, 0.5]}
        task = Task(type="grasp", goal="grasp object", params=params)
        assert task.params["object_position"] == [1.0, 2.0, 0.5]

    def test_params_not_shared(self):
        """Mutable default must not be shared between instances."""
        t1 = Task(type="a", goal="a")
        t2 = Task(type="b", goal="b")
        t1.params["key"] = "value"
        assert "key" not in t2.params

    def test_priority_range(self):
        for p in [1, 2, 3, 4, 5]:
            task = Task(type="scout", goal="explore", priority=p)
            assert task.priority == p

    def test_created_at_is_set(self):
        before = time.monotonic()
        task = Task(type="home", goal="go home")
        after = time.monotonic()
        assert before <= task.created_at <= after

    def test_deadline_s_optional(self):
        task = Task(type="dock", goal="dock", deadline_s=30.0)
        assert task.deadline_s == 30.0

    def test_custom_id(self):
        task = Task(type="grasp", goal="g", id="custom-id-123")
        assert task.id == "custom-id-123"


# ---------------------------------------------------------------------------
# TaskResult dataclass tests
# ---------------------------------------------------------------------------


class TestTaskResult:
    def test_success_result(self):
        result = TaskResult(
            task_id="abc",
            status=TaskStatus.SUCCESS,
            output={"value": 42},
            duration_s=1.5,
        )
        assert result.task_id == "abc"
        assert result.status == TaskStatus.SUCCESS
        assert result.output["value"] == 42
        assert result.duration_s == 1.5
        assert result.error is None

    def test_failed_result(self):
        result = TaskResult(
            task_id="xyz",
            status=TaskStatus.FAILED,
            error="something went wrong",
        )
        assert result.status == TaskStatus.FAILED
        assert result.error == "something went wrong"
        assert result.output == {}

    def test_output_not_shared(self):
        r1 = TaskResult(task_id="1", status=TaskStatus.SUCCESS)
        r2 = TaskResult(task_id="2", status=TaskStatus.SUCCESS)
        r1.output["k"] = "v"
        assert "k" not in r2.output

    def test_cancelled_result(self):
        result = TaskResult(task_id="t1", status=TaskStatus.CANCELLED)
        assert result.status == TaskStatus.CANCELLED

    def test_default_duration(self):
        result = TaskResult(task_id="t", status=TaskStatus.SUCCESS)
        assert result.duration_s == 0.0


# ---------------------------------------------------------------------------
# BaseSpecialist tests
# ---------------------------------------------------------------------------


class TestBaseSpecialist:
    def test_can_handle_matching_type(self):
        spec = _StubSpecialist()
        task = Task(type="foo", goal="do foo")
        assert spec.can_handle(task) is True

    def test_can_handle_second_capability(self):
        spec = _StubSpecialist()
        task = Task(type="bar", goal="do bar")
        assert spec.can_handle(task) is True

    def test_cannot_handle_unknown_type(self):
        spec = _StubSpecialist()
        task = Task(type="dance", goal="do dance")
        assert spec.can_handle(task) is False

    def test_empty_specialist_handles_nothing(self):
        spec = _EmptySpecialist()
        task = Task(type="foo", goal="foo")
        assert spec.can_handle(task) is False

    def test_estimate_duration(self):
        spec = _StubSpecialist()
        task = Task(type="foo", goal="foo")
        assert spec.estimate_duration_s(task) == 2.5

    def test_health_returns_dict(self):
        spec = _StubSpecialist()
        h = spec.health()
        assert isinstance(h, dict)
        assert h["name"] == "stub"
        assert h["status"] == "healthy"
        assert "capabilities" in h
        assert set(h["capabilities"]) == {"foo", "bar"}

    def test_execute_returns_result(self):
        spec = _StubSpecialist()
        task = Task(type="foo", goal="do foo")
        result = asyncio.run(spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert result.task_id == task.id

    def test_name_attribute(self):
        spec = _StubSpecialist()
        assert spec.name == "stub"

    def test_capabilities_attribute(self):
        spec = _StubSpecialist()
        assert "foo" in spec.capabilities
        assert "bar" in spec.capabilities
