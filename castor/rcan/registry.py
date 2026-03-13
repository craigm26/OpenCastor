"""
RCAN §21 Robot Registry Framework (RRF) protocol stubs.

Implements REGISTRY_REGISTER and REGISTRY_RESOLVE message types for
registering robots with the RRF and resolving Robot Registration Numbers (RRNs)
to Robot URIs (RURIs) and associated metadata.

Spec: https://rcan.dev/spec/section-21/
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from castor.rcan.message import MessageType


def _validate_rrn(rrn: str) -> None:
    """Validate Robot Resource Name format.

    An RRN must start with ``rrn://``, have a non-empty host, and a non-empty path
    separated by ``/``.  Examples of valid RRNs::

        rrn://example.org/robots/rover-1
        rrn://myorg/arm-2

    Args:
        rrn: The RRN string to validate.

    Raises:
        ValueError: If the RRN does not conform to the expected format.
    """
    if not rrn:
        raise ValueError("RRN must not be empty")
    if not rrn.startswith("rrn://"):
        raise ValueError(f"RRN must start with 'rrn://', got: {rrn!r}")
    rest = rrn[len("rrn://"):]
    slash_pos = rest.find("/")
    if slash_pos <= 0:
        raise ValueError(f"RRN must have a non-empty host and path (e.g. rrn://host/path), got: {rrn!r}")
    host = rest[:slash_pos]
    path = rest[slash_pos + 1:]
    if not host:
        raise ValueError(f"RRN host must not be empty, got: {rrn!r}")
    if not path:
        raise ValueError(f"RRN path must not be empty, got: {rrn!r}")


@dataclass
class RegistryMessage:
    """Payload for REGISTRY_REGISTER (§21.2).

    Sent by a robot to register itself with the RRF.

    Attributes:
        msg_id:     Unique message identifier (UUID).
        rrn:        Robot Registration Number (e.g. ``rrn://example.org/rover-1``).
        ruri:       Robot URI — the reachable endpoint for this robot.
        public_key: PEM-encoded public key for identity verification.
        timestamp:  Unix timestamp of registration request.
    """

    msg_id: str
    rrn: str
    ruri: str
    public_key: str
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        _validate_rrn(self.rrn)

    def to_message(self) -> dict[str, Any]:
        """Serialize to RCAN message format using REGISTRY_REGISTER type."""
        return {
            "type": MessageType.REGISTRY_REGISTER,
            "msg_id": self.msg_id,
            "payload": {
                "rrn": self.rrn,
                "ruri": self.ruri,
                "public_key": self.public_key,
                "timestamp": self.timestamp,
            },
        }

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> RegistryMessage:
        """Parse a REGISTRY_REGISTER message dict.

        Args:
            data: Raw message dict (as returned by ``to_message()``).

        Raises:
            ValueError: If any required field is missing or RRN is malformed.
        """
        payload = data.get("payload", data)
        required = ("rrn", "ruri", "public_key")
        for key in required:
            if key not in payload:
                raise ValueError(f"Missing required field: '{key}'")
        msg_id = data.get("msg_id") or str(uuid.uuid4())
        return cls(
            msg_id=msg_id,
            rrn=payload["rrn"],
            ruri=payload["ruri"],
            public_key=payload["public_key"],
            timestamp=payload.get("timestamp", time.time()),
        )


@dataclass
class RegistryResolveRequest:
    """Payload for REGISTRY_RESOLVE request (§21.3).

    Sent to the RRF to look up an RRN and retrieve RURI + metadata.

    Attributes:
        rrn:    Robot Registration Number to resolve.
        msg_id: Unique message identifier (UUID).
    """

    rrn: str
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        _validate_rrn(self.rrn)

    def to_message(self) -> dict[str, Any]:
        """Serialize to RCAN message format using REGISTRY_RESOLVE type."""
        return {
            "type": MessageType.REGISTRY_RESOLVE,
            "msg_id": self.msg_id,
            "payload": {"rrn": self.rrn},
        }


@dataclass
class RegistryResolveResponse:
    """Payload for REGISTRY_RESOLVE response (§21.3).

    Returned by the RRF with resolved RURI and verification status.

    Attributes:
        rrn:      The Robot Registration Number that was resolved.
        ruri:     Resolved Robot URI (reachable endpoint).
        verified: Whether the robot's identity has been cryptographically verified.
        tier:     Service tier (e.g. ``'free'``, ``'pro'``, ``'enterprise'``).
    """

    rrn: str
    ruri: str
    verified: bool
    tier: str

    def to_message(self) -> dict[str, Any]:
        """Serialize to a response dict."""
        return {
            "type": MessageType.REGISTRY_RESOLVE,
            "payload": {
                "rrn": self.rrn,
                "ruri": self.ruri,
                "verified": self.verified,
                "tier": self.tier,
            },
        }

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> RegistryResolveResponse:
        """Parse a REGISTRY_RESOLVE response message dict.

        Args:
            data: Raw message dict (as returned by ``to_message()``).

        Raises:
            ValueError: If any required field is missing.
        """
        payload = data.get("payload", data)
        required = ("rrn", "ruri", "verified", "tier")
        for key in required:
            if key not in payload:
                raise ValueError(f"Missing required field: '{key}'")
        return cls(
            rrn=payload["rrn"],
            ruri=payload["ruri"],
            verified=payload["verified"],
            tier=payload["tier"],
        )


@dataclass
class RegistryRegisterResult:
    """Result payload for REGISTRY_REGISTER (§21.4 — REGISTRY_REGISTER_RESULT).

    Sent by the RRF to the registering robot after processing a
    ``REGISTRY_REGISTER`` request.

    Attributes:
        msg_id:  Unique message identifier (UUID).
        status:  ``"success"`` or ``"failure"``.
        rrn:     Assigned or confirmed RRN (present on success).
        error:   Human-readable error description (present on failure).
    """

    msg_id: str
    status: str  # "success" | "failure"
    rrn: Optional[str] = None
    error: Optional[str] = None

    def to_message(self) -> dict[str, Any]:
        """Serialize to RCAN message format using REGISTRY_REGISTER_RESULT type."""
        payload: dict[str, Any] = {"status": self.status}
        if self.rrn is not None:
            payload["rrn"] = self.rrn
        if self.error is not None:
            payload["error"] = self.error
        return {
            "type": MessageType.REGISTRY_REGISTER_RESULT,
            "msg_id": self.msg_id,
            "payload": payload,
        }

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> RegistryRegisterResult:
        """Parse a REGISTRY_REGISTER_RESULT message dict.

        Args:
            data: Raw message dict (as returned by ``to_message()``).

        Raises:
            ValueError: If ``status`` field is missing.
        """
        payload = data.get("payload", data)
        if "status" not in payload:
            raise ValueError("Missing required field: 'status'")
        msg_id = data.get("msg_id") or str(uuid.uuid4())
        return cls(
            msg_id=msg_id,
            status=payload["status"],
            rrn=payload.get("rrn"),
            error=payload.get("error"),
        )


@dataclass
class RegistryResolveResult:
    """Result payload for REGISTRY_RESOLVE (§21.5 — REGISTRY_RESOLVE_RESULT).

    Sent by the RRF in response to a ``REGISTRY_RESOLVE`` request.

    Attributes:
        msg_id:   Unique message identifier (UUID).
        status:   ``"found"``, ``"not_found"``, or ``"auth_failure"``.
        rrn:      The RRN that was queried.
        ruri:     Resolved RURI (present when status is ``"found"``).
        error:    Human-readable error description (present on failure).
        verified: Whether the robot's identity is cryptographically verified.
        tier:     Service tier of the registered robot.
    """

    msg_id: str
    status: str  # "found" | "not_found" | "auth_failure"
    rrn: str
    ruri: Optional[str] = None
    error: Optional[str] = None
    verified: bool = False
    tier: str = "free"

    def to_message(self) -> dict[str, Any]:
        """Serialize to RCAN message format using REGISTRY_RESOLVE_RESULT type."""
        payload: dict[str, Any] = {
            "status": self.status,
            "rrn": self.rrn,
            "verified": self.verified,
            "tier": self.tier,
        }
        if self.ruri is not None:
            payload["ruri"] = self.ruri
        if self.error is not None:
            payload["error"] = self.error
        return {
            "type": MessageType.REGISTRY_RESOLVE_RESULT,
            "msg_id": self.msg_id,
            "payload": payload,
        }

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> RegistryResolveResult:
        """Parse a REGISTRY_RESOLVE_RESULT message dict.

        Args:
            data: Raw message dict (as returned by ``to_message()``).

        Raises:
            ValueError: If ``status`` or ``rrn`` fields are missing.
        """
        payload = data.get("payload", data)
        for key in ("status", "rrn"):
            if key not in payload:
                raise ValueError(f"Missing required field: '{key}'")
        msg_id = data.get("msg_id") or str(uuid.uuid4())
        return cls(
            msg_id=msg_id,
            status=payload["status"],
            rrn=payload["rrn"],
            ruri=payload.get("ruri"),
            error=payload.get("error"),
            verified=payload.get("verified", False),
            tier=payload.get("tier", "free"),
        )
