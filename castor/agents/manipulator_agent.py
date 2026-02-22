"""ManipulatorAgent — Layer 3 agent: wraps ManipulatorSpecialist for async swarm use.

Subscribes to ``swarm.manipulation_task`` in SharedState, executes the task via
the ManipulatorSpecialist, and publishes results to ``swarm.manipulation_result``.
"""

import logging
from typing import Any, Dict, Optional

from .base import BaseAgent
from .shared_state import SharedState

logger = logging.getLogger("OpenCastor.Agents.Manipulator")


class ManipulatorAgent(BaseAgent):
    """Arm/gripper agent for the Layer 3 swarm.

    Bridges the async agent layer with the synchronous ManipulatorSpecialist.
    Reads pending tasks from SharedState and publishes structured results.

    SharedState keys consumed:
        ``swarm.manipulation_task`` — dict with at least ``type`` and ``goal`` fields.

    SharedState keys published:
        ``swarm.manipulation_result`` — TaskResult fields as a plain dict.
    """

    name = "manipulator"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        shared_state: Optional[SharedState] = None,
    ):
        super().__init__(config)
        self._state = shared_state or SharedState()
        self._specialist = None

        try:
            from castor.specialists.manipulator import ManipulatorSpecialist

            self._specialist = ManipulatorSpecialist(config or {})
            logger.debug("ManipulatorSpecialist ready")
        except Exception as exc:
            logger.warning("ManipulatorSpecialist unavailable: %s", exc)

    async def observe(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        """Read pending manipulation task from SharedState or sensor_data."""
        task = self._state.get("swarm.manipulation_task") or sensor_data.get("manipulation_task")
        return {"pending_task": task}

    async def act(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the pending manipulation task and publish the result."""
        task_data = context.get("pending_task")

        if not task_data:
            return {"action": "idle", "agent": self.name}

        if not self._specialist:
            error_result = {"status": "error", "error": "specialist_unavailable"}
            self._state.set("swarm.manipulation_result", error_result)
            return {"action": "manipulate", "result": error_result}

        try:
            from castor.specialists.base_specialist import Task

            task = Task(
                type=task_data.get("type", "grasp"),
                goal=task_data.get("goal", ""),
                params=task_data.get("params", {}),
                priority=task_data.get("priority", 3),
            )
            result = await self._specialist.execute(task)
            result_dict = {
                "task_id": result.task_id,
                "status": result.status.value
                if hasattr(result.status, "value")
                else str(result.status),
                "output": result.output,
                "duration_s": result.duration_s,
                "error": result.error,
            }
            self._state.set("swarm.manipulation_result", result_dict)
            logger.info(
                "Manipulation %s: %s (%.2fs)",
                task.type,
                result_dict["status"],
                result_dict["duration_s"],
            )
            return {"action": "manipulate", "result": result_dict}

        except Exception as exc:
            logger.error("Manipulation failed: %s", exc)
            error_result = {"status": "error", "error": str(exc)}
            self._state.set("swarm.manipulation_result", error_result)
            return {"action": "manipulate", "result": error_result}
