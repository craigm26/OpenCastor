"""TaskPlanner — orchestrates task delegation across specialists."""

from __future__ import annotations

import asyncio
import heapq
from dataclasses import dataclass, field

from .base_specialist import BaseSpecialist, Task, TaskResult, TaskStatus


@dataclass(order=True)
class _PrioritizedTask:
    """Heap entry: sorted by (neg_priority, created_at) so highest priority, oldest first."""

    neg_priority: int  # negate so heapq (min-heap) gives highest priority first
    created_at: float
    task: Task = field(compare=False)


class TaskPlanner:
    """Orchestrates task delegation to the best available specialist."""

    def __init__(self, specialists: list[BaseSpecialist]) -> None:
        self._specialists: list[BaseSpecialist] = list(specialists)
        self._queue: list[_PrioritizedTask] = []  # heapq
        self._pending: dict[str, Task] = {}  # task_id -> Task (queued but not started)
        self._running: dict[str, Task] = {}  # task_id -> Task (in-flight)
        self._results: dict[str, TaskResult] = {}  # task_id -> TaskResult (completed)
        self._cancelled: set[str] = set()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def submit(self, task: Task) -> str:
        """Add a task to the priority queue. Returns the task ID."""
        entry = _PrioritizedTask(
            neg_priority=-task.priority,
            created_at=task.created_at,
            task=task,
        )
        heapq.heappush(self._queue, entry)
        self._pending[task.id] = task
        return task.id

    async def run_next(self) -> TaskResult | None:
        """Pop and execute the highest-priority pending task."""
        async with self._lock:
            # Find next non-cancelled task
            while self._queue:
                entry = heapq.heappop(self._queue)
                task = entry.task
                if task.id in self._cancelled:
                    self._pending.pop(task.id, None)
                    continue
                # Found a valid task
                break
            else:
                return None  # queue empty

        self._pending.pop(task.id, None)

        # Find best specialist
        specialist = self.best_specialist(task)
        if specialist is None:
            result = TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=f"No specialist available for task type '{task.type}'",
            )
            self._results[task.id] = result
            return result

        # Execute
        self._running[task.id] = task
        try:
            result = await specialist.execute(task)
        except Exception as exc:  # noqa: BLE001
            result = TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(exc),
            )
        finally:
            self._running.pop(task.id, None)

        self._results[task.id] = result
        return result

    async def run_all(self, max_concurrent: int = 2) -> list[TaskResult]:
        """
        Run all pending tasks, up to max_concurrent at a time.
        Returns all results in completion order.
        """
        results: list[TaskResult] = []
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _run_one(task: Task) -> None:
            specialist = self.best_specialist(task)
            if specialist is None:
                result = TaskResult(
                    task_id=task.id,
                    status=TaskStatus.FAILED,
                    error=f"No specialist available for task type '{task.type}'",
                )
                self._results[task.id] = result
                results.append(result)
                return

            async with semaphore:
                if task.id in self._cancelled:
                    result = TaskResult(
                        task_id=task.id,
                        status=TaskStatus.CANCELLED,
                    )
                    self._results[task.id] = result
                    results.append(result)
                    return

                self._running[task.id] = task
                try:
                    result = await specialist.execute(task)
                except Exception as exc:  # noqa: BLE001
                    result = TaskResult(
                        task_id=task.id,
                        status=TaskStatus.FAILED,
                        error=str(exc),
                    )
                finally:
                    self._running.pop(task.id, None)

                self._results[task.id] = result
                results.append(result)

        # Drain queue in priority order
        tasks_to_run: list[Task] = []
        while self._queue:
            entry = heapq.heappop(self._queue)
            task = entry.task
            self._pending.pop(task.id, None)
            if task.id not in self._cancelled:
                tasks_to_run.append(task)

        if not tasks_to_run:
            return results

        await asyncio.gather(*[_run_one(t) for t in tasks_to_run])
        return results

    def best_specialist(self, task: Task) -> BaseSpecialist | None:
        """
        Return the specialist that can handle the task with the lowest estimated duration.
        Returns None if no specialist can handle it.
        """
        candidates = [s for s in self._specialists if s.can_handle(task)]
        if not candidates:
            return None
        return min(candidates, key=lambda s: s.estimate_duration_s(task))

    def queue_status(self) -> dict:
        """Return counts of tasks in each state."""
        return {
            "pending": len(self._pending),
            "running": len(self._running),
            "done": len(self._results),
            "cancelled": len(self._cancelled),
            "queue_length": len(self._queue),
        }

    def get_result(self, task_id: str) -> TaskResult | None:
        """Retrieve the result of a completed task."""
        return self._results.get(task_id)

    async def cancel(self, task_id: str) -> bool:
        """
        Cancel a pending or running task.
        Returns True if the task was found and marked for cancellation.
        """
        if task_id in self._results:
            # Already done — cannot cancel
            return False

        if task_id in self._pending or task_id in self._running:
            self._cancelled.add(task_id)
            self._pending.pop(task_id, None)
            # Running tasks are cancelled opportunistically (checked on next await)
            if task_id in self._running:
                result = TaskResult(
                    task_id=task_id,
                    status=TaskStatus.CANCELLED,
                )
                self._results[task_id] = result
                self._running.pop(task_id, None)
            return True

        # Task might still be in the heap (not yet popped)
        # Check if it was ever submitted by looking at queue
        for entry in self._queue:
            if entry.task.id == task_id:
                self._cancelled.add(task_id)
                self._pending.pop(task_id, None)
                return True

        return False
