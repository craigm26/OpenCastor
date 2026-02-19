"""Tests for TaskPlanner."""

from __future__ import annotations

import asyncio
import time

import pytest

from castor.specialists.base_specialist import Task, TaskResult, TaskStatus
from castor.specialists.dock import DockSpecialist
from castor.specialists.manipulator import ManipulatorSpecialist
from castor.specialists.responder import ResponderSpecialist
from castor.specialists.scout import ScoutSpecialist
from castor.specialists.task_planner import TaskPlanner


def run(coro):
    return asyncio.run(coro)


def _make_task(type_: str, goal: str = "test", priority: int = 3, **kwargs) -> Task:
    return Task(type=type_, goal=goal, priority=priority, **kwargs)


class TestTaskPlannerInit:
    def test_empty_specialists(self):
        planner = TaskPlanner([])
        assert planner.queue_status()["pending"] == 0

    def test_with_specialists(self):
        specs = [ManipulatorSpecialist(), ScoutSpecialist()]
        planner = TaskPlanner(specs)
        assert len(planner._specialists) == 2


class TestSubmitTask:
    def setup_method(self):
        self.planner = TaskPlanner([ManipulatorSpecialist(), ScoutSpecialist()])

    def test_submit_returns_task_id(self):
        task = _make_task("grasp")
        tid = self.planner.submit(task)
        assert tid == task.id

    def test_submit_adds_to_pending(self):
        task = _make_task("grasp")
        self.planner.submit(task)
        status = self.planner.queue_status()
        assert status["pending"] == 1

    def test_submit_multiple_tasks(self):
        for _ in range(5):
            self.planner.submit(_make_task("grasp"))
        assert self.planner.queue_status()["pending"] == 5

    def test_submitted_task_in_queue(self):
        task = _make_task("scout")
        self.planner.submit(task)
        assert task.id in self.planner._pending


class TestBestSpecialist:
    def setup_method(self):
        self.manip = ManipulatorSpecialist()
        self.scout = ScoutSpecialist()
        self.planner = TaskPlanner([self.manip, self.scout])

    def test_best_for_grasp(self):
        task = _make_task("grasp")
        best = self.planner.best_specialist(task)
        assert isinstance(best, ManipulatorSpecialist)

    def test_best_for_scout(self):
        task = _make_task("scout")
        best = self.planner.best_specialist(task)
        assert isinstance(best, ScoutSpecialist)

    def test_none_for_unknown_type(self):
        task = _make_task("teleport")
        best = self.planner.best_specialist(task)
        assert best is None

    def test_picks_fastest(self):
        """When multiple specialists can handle, pick lowest duration."""
        from castor.specialists.base_specialist import BaseSpecialist

        class FastSpec(BaseSpecialist):
            name = "fast"
            capabilities = ["grasp"]

            async def execute(self, task: Task) -> TaskResult:
                return TaskResult(task_id=task.id, status=TaskStatus.SUCCESS)

            def estimate_duration_s(self, task: Task) -> float:
                return 0.1  # very fast

        class SlowSpec(BaseSpecialist):
            name = "slow"
            capabilities = ["grasp"]

            async def execute(self, task: Task) -> TaskResult:
                return TaskResult(task_id=task.id, status=TaskStatus.SUCCESS)

            def estimate_duration_s(self, task: Task) -> float:
                return 100.0  # very slow

        planner = TaskPlanner([SlowSpec(), FastSpec()])
        task = _make_task("grasp")
        best = planner.best_specialist(task)
        assert isinstance(best, FastSpec)


class TestRunNext:
    def setup_method(self):
        self.planner = TaskPlanner(
            [ManipulatorSpecialist(), ScoutSpecialist(), DockSpecialist(), ResponderSpecialist()]
        )

    def test_run_next_returns_none_when_empty(self):
        result = run(self.planner.run_next())
        assert result is None

    def test_run_next_returns_result(self):
        task = _make_task("home")
        self.planner.submit(task)
        result = run(self.planner.run_next())
        assert result is not None
        assert result.task_id == task.id

    def test_run_next_moves_to_done(self):
        task = _make_task("home")
        self.planner.submit(task)
        run(self.planner.run_next())
        status = self.planner.queue_status()
        assert status["pending"] == 0
        assert status["done"] == 1

    def test_run_next_no_specialist_fails(self):
        task = _make_task("quantum_leap")
        self.planner.submit(task)
        result = run(self.planner.run_next())
        assert result.status == TaskStatus.FAILED
        assert "No specialist" in result.error

    def test_run_next_result_retrievable(self):
        task = _make_task("home")
        self.planner.submit(task)
        result = run(self.planner.run_next())
        retrieved = self.planner.get_result(task.id)
        assert retrieved is not None
        assert retrieved.task_id == task.id


class TestPriorityOrdering:
    def setup_method(self):
        self.planner = TaskPlanner([ManipulatorSpecialist()])

    def test_high_priority_runs_first(self):
        low = _make_task("home", priority=1)
        high = _make_task("home", priority=5)
        low.created_at = time.monotonic()
        high.created_at = low.created_at + 0.001  # high is newer but higher priority

        self.planner.submit(low)
        self.planner.submit(high)

        result1 = run(self.planner.run_next())
        assert result1.task_id == high.id

    def test_same_priority_fifo(self):
        t1 = _make_task("home", priority=3)
        t2 = _make_task("home", priority=3)
        t1.created_at = 1000.0
        t2.created_at = 2000.0  # t2 is newer

        self.planner.submit(t1)
        self.planner.submit(t2)

        result1 = run(self.planner.run_next())
        # t1 submitted first with earlier created_at → runs first
        assert result1.task_id == t1.id

    def test_priority_ordering_multiple(self):
        tasks = [
            _make_task("home", priority=2),
            _make_task("home", priority=5),
            _make_task("home", priority=1),
            _make_task("home", priority=4),
        ]
        for i, t in enumerate(tasks):
            t.created_at = float(i)
            self.planner.submit(t)

        results = run(self.planner.run_all())
        priorities = []
        for r in results:
            for t in tasks:
                if t.id == r.task_id:
                    priorities.append(t.priority)
        # Should be in descending priority order
        assert priorities == sorted(priorities, reverse=True)


class TestRunAll:
    def setup_method(self):
        self.planner = TaskPlanner(
            [ManipulatorSpecialist(), ScoutSpecialist(), DockSpecialist(), ResponderSpecialist()]
        )

    def test_run_all_returns_all_results(self):
        tasks = [
            _make_task("home"),
            _make_task("scout"),
            _make_task("return_home"),
        ]
        for t in tasks:
            self.planner.submit(t)
        results = run(self.planner.run_all())
        assert len(results) == len(tasks)

    def test_run_all_clears_queue(self):
        self.planner.submit(_make_task("home"))
        self.planner.submit(_make_task("scout"))
        run(self.planner.run_all())
        status = self.planner.queue_status()
        assert status["pending"] == 0

    def test_run_all_concurrent(self):
        """run_all with max_concurrent=2 should handle tasks correctly."""
        for _ in range(4):
            self.planner.submit(_make_task("home"))
        results = run(self.planner.run_all(max_concurrent=2))
        assert len(results) == 4

    def test_run_all_mixed_specialists(self):
        tasks_data = [
            ("home", ManipulatorSpecialist.name),
            ("scout", ScoutSpecialist.name),
            ("report", ResponderSpecialist.name),
        ]
        for type_, _ in tasks_data:
            self.planner.submit(_make_task(type_))
        results = run(self.planner.run_all())
        for r in results:
            assert r.status == TaskStatus.SUCCESS

    def test_run_all_empty_queue(self):
        results = run(self.planner.run_all())
        assert results == []

    def test_concurrent_execution_correctness(self):
        """All results are present and valid."""
        task_ids = set()
        for _ in range(3):
            t = _make_task("home")
            task_ids.add(t.id)
            self.planner.submit(t)
        results = run(self.planner.run_all(max_concurrent=3))
        result_ids = {r.task_id for r in results}
        assert result_ids == task_ids


class TestCancelTask:
    def setup_method(self):
        self.planner = TaskPlanner([ManipulatorSpecialist()])

    def test_cancel_pending_task(self):
        task = _make_task("home")
        self.planner.submit(task)
        result = run(self.planner.cancel(task.id))
        assert result is True

    def test_cancel_removes_from_pending(self):
        task = _make_task("home")
        self.planner.submit(task)
        run(self.planner.cancel(task.id))
        assert task.id not in self.planner._pending

    def test_cancel_unknown_task_returns_false(self):
        result = run(self.planner.cancel("nonexistent-id"))
        assert result is False

    def test_cancel_completed_task_returns_false(self):
        task = _make_task("home")
        self.planner.submit(task)
        run(self.planner.run_next())
        result = run(self.planner.cancel(task.id))
        assert result is False

    def test_cancelled_task_skipped_in_run_next(self):
        task1 = _make_task("home", priority=5)
        task2 = _make_task("home", priority=1)
        task1.created_at = 1.0
        task2.created_at = 2.0

        self.planner.submit(task1)
        self.planner.submit(task2)

        # Cancel the high-priority task
        run(self.planner.cancel(task1.id))

        # Next run should execute task2
        result = run(self.planner.run_next())
        assert result is not None
        assert result.task_id == task2.id


class TestQueueStatus:
    def setup_method(self):
        self.planner = TaskPlanner([ManipulatorSpecialist()])

    def test_initial_status(self):
        status = self.planner.queue_status()
        assert status["pending"] == 0
        assert status["running"] == 0
        assert status["done"] == 0

    def test_status_after_submit(self):
        self.planner.submit(_make_task("home"))
        status = self.planner.queue_status()
        assert status["pending"] == 1

    def test_status_after_run(self):
        self.planner.submit(_make_task("home"))
        run(self.planner.run_next())
        status = self.planner.queue_status()
        assert status["pending"] == 0
        assert status["done"] == 1

    def test_status_keys_present(self):
        status = self.planner.queue_status()
        assert "pending" in status
        assert "running" in status
        assert "done" in status
        assert "cancelled" in status

    def test_status_cancelled_count(self):
        task = _make_task("home")
        self.planner.submit(task)
        run(self.planner.cancel(task.id))
        status = self.planner.queue_status()
        assert status["cancelled"] >= 1


class TestGetResult:
    def setup_method(self):
        self.planner = TaskPlanner([ManipulatorSpecialist()])

    def test_get_result_after_run(self):
        task = _make_task("home")
        self.planner.submit(task)
        run(self.planner.run_next())
        result = self.planner.get_result(task.id)
        assert result is not None
        assert result.task_id == task.id

    def test_get_result_missing(self):
        result = self.planner.get_result("not-a-real-id")
        assert result is None

    def test_get_result_success_status(self):
        task = _make_task("home")
        self.planner.submit(task)
        run(self.planner.run_next())
        result = self.planner.get_result(task.id)
        assert result.status == TaskStatus.SUCCESS
