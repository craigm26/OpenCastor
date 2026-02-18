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

import logging
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag, auto
from typing import Dict, List

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
        logger.warning(
            "Role '%s' is deprecated, use '%s' (RCAN spec alignment)", upper, new_name
        )
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
    RCANRole.OPERATOR: 7200,  # 2 hours
    RCANRole.ADMIN: 28800,  # 8 hours
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
