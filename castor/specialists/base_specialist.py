"""Base classes for Task Specialist Agents."""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    type: str  # "grasp", "navigate", "dock", "report", "scout", etc.
    goal: str  # human-readable goal
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    params: dict = field(default_factory=dict)
    priority: int = 3  # 1=low, 5=high
    created_at: float = field(default_factory=time.monotonic)
    deadline_s: float | None = None  # optional timeout in seconds


@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    output: dict = field(default_factory=dict)
    duration_s: float = 0.0
    error: str | None = None


class BaseSpecialist(ABC):
    """Abstract base class for all task specialists."""

    name: str = "base"
    capabilities: list[str] = []

    def can_handle(self, task: Task) -> bool:
        """Return True if this specialist can handle the given task type."""
        return task.type in self.capabilities

    @abstractmethod
    async def execute(self, task: Task) -> TaskResult:
        """Execute the task and return a TaskResult."""
        ...

    def estimate_duration_s(self, task: Task) -> float:
        """Estimate execution duration in seconds. Override for accuracy."""
        return 1.0

    def health(self) -> dict:
        """Return health status of this specialist."""
        return {
            "name": self.name,
            "status": "healthy",
            "capabilities": list(self.capabilities),
        }
