"""
RCAN Message Envelope.

Defines the standard JSON message format for RCAN protocol communication.
Messages are plain JSON (no protobuf) -- readable with ``curl``, zero deps.

Message types use the RCAN spec §3.2 numbering (see :class:`MessageType`).
The names OpenCastor uses most are COMMAND, STATUS, ACK, ERROR, SAFETY,
DISCOVER, PENDING_AUTH, AUTHORIZE, INVOKE / INVOKE_RESULT / INVOKE_CANCEL and
REGISTRY_REGISTER / REGISTRY_RESOLVE (plus their OpenCastor-only *_RESULT
replies).

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
    """RCAN message types, numbered per RCAN spec §3.2 (1-45).

    The integer values for every spec type mirror ``rcan.MessageType`` from
    rcan-py, which is generated from rcan-spec
    ``spec/sections/MESSAGE_TYPE_NUMBERING.md``.  ``tests/test_rcan_message.py``
    fails if this table and the installed rcan-py ever disagree.

    OpenCastor 3.7 renumbered this table.  Before that it used a pre-v2.1
    numbering for 1-19 (DISCOVER=1, COMMAND=3, AUTHORIZE=9, TRANSPARENCY=18,
    ...).  ``RCANMessage.from_dict`` still decodes messages from older
    OpenCastor peers by their ``type_name`` field; see :func:`resolve_type`.

    Names are unchanged from earlier releases so call sites keep working.
    """

    # Core protocol (1-8)
    COMMAND = 1
    RESPONSE = 2
    STATUS = 3
    HEARTBEAT = 4
    CONFIG = 5
    SAFETY = 6  # RCAN §6: STOP / ESTOP / RESUME, bypasses all queues
    AUTH = 7
    ERROR = 8

    # Discovery & authorization (9-10)
    DISCOVER = 9
    PENDING_AUTH = 10  # HiTL gate is awaiting authorization

    # Skill invocation (11-13), RCAN §19
    INVOKE = 11
    INVOKE_RESULT = 12
    INVOKE_CANCEL = 13

    # Registry (14-15), RCAN §21
    REGISTRY_REGISTER = 14
    REGISTRY_RESOLVE = 15

    # Audit & transparency (16)
    TRANSPARENCY = 16  # EU AI Act Art. 13 transparency disclosure

    # Acknowledgement & QoS (17-18)
    COMMAND_ACK = 17
    COMMAND_NACK = 18

    # Identity & consent (19-22)
    ROBOT_REVOCATION = 19
    CONSENT_REQUEST = 20  # R2RAM §5: request cross-owner robot-to-robot authorization
    CONSENT_GRANT = 21  # R2RAM §5: grant cross-owner authorization with scopes
    CONSENT_DENY = 22  # R2RAM §5: deny cross-owner authorization request

    # Fleet & telemetry (23-25)
    FLEET_COMMAND = 23
    SUBSCRIBE = 24
    UNSUBSCRIBE = 25

    # Diagnostics (26-28)
    FAULT_REPORT = 26
    KEY_ROTATION = 27
    COMMAND_COMMIT = 28  # Commit a pending command (HiTL confirmation)

    # Sensor & training consent (29-32)
    SENSOR_DATA = 29
    TRAINING_CONSENT_REQUEST = 30
    TRAINING_CONSENT_GRANT = 31
    TRAINING_CONSENT_DENY = 32

    # Idle compute contribution, v1.7 (33-35)
    CONTRIBUTE_REQUEST = 33
    CONTRIBUTE_RESULT = 34
    CONTRIBUTE_CANCEL = 35

    # Multimodal training data, v1.8 (36)
    TRAINING_DATA = 36

    # Competition protocol, v1.10 (37-40)
    COMPETITION_ENTER = 37
    COMPETITION_SCORE = 38
    SEASON_STANDING = 39
    PERSONAL_RESEARCH_RESULT = 40

    # Authority & attestation, v2.1 (41-44)
    AUTHORITY_ACCESS = 41  # EU AI Act §16(j) authority audit request
    AUTHORITY_RESPONSE = 42  # Response to authority audit request
    FIRMWARE_ATTESTATION = 43  # Firmware manifest attestation broadcast
    SBOM_UPDATE = 44  # SBOM update notification

    # HiTL authorization, v3.3 (45): approve or deny a PENDING_AUTH (§16.4)
    AUTHORIZE = 45

    # -- OpenCastor names that are not separate spec types ------------------
    # ACK is the router's acknowledgement of a command or INVOKE; on the wire
    # it is the spec's COMMAND_ACK (an alias, so MessageType(17).name is
    # "COMMAND_ACK").
    ACK = 17

    # OpenCastor extensions with no spec equivalent.  They live in the
    # vendor-specific range (100+, spec §3.2 "Reserved Ranges") so they can
    # never collide with a spec type.  A non-OpenCastor peer will not know them.
    STREAM = 100  # Continuous sensor data
    EVENT = 101  # Asynchronous notifications
    HANDOFF = 102  # Transfer control between principals
    REGISTRY_REGISTER_RESULT = 103  # Result of REGISTRY_REGISTER (§21)
    REGISTRY_RESOLVE_RESULT = 104  # Result of REGISTRY_RESOLVE (§21)


# Pre-3.7 OpenCastor numbering, kept only so tests and docs can state exactly
# what changed.  Never used to decode: a bare number is always read with the
# spec table above (see resolve_type).
LEGACY_OPENCASTOR_NUMBERS: dict[str, int] = {
    "DISCOVER": 1,
    "STATUS": 2,
    "COMMAND": 3,
    "STREAM": 4,
    "EVENT": 5,
    "ACK": 7,
    "AUTHORIZE": 9,
    "REGISTRY_REGISTER": 13,
    "REGISTRY_RESOLVE": 14,
    "INVOKE_CANCEL": 15,
    "REGISTRY_REGISTER_RESULT": 16,
    "REGISTRY_RESOLVE_RESULT": 17,
    "TRANSPARENCY": 18,
    "HANDOFF": 19,
}


def resolve_type(data: dict[str, Any]) -> Any:
    """Return the message type of an inbound message dict.

    Reads ``type`` (or ``msg_type``) as a spec §3.2 number or a type name.

    Compatibility with OpenCastor before 3.7: those releases always sent a
    ``type_name`` next to the number, and their numbers for 1-19 differ from
    the spec.  When ``type_name`` names a known type and disagrees with the
    number, the name wins and a deprecation warning is logged.  A bare number
    with no ``type_name`` is never reinterpreted.

    Returns a :class:`MessageType` when the type is known, the raw number
    otherwise (``None`` when the message carries no type at all).  An unknown
    type *name* raises ``KeyError``.
    """
    raw = data.get("type")
    if raw is None:
        raw = data.get("msg_type")

    if isinstance(raw, str):
        return MessageType[raw.upper()]  # unknown name: KeyError, as before

    name = data.get("type_name")
    by_name = None
    if isinstance(name, str):
        by_name = MessageType.__members__.get(name.upper())

    if raw is None:
        return by_name

    try:
        by_number = MessageType(raw)
    except (ValueError, TypeError):
        by_number = None

    if by_name is not None and by_name != by_number:
        log.warning(
            "RCAN message type %r with type_name %r uses pre-3.7 OpenCastor "
            "numbering; decoding by name as %s (%d). Upgrade the sender: "
            "this fallback will be removed.",
            raw,
            name,
            by_name.name,
            int(by_name),
        )
        return by_name

    return by_number if by_number is not None else raw


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

        # Remove display-only fields
        d.pop("type_name", None)
        d.pop("priority_name", None)

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

        # Resolve the type: spec number, name, or a pre-3.7 OpenCastor
        # number with its type_name (see resolve_type).
        resolved = resolve_type(data)
        if resolved is not None:
            d["type"] = resolved
        # Coerce priority from name if needed
        if isinstance(d.get("priority"), str):
            d["priority"] = Priority[d["priority"].upper()]

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
