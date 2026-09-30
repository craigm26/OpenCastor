"""
RCAN Message Envelope.

Defines the standard JSON message format for RCAN protocol communication.
Messages are plain JSON (no protobuf) -- readable with ``curl``, zero deps.

Message types are numbered per the canonical table in RCAN spec §3.2
(the same numbers rcan-py and rcan-ts use). Among them::

    COMMAND       -- Motor, config, or action command
    RESPONSE      -- Reply to a prior message (``ack()`` builds one)
    STATUS        -- Telemetry / state reporting
    SAFETY        -- STOP / ESTOP / RESUME safety events (highest priority)
    ERROR         -- Error response
    DISCOVER      -- mDNS / peer discovery
    AUTHORIZE     -- Out-of-band authorization for HiTL gate (v1.2)
    PENDING_AUTH  -- Notification that HiTL gate is awaiting authorization (v1.2)
    INVOKE             -- Trigger a named skill/behavior on the robot runtime (v1.3 §19)
    INVOKE_RESULT      -- Result of an INVOKE invocation (v1.3 §19)
    INVOKE_CANCEL      -- Cancel an in-flight INVOKE by invoke_id (v1.3 §19)
    REGISTRY_REGISTER        -- Register robot with RRF (v1.3 §21)
    REGISTRY_RESOLVE         -- Resolve RRN to RURI/metadata (v1.3 §21)

Each message carries a priority (LOW, NORMAL, HIGH, SAFETY) that determines
queue ordering.  SAFETY priority messages skip the queue entirely
(Safety Invariant 6).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any, Optional

log = logging.getLogger(__name__)

# RCAN spec version implemented by this module
RCAN_SPEC_VERSION = "3.0"


class MessageType(IntEnum):
    """RCAN message types, numbered per the canonical table in RCAN spec §3.2.

    The same numbers are used by rcan-py and rcan-ts. Until 3.6.0 OpenCastor
    used its own pre-v2.1 numbering for types 1-19 (DISCOVER was 1, COMMAND
    was 3, AUTHORIZE was 9), so a bare integer from an older OpenCastor peer
    meant something different. Older peers always send ``type_name`` as well,
    and :meth:`RCANMessage.from_dict` trusts the name over the number, which
    keeps them interoperable.
    """

    # Core protocol (1-8)
    COMMAND = 1
    RESPONSE = 2  # generic reply to a prior message
    STATUS = 3
    HEARTBEAT = 4
    CONFIG = 5
    SAFETY = 6  # RCAN §6: STOP / ESTOP / RESUME — bypasses all queues
    AUTH = 7
    ERROR = 8
    # Discovery & authorization (9-10)
    DISCOVER = 9
    PENDING_AUTH = 10  # HiTL gate awaiting authorization (§16.4)
    # Skill invocation (11-13)
    INVOKE = 11  # §19
    INVOKE_RESULT = 12  # §19
    INVOKE_CANCEL = 13  # §19
    # Registry (14-15)
    REGISTRY_REGISTER = 14  # §21
    REGISTRY_RESOLVE = 15  # §21
    # Audit & transparency (16)
    TRANSPARENCY = 16  # EU AI Act Art. 13
    # Acknowledgement & QoS (17-18)
    COMMAND_ACK = 17
    COMMAND_NACK = 18
    # Identity & consent (19-22)
    ROBOT_REVOCATION = 19
    CONSENT_REQUEST = 20  # R2RAM §5
    CONSENT_GRANT = 21
    CONSENT_DENY = 22
    # Fleet & telemetry (23-29)
    FLEET_COMMAND = 23
    SUBSCRIBE = 24
    UNSUBSCRIBE = 25
    FAULT_REPORT = 26
    KEY_ROTATION = 27
    COMMAND_COMMIT = 28
    SENSOR_DATA = 29
    # Training data consent (30-32)
    TRAINING_CONSENT_REQUEST = 30
    TRAINING_CONSENT_GRANT = 31
    TRAINING_CONSENT_DENY = 32
    # Idle compute contribution (33-36)
    CONTRIBUTE_REQUEST = 33
    CONTRIBUTE_RESULT = 34
    CONTRIBUTE_CANCEL = 35
    TRAINING_DATA = 36
    # Competition (37-40)
    COMPETITION_ENTER = 37
    COMPETITION_SCORE = 38
    SEASON_STANDING = 39
    PERSONAL_RESEARCH_RESULT = 40
    # Authority & attestation (41-44)
    AUTHORITY_ACCESS = 41  # EU AI Act Art. 16(j)
    AUTHORITY_RESPONSE = 42
    FIRMWARE_ATTESTATION = 43
    SBOM_UPDATE = 44
    # HiTL authorization (45)
    AUTHORIZE = 45  # approve or deny a PENDING_AUTH (§16.4)

    # Deprecated aliases (3.6.0). The canonical table has no separate ACK or
    # registry-result types; replies are RESPONSE. These names still resolve,
    # so older peers that send them by name are understood.
    ACK = 2
    REGISTRY_REGISTER_RESULT = 2
    REGISTRY_RESOLVE_RESULT = 2


def resolve_message_type(data: dict[str, Any]) -> MessageType:
    """Resolve the message type of a wire dict, name first.

    ``type_name`` (or a string ``type``) wins over an integer ``type``, because
    older OpenCastor peers send pre-3.6.0 integers alongside the name. A bare
    integer is read as the §3.2 canonical number. An unknown name is an error,
    never a fallback to the integer, so an old-only name cannot be silently
    reinterpreted.

    Raises:
        ValueError: if no type is present or it is not a known type.
    """
    name = data.get("type_name")
    if name is None and isinstance(data.get("type"), str):
        name = data["type"]
    if name is not None:
        key = str(name).upper()
        if key not in MessageType.__members__:
            raise ValueError(f"unknown RCAN message type name: {name!r}")
        return MessageType.__members__[key]
    raw = data.get("type", data.get("msg_type"))
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"missing or non-integer RCAN message type: {raw!r}")
    try:
        return MessageType(raw)
    except ValueError:
        raise ValueError(f"unknown RCAN message type number: {raw}") from None


def resolve_priority(data: dict[str, Any]) -> Priority:
    """Resolve the priority of a wire dict, name first (see resolve_message_type).

    A missing priority is NORMAL.
    """
    name = data.get("priority_name")
    if name is None and isinstance(data.get("priority"), str):
        name = data["priority"]
    if name is not None:
        key = str(name).upper()
        if key not in Priority.__members__:
            raise ValueError(f"unknown RCAN priority name: {name!r}")
        return Priority.__members__[key]
    raw = data.get("priority")
    if raw is None:
        return Priority.NORMAL
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"non-integer RCAN priority: {raw!r}")
    try:
        return Priority(raw)
    except ValueError:
        raise ValueError(f"unknown RCAN priority number: {raw}") from None


@dataclass
class DelegationHop:
    """A single hop in a delegation chain."""

    robot_rrn: str
    scope: str
    issued_at: str
    expires_at: str
    sig: str = ""


@dataclass
class MediaChunk:
    """A media chunk attached to an RCAN message."""

    chunk_id: str
    mime_type: str
    size_bytes: int
    hash_sha256: str
    data: str = ""
    ref_url: str = ""

    def verify_hash(self) -> None:
        """Verify the SHA-256 hash of the data field.

        Raises:
            ValueError: If the hash does not match.
        """
        import hashlib

        actual = "sha256:" + hashlib.sha256(self.data.encode()).hexdigest()
        if actual != self.hash_sha256:
            raise ValueError(f"MediaChunk hash mismatch: expected {self.hash_sha256}, got {actual}")


class Priority(IntEnum):
    """Message priority levels, numbered per RCAN spec §3.4.  SAFETY skips the
    normal queue.  (Before 3.6.0 OpenCastor used 0-3; older peers also send
    ``priority_name``, which :func:`resolve_priority` trusts.)"""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    SAFETY = 4


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
    rcan_version: str = field(default_factory=lambda: RCAN_SPEC_VERSION)  # v1.5 §3.5

    # v2.2 envelope fields
    firmware_hash: str = ""
    attestation_ref: str = ""
    pq_sig: str = ""
    pq_alg: str = "ml-dsa-65"
    delegation_chain: list = field(default_factory=list)
    media_chunks: list = field(default_factory=list)

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
        """Serialise to a plain dict (JSON-ready).

        v1.5: includes rcan_version in outgoing messages (GAP-12).
        v2.2: includes envelope fields (firmware_hash, attestation_ref, pq_sig,
              pq_alg, delegation_chain, media_chunks).
        """
        d = asdict(self)
        # Convert enum ints to their names for readability
        d["type_name"] = MessageType(self.type).name
        d["priority_name"] = Priority(self.priority).name
        # Ensure rcan_version is always present in outgoing messages
        d.setdefault("rcan_version", RCAN_SPEC_VERSION)
        # v2.2 envelope fields are already included via asdict()
        d.setdefault("firmware_hash", self.firmware_hash)
        d.setdefault("attestation_ref", self.attestation_ref)
        d.setdefault("pq_sig", self.pq_sig)
        d.setdefault("pq_alg", self.pq_alg)
        d.setdefault("delegation_chain", self.delegation_chain)
        d.setdefault("media_chunks", self.media_chunks)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RCANMessage:
        """Deserialise from a dict.

        Accepts both integer type/priority values and string names.

        v1.5: logs a warning (not error) when receiving messages with a
        different rcan_version (GAP-12 — forward/backward compat).
        """
        d = dict(data)

        # Resolve type and priority name-first (older peers used different
        # integers but always send the names), then drop the display fields.
        d["type"] = resolve_message_type(d)
        d["priority"] = resolve_priority(d)
        d.pop("type_name", None)
        d.pop("priority_name", None)
        d.pop("msg_type", None)

        # v1.5 version negotiation — warn on mismatch, don't reject
        incoming_version = d.get("rcan_version")
        if incoming_version and incoming_version != RCAN_SPEC_VERSION:
            try:
                inc_parts = incoming_version.split(".")
                our_parts = RCAN_SPEC_VERSION.split(".")
                inc_major = int(inc_parts[0])
                our_major = int(our_parts[0])
                if inc_major != our_major:
                    log.warning(
                        "Received RCAN message with incompatible MAJOR version "
                        "%s (ours: %s) — proceeding with caution",
                        incoming_version,
                        RCAN_SPEC_VERSION,
                    )
                else:
                    log.warning(
                        "Received RCAN message with version %s (ours: %s) — "
                        "unknown fields will be ignored",
                        incoming_version,
                        RCAN_SPEC_VERSION,
                    )
            except (ValueError, IndexError):
                log.warning(
                    "Received RCAN message with unparseable rcan_version=%r",
                    incoming_version,
                )

        # Populate v2.2 envelope fields
        d.setdefault("firmware_hash", "")
        d.setdefault("attestation_ref", "")
        d.setdefault("pq_sig", "")
        d.setdefault("pq_alg", "ml-dsa-65")
        d.setdefault("delegation_chain", [])
        d.setdefault("media_chunks", [])

        # Validate delegation chain depth (RCAN v2.2 §7)
        if len(d["delegation_chain"]) > 3:
            raise ValueError("RCAN: delegation chain max depth is 3")

        # Strip unknown fields not in the dataclass (forward-compat)
        import dataclasses as _dc

        known = {f.name for f in _dc.fields(cls)}
        d = {k: v for k, v in d.items() if k in known}

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
