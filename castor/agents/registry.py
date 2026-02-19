"""AgentRegistry — spawn, list, and health-check named agents.

Provides a centralised factory for creating and managing
:class:`~castor.agents.base.BaseAgent` instances by name.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Type

from .base import BaseAgent

logger = logging.getLogger("OpenCastor.AgentRegistry")


class AgentRegistry:
    """Manages a pool of named :class:`~castor.agents.base.BaseAgent` instances.

    Agents are first *registered* (class stored by name), then *spawned*
    (instantiated with optional config) and stored for later retrieval.

    Example::

        registry = AgentRegistry()
        registry.register(ObserverAgent)
        registry.register(NavigatorAgent)

        observer = registry.spawn("observer", config={"obstacle_labels": ["bottle"]})
        navigator = registry.spawn("navigator", config={"max_speed": 0.4})

        # Start both
        await observer.start()
        await navigator.start()

        # Health overview
        print(registry.health_report())

        # Graceful shutdown
        await registry.stop_all()
    """

    def __init__(self) -> None:
        self._classes: Dict[str, Type[BaseAgent]] = {}
        self._agents: Dict[str, BaseAgent] = {}
        self._spawn_times: Dict[str, float] = {}

    def register(self, agent_class: Type[BaseAgent]) -> None:
        """Register an agent class so it can later be spawned by name.

        The agent's ``name`` class attribute is used as the registry key.
        Re-registering an existing name overwrites the previous class.

        Args:
            agent_class: A concrete subclass of :class:`BaseAgent`.
        """
        self._classes[agent_class.name] = agent_class
        logger.debug(f"Registered agent class '{agent_class.name}'")

    def spawn(self, name: str, config: Optional[Dict[str, Any]] = None) -> BaseAgent:
        """Instantiate a registered agent by name and store it.

        Args:
            name: Registry key (matches the agent class ``name`` attribute).
            config: Optional configuration dict forwarded to the agent constructor.

        Returns:
            The newly created :class:`BaseAgent` instance.

        Raises:
            KeyError: If *name* has not been registered.
        """
        if name not in self._classes:
            raise KeyError(
                f"No agent class registered for name '{name}'. "
                f"Available: {list(self._classes)}"
            )
        agent = self._classes[name](config=config or {})
        self._agents[name] = agent
        self._spawn_times[name] = time.monotonic()
        logger.info(f"Spawned agent '{name}'")
        return agent

    def get(self, name: str) -> Optional[BaseAgent]:
        """Return a spawned agent by name, or ``None`` if not found.

        Args:
            name: Agent name to look up.

        Returns:
            :class:`BaseAgent` instance, or ``None``.
        """
        return self._agents.get(name)

    def list_agents(self) -> List[Dict[str, Any]]:
        """Return a summary list for all spawned agents.

        Returns:
            List of dicts, each with keys ``name``, ``status``, ``uptime_s``.
        """
        result = []
        for name, agent in self._agents.items():
            spawn_t = self._spawn_times.get(name, 0.0)
            result.append(
                {
                    "name": name,
                    "status": agent.status.value,
                    "uptime_s": round(time.monotonic() - spawn_t, 2),
                }
            )
        return result

    async def stop_all(self) -> None:
        """Gracefully stop all spawned agents concurrently."""
        tasks = [agent.stop() for agent in self._agents.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All agents stopped")

    def health_report(self) -> Dict[str, Any]:
        """Return a health dict for every spawned agent.

        Returns:
            Dict mapping agent name → :meth:`~BaseAgent.health` dict.
        """
        return {name: agent.health() for name, agent in self._agents.items()}
