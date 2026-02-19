"""BaseAgent ABC — all agents extend this.

Defines the lifecycle (start/stop), sensor interface (observe/act),
and health-reporting contract that every OpenCastor agent must implement.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentStatus(Enum):
    """Lifecycle states for a BaseAgent."""

    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


class BaseAgent(ABC):
    """Abstract base for all OpenCastor agents.

    Subclasses must implement :meth:`observe` and :meth:`act`.
    :meth:`start` / :meth:`stop` manage an optional background asyncio task.

    Example::

        class MyAgent(BaseAgent):
            name = "my_agent"

            async def observe(self, sensor_data):
                return {"parsed": sensor_data}

            async def act(self, context):
                return {"action": "move", "direction": "forward", "speed": 0.5}
    """

    #: Unique agent name — must be overridden in subclasses.
    name: str = "base"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, Any] = config or {}
        self.status: AgentStatus = AgentStatus.IDLE
        self._start_time: Optional[float] = None
        self._errors: List[str] = []
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._stop_event: asyncio.Event = asyncio.Event()
        self._logger = logging.getLogger(f"OpenCastor.Agents.{self.name}")

    async def start(self) -> None:
        """Begin the agent's background loop.

        Idempotent — calling start on a RUNNING agent is a no-op.
        """
        if self.status == AgentStatus.RUNNING:
            return
        self.status = AgentStatus.RUNNING
        self._start_time = time.monotonic()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        self._logger.info(f"Agent '{self.name}' started")

    async def stop(self) -> None:
        """Gracefully shut down the background loop."""
        self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status = AgentStatus.STOPPED
        self._logger.info(f"Agent '{self.name}' stopped")

    async def _run_loop(self) -> None:
        """Default background loop.

        Subclasses may override to implement continuous processing.
        The default implementation simply sleeps until stop() is called.
        """
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._record_error(str(exc))
            raise

    @abstractmethod
    async def observe(self, sensor_data: Dict[str, Any]) -> Any:
        """Process raw sensor data and return structured output.

        Args:
            sensor_data: Dict of sensor readings keyed by sensor name.

        Returns:
            Structured observation (type defined by subclass).
        """
        ...

    @abstractmethod
    async def act(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Produce an action dict from current context.

        Args:
            context: Dict containing scene data, goals, or other inputs.

        Returns:
            Action dict — at minimum ``{"action": str}``.
        """
        ...

    def health(self) -> Dict[str, Any]:
        """Return a snapshot of agent health.

        Returns:
            Dict with keys:
            - ``status``: current status string
            - ``uptime_s``: seconds since start (0.0 if never started)
            - ``errors``: list of error message strings
        """
        uptime = (time.monotonic() - self._start_time) if self._start_time is not None else 0.0
        return {
            "status": self.status.value,
            "uptime_s": round(uptime, 2),
            "errors": list(self._errors),
        }

    def _record_error(self, msg: str) -> None:
        """Record an error message and transition to ERROR status."""
        self._errors.append(msg)
        self.status = AgentStatus.ERROR
        self._logger.error(msg)
