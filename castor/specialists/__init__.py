"""castor.specialists — Task Specialist Agents + TaskPlanner (Phase 3).

Specialist agents extend the base brain with domain-specific behaviors.
A :class:`TaskPlanner` decomposes high-level instructions into typed
:class:`Task` objects and dispatches them to the appropriate specialist.

Built-in specialists:

- :class:`ScoutSpecialist` — Visual exploration and mapping.
- :class:`ManipulatorSpecialist` — Arm/gripper grasping tasks.
- :class:`DockSpecialist` — Docking / charging station approach.
- :class:`ResponderSpecialist` — Alert and response actions.

To add a specialist, subclass :class:`BaseSpecialist`, implement
``can_handle(task)`` and ``execute(task)``, then register it with the
:class:`TaskPlanner`::

    from castor.specialists import BaseSpecialist, Task, TaskResult

    class MySpecialist(BaseSpecialist):
        name = "my_specialist"

        def can_handle(self, task: Task) -> bool:
            return task.type == "my_task_type"

        async def execute(self, task: Task) -> TaskResult:
            # Do work...
            return TaskResult(success=True, output={})
"""

from .base_specialist import BaseSpecialist, Task, TaskResult, TaskStatus
from .dock import DockSpecialist
from .manipulator import ManipulatorSpecialist
from .responder import ResponderSpecialist
from .scout import ScoutSpecialist
from .task_planner import TaskPlanner

__all__ = [
    "BaseSpecialist",
    "Task",
    "TaskResult",
    "TaskStatus",
    "ManipulatorSpecialist",
    "ScoutSpecialist",
    "DockSpecialist",
    "ResponderSpecialist",
    "TaskPlanner",
]
