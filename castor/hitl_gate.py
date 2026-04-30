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
    """Manages HiTL gate lifecycle: pending queue, auth futures, notifications.

    Supports two flows:

    * **Long-poll** (legacy) — :meth:`check` blocks until the gate resolves
      via :meth:`authorize` or times out. Single async call from the caller.

    * **Two-step** — :meth:`start_pending` returns a ``pending_id`` immediately
      without blocking; the caller surfaces it (HTTP 202 body, channel
      notification, etc.). Once :meth:`authorize` runs, :meth:`consume_decision`
      atomically yields the resolved decision. Matches RCAN §8 PENDING_AUTH /
      AUTHORIZE semantics.
    """

    def __init__(self, gates: list[HiTLGate], audit: Any = None):
        self._gates = gates
        self._audit = audit
        # Long-poll: pending_id -> asyncio.Future[str] ("approve"|"deny")
        self._pending: dict[str, asyncio.Future] = {}
        # Two-step: pending_ids issued via start_pending(), still unresolved
        self._known_pending: set[str] = set()
        # Two-step: resolved decisions waiting for consume_decision()
        self._resolved: dict[str, str] = {}

    def _match_gate(self, action_type: str) -> Optional[HiTLGate]:
        for gate in self._gates:
            if action_type in gate.action_types:
                return gate
        return None

    def requires_auth_for(self, action_type: str) -> bool:
        """Return True if any registered gate requires auth for this action type."""
        gate = self._match_gate(action_type)
        return gate is not None and gate.require_auth

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

    def start_pending(self, action: dict, thought: Any = None) -> str:
        """Create a pending request and return its ID without blocking.

        Returns ``""`` (empty string) when no gate covers this action — caller
        should treat that as "no consent required, proceed."
        """
        action_type = action.get("type", "")
        gate = self._match_gate(action_type)
        if gate is None or not gate.require_auth:
            return ""

        pending_id = str(uuid.uuid4())
        self._known_pending.add(pending_id)
        # Loud INFO log so operators tailing gateway logs can see the
        # pending_id even when channel notify isn't wired (#867 Bug B).
        logger.info(
            "HiTL pending (two-step): id=%s action_type=%s notify=%s",
            pending_id,
            action_type,
            gate.notify or "[none]",
        )
        # Best-effort channel notify; current _notify only logs but the
        # hook is here for when channel dispatch lands.
        try:
            asyncio.get_event_loop().create_task(
                self._notify(gate.notify, pending_id, action, thought)
            )
        except RuntimeError:
            # No running loop (e.g. called from sync test setup) — skip notify
            pass
        return pending_id

    def consume_decision(self, pending_id: str) -> Optional[str]:
        """Atomically pop a resolved two-step decision.

        Returns:
            ``"approve"`` / ``"deny"`` when the operator has authorized;
            ``None`` when still pending OR when ``pending_id`` is unknown.
            Distinguish via :meth:`is_known_pending`.
        """
        if pending_id not in self._known_pending:
            return None
        decision = self._resolved.pop(pending_id, None)
        if decision in ("approve", "deny"):
            self._known_pending.discard(pending_id)
        return decision

    def is_known_pending(self, pending_id: str) -> bool:
        """Whether this pending_id was issued by :meth:`start_pending`."""
        return pending_id in self._known_pending

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

        Resolves whichever flow is active for ``pending_id``:

        * Long-poll: sets the future awaited by :meth:`check`.
        * Two-step:  stashes the decision for :meth:`consume_decision`.

        Args:
            pending_id: UUID returned by :meth:`check` (long-poll) or
                :meth:`start_pending` (two-step).
            decision:   ``"approve"`` or ``"deny"``.

        Returns:
            ``True`` if the ``pending_id`` was known to either flow.
        """
        # Long-poll path
        future = self._pending.get(pending_id)
        if future is not None and not future.done():
            future.set_result(decision)
            logger.info("HiTL authorized (long-poll): %s -> %s", pending_id, decision)
            return True

        # Two-step path
        if pending_id in self._known_pending:
            self._resolved[pending_id] = decision
            logger.info("HiTL authorized (two-step): %s -> %s", pending_id, decision)
            return True

        return False
