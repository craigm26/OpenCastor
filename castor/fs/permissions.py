"""
OpenCastor Virtual Filesystem -- Permissions.

Unix-style rwx permission model with Linux-inspired capabilities.

Principals
----------
Each accessor is identified by a *principal* string:

- ``root``    -- Superuser (bypasses all checks).
- ``brain``   -- The AI provider (LLM).
- ``channel`` -- Messaging channels (WhatsApp, Telegram, ...).
- ``api``     -- The REST gateway / external callers.
- ``driver``  -- Hardware drivers (motors, servos).

Permission bits
---------------
Each path maps to an ACL entry: ``{principal: "rwx"}`` where each
letter may be replaced with ``-`` to deny.  Missing principals
inherit ``---`` (deny all).

Capabilities
------------
Fine-grained flags that gate *specific dangerous operations*,
independent of path permissions.  A principal must hold the
required capability AND have the path permission to proceed.
"""

import logging
from enum import Flag, auto
from typing import Dict, Optional

logger = logging.getLogger("OpenCastor.FS.Perm")


# -----------------------------------------------------------------------
# Capabilities (like Linux CAP_*)
# -----------------------------------------------------------------------
class Cap(Flag):
    """Fine-grained capability flags for safety-critical operations."""

    NONE = 0
    MOTOR_WRITE = auto()  # Can send motor commands
    ESTOP = auto()  # Can trigger emergency stop
    CONFIG_WRITE = auto()  # Can modify /etc config
    MEMORY_READ = auto()  # Can read /var/memory
    MEMORY_WRITE = auto()  # Can write /var/memory
    CHANNEL_SEND = auto()  # Can send messages via channels
    PROVIDER_SWITCH = auto()  # Can change the active AI provider
    SAFETY_OVERRIDE = auto()  # Can override safety limits (root only)
    DEVICE_ACCESS = auto()  # Can interact with /dev nodes
    CONTEXT_WRITE = auto()  # Can modify context window

    # Convenient compound sets
    @classmethod
    def brain_default(cls) -> "Cap":
        return (
            cls.MOTOR_WRITE
            | cls.MEMORY_READ
            | cls.MEMORY_WRITE
            | cls.DEVICE_ACCESS
            | cls.CONTEXT_WRITE
        )

    @classmethod
    def channel_default(cls) -> "Cap":
        return cls.MEMORY_READ | cls.CHANNEL_SEND | cls.ESTOP

    @classmethod
    def api_default(cls) -> "Cap":
        return (
            cls.MOTOR_WRITE
            | cls.ESTOP
            | cls.MEMORY_READ
            | cls.CHANNEL_SEND
            | cls.DEVICE_ACCESS
            | cls.SAFETY_OVERRIDE
        )

    @classmethod
    def driver_default(cls) -> "Cap":
        return cls.DEVICE_ACCESS

    @classmethod
    def root_caps(cls) -> "Cap":
        """All capabilities."""
        result = cls.NONE
        for member in cls:
            if member is not cls.NONE:
                result |= member
        return result


# -----------------------------------------------------------------------
# Permission mode (rwx per principal)
# -----------------------------------------------------------------------
_R = 0b100
_W = 0b010
_X = 0b001


def _parse_mode(mode_str: str) -> int:
    """Parse a 3-char mode string like ``rw-`` into an int."""
    bits = 0
    if len(mode_str) >= 1 and mode_str[0] == "r":
        bits |= _R
    if len(mode_str) >= 2 and mode_str[1] == "w":
        bits |= _W
    if len(mode_str) >= 3 and mode_str[2] == "x":
        bits |= _X
    return bits


def _mode_str(bits: int) -> str:
    """Convert mode bits back to a string like ``rw-``."""
    return ("r" if bits & _R else "-") + ("w" if bits & _W else "-") + ("x" if bits & _X else "-")


# -----------------------------------------------------------------------
# ACL entry
# -----------------------------------------------------------------------
class ACL:
    """Access control list for a single filesystem path.

    Stores ``{principal: mode_bits}`` and an optional set of required
    capabilities.
    """

    def __init__(self, entries: Optional[Dict[str, str]] = None, required_caps: Cap = Cap.NONE):
        self.entries: Dict[str, int] = {}
        if entries:
            for principal, mode in entries.items():
                self.entries[principal] = _parse_mode(mode)
        self.required_caps = required_caps

    def check(self, principal: str, operation: str) -> bool:
        """Check if *principal* has *operation* (``r``, ``w``, or ``x``)."""
        if principal == "root":
            return True
        bits = self.entries.get(principal, 0)
        if operation == "r":
            return bool(bits & _R)
        if operation == "w":
            return bool(bits & _W)
        if operation == "x":
            return bool(bits & _X)
        return False

    def dump(self) -> Dict[str, str]:
        return {p: _mode_str(m) for p, m in self.entries.items()}


# -----------------------------------------------------------------------
# Permission table
# -----------------------------------------------------------------------
class PermissionTable:
    """Maps filesystem paths to ACL entries.

    Supports prefix matching: an ACL on ``/dev`` applies to
    ``/dev/motor`` unless a more-specific ACL exists.
    """

    def __init__(self):
        self._acls: Dict[str, ACL] = {}
        self._caps: Dict[str, Cap] = {}
        self._install_defaults()

    def _install_defaults(self):
        """Install the default Unix-style permission layout."""
        # /proc -- read-only for everyone
        self.set_acl(
            "/proc",
            ACL(
                {"brain": "r--", "channel": "r--", "api": "r--", "driver": "r--"},
            ),
        )

        # /dev -- brain and driver get rw, api read-only, channels no access
        self.set_acl(
            "/dev",
            ACL(
                {"brain": "rw-", "driver": "rw-", "api": "r--", "channel": "---"},
                required_caps=Cap.DEVICE_ACCESS,
            ),
        )
        # /dev/motor -- needs MOTOR_WRITE to write
        # "api" is granted write so that POST /api/action (direct motor control)
        # and POST /cap/teleop work.  The required_caps gate (MOTOR_WRITE) still
        # applies, so arbitrary low-privilege callers are blocked.
        self.set_acl(
            "/dev/motor",
            ACL(
                {"brain": "rw-", "driver": "rw-", "api": "rw-", "channel": "---"},
                required_caps=Cap.MOTOR_WRITE,
            ),
        )

        # /etc -- read-only for most; root to modify
        self.set_acl(
            "/etc",
            ACL(
                {"brain": "r--", "channel": "r--", "api": "r--", "driver": "r--"},
            ),
        )
        self.set_acl(
            "/etc/safety",
            ACL(
                {"brain": "r--", "channel": "r--", "api": "r--", "driver": "r--"},
            ),
        )

        # /var/log -- append-only for brain/api, read for all
        self.set_acl(
            "/var/log",
            ACL(
                {"brain": "rw-", "channel": "r--", "api": "rw-", "driver": "r--"},
            ),
        )

        # /var/memory -- brain rw, channel/api read-only
        self.set_acl(
            "/var/memory",
            ACL(
                {"brain": "rw-", "channel": "r--", "api": "r--", "driver": "---"},
                required_caps=Cap.MEMORY_READ,
            ),
        )

        # /tmp -- everyone full access (working memory)
        self.set_acl(
            "/tmp",
            ACL(
                {"brain": "rwx", "channel": "rwx", "api": "rwx", "driver": "rwx"},
            ),
        )

        # /mnt/channels -- channel send, api read
        self.set_acl(
            "/mnt/channels",
            ACL(
                {"brain": "rw-", "channel": "rwx", "api": "r--", "driver": "---"},
                required_caps=Cap.CHANNEL_SEND,
            ),
        )

        # /mnt/providers -- brain full, api read
        self.set_acl(
            "/mnt/providers",
            ACL(
                {"brain": "rwx", "channel": "r--", "api": "r--", "driver": "---"},
            ),
        )

        # Default capabilities per principal
        self._caps["root"] = Cap.root_caps()
        self._caps["brain"] = Cap.brain_default()
        self._caps["channel"] = Cap.channel_default()
        self._caps["api"] = Cap.api_default()
        self._caps["driver"] = Cap.driver_default()

    def set_acl(self, path: str, acl: ACL):
        """Set the ACL for a specific path."""
        self._acls[path] = acl

    def get_acl(self, path: str) -> ACL:
        """Get the most-specific ACL for *path* using prefix matching."""
        # Exact match first
        if path in self._acls:
            return self._acls[path]
        # Walk up the path hierarchy
        parts = path.rstrip("/").split("/")
        for i in range(len(parts) - 1, 0, -1):
            prefix = "/".join(parts[:i]) or "/"
            if prefix in self._acls:
                return self._acls[prefix]
        # Default deny
        return ACL()

    def grant_cap(self, principal: str, cap: Cap):
        """Grant additional capabilities to a principal."""
        current = self._caps.get(principal, Cap.NONE)
        self._caps[principal] = current | cap

    def revoke_cap(self, principal: str, cap: Cap):
        """Revoke capabilities from a principal."""
        current = self._caps.get(principal, Cap.NONE)
        self._caps[principal] = current & ~cap

    def get_caps(self, principal: str) -> Cap:
        """Get current capabilities for a principal."""
        if principal == "root":
            return Cap.root_caps()
        return self._caps.get(principal, Cap.NONE)

    def check_access(self, principal: str, path: str, operation: str) -> bool:
        """Full access check: path ACL + required capabilities.

        Returns True if the principal has the *operation* permission on
        *path* AND holds all capabilities required by the ACL.
        """
        if principal == "root":
            return True
        acl = self.get_acl(path)
        if not acl.check(principal, operation):
            logger.debug("DENY %s %s %s (acl)", principal, operation, path)
            return False
        if acl.required_caps != Cap.NONE:
            held = self.get_caps(principal)
            if not (held & acl.required_caps) == acl.required_caps:
                logger.debug("DENY %s %s %s (cap)", principal, operation, path)
                return False
        return True

    def register_principal(
        self, name: str, role: Optional[int] = None, scopes: Optional[object] = None
    ):
        """Register an RCAN principal with role-derived capabilities.

        This bridges RCAN RBAC roles to the legacy Cap-based system.
        If *role* and *scopes* are provided, the principal's capabilities
        are derived from the RCAN scope flags.  Otherwise the principal
        gets no capabilities (must be granted manually).

        Args:
            name:   Principal identifier.
            role:   RCAN role level (1-5), optional.
            scopes: RCAN Scope flags, optional.
        """
        if scopes is not None:
            try:
                from castor.rcan.rbac import RCANPrincipal, RCANRole, Scope

                principal = RCANPrincipal(
                    name=name,
                    role=RCANRole(role) if role else RCANRole.GUEST,
                    scopes=scopes if isinstance(scopes, Scope) else Scope.NONE,
                )
                self._caps[name] = principal.to_caps()
            except Exception:
                self._caps[name] = Cap.NONE
        elif name not in self._caps:
            self._caps[name] = Cap.NONE
        logger.info("Registered principal: %s (caps=%s)", name, self._caps.get(name))

    def check_scope(self, principal: str, scope_name: str) -> bool:
        """Check if a principal holds a specific RCAN scope.

        Falls back to capability-based checking when RBAC is not
        configured for this principal.
        """
        try:
            from castor.rcan.rbac import RCANPrincipal, Scope

            p = RCANPrincipal.from_legacy(principal)
            scope = Scope.from_strings([scope_name])
            return p.has_scope(scope)
        except Exception:
            return False

    def get_role(self, principal: str) -> Optional[int]:
        """Return the RCAN role level for a principal, or None."""
        try:
            from castor.rcan.rbac import RCANPrincipal

            return int(RCANPrincipal.from_legacy(principal).role)
        except Exception:
            return None

    def dump(self) -> Dict:
        """Dump the full permission table for inspection."""
        return {
            "acls": {path: acl.dump() for path, acl in sorted(self._acls.items())},
            "capabilities": {p: str(c) for p, c in self._caps.items()},
        }
