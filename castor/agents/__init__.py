"""OpenCastor agent swarm framework — Layer 3.

Provides the BaseAgent contract, SharedState pub/sub bus, and the full
Layer 3 agent roster plus an AgentRegistry for lifecycle management.

Layer 3 agents::

    ObserverAgent      — scene understanding, detection parsing
    NavigatorAgent     — path planning (potential fields)
    ManipulatorAgent   — arm/gripper task execution (wraps ManipulatorSpecialist)
    CommunicatorAgent  — NL intent routing from messaging channels
    GuardianAgent      — safety meta-agent, veto + e-stop
    OrchestratorAgent  — master agent, resolves all outputs → single RCAN action

Quick-start::

    from castor.agents import (
        AgentRegistry,
        ObserverAgent,
        NavigatorAgent,
        OrchestratorAgent,
        SharedState,
    )

    state = SharedState()
    registry = AgentRegistry()
    registry.register(ObserverAgent)
    registry.register(NavigatorAgent)
    registry.register(OrchestratorAgent)

    orchestrator = registry.spawn("orchestrator", shared_state=state)
    action = orchestrator.sync_think({"incoming_message": "go forward"})
"""

from .base import AgentStatus, BaseAgent
from .communicator import CommunicatorAgent
from .guardian import GuardianAgent, SafetyVeto
from .manipulator_agent import ManipulatorAgent
from .navigator import NavigationPlan, NavigatorAgent, Waypoint
from .observer import Detection, ObserverAgent, SceneGraph
from .orchestrator import OrchestratorAgent
from .registry import AgentRegistry
from .shared_state import SharedState

__all__ = [
    "AgentStatus",
    "AgentRegistry",
    "BaseAgent",
    "CommunicatorAgent",
    "Detection",
    "GuardianAgent",
    "ManipulatorAgent",
    "NavigationPlan",
    "NavigatorAgent",
    "ObserverAgent",
    "OrchestratorAgent",
    "SafetyVeto",
    "SceneGraph",
    "SharedState",
    "Waypoint",
]
