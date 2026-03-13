"""
OpenCastor Human-in-the-Loop Gate — F3.

HiTL gates intercept commands after confidence evaluation, before actuator
dispatch. They hold commands in a pending queue and wait for out-of-band
authorization from a qualifying principal.

Config example (RCAN YAML):
    agent:
      hitl_gates:
        - action_types: [grip]
          require_auth: true
          auth_timeout_ms: 30000
          on_timeout: block
          notify: [whatsapp]
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

logger = logging.getLogger("OpenCastor.HiTLGate")


@dataclass
class HiTLGate:
    action_types: list[str]
    require_auth: bool = True
    auth_timeout_ms: int = 30000
    on_timeout: Literal["block", "allow"] = "block"
    notify: list[str] = field(default_factory=list)


class HiTLGateManager:
    """Manages HiTL gate lifecycle: pending queue, auth futures, notifications."""

    def __init__(self, gates: list[HiTLGate], audit: Any = None):
        self._gates = gates
        self._audit = audit
        # pending_id -> asyncio.Future[str] ("approve"|"deny")
        self._pending: dict[str, asyncio.Future] = {}

    def _match_gate(self, action_type: str) -> Optional[HiTLGate]:
        for gate in self._gates:
            if action_type in gate.action_types:
                return gate
        return None

    def _create_pending(self, action: dict, thought: Any) -> str:
        pending_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        self._pending[pending_id] = loop.create_future()
        logger.info(
            "HiTL pending: %s action_type=%s thought_id=%s",
            pending_id,
            action.get("type", "?"),
            getattr(thought, "id", "?"),
        )
        return pending_id

    async def _wait_for_auth(self, pending_id: str) -> str:
        future = self._pending.get(pending_id)
        if future is None:
            return "deny"
        return await future

    async def _notify(
        self, channels: list[str], pending_id: str, action: dict, thought: Any
    ) -> None:
        """Emit notification to configured channels (best-effort)."""
        action_type = action.get("type", "unknown")
        timeout_s = 30  # default display; caller has actual timeout
        msg = (
            f"⚠️ Authorization required: {action_type} — "
            f"reply 'approve {pending_id}' or 'deny {pending_id}' within {timeout_s}s"
        )
        logger.info("HiTL notify channels=%s: %s", channels, msg)
        # Actual channel dispatch is application-layer; this logs the intent.

    def _audit_gate_event(self, pending_id: str, action: dict, thought: Any) -> None:
        if self._audit is not None:
            try:
                self._audit.log(
                    "hitl_gate",
                    source="hitl_gate",
                    pending_id=pending_id,
                    action_type=action.get("type", "?"),
                    thought_id=getattr(thought, "id", None),
                )
            except Exception as exc:
                logger.debug("HiTL audit failed (non-fatal): %s", exc)

    async def check(self, action: dict, thought: Any) -> bool:
        """Return True if the command may proceed, False if blocked.

        Args:
            action: The action dict (must contain ``"type"`` key).
            thought: The Thought that produced this action.

        Returns:
            ``True`` to proceed, ``False`` to block.
        """
        gate = self._match_gate(action.get("type", ""))
        if gate is None or not gate.require_auth:
            return True

        pending_id = self._create_pending(action, thought)
        await self._notify(gate.notify, pending_id, action, thought)
        try:
            decision = await asyncio.wait_for(
                self._wait_for_auth(pending_id),
                timeout=gate.auth_timeout_ms / 1000,
            )
            return decision == "approve"
        except asyncio.TimeoutError:
            logger.warning("HiTL timeout for pending_id=%s", pending_id)
            return gate.on_timeout == "allow"
        finally:
            self._audit_gate_event(pending_id, action, thought)
            self._pending.pop(pending_id, None)

    def authorize(self, pending_id: str, decision: Literal["approve", "deny"]) -> bool:
        """Resolve a pending authorization request.

        Args:
            pending_id: UUID returned when the gate was triggered.
            decision:   ``"approve"`` or ``"deny"``.

        Returns:
            ``True`` if the pending request was found and resolved.
        """
        future = self._pending.get(pending_id)
        if future is None or future.done():
            return False
        future.set_result(decision)
        logger.info("HiTL authorized: %s -> %s", pending_id, decision)
        return True
