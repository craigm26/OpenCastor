"""
castor.rcan.config_safety — Config update safety for RCAN v1.5.

Enforces scope requirements for CONFIG_UPDATE commands sent via RCAN,
with:
  - control scope required for all config updates (not just chat)
  - safety scope required for dangerous fields (brain.provider, safety.*, hardware.*)
  - 5-minute rollback snapshot window

This implements RCAN v1.5 GAP-07 (Config Update Authorization).

Usage::

    from castor.rcan.config_safety import validate_config_update, ConfigUpdateRequest

    req = ConfigUpdateRequest(
        scope="control",
        fields={"agent.model": "gemini-2.5-flash"},
        requester="rrn://my-owner",
    )
    ok, reason, requires_safety = validate_config_update(req)
    if not ok:
        raise PermissionError(reason)

Spec: RCAN v1.5 §11.3 — Config Update Authorization (GAP-07)
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# Dangerous fields that require safety scope (not just control)
# These are prefixes — any key starting with one of these requires safety scope.
_SAFETY_REQUIRED_PREFIXES: tuple[str, ...] = (
    "safety.",
    "safety",  # exact match
    "hardware.",
    "hardware",
    "brain.provider",
    "agent.provider",
)

# Fields that are outright forbidden via RCAN CONFIG_UPDATE
_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "firebase_uid",
        "api_token",
        "credentials",
    }
)

# How long to keep the config rollback snapshot (seconds)
ROLLBACK_WINDOW_S: int = 300  # 5 minutes


def _is_dangerous_field(key: str) -> bool:
    """Return True if *key* requires safety scope for modification."""
    k = key.strip().lower()
    for prefix in _SAFETY_REQUIRED_PREFIXES:
        if k == prefix or k.startswith(prefix + "."):
            return True
    return False


@dataclass
class ConfigUpdateRequest:
    """A request to update the robot config via RCAN.

    Attributes:
        scope:       Scope of the requesting principal.
        fields:      Dict of field_path → new_value to apply.
        requester:   Identity of the requester (owner RRN or UID).
        cmd_id:      Optional command ID for audit trail.
    """

    scope: str
    fields: dict[str, Any]
    requester: str = ""
    cmd_id: str = ""


@dataclass
class ConfigSnapshot:
    """Snapshot of config state for rollback.

    Attributes:
        config:      Deep copy of the config at snapshot time.
        snapshot_at: Unix timestamp when snapshot was taken.
        cmd_id:      Command ID that triggered the update.
    """

    config: dict[str, Any]
    snapshot_at: float = field(default_factory=time.time)
    cmd_id: str = ""

    @property
    def is_expired(self) -> bool:
        """Return True if this snapshot is older than ROLLBACK_WINDOW_S."""
        return (time.time() - self.snapshot_at) > ROLLBACK_WINDOW_S

    @property
    def age_s(self) -> float:
        return time.time() - self.snapshot_at


def validate_config_update(
    req: ConfigUpdateRequest,
) -> tuple[bool, str, bool]:
    """Validate a CONFIG_UPDATE request.

    Args:
        req: The config update request.

    Returns:
        Tuple of (allowed, reason, requires_safety_scope):
          - allowed:               True if the update can proceed.
          - reason:                Human-readable reason string.
          - requires_safety_scope: True if the update contains dangerous fields.

    Scope requirements:
        - All CONFIG_UPDATE commands require at minimum ``control`` scope.
        - Dangerous fields (brain.provider, safety.*, hardware.*) require
          ``safety`` scope.
        - ``chat`` scope is never sufficient for config updates.
    """
    # chat scope is never sufficient
    if req.scope == "chat":
        return (
            False,
            "CONFIG_UPDATE requires 'control' scope minimum — 'chat' scope is insufficient",
            False,
        )

    # Check for forbidden fields
    forbidden = [k for k in req.fields if k in _FORBIDDEN_FIELDS]
    if forbidden:
        return (
            False,
            f"CONFIG_UPDATE: forbidden fields cannot be updated via RCAN: {forbidden}",
            False,
        )

    # Identify dangerous fields
    dangerous = [k for k in req.fields if _is_dangerous_field(k)]
    requires_safety = len(dangerous) > 0

    if requires_safety:
        if req.scope != "safety":
            return (
                False,
                (
                    f"CONFIG_UPDATE: fields {dangerous} require 'safety' scope "
                    f"— got '{req.scope}'. "
                    "Dangerous fields: brain.provider, safety.*, hardware.*"
                ),
                True,
            )
        log.info(
            "config_safety: safety-scope CONFIG_UPDATE approved for dangerous fields %s "
            "(requester=%s, cmd_id=%s)",
            dangerous,
            req.requester,
            req.cmd_id,
        )
        return True, "approved (safety scope)", True

    # control scope is sufficient for non-dangerous fields
    if req.scope not in ("control", "safety", "admin"):
        return (
            False,
            f"CONFIG_UPDATE requires 'control' scope — got '{req.scope}'",
            False,
        )

    log.info(
        "config_safety: CONFIG_UPDATE approved for fields %s (scope=%s, requester=%s, cmd_id=%s)",
        list(req.fields.keys()),
        req.scope,
        req.requester,
        req.cmd_id,
    )
    return True, "approved", False


class ConfigRollbackManager:
    """Manages a 5-minute rollback snapshot for config updates.

    Usage::

        mgr = ConfigRollbackManager()
        mgr.take_snapshot(current_config, cmd_id="abc-123")
        # ... apply update ...
        # If update goes wrong:
        restored = mgr.rollback()

    Thread-safe (uses a simple lock).
    """

    def __init__(self) -> None:
        self._snapshot: Optional[ConfigSnapshot] = None
        import threading

        self._lock = threading.Lock()

    def take_snapshot(self, config: dict[str, Any], cmd_id: str = "") -> ConfigSnapshot:
        """Take a deep-copy snapshot of *config*.

        Args:
            config: Current config dict (deep-copied for snapshot).
            cmd_id: Optional command ID for audit.

        Returns:
            The created snapshot.
        """
        snapshot = ConfigSnapshot(
            config=copy.deepcopy(config),
            cmd_id=cmd_id,
        )
        with self._lock:
            self._snapshot = snapshot
        log.debug(
            "config_safety: snapshot taken (cmd_id=%s, fields=%d)",
            cmd_id,
            len(config),
        )
        return snapshot

    def rollback(self) -> Optional[dict[str, Any]]:
        """Restore the most recent snapshot if still within the rollback window.

        Returns:
            Deep copy of the snapshot config, or None if no snapshot or expired.
        """
        with self._lock:
            snap = self._snapshot

        if snap is None:
            log.warning("config_safety: rollback requested but no snapshot available")
            return None

        if snap.is_expired:
            log.warning(
                "config_safety: rollback snapshot expired (age=%.0fs > %ds) — cannot restore",
                snap.age_s,
                ROLLBACK_WINDOW_S,
            )
            return None

        log.info(
            "config_safety: rolling back to snapshot (age=%.0fs, cmd_id=%s)",
            snap.age_s,
            snap.cmd_id,
        )
        return copy.deepcopy(snap.config)

    def has_valid_snapshot(self) -> bool:
        """Return True if a non-expired snapshot exists."""
        with self._lock:
            snap = self._snapshot
        return snap is not None and not snap.is_expired

    def clear(self) -> None:
        """Clear the snapshot (e.g. after a successful stabilization period)."""
        with self._lock:
            self._snapshot = None

    @property
    def snapshot_age_s(self) -> Optional[float]:
        """Age of the current snapshot in seconds, or None if no snapshot."""
        with self._lock:
            snap = self._snapshot
        return snap.age_s if snap else None
