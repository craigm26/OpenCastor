"""GuardianAgent — Layer 3 safety meta-agent.

Monitors all swarm agent outputs in SharedState, validates actions against
safety rules, issues vetoes, and maintains emergency-stop state. The
OrchestratorAgent consults the guardian report before dispatching actions.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .base import BaseAgent
from .shared_state import SharedState

logger = logging.getLogger("OpenCastor.Agents.Guardian")

# Action types that are always vetoed regardless of context
_FORBIDDEN_ACTION_TYPES: set[str] = {"self_destruct", "unsafe_move", "override_estop"}

# Default max allowed speed fraction (0.0–1.0)
_DEFAULT_MAX_SPEED = 0.9

# ---------------------------------------------------------------------------
# RCAN scope → allowed action type allowlist (RCAN v1.6 §4.2)
#
# Scopes with ``None`` are unrestricted (all action types allowed).
# Scopes with a set are fail-closed: unknown/unlisted action types are vetoed.
# ---------------------------------------------------------------------------
SCOPE_ACTION_ALLOWLIST: dict[str, Optional[set[str]]] = {
    "discover": {"ping", "status", "get_info"},
    "status": {"ping", "status", "get_info", "get_telemetry", "get_pose"},
    "chat": {
        "ping",
        "status",
        "get_info",
        "get_telemetry",
        "get_pose",
        "speak",
        "navigate_to",
        "describe_scene",
    },
    "control": None,  # None = all actions allowed (unrestricted)
    "system": None,
    "safety": None,
}

# Scope levels mirror castor.swarm.consensus.SCOPE_LEVELS (avoid circular import)
_SCOPE_LEVELS: dict[str, int] = {
    "discover": 0,
    "transparency": 0,
    "status": 1,
    "chat": 2,
    "control": 3,
    "system": 3,
    "safety": 99,
}

# SharedState keys to monitor by default
_DEFAULT_MONITORED_KEYS = [
    "swarm.nav_action",
    "swarm.manipulation_result",
    "swarm.routed_task.navigator",
    "swarm.routed_task.manipulator",
]


@dataclass
class SafetyVeto:
    """Record of a safety veto issued by GuardianAgent."""

    vetoed_key: str
    action: dict[str, Any]
    reason: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "vetoed_key": self.vetoed_key,
            "action": self.action,
            "reason": self.reason,
        }


class GuardianAgent(BaseAgent):
    """Safety meta-agent: validates all swarm actions before dispatch.

    Rules applied (in order):
    1. **Forbidden types** — ``self_destruct``, ``unsafe_move``, ``override_estop``
       are always vetoed.
    2. **E-stop active** — when e-stop is active, any action other than
       ``stop``, ``idle``, or ``wait`` is vetoed.
    3. **Speed limit** — ``action.speed > max_speed`` is vetoed.

    Config keys (under ``agents.guardian``):
        ``max_speed`` (float, default 0.9) — maximum allowed speed fraction.
        ``monitored_keys`` (list[str]) — SharedState keys to validate.

    SharedState keys published:
        ``swarm.guardian_report`` — dict with estop_active, vetoes, approved,
            and cumulative veto_count.
        ``swarm.estop_active`` (bool) — set by trigger_estop() / clear_estop().
        ``swarm.estop_reason`` (str) — reason for the last e-stop trigger.
    """

    name = "guardian"

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        shared_state: Optional[SharedState] = None,
    ):
        super().__init__(config)
        cfg = config or {}
        self._state = shared_state or SharedState()
        self.max_speed: float = cfg.get("max_speed", _DEFAULT_MAX_SPEED)
        self.monitored_keys: list[str] = cfg.get("monitored_keys", list(_DEFAULT_MONITORED_KEYS))
        self.estop_active: bool = False
        self.vetoes: list[SafetyVeto] = []

    # ------------------------------------------------------------------
    # E-stop control
    # ------------------------------------------------------------------

    def trigger_estop(self, reason: str = "guardian_veto") -> None:
        """Activate emergency stop — blocks all movement actions."""
        self.estop_active = True
        self._state.set("swarm.estop_active", True)
        self._state.set("swarm.estop_reason", reason)
        logger.critical("Guardian ESTOP triggered: %s", reason)

    def clear_estop(self) -> None:
        """Clear emergency stop (requires manual operator override)."""
        self.estop_active = False
        self._state.set("swarm.estop_active", False)
        self._state.set("swarm.estop_reason", None)
        logger.info("Guardian ESTOP cleared")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, key: str, action: dict[str, Any]) -> Optional[SafetyVeto]:
        """Validate a single action dict. Returns SafetyVeto on violation.

        Validation rules (applied in order):
        1. Forbidden action types — always vetoed.
        2. E-stop active — any non-safe action is vetoed.
        3. Speed limit — speed > max_speed is vetoed.
        4. Scope enforcement — action type must be in the RCAN scope allowlist.
           Unknown action types at restricted scopes are fail-closed (vetoed).
           Scopes with ``None`` in the allowlist are unrestricted (pass-through).
        """
        action_type = action.get("type") or action.get("action", "")

        # Rule 1: Forbidden types
        if action_type in _FORBIDDEN_ACTION_TYPES:
            return SafetyVeto(key, action, f"forbidden:{action_type}")

        # Rule 2: E-stop
        if self.estop_active and action_type not in ("stop", "idle", "wait", ""):
            return SafetyVeto(key, action, "estop_active")

        # Rule 3: Speed limit
        speed = action.get("speed", 0.0)
        if isinstance(speed, (int, float)) and speed > self.max_speed:
            return SafetyVeto(key, action, f"speed_limit:{speed:.2f}>{self.max_speed:.2f}")

        # Rule 4: RCAN scope enforcement
        # Only enforced when the action explicitly carries a ``scope`` key.
        # Actions without a scope field (e.g. legacy actions from pre-scope code) pass
        # through unchanged — this preserves backward-compatibility with existing tests.
        if "scope" in action:
            scope = action["scope"]
            allowed = SCOPE_ACTION_ALLOWLIST.get(scope)
            if allowed is not None:
                # Restricted scope — check allowlist (fail-closed for unknown types)
                if action_type and action_type not in ("stop", "idle", "wait", ""):
                    if action_type not in allowed:
                        return SafetyVeto(
                            key,
                            action,
                            f"scope_violation:action '{action_type}' not allowed under scope '{scope}'",
                        )
            # allowed is None → unrestricted scope (control/system/safety) — no scope veto

        return None

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def observe(self, sensor_data: dict[str, Any]) -> dict[str, Any]:
        """Collect pending actions from all monitored SharedState keys."""
        pending: dict[str, Any] = {}
        for key in self.monitored_keys:
            val = self._state.get(key)
            if val is not None:
                pending[key] = val
        # Allow direct injection via sensor_data
        if "proposed_action" in sensor_data:
            pending["direct"] = sensor_data["proposed_action"]
        return {"pending_actions": pending}

    async def act(self, context: dict[str, Any]) -> dict[str, Any]:
        """Validate all pending actions; publish guardian report."""
        pending = context.get("pending_actions", {})
        new_vetoes: list[dict[str, Any]] = []
        approved: list[str] = []

        for key, action in pending.items():
            if not isinstance(action, dict):
                continue
            veto = self._validate(key, action)
            if veto:
                self.vetoes.append(veto)
                new_vetoes.append(veto.to_dict())
                logger.warning("Guardian VETO [%s]: %s", key, veto.reason)
            else:
                approved.append(key)

        report: dict[str, Any] = {
            "estop_active": self.estop_active,
            "vetoes": new_vetoes,
            "approved": approved,
            "veto_count": len(self.vetoes),
        }
        self._state.set("swarm.guardian_report", report)

        if new_vetoes:
            return {"action": "veto", "report": report}
        return {"action": "approve", "report": report}
