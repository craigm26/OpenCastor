"""
RCAN Message Router.

Routes :class:`~castor.rcan.message.RCANMessage` objects to the
appropriate handler based on the target RURI's capability path.

The router:
    1. Validates the target RURI matches this robot.
    2. Checks TTL expiration.
    3. Checks authorization (scope matching).
    4. Extracts the capability from the RURI path.
    5. Dispatches to the registered handler.
    6. Returns an ACK or ERROR message.

SAFETY priority messages skip the queue (Safety Invariant 6).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional

from castor.rcan.capabilities import CapabilityRegistry
from castor.rcan.invoke import InvokeCancelRequest, SkillRegistry
from castor.rcan.message import MessageType, RCANMessage
from castor.rcan.rbac import RCANPrincipal, Scope
from castor.rcan.ruri import RURI

logger = logging.getLogger("OpenCastor.RCAN.Router")

# Map capabilities to required scopes
_CAP_SCOPE_MAP: dict[str, Scope] = {
    "status": Scope.STATUS,
    "nav": Scope.CONTROL,
    "teleop": Scope.CONTROL,
    "vision": Scope.STATUS,
    "chat": Scope.CONTROL,
    "arm": Scope.CONTROL,
}

# Handler signature: (message, principal) -> payload dict
HandlerFn = Callable[[RCANMessage, Optional[RCANPrincipal]], dict[str, Any]]


class MessageRouter:
    """Route RCAN messages to capability handlers.

    Args:
        ruri:          This robot's RURI.
        capabilities:  Capability registry.
    """

    def __init__(
        self,
        ruri: RURI,
        capabilities: CapabilityRegistry,
        skill_registry: Optional[SkillRegistry] = None,
    ):
        self.ruri = ruri
        self.capabilities = capabilities
        self._skill_registry = skill_registry
        self._handlers: dict[str, HandlerFn] = {}
        self._messages_routed = 0

    @property
    def messages_routed(self) -> int:
        return self._messages_routed

    def register_handler(self, capability: str, handler: HandlerFn):
        """Register a handler for a capability."""
        self._handlers[capability] = handler

    def route_invoke_cancel(self, message: RCANMessage) -> RCANMessage:
        """Handle an INVOKE_CANCEL message (§19 v1.3).

        Dispatches to :meth:`SkillRegistry.cancel` if a skill registry was
        provided at construction time.  Returns an ACK on success or ERROR if
        no registry is configured or the message type is wrong.

        Args:
            message: Incoming RCANMessage with type INVOKE_CANCEL.

        Returns:
            ACK or ERROR RCANMessage.
        """
        source_ruri = str(self.ruri)

        if message.type != MessageType.INVOKE_CANCEL:
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="WRONG_MESSAGE_TYPE",
                detail=f"Expected INVOKE_CANCEL, got {message.type}",
                reply_to=message.id,
            )

        if self._skill_registry is None:
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="NO_SKILL_REGISTRY",
                detail="No SkillRegistry configured on this router",
                reply_to=message.id,
            )

        payload = message.payload or {}
        msg_id = payload.get("msg_id", "")
        if not msg_id:
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="MISSING_MSG_ID",
                detail="INVOKE_CANCEL payload must include 'msg_id'",
                reply_to=message.id,
            )

        cancel_req = InvokeCancelRequest(
            msg_id=msg_id,
            reason=payload.get("reason"),
        )
        found = self._skill_registry.cancel(cancel_req.msg_id)
        logger.debug("INVOKE_CANCEL msg_id=%s found=%s", msg_id, found)
        self._messages_routed += 1
        return RCANMessage.ack(
            source=source_ruri,
            target=message.source,
            reply_to=message.id,
            payload={"cancelled": found, "msg_id": msg_id},
        )

    def route(
        self,
        message: RCANMessage,
        principal: Optional[RCANPrincipal] = None,
    ) -> RCANMessage:
        """Route a message and return an ACK or ERROR response.

        Args:
            message:    Incoming RCAN message.
            principal:  Authenticated principal (for scope checking).

        Returns:
            An ACK or ERROR :class:`RCANMessage`.
        """
        source_ruri = str(self.ruri)

        # 1. Validate target RURI matches this robot
        try:
            target = RURI.parse(message.target)
        except ValueError:
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="INVALID_TARGET",
                detail=f"Invalid target RURI: {message.target}",
                reply_to=message.id,
            )

        # Compare base RURIs (without capability -- capability is used for routing)
        target_base = RURI(target.manufacturer, target.model, target.instance)
        if not self.ruri.matches(target_base):
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="NOT_FOR_ME",
                detail=f"Target {message.target} does not match {self.ruri}",
                reply_to=message.id,
            )

        # 2. Check TTL
        if message.is_expired():
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="EXPIRED",
                detail="Message TTL exceeded",
                reply_to=message.id,
            )

        # 2b. Dispatch INVOKE / INVOKE_CANCEL by message type before capability routing
        if message.type == MessageType.INVOKE_CANCEL:
            return self.route_invoke_cancel(message)

        if message.type == MessageType.INVOKE:
            if self._skill_registry is None:
                return RCANMessage.error(
                    source=source_ruri,
                    target=message.source,
                    code="NO_SKILL_REGISTRY",
                    detail="No SkillRegistry configured on this router",
                    reply_to=message.id,
                )
            from castor.rcan.invoke import InvokeRequest  # local import avoids cycles

            payload = message.payload or {}
            req = InvokeRequest(
                skill=payload.get("skill", ""),
                params=payload.get("params", {}),
                invoke_id=payload.get("msg_id", message.id),
                timeout_ms=payload.get("timeout_ms"),
            )
            result = self._skill_registry.invoke(req)
            self._messages_routed += 1
            return RCANMessage.ack(
                source=source_ruri,
                target=message.source,
                reply_to=message.id,
                payload={
                    "status": result.status,
                    "result": result.result,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                },
            )

        # 3. Check authorization
        capability = target.capability or "status"
        required_scope = _CAP_SCOPE_MAP.get(capability, Scope.STATUS)

        if principal is None or not principal.has_scope(required_scope):
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="UNAUTHORIZED",
                detail=f"Missing scope for capability '{capability}': "
                f"need {required_scope.to_strings()}, have {principal.scopes.to_strings()}",
                reply_to=message.id,
            )

        # 4. Check capability exists
        if not self.capabilities.has(capability):
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="CAPABILITY_NOT_FOUND",
                detail=f"Capability '{capability}' is not available. "
                f"Available: {self.capabilities.names}",
                reply_to=message.id,
            )

        # 5. Dispatch to handler
        handler = self._handlers.get(capability)
        if handler is None:
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="NO_HANDLER",
                detail=f"No handler registered for capability '{capability}'",
                reply_to=message.id,
            )

        try:
            result = handler(message, principal)
            self._messages_routed += 1
            return RCANMessage.ack(
                source=source_ruri,
                target=message.source,
                reply_to=message.id,
                payload=result,
            )
        except Exception as e:
            logger.error("Handler error for %s: %s", capability, e)
            return RCANMessage.error(
                source=source_ruri,
                target=message.source,
                code="HANDLER_ERROR",
                detail=str(e),
                reply_to=message.id,
            )
