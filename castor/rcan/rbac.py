"""
RCAN RBAC (Role-Based Access Control).

Implements the 5-tier RCAN role hierarchy::

    GUEST   (1) -- Read-only status, no control.
    USER    (2) -- Basic teleoperation, chat.
    LEASEE  (3) -- Full control, config reads.
    OWNER   (4) -- Config writes, training, provider switching.
    CREATOR (5) -- Safety overrides, firmware, full access.

Each role maps to a set of scopes that determine what actions
a principal can perform via the RCAN protocol.

Legacy principal names (``brain``, ``api``, ``channel``, ``driver``)
are mapped to RCAN roles via :meth:`RCANPrincipal.from_legacy`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag, auto
from typing import Callable, Dict, List, Optional

from castor.fs.permissions import Cap

logger = logging.getLogger(__name__)

# Backward compatibility: map deprecated role names to new RCAN spec names
_DEPRECATED_ROLE_NAMES: Dict[str, str] = {
    "ADMIN": "OWNER",
    "OPERATOR": "LEASEE",
}


def resolve_role_name(name: str) -> str:
    """Resolve a role name, emitting a deprecation warning for old names."""
    upper = name.upper()
    if upper in _DEPRECATED_ROLE_NAMES:
        new_name = _DEPRECATED_ROLE_NAMES[upper]
        logger.warning("Role '%s' is deprecated, use '%s' (RCAN spec alignment)", upper, new_name)
        return new_name
    return upper


class RCANRole(IntEnum):
    """RCAN 5-tier role hierarchy (RCAN spec: CREATOR, OWNER, LEASEE, USER, GUEST)."""

    GUEST = 1
    USER = 2
    LEASEE = 3
    OWNER = 4
    CREATOR = 5


class Scope(IntFlag):
    """RCAN permission scopes (bit flags)."""

    NONE = 0
    STATUS = auto()  # Read /proc, telemetry
    CONTROL = auto()  # Motor commands, teleop
    CONFIG = auto()  # Read/write config
    TRAINING = auto()  # Memory writes, context writes
    ADMIN = auto()  # Safety overrides, firmware

    @classmethod
    def for_role(cls, role: RCANRole) -> Scope:
        """Return the default scope set for a given role."""
        if role == RCANRole.GUEST:
            return cls.STATUS
        if role == RCANRole.USER:
            return cls.STATUS | cls.CONTROL
        if role == RCANRole.LEASEE:
            return cls.STATUS | cls.CONTROL | cls.CONFIG
        if role == RCANRole.OWNER:
            return cls.STATUS | cls.CONTROL | cls.CONFIG | cls.TRAINING
        if role == RCANRole.CREATOR:
            return cls.STATUS | cls.CONTROL | cls.CONFIG | cls.TRAINING | cls.ADMIN
        return cls.NONE

    @classmethod
    def from_strings(cls, names: List[str]) -> Scope:
        """Parse a list of scope name strings into a Scope flag set."""
        result = cls.NONE
        mapping = {
            "status": cls.STATUS,
            "control": cls.CONTROL,
            "config": cls.CONFIG,
            "training": cls.TRAINING,
            "admin": cls.ADMIN,
        }
        for name in names:
            flag = mapping.get(name.lower())
            if flag:
                result |= flag
        return result

    def to_strings(self) -> List[str]:
        """Convert scope flags to a list of name strings."""
        names = []
        if self & Scope.STATUS:
            names.append("status")
        if self & Scope.CONTROL:
            names.append("control")
        if self & Scope.CONFIG:
            names.append("config")
        if self & Scope.TRAINING:
            names.append("training")
        if self & Scope.ADMIN:
            names.append("admin")
        return names


# Mapping from RCAN scopes to legacy Cap flags
_SCOPE_TO_CAPS: Dict[Scope, Cap] = {
    Scope.STATUS: Cap.MEMORY_READ,
    Scope.CONTROL: Cap.MOTOR_WRITE | Cap.DEVICE_ACCESS | Cap.ESTOP,
    Scope.CONFIG: Cap.CONFIG_WRITE | Cap.PROVIDER_SWITCH,
    Scope.TRAINING: Cap.MEMORY_WRITE | Cap.CONTEXT_WRITE,
    Scope.ADMIN: Cap.SAFETY_OVERRIDE,
}

# Mapping from legacy principal names to RCAN roles
_LEGACY_ROLE_MAP: Dict[str, RCANRole] = {
    "root": RCANRole.CREATOR,
    "brain": RCANRole.OWNER,
    "api": RCANRole.LEASEE,
    "channel": RCANRole.USER,
    "driver": RCANRole.GUEST,
}

# Rate limits per role (requests per minute) per RCAN spec
ROLE_RATE_LIMITS: Dict[RCANRole, int] = {
    RCANRole.GUEST: 10,
    RCANRole.USER: 100,
    RCANRole.LEASEE: 500,
    RCANRole.OWNER: 1000,
    RCANRole.CREATOR: 0,  # 0 = unlimited
}

# Session timeout per role (seconds, 0 = no timeout)
ROLE_SESSION_TIMEOUT: Dict[RCANRole, int] = {
    RCANRole.GUEST: 300,  # 5 minutes
    RCANRole.USER: 3600,  # 1 hour
    RCANRole.LEASEE: 7200,  # 2 hours
    RCANRole.OWNER: 28800,  # 8 hours
    RCANRole.CREATOR: 0,  # no timeout
}


@dataclass
class RCANPrincipal:
    """An authenticated principal with a role and scopes.

    Attributes:
        name:    Principal identifier (e.g. username or legacy name).
        role:    RCAN role tier.
        scopes:  Active scope flags.
        fleet:   Optional list of RURI patterns this principal can access.
    """

    name: str
    role: RCANRole
    scopes: Scope = field(default=Scope.NONE)
    fleet: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.scopes == Scope.NONE:
            self.scopes = Scope.for_role(self.role)

    @classmethod
    def from_legacy(cls, legacy_name: str) -> RCANPrincipal:
        """Map a legacy OpenCastor principal name to an RCANPrincipal.

        Legacy names: ``root``, ``brain``, ``api``, ``channel``, ``driver``.
        """
        role = _LEGACY_ROLE_MAP.get(legacy_name, RCANRole.GUEST)
        return cls(name=legacy_name, role=role)

    def has_scope(self, scope: Scope) -> bool:
        """Check if this principal holds a specific scope."""
        return bool(self.scopes & scope)

    def to_caps(self) -> Cap:
        """Convert RCAN scopes to legacy Cap flags."""
        caps = Cap.NONE
        for scope_flag, cap_flags in _SCOPE_TO_CAPS.items():
            if self.scopes & scope_flag:
                caps |= cap_flags
        return caps

    @property
    def rate_limit(self) -> int:
        """Requests per minute allowed for this role."""
        return ROLE_RATE_LIMITS.get(self.role, 100)

    @property
    def session_timeout(self) -> int:
        """Session timeout in seconds for this role."""
        return ROLE_SESSION_TIMEOUT.get(self.role, 3600)

    def to_dict(self) -> dict:
        """Serialise for API responses / JWT claims."""
        return {
            "name": self.name,
            "role": self.role.name,
            "role_level": int(self.role),
            "scopes": self.scopes.to_strings(),
            "fleet": self.fleet,
            "rate_limit": self.rate_limit,
            "session_timeout": self.session_timeout,
        }


@dataclass
class CapabilityLease:
    """A signed capability lease bound to a principal + resource scope."""

    lease_id: str
    principal: str
    scope: Scope
    resource: str
    intent_context: dict
    issued_at: float
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_payload(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "principal": self.principal,
            "scope": int(self.scope),
            "resource": self.resource,
            "intent_context": self.intent_context,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> CapabilityLease:
        return cls(
            lease_id=payload["lease_id"],
            principal=payload["principal"],
            scope=Scope(payload["scope"]),
            resource=payload["resource"],
            intent_context=payload.get("intent_context", {}),
            issued_at=float(payload["issued_at"]),
            expires_at=float(payload["expires_at"]),
        )


class CapabilityBroker:
    """Issues and validates scoped signed capability leases."""

    def __init__(
        self,
        signing_key: str,
        max_ttl_seconds: float = 600.0,
        approval_hook: Optional[Callable[..., bool]] = None,
        audit_hook: Optional[Callable[..., None]] = None,
    ):
        self._key = signing_key.encode("utf-8")
        self._max_ttl = max_ttl_seconds
        self._approval_hook = approval_hook
        self._audit_hook = audit_hook
        self._revoked_lease_ids: set[str] = set()

    def _audit(self, event: str, **kwargs: object) -> None:
        if self._audit_hook:
            self._audit_hook(event=event, **kwargs)

    def _encode(self, data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    def _decode(self, text: str) -> bytes:
        pad = "=" * (-len(text) % 4)
        return base64.urlsafe_b64decode(text + pad)

    def _sign(self, payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        mac = hmac.new(self._key, raw, hashlib.sha256).digest()
        return f"{self._encode(raw)}.{self._encode(mac)}"

    def _verify(self, token: str) -> Optional[CapabilityLease]:
        try:
            payload_part, sig_part = token.split(".", 1)
            raw = self._decode(payload_part)
            got = self._decode(sig_part)
            want = hmac.new(self._key, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(got, want):
                return None
            payload = json.loads(raw.decode("utf-8"))
            return CapabilityLease.from_payload(payload)
        except Exception:
            return None

    def issue_lease(
        self,
        principal: RCANPrincipal,
        scope: Scope,
        resource: str,
        ttl_seconds: float,
        intent_context: Optional[dict] = None,
    ) -> str:
        if not principal.has_scope(scope):
            raise PermissionError(
                f"principal {principal.name} does not hold requested scope {scope}"
            )
        ttl = max(1.0, min(float(ttl_seconds), self._max_ttl))
        now = time.time()
        lease = CapabilityLease(
            lease_id=str(uuid.uuid4()),
            principal=principal.name,
            scope=scope,
            resource=resource,
            intent_context=intent_context or {},
            issued_at=now,
            expires_at=now + ttl,
        )
        token = self._sign(lease.to_payload())
        self._audit(
            "capability_grant",
            principal=principal.name,
            lease_id=lease.lease_id,
            resource=resource,
            scope=scope.to_strings(),
            intent_context=lease.intent_context,
            expires_at=lease.expires_at,
        )
        return token

    def revoke_lease(self, token: str, principal: str, reason: str = "manual_revoke") -> bool:
        lease = self._verify(token)
        if not lease:
            self._audit("capability_revoke_failed", principal=principal, reason="invalid_token")
            return False
        self._revoked_lease_ids.add(lease.lease_id)
        self._audit(
            "capability_revoke",
            principal=principal,
            lease_id=lease.lease_id,
            target_principal=lease.principal,
            reason=reason,
            intent_context=lease.intent_context,
        )
        return True

    def _resource_matches(self, granted: str, requested: str) -> bool:
        if granted == "*":
            return True
        if granted.endswith("*"):
            return requested.startswith(granted[:-1])
        return granted == requested

    def _requires_high_risk_approval(self, path: str, data: object) -> bool:
        if "/property" in path or path.startswith("/dev/property"):
            return True
        if path.startswith("/dev/arm") and isinstance(data, dict):
            for key in ("force", "max_force", "force_threshold"):
                if key in data and float(data[key]) > 40.0:
                    return True
        return False

    def validate_lease(
        self,
        token: str,
        principal: str,
        required_scope: Scope,
        resource: str,
        *,
        path: str,
        data: object,
        intent_context: Optional[dict] = None,
    ) -> bool:
        lease = self._verify(token)
        if not lease:
            self._audit("capability_denied", principal=principal, reason="invalid_signature")
            return False
        if lease.lease_id in self._revoked_lease_ids:
            self._audit(
                "capability_denied",
                principal=principal,
                lease_id=lease.lease_id,
                reason="revoked",
                intent_context=lease.intent_context,
            )
            return False
        if lease.is_expired:
            self._audit(
                "capability_expired",
                principal=principal,
                lease_id=lease.lease_id,
                intent_context=lease.intent_context,
            )
            return False
        if lease.principal != principal:
            self._audit("capability_denied", principal=principal, reason="principal_mismatch")
            return False
        if not (lease.scope & required_scope):
            self._audit(
                "capability_denied",
                principal=principal,
                lease_id=lease.lease_id,
                reason="scope_mismatch",
                required_scope=required_scope.to_strings(),
            )
            return False
        if not self._resource_matches(lease.resource, resource):
            self._audit(
                "capability_denied",
                principal=principal,
                lease_id=lease.lease_id,
                reason="resource_mismatch",
                resource=resource,
            )
            return False

        if self._requires_high_risk_approval(path, data):
            if not self._approval_hook:
                self._audit(
                    "capability_denied",
                    principal=principal,
                    lease_id=lease.lease_id,
                    reason="high_risk_requires_approval",
                    intent_context=intent_context or lease.intent_context,
                )
                return False
            allowed = self._approval_hook(
                principal=principal,
                lease=lease,
                path=path,
                data=data,
                intent_context=intent_context or lease.intent_context,
            )
            if not allowed:
                self._audit(
                    "capability_denied",
                    principal=principal,
                    lease_id=lease.lease_id,
                    reason="approval_hook_denied",
                    intent_context=intent_context or lease.intent_context,
                )
                return False
        return True
