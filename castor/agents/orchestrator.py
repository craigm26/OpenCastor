"""OrchestratorAgent — Layer 3 master agent: task decomposition and swarm delegation.

Sits at the top of the single-robot agent hierarchy. Collects outputs from
all swarm agents via SharedState, resolves them into a single RCAN action,
and exposes a synchronous entry-point for the TieredBrain.

Architecture::

    TieredBrain.think()
        └── OrchestratorAgent.sync_think(sensor_data)
                ├── GuardianAgent        (safety veto)
                ├── NavigatorAgent       (where to go)
                ├── ObserverAgent        (what to see)
                ├── ManipulatorAgent     (what to touch)
                └── CommunicatorAgent    (what was said)

The orchestrator does *not* call other agents directly; it reads their
published outputs from SharedState and merges them into one action.
"""

import asyncio
import logging
import time
from typing import Any, Optional

from .base import BaseAgent
from .shared_state import Intent, SharedState

logger = logging.getLogger("OpenCastor.Agents.Orchestrator")


class OrchestratorAgent(BaseAgent):
    """Master agent: resolves multi-agent outputs into a single RCAN action.

    Config keys (under ``agents.orchestrator``):
        None required; all defaults are safe.

    SharedState keys consumed:
        ``swarm.guardian_report``       — GuardianAgent output.
        ``swarm.estop_active``          — bool, set by GuardianAgent.
        ``swarm.nav_plan``              — NavigatorAgent NavigationPlan dict.
        ``swarm.scene_graph``           — ObserverAgent SceneGraph dict.
        ``swarm.manipulation_result``   — ManipulatorAgent result dict.
        ``swarm.incoming_message``      — CommunicatorAgent pending message.

    SharedState keys published:
        ``swarm.orchestrated_action``   — final resolved RCAN action dict.
    """

    name = "orchestrator"

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        shared_state: Optional[SharedState] = None,
    ):
        super().__init__(config)
        self._state = shared_state or SharedState()
        self._tick = 0
        self._last_action: Optional[dict[str, Any]] = None
        self._log: list[dict[str, Any]] = []  # last 100 delegation entries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect(self) -> dict[str, Any]:
        """Read latest outputs from all swarm agents via SharedState."""
        return {
            "guardian_report": self._state.get("swarm.guardian_report"),
            "estop_active": self._state.get("swarm.estop_active", False),
            "nav_plan": self._state.get("swarm.nav_plan"),
            "scene_graph": self._state.get("swarm.scene_graph"),
            "manipulation_result": self._state.get("swarm.manipulation_result"),
            "incoming_message": self._state.get("swarm.incoming_message"),
            "current_intent": self._state.current_intent(),
        }

    def _active_intent_id(self) -> Optional[str]:
        current = self._state.current_intent()
        if isinstance(current, dict):
            return current.get("intent_id")
        return self._state.get("swarm.current_intent_id")

    def submit_intent(
        self,
        goal: str,
        priority: int = 0,
        deadline_ts: Optional[float] = None,
        safety_class: str = "normal",
        owner: str = "system",
    ) -> dict[str, Any]:
        """Create and enqueue a new orchestration intent."""
        intent = Intent(
            goal=goal,
            priority=priority,
            deadline_ts=deadline_ts,
            safety_class=safety_class,
            owner=owner,
        )
        result = self._state.add_intent(intent)
        logger.info(
            "Intent queued: %s (priority=%s safety=%s preempted=%s)",
            intent.intent_id,
            priority,
            safety_class,
            result.get("preempted"),
        )
        return {"intent": intent.to_dict(), **result}

    def list_intents(self) -> list[dict[str, Any]]:
        """List active and queued intents."""
        return self._state.list_intents()

    def pause_intent(self, intent_id: str, paused: bool = True) -> bool:
        """Pause or resume an intent by ID."""
        return self._state.pause_intent(intent_id, paused=paused)

    def reprioritize_intent(self, intent_id: str, priority: int) -> bool:
        """Change priority of an existing intent."""
        return self._state.reprioritize_intent(intent_id, priority=priority)

    def checkpoint_specialist(self, specialist: str, checkpoint: dict[str, Any]) -> None:
        """Store resumable specialist state (Scout/Navigator/Manipulator)."""
        self._state.set_specialist_checkpoint(specialist, checkpoint)

    def get_specialist_checkpoint(self, specialist: str) -> Optional[dict[str, Any]]:
        """Get latest checkpoint for specialist."""
        return self._state.get_specialist_checkpoint(specialist)

    def _resolve(self, outputs: dict[str, Any]) -> dict[str, Any]:
        """Merge agent outputs into a single RCAN action."""
        intent_id = self._active_intent_id()

        # 1. E-stop
        if outputs.get("estop_active"):
            return {"type": "stop", "reason": "orchestrator_estop", "intent_id": intent_id}

        # 2. Guardian veto
        report = outputs.get("guardian_report") or {}
        if report.get("vetoes"):
            first_reason = report["vetoes"][0].get("reason", "unknown")
            return {
                "type": "stop",
                "reason": f"guardian_veto:{first_reason}",
                "intent_id": intent_id,
            }

        # 3. Active manipulation
        manip = outputs.get("manipulation_result") or {}
        if manip.get("status") == "running":
            self.checkpoint_specialist("Manipulator", {"status": "running", "intent_id": intent_id})
            return {"type": "wait", "reason": "manipulation_in_progress", "intent_id": intent_id}

        # 4. Navigation plan
        nav = outputs.get("nav_plan")
        if isinstance(nav, dict):
            action = nav.get("action") or nav
            if isinstance(action, dict) and action.get("type"):
                self.checkpoint_specialist("Navigator", {"nav_plan": nav, "intent_id": intent_id})
                return {**action, "intent_id": intent_id}

        # 5. Default
        return {"type": "idle", "intent_id": intent_id}

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def observe(self, sensor_data: dict[str, Any]) -> dict[str, Any]:
        """Collect all swarm agent outputs from SharedState."""
        outputs = self._collect()
        # Direct sensor_data can override/supplement SharedState values
        for key, val in sensor_data.items():
            if val is not None:
                outputs[key] = val
        return outputs

    async def act(self, context: dict[str, Any]) -> dict[str, Any]:
        """Resolve and return the coordinated RCAN action."""
        self._tick += 1
        action = self._resolve(context)
        self._last_action = action

        self._log.append({"tick": self._tick, "ts": time.time(), "action": action})
        if len(self._log) > 100:
            self._log = self._log[-100:]

        self._state.set("swarm.orchestrated_action", action)
        logger.debug("Orchestrator tick %d: %s", self._tick, action.get("type"))
        return action

    # ------------------------------------------------------------------
    # Synchronous entry-point for TieredBrain
    # ------------------------------------------------------------------

    async def _async_think(self, sensor_data: dict[str, Any]) -> dict[str, Any]:
        ctx = await self.observe(sensor_data)
        return await self.act(ctx)

    def sync_think(self, sensor_data: dict[str, Any]) -> dict[str, Any]:
        """Synchronous wrapper used by TieredBrain.think().

        Runs the async observe→act pipeline in a new event loop when called
        from a synchronous context (the normal case during the robot tick).
        If an event loop is already running (async tests / FastAPI), falls
        back to a thread-pool-based run to avoid nesting.
        """
        try:
            asyncio.get_running_loop()
            # Already inside an async context — run in a thread to avoid nesting
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self._async_think(sensor_data)).result(timeout=5.0)
        except RuntimeError:
            # No running loop — safe to call asyncio.run()
            return asyncio.run(self._async_think(sensor_data))

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Snapshot of current orchestrator state for health/telemetry."""
        return {
            "tick": self._tick,
            "last_action": self._last_action,
            "estop": self._state.get("swarm.estop_active", False),
            "log_entries": len(self._log),
            "current_intent": self._state.current_intent(),
            "queued_intents": len(self._state.list_intents()),
        }
