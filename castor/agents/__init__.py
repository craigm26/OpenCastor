"""OpenCastor agent swarm framework — Phase 2.

Provides the BaseAgent contract, SharedState pub/sub bus, and two
built-in agents (ObserverAgent, NavigatorAgent) plus an AgentRegistry
for lifecycle management.

Quick-start::

    from castor.agents import (
        AgentRegistry,
        ObserverAgent,
        NavigatorAgent,
        SharedState,
    )

    state = SharedState()
    registry = AgentRegistry()
    registry.register(ObserverAgent)
    registry.register(NavigatorAgent)

    observer = registry.spawn("observer", config={})
    navigator = registry.spawn("navigator", config={"max_speed": 0.5})
"""

from .base import AgentStatus, BaseAgent
from .navigator import NavigationPlan, NavigatorAgent, Waypoint
from .observer import Detection, ObserverAgent, SceneGraph
from .registry import AgentRegistry
from .shared_state import SharedState

__all__ = [
    "AgentStatus",
    "AgentRegistry",
    "BaseAgent",
    "Detection",
    "NavigationPlan",
    "NavigatorAgent",
    "ObserverAgent",
    "SceneGraph",
    "SharedState",
    "Waypoint",
]
