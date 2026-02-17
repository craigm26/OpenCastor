"""Work authorization module for destructive actions.

Provides work-order-based authorization for dangerous operations like
cutting, welding, grinding, etc. All destructive actions require explicit
approval from a CREATOR or OWNER principal before execution.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Roles with authorization privileges (ordered by power)
AUTHORIZED_ROLES = ("CREATOR", "OWNER")

# Default destructive action types
DESTRUCTIVE_ACTION_TYPES = frozenset(
    {
        "demolish",
        "cut",
        "burn",
        "drill",
        "grind",
        "weld",
        "compress",
        "dissolve",
    }
)

# Default destructive path patterns
_DEFAULT_DESTRUCTIVE_PATTERNS: list[str] = [
    r"^/dev/gpio/.*",  # GPIO pins (cutting/heating tools)
    r".*/motor[_/].*speed\s*[:=]\s*(\d+)",  # Motor commands
]

# Default work order TTL: 1 hour
DEFAULT_TTL_SECONDS = 3600.0


@dataclass
class WorkOrder:
    """Represents authorization for a single destructive action."""

    order_id: str
    action_type: str
    target: str
    requested_by: str
    authorized_by: str = ""
    authorized_at: float = 0.0
    expires_at: float = 0.0
    required_role: str = "CREATOR"
    conditions: dict = field(default_factory=dict)
    executed: bool = False
    revoked: bool = False
    created_at: float = field(default_factory=time.time)

    @property
    def is_approved(self) -> bool:
        return bool(self.authorized_by) and not self.revoked

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return self.is_approved and not self.is_expired and not self.executed and not self.revoked


class DestructiveActionDetector:
    """Classifies paths and commands as potentially destructive."""

    def __init__(self, extra_patterns: list[str] | None = None):
        self._patterns: list[re.Pattern[str]] = []
        for p in _DEFAULT_DESTRUCTIVE_PATTERNS:
            self._patterns.append(re.compile(p, re.IGNORECASE))
        if extra_patterns:
            for p in extra_patterns:
                self._patterns.append(re.compile(p, re.IGNORECASE))
        self._load_config_patterns()

    def _load_config_patterns(self) -> None:
        config = Path("/etc/safety/destructive_patterns")
        if config.is_file():
            try:
                for line in config.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self._patterns.append(re.compile(line, re.IGNORECASE))
            except Exception:
                logger.warning("Failed to load destructive patterns from %s", config)

    def is_destructive_path(self, path: str) -> bool:
        for pat in self._patterns:
            if pat.search(path):
                return True
        return False

    def is_destructive_command(self, command: str) -> bool:
        """Check if a command string contains destructive operations."""
        # Motor commands with extreme values (>80% of max)
        motor_match = re.search(r"motor[_/].*speed\s*[:=]\s*(\d+)", command, re.IGNORECASE)
        if motor_match:
            val = int(motor_match.group(1))
            if val > 80:
                return True
        for pat in self._patterns:
            if pat.search(command):
                return True
        return False

    def classify(self, path_or_command: str) -> bool:
        return self.is_destructive_path(path_or_command) or self.is_destructive_command(
            path_or_command
        )


class WorkAuthority:
    """Manages work orders for destructive actions."""

    def __init__(
        self,
        role_resolver: dict[str, str] | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
        detector: DestructiveActionDetector | None = None,
    ):
        self._orders: dict[str, WorkOrder] = {}
        # Maps principal -> role (e.g. {"alice": "CREATOR", "bob": "OPERATOR"})
        self._roles: dict[str, str] = role_resolver or {}
        self._ttl = ttl
        self._audit_log: list[dict] = []
        self.detector = detector or DestructiveActionDetector()

    def _audit(self, event: str, **kwargs: object) -> None:
        entry = {"event": event, "timestamp": time.time(), **kwargs}
        self._audit_log.append(entry)
        logger.info("AUDIT: %s", json.dumps(entry, default=str))

    def _get_role(self, principal: str) -> str:
        return self._roles.get(principal, "NONE")

    def _can_approve(self, principal: str) -> bool:
        return self._get_role(principal) in AUTHORIZED_ROLES

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [
            oid
            for oid, wo in self._orders.items()
            if wo.expires_at > 0 and now > wo.expires_at and not wo.executed
        ]
        for oid in expired:
            self._orders[oid].revoked = True
            self._audit("auto_expired", order_id=oid)

    def request_authorization(
        self,
        action_type: str,
        target: str,
        principal: str,
        required_role: str = "CREATOR",
        conditions: dict | None = None,
    ) -> WorkOrder:
        if required_role not in AUTHORIZED_ROLES:
            raise ValueError(f"required_role must be one of {AUTHORIZED_ROLES}")

        order = WorkOrder(
            order_id=str(uuid.uuid4()),
            action_type=action_type,
            target=target,
            requested_by=principal,
            required_role=required_role,
            conditions=conditions or {},
        )
        self._orders[order.order_id] = order
        self._audit(
            "requested",
            order_id=order.order_id,
            action_type=action_type,
            target=target,
            principal=principal,
        )
        return order

    def approve(self, order_id: str, principal: str) -> bool:
        self._cleanup_expired()
        order = self._orders.get(order_id)
        if not order:
            self._audit(
                "approve_failed", order_id=order_id, reason="not_found", principal=principal
            )
            return False

        if order.revoked:
            self._audit("approve_failed", order_id=order_id, reason="revoked", principal=principal)
            return False

        if order.is_approved:
            self._audit(
                "approve_failed", order_id=order_id, reason="already_approved", principal=principal
            )
            return False

        # Role check: principal must have the required role or higher
        principal_role = self._get_role(principal)
        if principal_role not in AUTHORIZED_ROLES:
            self._audit(
                "approve_denied",
                order_id=order_id,
                principal=principal,
                role=principal_role,
                required=order.required_role,
            )
            return False

        # OWNER cannot approve CREATOR-only orders
        if order.required_role == "CREATOR" and principal_role != "CREATOR":
            self._audit(
                "approve_denied",
                order_id=order_id,
                principal=principal,
                role=principal_role,
                required="CREATOR",
            )
            return False

        # Prevent self-approval: requester cannot approve their own order
        if order.requested_by == principal:
            self._audit(
                "approve_denied",
                order_id=order_id,
                principal=principal,
                reason="self_approval",
            )
            return False

        now = time.time()
        order.authorized_by = principal
        order.authorized_at = now
        order.expires_at = now + self._ttl
        self._audit("approved", order_id=order_id, principal=principal)
        return True

    def check_authorization(self, action_type: str, target: str) -> Optional[WorkOrder]:
        self._cleanup_expired()
        for order in self._orders.values():
            if order.action_type == action_type and order.target == target and order.is_valid:
                return order
        return None

    def mark_executed(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if not order or not order.is_valid:
            return False
        order.executed = True
        self._audit("executed", order_id=order_id)
        return True

    def revoke(self, order_id: str, principal: str) -> bool:
        order = self._orders.get(order_id)
        if not order:
            self._audit("revoke_failed", order_id=order_id, reason="not_found", principal=principal)
            return False

        if not self._can_approve(principal):
            self._audit("revoke_denied", order_id=order_id, principal=principal)
            return False

        order.revoked = True
        self._audit("revoked", order_id=order_id, principal=principal)
        return True

    def list_pending(self) -> list[WorkOrder]:
        self._cleanup_expired()
        return [wo for wo in self._orders.values() if not wo.is_approved and not wo.revoked]

    def list_active(self) -> list[WorkOrder]:
        self._cleanup_expired()
        return [wo for wo in self._orders.values() if wo.is_valid]

    def get_audit_log(self) -> list[dict]:
        return list(self._audit_log)

    def requires_authorization(self, path_or_command: str) -> bool:
        return self.detector.classify(path_or_command)
