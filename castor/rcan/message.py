"""
RCAN Message Envelope.

Defines the standard JSON message format for RCAN protocol communication.
Messages are plain JSON (no protobuf) -- readable with ``curl``, zero deps.

Message types follow the RCAN spec::

    DISCOVER      -- mDNS / peer discovery
    STATUS        -- Telemetry / state reporting
    COMMAND       -- Motor, config, or action command
    STREAM        -- Continuous sensor data
    EVENT         -- Asynchronous notifications
    HANDOFF       -- Transfer control between principals
    ACK           -- Acknowledgement of a prior message
    ERROR         -- Error response
    AUTHORIZE     -- Out-of-band authorization for HiTL gate (v1.2)
    PENDING_AUTH  -- Notification that HiTL gate is awaiting authorization (v1.2)
    INVOKE        -- Trigger a named skill/behavior on the robot runtime (v1.3 §19)
    INVOKE_RESULT -- Result of an INVOKE invocation (v1.3 §19)

Each message carries a priority (LOW, NORMAL, HIGH, SAFETY) that determines
queue ordering.  SAFETY priority messages skip the queue entirely
(Safety Invariant 6).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any, Optional


class MessageType(IntEnum):
    """RCAN message types."""

    DISCOVER = 1
    STATUS = 2
    COMMAND = 3
    STREAM = 4
    EVENT = 5
    HANDOFF = 6
    ACK = 7
    ERROR = 8
    AUTHORIZE = 9  # Out-of-band authorization for HiTL gate (RCAN v1.2)
    PENDING_AUTH = 10  # Notification that HiTL gate is awaiting authorization (RCAN v1.2)
    INVOKE = 11  # Trigger a named skill/behavior on the robot runtime (RCAN v1.3 §19)
    INVOKE_RESULT = 12  # Result of an INVOKE invocation (RCAN v1.3 §19)


class Priority(IntEnum):
    """Message priority levels.  SAFETY skips the normal queue."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    SAFETY = 3


@dataclass
class RCANMessage:
    """Standard RCAN protocol message envelope.

    Attributes:
        id:          Unique message identifier (UUID).
        type:        Message type enum value.
        priority:    Priority level.
        source:      Source RURI string.
        target:      Target RURI string (may contain wildcards).
        payload:     Arbitrary JSON-serialisable data.
        timestamp:   Unix timestamp (seconds since epoch).
        ttl:         Time-to-live in seconds (0 = no expiry).
        reply_to:    ID of the message this is a reply to.
        scope:       Required RBAC scopes for this message.
        version:     RCAN protocol version.
    """

    type: int
    source: str
    target: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    priority: int = field(default=Priority.NORMAL)
    ttl: int = field(default=0)
    reply_to: Optional[str] = field(default=None)
    scope: list[str] = field(default_factory=list)
    version: str = field(default="1.0.0")

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------
    @classmethod
    def command(
        cls,
        source: str,
        target: str,
        payload: dict[str, Any],
        priority: int = Priority.NORMAL,
        scope: Optional[list[str]] = None,
    ) -> RCANMessage:
        """Create a COMMAND message."""
        return cls(
            type=MessageType.COMMAND,
            source=source,
            target=target,
            payload=payload,
            priority=priority,
            scope=scope or ["control"],
        )

    @classmethod
    def status(
        cls,
        source: str,
        target: str,
        payload: dict[str, Any],
    ) -> RCANMessage:
        """Create a STATUS message."""
        return cls(
            type=MessageType.STATUS,
            source=source,
            target=target,
            payload=payload,
            scope=["status"],
        )

    @classmethod
    def ack(
        cls,
        source: str,
        target: str,
        reply_to: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> RCANMessage:
        """Create an ACK for a prior message."""
        return cls(
            type=MessageType.ACK,
            source=source,
            target=target,
            reply_to=reply_to,
            payload=payload or {},
        )

    @classmethod
    def error(
        cls,
        source: str,
        target: str,
        code: str,
        detail: str,
        reply_to: Optional[str] = None,
    ) -> RCANMessage:
        """Create an ERROR message."""
        return cls(
            type=MessageType.ERROR,
            source=source,
            target=target,
            reply_to=reply_to,
            payload={"code": code, "detail": detail},
        )

    @classmethod
    def authorize(
        cls,
        source: str,
        target: str,
        ref_message_id: str,
        principal: str,
        decision: str,
        **kwargs: Any,
    ) -> RCANMessage:
        """Create an AUTHORIZE message for out-of-band HiTL gate authorization.

        Args:
            source:         RURI of the authorizing principal.
            target:         RURI of the robot or gateway receiving the decision.
            ref_message_id: ID of the PENDING_AUTH message being responded to.
            principal:      Identity of the authorizing principal (e.g. user ID).
            decision:       Must be ``'approve'`` or ``'deny'``.
            **kwargs:       Additional fields forwarded to the message payload.

        Raises:
            ValueError: If *decision* is not ``'approve'`` or ``'deny'``.
        """
        if decision not in ("approve", "deny"):
            raise ValueError(f"AUTHORIZE decision must be 'approve' or 'deny', got {decision!r}")
        payload: dict[str, Any] = {
            "ref_message_id": ref_message_id,
            "principal": principal,
            "decision": decision,
        }
        payload.update(kwargs)
        return cls(
            type=MessageType.AUTHORIZE,
            source=source,
            target=target,
            payload=payload,
            priority=Priority.HIGH,
            scope=["hitl", "control"],
        )

    @classmethod
    def pending_auth(
        cls,
        source: str,
        target: str,
        pending_id: str,
        action_type: str,
        description: str,
        timeout_remaining_ms: int,
        **kwargs: Any,
    ) -> RCANMessage:
        """Create a PENDING_AUTH notification message.

        Sent by the HiTL gate to notify subscribers that an action is
        awaiting out-of-band authorization before it can be dispatched.

        Args:
            source:              RURI of the robot / gateway.
            target:              RURI of the principal(s) who can authorize.
            pending_id:          Unique ID for this pending authorization request.
            action_type:         The action type awaiting authorization.
            description:         Human-readable description of the action.
            timeout_remaining_ms: Milliseconds until the gate times out.
            **kwargs:            Additional fields forwarded to the message payload.
        """
        payload: dict[str, Any] = {
            "pending_id": pending_id,
            "action_type": action_type,
            "description": description,
            "timeout_remaining_ms": timeout_remaining_ms,
        }
        payload.update(kwargs)
        return cls(
            type=MessageType.PENDING_AUTH,
            source=source,
            target=target,
            payload=payload,
            priority=Priority.HIGH,
            scope=["hitl", "status"],
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (JSON-ready)."""
        d = asdict(self)
        # Convert enum ints to their names for readability
        d["type_name"] = MessageType(self.type).name
        d["priority_name"] = Priority(self.priority).name
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RCANMessage:
        """Deserialise from a dict.

        Accepts both integer type/priority values and string names.
        """
        d = dict(data)

        # Remove display-only fields
        d.pop("type_name", None)
        d.pop("priority_name", None)

        # Coerce type from name if needed
        if isinstance(d.get("type"), str):
            d["type"] = MessageType[d["type"].upper()]
        # Coerce priority from name if needed
        if isinstance(d.get("priority"), str):
            d["priority"] = Priority[d["priority"].upper()]

        return cls(**d)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def is_expired(self) -> bool:
        """Check if the message TTL has been exceeded."""
        if self.ttl <= 0:
            return False
        return (time.time() - self.timestamp) > self.ttl

    @property
    def is_safety(self) -> bool:
        """Return True if this is a SAFETY-priority message."""
        return self.priority == Priority.SAFETY
