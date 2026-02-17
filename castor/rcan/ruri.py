"""
RCAN Uniform Resource Identifier (RURI).

A RURI uniquely addresses a robot, subsystem, or capability on a network::

    rcan://manufacturer.model.uuid/capability

Examples::

    rcan://opencastor.rover.abc123/nav
    rcan://opencastor.arm.def456/teleop
    rcan://*.*.*/status              # wildcard -- matches any robot

The scheme is always ``rcan``.  The authority section has three
dot-separated parts: ``manufacturer.model.instance``.  The optional
path identifies a capability (``/nav``, ``/vision``, ``/chat``, etc.).

Wildcards (``*``) are supported in each authority segment for pattern
matching (e.g. discovering all robots from a manufacturer).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

# rcan://manufacturer.model.instance[/capability]
_RURI_RE = re.compile(
    r"^rcan://"
    r"(?P<manufacturer>[a-zA-Z0-9_*-]+)"
    r"\.(?P<model>[a-zA-Z0-9_*-]+)"
    r"\.(?P<instance>[a-zA-Z0-9_*-]+)"
    r"(?:/(?P<capability>[a-zA-Z0-9_/-]*))?$"
)


@dataclass(frozen=True)
class RURI:
    """Parsed RCAN Uniform Resource Identifier.

    Attributes:
        manufacturer:  Manufacturer or org identifier.
        model:         Robot model name.
        instance:      Unique instance identifier (typically short UUID).
        capability:    Optional capability path (e.g. ``nav``, ``vision``).
    """

    manufacturer: str
    model: str
    instance: str
    capability: Optional[str] = field(default=None)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------
    @classmethod
    def parse(cls, uri: str) -> RURI:
        """Parse a RURI string into a :class:`RURI` object.

        Raises:
            ValueError: If the string is not a valid RURI.
        """
        m = _RURI_RE.match(uri.strip())
        if not m:
            raise ValueError(f"Invalid RURI: {uri!r}")
        cap = m.group("capability") or None
        return cls(
            manufacturer=m.group("manufacturer"),
            model=m.group("model"),
            instance=m.group("instance"),
            capability=cap if cap else None,
        )

    @classmethod
    def from_config(cls, config: dict) -> RURI:
        """Auto-generate a RURI from an RCAN config dict.

        Resolution order:
            1. ``metadata.ruri`` -- explicit RURI string in config.
            2. Constructed from ``metadata.manufacturer``, ``metadata.model``,
               and ``metadata.robot_uuid``.
            3. Falls back to ``opencastor.<robot_name>.<short_uuid>``.
        """
        meta = config.get("metadata", {})

        # 1. Explicit RURI in config
        if meta.get("ruri"):
            return cls.parse(meta["ruri"])

        # 2. Structured fields
        manufacturer = meta.get("manufacturer", "opencastor")
        model = meta.get("model", _safe_id(meta.get("robot_name", "robot")))
        raw_uuid = meta.get("robot_uuid", str(uuid.uuid4()))
        instance = _short_uuid(raw_uuid)

        return cls(manufacturer=manufacturer, model=model, instance=instance)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------
    def __str__(self) -> str:
        base = f"rcan://{self.manufacturer}.{self.model}.{self.instance}"
        if self.capability:
            return f"{base}/{self.capability}"
        return base

    @property
    def base(self) -> str:
        """Return the RURI without the capability path."""
        return f"rcan://{self.manufacturer}.{self.model}.{self.instance}"

    def with_capability(self, capability: str) -> RURI:
        """Return a new RURI with a different capability path."""
        return RURI(
            manufacturer=self.manufacturer,
            model=self.model,
            instance=self.instance,
            capability=capability,
        )

    # ------------------------------------------------------------------
    # Pattern matching
    # ------------------------------------------------------------------
    def matches(self, pattern: RURI) -> bool:
        """Check if this RURI matches a pattern RURI (with wildcards).

        Each segment of *pattern* can be ``*`` to match any value.
        If pattern has no capability, it matches any capability.
        """
        if pattern.manufacturer != "*" and pattern.manufacturer != self.manufacturer:
            return False
        if pattern.model != "*" and pattern.model != self.model:
            return False
        if pattern.instance != "*" and pattern.instance != self.instance:
            return False
        if pattern.capability is not None:
            if pattern.capability != "*" and pattern.capability != self.capability:
                return False
        return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _safe_id(name: str) -> str:
    """Convert a human-readable name to a RURI-safe identifier."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower().strip("_") or "robot"


def _short_uuid(raw: str) -> str:
    """Extract a short (8-char) identifier from a UUID string."""
    return raw.replace("-", "")[:8]
