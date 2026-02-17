"""
OpenCastor Virtual Filesystem -- Safety Enforcement.

Sits between the caller and the namespace, enforcing:

1. **Permission checks** -- rwx ACL + capability gates.
2. **Rate limiting** -- Prevents motor command flooding.
3. **Value clamping** -- Physical safety bounds on motor outputs.
4. **Audit logging** -- Every write and denied access is recorded.
5. **Lockout** -- Repeated violations trigger temporary lockout.
6. **Emergency stop** -- Immediate halt through any principal with CAP_ESTOP.

The SafetyLayer wraps a :class:`~castor.fs.namespace.Namespace` and a
:class:`~castor.fs.permissions.PermissionTable`, providing the same
read/write/ls API but with enforcement.
"""

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from castor.fs.namespace import Namespace
from castor.fs.permissions import Cap, PermissionTable
from castor.safety.anti_subversion import scan_before_write as _scan_before_write
from castor.safety.bounds import BoundsChecker, check_write_bounds

logger = logging.getLogger("OpenCastor.FS.Safety")

# -----------------------------------------------------------------------
# Default safety limits (can be overridden via /etc/safety/limits)
# -----------------------------------------------------------------------
DEFAULT_LIMITS = {
    "motor_linear_range": (-1.0, 1.0),
    "motor_angular_range": (-1.0, 1.0),
    "motor_rate_hz": 20.0,  # Max motor commands per second
    "max_violations_before_lockout": 5,
    "lockout_duration_s": 30.0,
    "audit_ring_size": 1000,  # Max audit log entries before rotation
}

# -----------------------------------------------------------------------
# Safety policy definitions
# -----------------------------------------------------------------------
POLICIES = {
    "clamp_motor": {
        "description": "Clamp motor values to safe physical ranges",
        "enabled": True,
    },
    "rate_limit_motor": {
        "description": "Rate-limit motor commands to prevent flooding",
        "enabled": True,
    },
    "audit_writes": {
        "description": "Log all write operations to /var/log/actions",
        "enabled": True,
    },
    "audit_denials": {
        "description": "Log all access denials to /var/log/safety",
        "enabled": True,
    },
    "lockout_on_violations": {
        "description": "Lock out principals after repeated violations",
        "enabled": True,
    },
}


class SafetyLayer:
    """Permission-enforced, audited, rate-limited filesystem access.

    This is the primary interface for all filesystem operations.  It
    wraps the raw :class:`Namespace` with safety checks.

    Args:
        ns:     The underlying namespace.
        perms:  The permission table.
        limits: Optional dict overriding default safety limits.
    """

    def __init__(self, ns: Namespace, perms: PermissionTable, limits: Optional[Dict] = None):
        self.ns = ns
        self.perms = perms
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._lock = threading.Lock()

        # Rate limiting state
        self._motor_timestamps: List[float] = []

        # Per-role API rate limiting (RCAN Safety Invariant 5)
        self._role_request_timestamps: Dict[str, List[float]] = {}
        self._session_starts: Dict[str, float] = {}

        # Violation tracking per principal
        self._violations: Dict[str, int] = {}
        self._lockouts: Dict[str, float] = {}

        # Emergency stop flag
        self._estop = False

        # Safety telemetry
        from castor.safety.state import SafetyTelemetry

        self._telemetry = SafetyTelemetry()

        # Physical bounds checker
        self._bounds_checker = BoundsChecker.from_virtual_fs(ns)

        # Install safety config into the namespace
        self._install_safety_config()

    def _install_safety_config(self):
        """Populate /etc/safety with current limits and policies."""
        self.ns.mkdir("/etc/safety")
        self.ns.write("/etc/safety/limits", dict(self.limits))
        self.ns.write("/etc/safety/policies", dict(POLICIES))
        self.ns.write("/etc/safety/capabilities", self.perms.dump().get("capabilities", {}))
        self.ns.mkdir("/var/log")
        self.ns.write("/var/log/actions", [])
        self.ns.write("/var/log/safety", [])
        self.ns.write("/var/log/access", [])

        # Safety telemetry -- on-demand via /proc/safety
        self.ns.mkdir("/proc")
        self._update_safety_telemetry()

    def _update_safety_telemetry(self):
        """Write current safety state to ``/proc/safety``."""
        try:
            self.ns.write("/proc/safety", self._telemetry.snapshot_dict(self))
        except Exception as exc:
            logger.debug("Failed to update /proc/safety: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _is_locked_out(self, principal: str) -> bool:
        """Check if a principal is currently locked out."""
        if principal == "root":
            return False
        lockout_until = self._lockouts.get(principal, 0)
        if time.time() < lockout_until:
            return True
        if lockout_until > 0:
            # Lockout expired, clear it
            del self._lockouts[principal]
            self._violations.pop(principal, None)
        return False

    def _record_violation(self, principal: str, path: str, operation: str, reason: str):
        """Record a violation and potentially trigger lockout."""
        if not POLICIES["lockout_on_violations"]["enabled"]:
            return
        with self._lock:
            count = self._violations.get(principal, 0) + 1
            self._violations[principal] = count
            if count >= self.limits["max_violations_before_lockout"]:
                duration = self.limits["lockout_duration_s"]
                self._lockouts[principal] = time.time() + duration
                logger.warning(
                    "LOCKOUT %s for %ss after %d violations",
                    principal,
                    duration,
                    count,
                )
                self._audit_safety(
                    principal, path, "lockout", f"Locked out after {count} violations"
                )

    def _audit_action(self, principal: str, path: str, operation: str, data: Any = None):
        """Append to /var/log/actions."""
        if not POLICIES["audit_writes"]["enabled"]:
            return
        entry = {
            "t": time.time(),
            "who": principal,
            "op": operation,
            "path": path,
        }
        if data is not None:
            entry["data"] = repr(data)[:200]
        self.ns.append("/var/log/actions", entry)
        self._trim_log("/var/log/actions")

    def _audit_safety(self, principal: str, path: str, event: str, detail: str = ""):
        """Append to /var/log/safety."""
        if not POLICIES["audit_denials"]["enabled"]:
            return
        entry = {
            "t": time.time(),
            "who": principal,
            "event": event,
            "path": path,
            "detail": detail,
        }
        self.ns.append("/var/log/safety", entry)
        self._trim_log("/var/log/safety")

    def _audit_access(self, principal: str, path: str, operation: str, granted: bool):
        """Append to /var/log/access."""
        entry = {
            "t": time.time(),
            "who": principal,
            "op": operation,
            "path": path,
            "granted": granted,
        }
        self.ns.append("/var/log/access", entry)
        self._trim_log("/var/log/access")

    def _trim_log(self, path: str):
        """Keep log lists within the configured ring size."""
        data = self.ns.read(path)
        if isinstance(data, list) and len(data) > self.limits["audit_ring_size"]:
            trim = len(data) - self.limits["audit_ring_size"]
            self.ns.write(path, data[trim:])

    def check_role_rate_limit(self, principal: str) -> bool:
        """Enforce per-role RCAN rate limiting (requests per minute).

        Returns True if the request is within the rate limit.
        """
        try:
            from castor.rcan.rbac import RCANPrincipal

            p = RCANPrincipal.from_legacy(principal)
            limit = p.rate_limit
            if limit == 0:  # unlimited
                return True

            now = time.time()
            window = 60.0  # 1-minute window
            with self._lock:
                timestamps = self._role_request_timestamps.get(principal, [])
                timestamps = [t for t in timestamps if now - t < window]
                if len(timestamps) >= limit:
                    self._audit_safety(
                        principal,
                        "/",
                        "role_rate_limited",
                        f"Exceeded {limit} req/min for role {p.role.name}",
                    )
                    return False
                timestamps.append(now)
                self._role_request_timestamps[principal] = timestamps
            return True
        except Exception:
            return True  # Graceful fallback

    def check_session_timeout(self, principal: str) -> bool:
        """Check if a principal's session has expired (Safety Invariant 5).

        Returns True if the session is still valid.
        """
        try:
            from castor.rcan.rbac import RCANPrincipal

            p = RCANPrincipal.from_legacy(principal)
            timeout = p.session_timeout
            if timeout == 0:  # no timeout
                return True

            now = time.time()
            with self._lock:
                start = self._session_starts.get(principal)
                if start is None:
                    self._session_starts[principal] = now
                    return True
                if now - start > timeout:
                    self._audit_safety(
                        principal, "/", "session_timeout", f"Session expired after {timeout}s"
                    )
                    return False
            return True
        except Exception:
            return True  # Graceful fallback

    def reset_session(self, principal: str):
        """Reset the session timer for a principal (e.g. after re-auth)."""
        with self._lock:
            self._session_starts[principal] = time.time()

    def _check_motor_rate(self) -> bool:
        """Enforce motor command rate limiting."""
        if not POLICIES["rate_limit_motor"]["enabled"]:
            return True
        now = time.time()
        max_hz = self.limits["motor_rate_hz"]
        window = 1.0  # 1-second sliding window
        with self._lock:
            self._motor_timestamps = [t for t in self._motor_timestamps if now - t < window]
            if len(self._motor_timestamps) >= max_hz:
                return False
            self._motor_timestamps.append(now)
        return True

    def _clamp_motor_data(self, data: Any) -> Any:
        """Clamp motor command values to safe ranges."""
        if not POLICIES["clamp_motor"]["enabled"]:
            return data
        if not isinstance(data, dict):
            return data
        lin_min, lin_max = self.limits["motor_linear_range"]
        ang_min, ang_max = self.limits["motor_angular_range"]
        clamped = dict(data)
        if "linear" in clamped:
            orig = clamped["linear"]
            clamped["linear"] = max(lin_min, min(lin_max, float(clamped["linear"])))
            if clamped["linear"] != orig:
                logger.info("Clamped motor linear: %s -> %s", orig, clamped["linear"])
        if "angular" in clamped:
            orig = clamped["angular"]
            clamped["angular"] = max(ang_min, min(ang_max, float(clamped["angular"])))
            if clamped["angular"] != orig:
                logger.info("Clamped motor angular: %s -> %s", orig, clamped["angular"])
        return clamped

    # ------------------------------------------------------------------
    # Public API (permission-enforced)
    # ------------------------------------------------------------------
    def read(self, path: str, principal: str = "root") -> Any:
        """Read a file node, checking permissions."""
        if self._is_locked_out(principal):
            logger.warning("READ denied: %s is locked out", principal)
            return None
        if not self.check_role_rate_limit(principal):
            self._audit_safety(principal, path, "role_rate_limited", "rate limit exceeded")
            return None
        if not self.check_session_timeout(principal):
            self._audit_safety(principal, path, "session_expired", "session timed out")
            return None
        if not self.perms.check_access(principal, path, "r"):
            self._audit_access(principal, path, "r", False)
            self._record_violation(principal, path, "r", "permission denied")
            self._audit_safety(principal, path, "deny_read", "permission denied")
            return None
        self._audit_access(principal, path, "r", True)
        return self.ns.read(path)

    def write(
        self, path: str, data: Any, principal: str = "root", meta: Optional[Dict] = None
    ) -> bool:
        """Write to a file node, checking permissions and safety."""
        if self._estop and path.startswith("/dev/motor"):
            logger.warning("WRITE denied: emergency stop active")
            self._audit_safety(principal, path, "deny_estop", "e-stop active, motor writes blocked")
            return False

        if self._is_locked_out(principal):
            logger.warning("WRITE denied: %s is locked out", principal)
            return False

        if not self.check_role_rate_limit(principal):
            self._audit_safety(principal, path, "role_rate_limited", "rate limit exceeded")
            return False
        if not self.check_session_timeout(principal):
            self._audit_safety(principal, path, "session_expired", "session timed out")
            return False

        if not self.perms.check_access(principal, path, "w"):
            self._audit_access(principal, path, "w", False)
            self._record_violation(principal, path, "w", "permission denied")
            self._audit_safety(principal, path, "deny_write", "permission denied")
            return False

        # Anti-subversion scan for AI-generated /dev/ writes
        if path.startswith("/dev/"):
            subversion_result = _scan_before_write(path, data, principal)
            if not subversion_result.ok:
                self._audit_safety(
                    principal,
                    path,
                    "anti_subversion",
                    "; ".join(subversion_result.reasons),
                )
                if subversion_result.verdict.value == "block":
                    return False

        # Physical bounds enforcement for motor and arm paths
        if path.startswith(("/dev/motor", "/dev/arm")):
            bounds_result = check_write_bounds(self._bounds_checker, path, data)
            if bounds_result.violated:
                logger.warning("WRITE denied: bounds violation on %s: %s", path, bounds_result.details)
                self._audit_safety(principal, path, "bounds_violation", bounds_result.details)
                return False
            if bounds_result.status == "warning":
                logger.info("Bounds warning on %s: %s", path, bounds_result.details)
                self._audit_safety(principal, path, "bounds_warning", bounds_result.details)

        # Motor-specific safety enforcement
        if path.startswith("/dev/motor"):
            if not self._check_motor_rate():
                self._audit_safety(principal, path, "rate_limited", "motor command rate exceeded")
                logger.warning("Motor rate limit hit by %s", principal)
                return False
            data = self._clamp_motor_data(data)

        self._audit_action(principal, path, "w", data)
        self._audit_access(principal, path, "w", True)
        return self.ns.write(path, data, meta=meta)

    def append(self, path: str, entry: Any, principal: str = "root") -> bool:
        """Append to a list node, checking permissions."""
        if self._is_locked_out(principal):
            return False
        if not self.check_role_rate_limit(principal):
            self._audit_safety(principal, path, "role_rate_limited", "rate limit exceeded")
            return False
        if not self.check_session_timeout(principal):
            self._audit_safety(principal, path, "session_expired", "session timed out")
            return False
        if not self.perms.check_access(principal, path, "w"):
            self._audit_access(principal, path, "w", False)
            self._record_violation(principal, path, "w", "permission denied")
            return False
        self._audit_access(principal, path, "w", True)
        return self.ns.append(path, entry)

    def ls(self, path: str = "/", principal: str = "root") -> Optional[List[str]]:
        """List directory contents, checking read permission."""
        if self._is_locked_out(principal):
            return None
        if not self.check_role_rate_limit(principal):
            self._audit_safety(principal, path, "role_rate_limited", "rate limit exceeded")
            return None
        if not self.check_session_timeout(principal):
            self._audit_safety(principal, path, "session_expired", "session timed out")
            return None
        if not self.perms.check_access(principal, path, "r"):
            return None
        return self.ns.ls(path)

    def stat(self, path: str, principal: str = "root") -> Optional[Dict]:
        """Stat a node, checking read permission."""
        if self._is_locked_out(principal):
            return None
        if not self.check_role_rate_limit(principal):
            self._audit_safety(principal, path, "role_rate_limited", "rate limit exceeded")
            return None
        if not self.check_session_timeout(principal):
            self._audit_safety(principal, path, "session_expired", "session timed out")
            return None
        if not self.perms.check_access(principal, path, "r"):
            return None
        return self.ns.stat(path)

    def mkdir(self, path: str, principal: str = "root", meta: Optional[Dict] = None) -> bool:
        """Create a directory, checking write permission on parent."""
        if self._is_locked_out(principal):
            return False
        if not self.check_role_rate_limit(principal):
            self._audit_safety(principal, path, "role_rate_limited", "rate limit exceeded")
            return False
        if not self.check_session_timeout(principal):
            self._audit_safety(principal, path, "session_expired", "session timed out")
            return False
        parent = "/".join(path.rstrip("/").split("/")[:-1]) or "/"
        if not self.perms.check_access(principal, parent, "w"):
            self._record_violation(principal, path, "w", "mkdir denied")
            return False
        return self.ns.mkdir(path, meta=meta)

    def exists(self, path: str) -> bool:
        """Check existence (no permission check -- like stat on /proc)."""
        return self.ns.exists(path)

    # ------------------------------------------------------------------
    # Emergency stop
    # ------------------------------------------------------------------
    def estop(self, principal: str = "root") -> bool:
        """Trigger emergency stop. Requires CAP_ESTOP."""
        caps = self.perms.get_caps(principal)
        if not (caps & Cap.ESTOP) and principal != "root":
            self._audit_safety(principal, "/dev/motor", "deny_estop", "missing CAP_ESTOP")
            return False
        self._estop = True
        self.ns.write("/proc/status", "estop")
        self._audit_safety(principal, "/dev/motor", "estop", "emergency stop activated")
        logger.warning("EMERGENCY STOP activated by %s", principal)
        return True

    def clear_estop(self, principal: str = "root", auth_code: Optional[str] = None) -> bool:
        """Clear emergency stop. Requires root or CAP_SAFETY_OVERRIDE.

        If the ``OPENCASTOR_ESTOP_AUTH`` environment variable is set, the
        caller must supply a matching *auth_code* to authorise the clear.
        """
        if principal != "root":
            caps = self.perms.get_caps(principal)
            if not (caps & Cap.SAFETY_OVERRIDE):
                self._audit_safety(
                    principal, "/dev/motor", "deny_clear_estop", "missing CAP_SAFETY_OVERRIDE"
                )
                return False

        required_code = os.environ.get("OPENCASTOR_ESTOP_AUTH")
        if required_code:
            if auth_code != required_code:
                self._audit_safety(
                    principal,
                    "/dev/motor",
                    "deny_clear_estop",
                    "invalid or missing auth code",
                )
                logger.warning("clear_estop denied for %s: bad auth code", principal)
                return False

        self._estop = False
        self.ns.write("/proc/status", "active")
        self._audit_safety(principal, "/dev/motor", "clear_estop", "emergency stop cleared")
        logger.info("Emergency stop cleared by %s", principal)
        return True

    @property
    def is_estopped(self) -> bool:
        return self._estop

    # ------------------------------------------------------------------
    # Policy management
    # ------------------------------------------------------------------
    def set_policy(self, name: str, enabled: bool, principal: str = "root") -> bool:
        """Enable or disable a safety policy.  Requires root."""
        if principal != "root":
            self._audit_safety(
                principal,
                "/etc/safety/policies",
                "deny_policy",
                f"only root can modify policy {name}",
            )
            return False
        if name in POLICIES:
            POLICIES[name]["enabled"] = enabled
            self.ns.write("/etc/safety/policies", dict(POLICIES))
            logger.info("Policy %s set to %s", name, enabled)
            return True
        return False
