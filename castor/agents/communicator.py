"""CommunicatorAgent — Layer 3 agent: NL intent parsing and channel-to-swarm routing.

Parses natural language instructions arriving from messaging channels
(WhatsApp, Telegram, Discord, Slack), extracts intent, and routes each
message to the correct swarm agent via SharedState.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from .shared_state import SharedState

logger = logging.getLogger("OpenCastor.Agents.Communicator")

# Keyword → target agent routing table (first match wins, ordered by specificity)
_INTENT_ROUTING: List[tuple] = [
    # Manipulation
    ("grasp", "manipulator"),
    ("grab", "manipulator"),
    ("pick up", "manipulator"),
    ("pick", "manipulator"),
    ("place", "manipulator"),
    ("put down", "manipulator"),
    ("put", "manipulator"),
    ("push", "manipulator"),
    ("arm", "manipulator"),
    # Safety
    ("emergency", "guardian"),
    ("estop", "guardian"),
    ("e-stop", "guardian"),
    ("halt", "guardian"),
    ("stop", "guardian"),
    ("abort", "guardian"),
    # Navigation
    ("navigate", "navigator"),
    ("patrol", "navigator"),
    ("go to", "navigator"),
    ("move to", "navigator"),
    ("go", "navigator"),
    ("move", "navigator"),
    ("turn", "navigator"),
    ("forward", "navigator"),
    ("backward", "navigator"),
    ("left", "navigator"),
    ("right", "navigator"),
    # Observation
    ("detect", "observer"),
    ("scan", "observer"),
    ("watch", "observer"),
    ("observe", "observer"),
    ("look", "observer"),
    ("what do you see", "observer"),
    # Self-referential
    ("status", "communicator"),
    ("report", "communicator"),
    ("help", "communicator"),
    ("what can you", "communicator"),
]


class CommunicatorAgent(BaseAgent):
    """Human-robot interface agent for the Layer 3 swarm.

    Accepts raw text messages from any channel, determines intent, publishes
    a routed task to SharedState so the target agent can act on it.

    SharedState keys consumed:
        ``swarm.incoming_message`` — raw text string from a channel.

    SharedState keys published:
        ``swarm.routed_task.<agent>`` — dict with instruction, intent, from fields.
        ``swarm.incoming_message`` — cleared (set to None) after routing.
        ``swarm.communicator_response`` — formatted response string for channel reply.
    """

    name = "communicator"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        shared_state: Optional[SharedState] = None,
    ):
        super().__init__(config)
        self._state = shared_state or SharedState()
        self._conversation_history: List[str] = []
        self._last_intent: Optional[str] = None
        self._last_routed_to: Optional[str] = None

    # ------------------------------------------------------------------
    # Public helpers (also useful in tests and channels)
    # ------------------------------------------------------------------

    def receive_message(self, text: str) -> None:
        """Inject an incoming message directly (bypasses SharedState read)."""
        self._state.set("swarm.incoming_message", text)

    def parse_intent(self, text: str) -> str:
        """Extract the primary intent keyword from a natural-language message.

        Returns the matching keyword or ``"unknown"`` if none match.
        """
        lower = text.lower().strip()
        for keyword, _ in _INTENT_ROUTING:
            if keyword in lower:
                return keyword
        return "unknown"

    def route_intent(self, intent: str, text: str) -> Optional[str]:
        """Publish a routed task to SharedState for the appropriate agent.

        Returns the target agent name, or ``None`` if intent is unknown.
        """
        target = next(
            (agent for kw, agent in _INTENT_ROUTING if kw == intent), None
        )
        if target and target != "communicator":
            self._state.set(
                f"swarm.routed_task.{target}",
                {"instruction": text, "intent": intent, "from": self.name},
            )
            logger.info("Routed '%s' intent → %s", intent, target)
        return target

    def format_response(self, raw: str, intent: Optional[str] = None) -> str:
        """Format an agent result string for human-readable channel output."""
        raw = (raw or "").strip()
        if intent in ("stop", "emergency", "estop", "e-stop", "halt", "abort"):
            return f"Emergency stop acknowledged. {raw}".strip()
        if intent == "status":
            return f"Status: {raw}" if raw else "All systems normal."
        if intent == "help":
            return (
                "I can navigate, grab objects, scan the scene, and report status. "
                "Try: 'go forward', 'grab the cup', 'scan', or 'stop'."
            )
        return raw or "Done."

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def observe(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        """Pull latest incoming message from SharedState or sensor_data."""
        msg = sensor_data.get("incoming_message") or self._state.get(
            "swarm.incoming_message"
        )
        return {"message": msg}

    async def act(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Parse intent, route to target agent, and return routing result."""
        msg = context.get("message")
        if not msg:
            return {"action": "idle", "agent": self.name}

        self._conversation_history.append(msg)
        intent = self.parse_intent(msg)
        self._last_intent = intent
        target = self.route_intent(intent, msg)
        self._last_routed_to = target

        # Clear the processed message
        self._state.set("swarm.incoming_message", None)

        response = self.format_response("", intent)
        self._state.set("swarm.communicator_response", response)

        logger.debug("Communicator: '%s' → intent=%s target=%s", msg[:60], intent, target)
        return {
            "action": "route",
            "intent": intent,
            "routed_to": target,
            "message": msg,
        }
