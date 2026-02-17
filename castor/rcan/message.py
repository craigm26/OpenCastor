"""
RCAN Message Envelope.

Defines the standard JSON message format for RCAN protocol communication.
Messages are plain JSON (no protobuf) -- readable with ``curl``, zero deps.

Message types follow the RCAN spec::

    DISCOVER  -- mDNS / peer discovery
    STATUS    -- Telemetry / state reporting
    COMMAND   -- Motor, config, or action command
    STREAM    -- Continuous sensor data
    EVENT     -- Asynchronous notifications
    HANDOFF   -- Transfer control between principals
    ACK       -- Acknowledgement of a prior message
    ERROR     -- Error response

Each message carries a priority (LOW, NORMAL, HIGH, SAFETY) that determines
queue ordering.  SAFETY priority messages skip the queue entirely
(Safety Invariant 6).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional


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
    payload: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    priority: int = field(default=Priority.NORMAL)
    ttl: int = field(default=0)
    reply_to: Optional[str] = field(default=None)
    scope: List[str] = field(default_factory=list)
    version: str = field(default="1.0.0")

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------
    @classmethod
    def command(
        cls,
        source: str,
        target: str,
        payload: Dict[str, Any],
        priority: int = Priority.NORMAL,
        scope: Optional[List[str]] = None,
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
        payload: Dict[str, Any],
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
        payload: Optional[Dict[str, Any]] = None,
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

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict (JSON-ready)."""
        d = asdict(self)
        # Convert enum ints to their names for readability
        d["type_name"] = MessageType(self.type).name
        d["priority_name"] = Priority(self.priority).name
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RCANMessage:
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
