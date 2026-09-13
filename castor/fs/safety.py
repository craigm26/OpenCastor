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

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

from castor.fs.namespace import Namespace
from castor.fs.permissions import Cap, PermissionTable
from castor.rcan.rbac import CapabilityBroker, Scope
from castor.safety.anti_subversion import scan_before_write as _scan_before_write
from castor.safety.bounds import BoundsChecker, check_write_bounds
from castor.safety.protocol import check_write_protocol

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

    def __init__(
        self,
        ns: Namespace,
        perms: PermissionTable,
        limits: Optional[dict] = None,
        capability_broker: Optional[CapabilityBroker] = None,
    ):
        self.ns = ns
        self.perms = perms
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._lock = threading.Lock()
        self.capability_broker = capability_broker

        # Rate limiting state
        self._motor_timestamps: list[float] = []

        # Per-role API rate limiting (RCAN Safety Invariant 5)
        self._role_request_timestamps: dict[str, list[float]] = {}
        self._session_starts: dict[str, float] = {}

        # Violation tracking per principal
        self._violations: dict[str, int] = {}
        self._lockouts: dict[str, float] = {}

        # Emergency stop flag. RESTORED FROM DISK, not defaulted to False: a
        # robot that e-stopped and was then restarted by systemd twelve seconds
        # later used to come back free, because the only record of the stop was
        # a bool in the process that died. castor/safety/latch.py reads the
        # latch out of ROBOT_HOME, which every generated unit already exports;
        # with ROBOT_HOME unset (a test, a notebook) there is no file and this
        # is the old behaviour exactly.
        self._estop = False
        self._estop_source = ""
        #: A pause is a person standing the robot down: not an e-stop, but it
        #: blocks motor writes exactly the same way. Restored from the same
        #: latch, lifted by `castor resume`.
        self._paused = False
        self._pause_detail = ""
        try:
            from castor.safety.latch import load as _load_latch

            _latch = _load_latch()
            if _latch.estop_engaged:
                self._estop = True
                self._estop_source = _latch.estop_source or "unknown"
                logger.warning(
                    "EMERGENCY STOP restored from the persisted latch: %s", _latch.describe()
                )
            if _latch.paused:
                self._paused = True
                self._pause_detail = _latch.describe()
                logger.warning("PAUSE restored from the persisted latch: %s", _latch.describe())
        except Exception as exc:  # noqa: BLE001 - a boot must not die on this
            logger.warning("Safety latch could not be read: %s", exc)

        # Per-principal session-expiry stop flags (separate from full estop)
        self._session_expired_stops: set[str] = set()

        # Last write denial reason (set on every return False in write())
        self._last_write_denial: str = ""

        # Safety telemetry. enable_persistence() had no caller anywhere in the
        # runtime, so the rolling history it writes never existed and an
        # operator asking "what was the safety state an hour ago" had nothing
        # to read. It is a ROTATING RING, not an archive: _rotate_if_needed
        # trims the file back to MAX_LOG_LINES, so old snapshots are dropped.
        from castor.safety.state import SafetyTelemetry

        self._telemetry = SafetyTelemetry()
        try:
            self._telemetry.enable_persistence()
        except Exception as exc:
            logger.warning("Safety telemetry persistence unavailable: %s", exc)

        # Physical bounds checker
        self._bounds_checker = BoundsChecker.from_virtual_fs(ns)

        # Safety protocol engine
        from castor.safety.protocol import SafetyProtocol

        self._protocol = SafetyProtocol(ns=ns)

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

    def _audit_action(
        self,
        principal: str,
        path: str,
        operation: str,
        data: Any = None,
        source: str = "local",
    ):
        """Append to /var/log/actions."""
        if not POLICIES["audit_writes"]["enabled"]:
            return
        entry = {
            "t": time.time(),
            "who": principal,
            "op": operation,
            "path": path,
            "source": source,
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
        When a CONTROL-scoped principal's session expires, triggers a
        session-expiry stop (RCAN spec §6).
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
                    # Trigger controlled stop for CONTROL-capable principals (RCAN §6)
                    try:
                        from castor.rcan.rbac import Scope

                        if p.scopes & Scope.CONTROL:
                            self._trigger_session_expiry_stop(principal)
                    except Exception:
                        pass
                    return False
            return True
        except Exception:
            return True  # Graceful fallback

    def reset_session(self, principal: str):
        """Reset the session timer for a principal (e.g. after re-auth).

        Also clears any session-expiry stop so the re-authenticated principal
        can issue CONTROL commands again.
        """
        with self._lock:
            self._session_starts[principal] = time.time()
            self._session_expired_stops.discard(principal)

    def _trigger_session_expiry_stop(self, principal: str) -> None:
        """Flag a session-expiry stop for *principal* (RCAN spec §6).

        Less severe than estop(): self._estop is NOT set, so a re-authenticated
        principal can immediately resume.  The flag is cleared by reset_session().
        """
        self._session_expired_stops.add(principal)
        try:
            self.ns.write("/proc/status", "session_stop")
        except Exception:
            pass
        self._audit_safety(
            principal,
            "/",
            "session_expiry_stop",
            f"Session expired for CONTROL principal '{principal}'"
            " — motor commands blocked until re-auth",
        )
        logger.warning(
            "SESSION EXPIRY STOP for %s — motor commands blocked until re-auth", principal
        )
        # Send zero-velocity stop to motor controller
        try:
            import json
            import os

            motor_path = "/dev/motor"
            if os.path.exists(motor_path):
                with open(motor_path, "w") as _mf:
                    _mf.write(
                        json.dumps(
                            {"velocity": 0.0, "angular": 0.0, "source": "session_expiry_stop"}
                        )
                    )
                self._audit_action(
                    principal, motor_path, "session_expiry_zero_velocity", source="local"
                )
        except Exception as _e:
            logger.warning(f"session expiry zero-velocity write failed: {_e}")

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

    def _required_scope_for_path(self, path: str) -> Scope:
        if path.startswith("/etc/"):
            return Scope.CONFIG
        if path.startswith(("/mem", "/ctx")):
            return Scope.TRAINING
        if path.startswith("/dev/"):
            return Scope.CONTROL
        return Scope.STATUS

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
        self,
        path: str,
        data: Any,
        principal: str = "root",
        meta: Optional[dict] = None,
        source: str = "local",
    ) -> bool:
        """Write to a file node, checking permissions and safety."""
        if self._estop and path.startswith("/dev/motor"):
            logger.warning("WRITE denied: emergency stop active")
            self._audit_safety(principal, path, "deny_estop", "e-stop active, motor writes blocked")
            self._last_write_denial = "Emergency stop is active. POST /api/estop/clear to resume."
            return False

        # A PAUSE IS NOT AN E-STOP, and it still has to stop the robot moving.
        # `castor pause` is a person standing the robot down; it does not latch
        # the e-stop (nothing is wrong, and lifting it must not need the e-stop
        # clear code). But a pause that let the next /api/action through would
        # be a note in a file. Checked here so both the duck's real driver path
        # and the car's mock one refuse, and `castor resume` lifts it.
        if self._paused and path.startswith("/dev/motor"):
            logger.warning("WRITE denied: %s", self._pause_detail or "paused")
            self._audit_safety(principal, path, "deny_paused", self._pause_detail or "paused")
            self._last_write_denial = (
                f"This robot is paused ({self._pause_detail or 'no reason recorded'}). "
                "Run `castor resume` at the robot."
            )
            return False

        if self._is_locked_out(principal):
            logger.warning("WRITE denied: %s is locked out", principal)
            self._last_write_denial = (
                f"Principal '{principal}' is locked out due to repeated violations."
            )
            return False

        if not self.check_role_rate_limit(principal):
            self._audit_safety(principal, path, "role_rate_limited", "rate limit exceeded")
            self._last_write_denial = f"Rate limit exceeded for principal '{principal}'."
            return False
        if not self.check_session_timeout(principal):
            self._audit_safety(principal, path, "session_expired", "session timed out")
            self._last_write_denial = (
                f"Session expired for principal '{principal}'. Re-authenticate to reset."
            )
            return False

        if not self.perms.check_access(principal, path, "w"):
            self._audit_access(principal, path, "w", False)
            self._record_violation(principal, path, "w", "permission denied")
            self._audit_safety(principal, path, "deny_write", "permission denied")
            self._last_write_denial = f"Principal '{principal}' lacks write permission on '{path}'."
            return False

        if self.capability_broker and principal != "root":
            lease_token = (meta or {}).get("lease_token")
            if not lease_token:
                self._audit_safety(principal, path, "deny_lease", "missing capability lease")
                self._last_write_denial = "Missing capability lease token."
                return False
            required_scope = self._required_scope_for_path(path)
            if not self.capability_broker.validate_lease(
                lease_token,
                principal,
                required_scope,
                path,
                path=path,
                data=data,
                intent_context=(meta or {}).get("intent_context"),
            ):
                self._audit_safety(
                    principal, path, "deny_lease", "invalid or expired capability lease"
                )
                self._last_write_denial = "Invalid or expired capability lease."
                return False

        # Confidence gate check for AI-generated /dev/ writes (RCAN spec §16.2)
        if path.startswith("/dev/") and meta and "confidence" in meta:
            try:
                from castor.confidence_gate import ConfidenceGateManager, GateOutcome

                scope_name = self._required_scope_for_path(path).name.lower()
                gate_outcome = ConfidenceGateManager.check(scope_name, meta.get("confidence"))
                if gate_outcome == GateOutcome.BLOCK:
                    confidence_val = meta.get("confidence")
                    self._audit_safety(
                        principal,
                        path,
                        "confidence_gate_block",
                        f"confidence={confidence_val} below threshold for scope={scope_name}",
                    )
                    self._last_write_denial = (
                        f"Confidence gate blocked: confidence={confidence_val} "
                        f"is below the required threshold for scope={scope_name}."
                    )
                    return False
                elif gate_outcome == GateOutcome.ESCALATE:
                    self._audit_safety(
                        principal,
                        path,
                        "confidence_gate_escalate",
                        f"confidence={meta.get('confidence')} escalated for scope={scope_name}",
                    )
            except Exception as exc:
                logger.error("Confidence gate check failed (allowing write): %s", exc)

        # Anti-subversion scan for AI-generated /dev/ writes
        if path.startswith("/dev/"):
            try:
                subversion_result = _scan_before_write(path, data, principal)
                if not subversion_result.ok:
                    self._audit_safety(
                        principal,
                        path,
                        "anti_subversion",
                        "; ".join(subversion_result.reasons),
                    )
                    if subversion_result.verdict.value == "block":
                        self._last_write_denial = (
                            f"Anti-subversion block: {'; '.join(subversion_result.reasons)}"
                        )
                        return False
            except Exception as exc:
                logger.error("Anti-subversion scan failed (allowing write): %s", exc)

        # Physical bounds enforcement for motor and arm paths
        if path.startswith(("/dev/motor", "/dev/arm")):
            try:
                bounds_result = check_write_bounds(self._bounds_checker, path, data)
                if bounds_result.violated:
                    logger.warning(
                        "WRITE denied: bounds violation on %s: %s", path, bounds_result.details
                    )
                    self._audit_safety(principal, path, "bounds_violation", bounds_result.details)
                    self._last_write_denial = f"Bounds violation: {bounds_result.details}"
                    return False
                if bounds_result.status == "warning":
                    logger.info("Bounds warning on %s: %s", path, bounds_result.details)
                    self._audit_safety(principal, path, "bounds_warning", bounds_result.details)
            except Exception as exc:
                logger.error("Bounds check failed (allowing write): %s", exc)

        # Safety protocol rules for /dev/ writes
        if path.startswith("/dev/"):
            try:
                protocol_violations = check_write_protocol(self._protocol, path, data)
                critical = [v for v in protocol_violations if v.severity == "critical"]
                if critical:
                    logger.warning(
                        "WRITE denied: protocol violation on %s: %s",
                        path,
                        critical[0].message,
                    )
                    self._audit_safety(principal, path, "protocol_violation", critical[0].message)
                    self._last_write_denial = f"Safety protocol violation: {critical[0].message}"
                    return False
                for v in protocol_violations:
                    if v.severity == "violation":
                        logger.warning(
                            "WRITE denied: protocol violation on %s: %s", path, v.message
                        )
                        self._audit_safety(principal, path, "protocol_violation", v.message)
                        self._last_write_denial = f"Safety protocol violation: {v.message}"
                        return False
                    if v.severity == "warning":
                        logger.info("Protocol warning on %s: %s", path, v.message)
                        self._audit_safety(principal, path, "protocol_warning", v.message)
            except Exception as exc:
                logger.error("Protocol check failed (allowing write): %s", exc)

        # Motor-specific safety enforcement
        if path.startswith("/dev/motor"):
            if not self._check_motor_rate():
                self._audit_safety(principal, path, "rate_limited", "motor command rate exceeded")
                logger.warning("Motor rate limit hit by %s", principal)
                self._last_write_denial = "Motor command rate limit exceeded."
                return False
            data = self._clamp_motor_data(data)

        self._audit_action(principal, path, "w", data, source=source)
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

    def ls(self, path: str = "/", principal: str = "root") -> Optional[list[str]]:
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

    def stat(self, path: str, principal: str = "root") -> Optional[dict]:
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

    def mkdir(self, path: str, principal: str = "root", meta: Optional[dict] = None) -> bool:
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
    # ------------------------------------------------------------------
    # Source tracking helpers (RCAN §6 — local_safety_wins)
    # ------------------------------------------------------------------
    def _audit_action_sourced(
        self,
        principal: str,
        path: str,
        operation: str,
        data: Any = None,
        source: str = "local",
    ):
        """Write an audit record that includes command source (local|rcan|api|swarm)."""
        if not POLICIES["audit_writes"]["enabled"]:
            return
        entry = {
            "t": time.time(),
            "who": principal,
            "op": operation,
            "path": path,
            "source": source,  # RCAN §6: track command origin
            "local_safety_enforced": True,  # always True — local safety always wins
        }
        if data is not None:
            entry["data"] = repr(data)[:200]
        self.ns.append("/var/log/actions", entry)
        self._trim_log("/var/log/actions")

    def write_remote(
        self, path: str, data: Any, principal: str = "root", meta: Optional[dict] = None
    ) -> bool:
        """Write from a remote RCAN command — runs the same local safety checks.

        This exists to make it architecturally explicit that remote commands
        are NOT trusted more than local ones. All SafetyLayer checks run
        identically; the only difference is the ``source='rcan'`` audit tag.

        RCAN §6 invariant: local safety always wins — no remote command can
        bypass bounds checking, e-stop state, or protocol rules.
        """
        # Re-use the standard write() path with source="rcan" — every check runs
        result = self.write(path, data, principal=principal, meta=meta, source="rcan")
        if not result:
            self._audit_safety(
                principal,
                path,
                "remote_write_denied",
                f"RCAN remote write blocked by local safety: {self._last_write_denial}",
            )
        return result

    def estop(self, principal: str = "root", source: str = "local", reason: str = "") -> bool:
        """Trigger emergency stop. Requires CAP_ESTOP.

        Args:
            principal: Caller identity.
            source:    Command origin — 'local', 'rcan', 'api', 'swarm'.
            reason:    Human-readable explanation for audit trail.
        """
        caps = self.perms.get_caps(principal)
        if not (caps & Cap.ESTOP) and principal != "root":
            self._audit_safety(principal, "/dev/motor", "deny_estop", "missing CAP_ESTOP")
            return False
        self._estop = True
        self._estop_source = source
        self.ns.write("/proc/status", "estop")
        # Persist BEFORE auditing: if this process dies in the next millisecond
        # the next one must still come back stopped. A missing audit row is a
        # gap in the record; a forgotten stop is a robot that moves.
        try:
            from castor.safety.latch import record_estop as _record_estop

            _record_estop(principal=principal, source=source, reason=reason)
        except Exception as exc:  # noqa: BLE001 - never let the stop itself fail
            logger.warning("Safety latch could not be persisted: %s", exc)
        detail = f"ESTOP activated — source={source}" + (f" — {reason}" if reason else "")
        self._audit_safety(principal, "/dev/motor", "estop", detail)
        logger.warning("EMERGENCY STOP [%s] activated by %s: %s", source, principal, reason)
        # The stop has already happened. Filing is strictly after, and strictly
        # best effort, so a log write can never stop a stop (OC-13).
        self._file_incident(
            category="estop",
            description=detail,
            principal=principal,
            source=source,
            reason=reason,
            event_source="estop",
        )
        return True

    def _file_incident(
        self,
        *,
        category: str,
        description: str,
        principal: str,
        source: str,
        reason: str,
        event_source: str,
    ) -> None:
        """File a serious-incident record for a safety event. Never raises.

        Called only AFTER the stop has been applied. Every failure is absorbed
        here so that no incident-log problem can interfere with stopping.
        """
        try:
            from castor.incidents import IncidentSeverity, file_incident

            file_incident(
                severity=IncidentSeverity.SERIOUS_HARM,
                category=category,
                description=description,
                system_state={
                    "principal": principal,
                    "command_source": source,
                    "reason": reason,
                    "estop_active": bool(getattr(self, "_estop", False)),
                },
                source=event_source,
            )
        except Exception as exc:
            logger.error("Could not file %s incident: %s", category, exc)

    def controlled_stop(
        self, principal: str = "root", source: str = "local", reason: str = ""
    ) -> bool:
        """Controlled deceleration stop (RCAN SAFETY STOP — not ESTOP).

        Unlike estop() which cuts actuators immediately, controlled_stop() sets
        a flag that the motion controller should respect to decelerate smoothly
        to rest. The e-stop flag is NOT set, so the robot can resume without
        manual clear. Audit is identical to ESTOP for traceability.

        RCAN §6: STOP = controlled deceleration. ESTOP = immediate actuator cut.
        """
        caps = self.perms.get_caps(principal)
        if not (caps & Cap.ESTOP) and principal != "root":
            self._audit_safety(principal, "/dev/motor", "deny_stop", "missing CAP_ESTOP")
            return False
        self.ns.write("/proc/status", "stopping")
        detail = f"Controlled STOP — source={source}" + (f" — {reason}" if reason else "")
        self._audit_safety(principal, "/dev/motor", "controlled_stop", detail)
        logger.warning("CONTROLLED STOP [%s] by %s: %s", source, principal, reason)
        self._file_incident(
            category="controlled_stop",
            description=detail,
            principal=principal,
            source=source,
            reason=reason,
            event_source="controlled_stop",
        )
        return True

    def clear_estop(
        self,
        principal: str = "root",
        auth_code: Optional[str] = None,
        source: str = "local",
    ) -> bool:
        """Clear emergency stop. Requires root or CAP_SAFETY_OVERRIDE.

        If the ``OPENCASTOR_ESTOP_AUTH`` environment variable is set, the
        caller must supply a matching *auth_code* to authorise the clear.
        ``castor up`` now writes that variable into the generated tokens.env,
        so on a robot built by the ten-minute path the code is always required.

        A latch set by an on-device sensor is NOT clearable from a remote
        source. That rule was already advertised in the docstring of
        ``POST /api/safety/rcan``; until now nothing enforced it. *source* says
        where this clear came from: 'local' is the CLI on the robot's own host,
        and is the only source allowed to lift a sensor latch.
        """
        blocked = ""
        try:
            from castor.safety.latch import clear_blocked_by

            blocked = clear_blocked_by(self._estop_source, source)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sensor-latch rule could not be evaluated: %s", exc)
        if self._estop and blocked:
            self._audit_safety(
                principal,
                "/dev/motor",
                "deny_clear_estop",
                f"latched source={self._estop_source or 'unknown'}, clearing source={source}",
            )
            logger.warning("clear_estop denied for %s: %s", principal, blocked)
            return False

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
        self._estop_source = ""
        self.ns.write("/proc/status", "active")
        try:
            from castor.safety.latch import record_clear as _record_clear

            _record_clear()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Safety latch could not be cleared on disk: %s", exc)
        self._audit_safety(
            principal, "/dev/motor", "clear_estop", f"emergency stop cleared — source={source}"
        )
        logger.info("Emergency stop cleared by %s (source=%s)", principal, source)
        return True

    def resync_from_latch(self) -> bool:
        """Reconcile the in-memory e-stop flag with the latch file on disk.

        THE FILE IS THE SOURCE OF TRUTH ACROSS PROCESSES, and it has to be:
        `castor pause`, `castor resume` and the CLI's clear all run in a
        different process from the runtime, and without this the runtime would
        hold a stop the operator cleared thirty seconds ago and had no way to
        see. Called from the generated runtime's always-on guard loop.

        Returns True when the flag changed. Cheap and safe to call often: with
        ``ROBOT_HOME`` unset there is no file and this does nothing at all.

        A MISSING FILE NEVER LIFTS A HOLD. ``load()`` reads an absent latch as
        "nothing held", which is right at boot and wrong here: if that answer
        were allowed to reconcile, ``rm $ROBOT_HOME/safety-latch.json`` would
        clear a latched e-stop within one guard cycle, with no auth code and
        without the sensor rule ever being consulted. A clear always LEAVES a
        file behind (``record_clear`` writes engaged=false), so the absence of
        one is never evidence that anybody cleared anything.
        """
        try:
            from castor.safety.latch import latch_path
            from castor.safety.latch import load as _load_latch

            path = latch_path()
            if path is None:
                return False
            if not path.exists():
                if self._estop or self._paused:
                    logger.warning(
                        "safety latch file %s is gone; the hold in this process STANDS. "
                        "Clear it with `castor resume --clear-estop` or POST /api/estop/clear.",
                        path,
                    )
                return False
            latch = _load_latch()
        except Exception as exc:  # noqa: BLE001
            logger.debug("latch resync skipped: %s", exc)
            return False
        changed = False
        if latch.paused != self._paused:
            self._paused = latch.paused
            self._pause_detail = latch.describe() if latch.paused else ""
            logger.info("pause %s out of process", "set" if latch.paused else "lifted")
            changed = True
        if latch.estop_engaged and not self._estop:
            self._estop = True
            self._estop_source = latch.estop_source or "unknown"
            self.ns.write("/proc/status", "estop")
            self._audit_safety(
                latch.estop_principal or "latch",
                "/dev/motor",
                "estop",
                f"adopted from the persisted latch — source={self._estop_source}",
            )
            logger.warning("EMERGENCY STOP adopted from the latch: %s", latch.describe())
            return True
        if not latch.estop_engaged and self._estop:
            cleared_from = self._estop_source
            self._estop = False
            self._estop_source = ""
            self.ns.write("/proc/status", "active")
            self._audit_safety(
                "latch",
                "/dev/motor",
                "clear_estop",
                f"cleared out of process — previous source={cleared_from or 'unknown'}",
            )
            logger.info("Emergency stop lifted out of process (was source=%s)", cleared_from)
            return True
        return changed

    @property
    def is_estopped(self) -> bool:
        return self._estop

    @property
    def estop_source(self) -> str:
        """Where the current latch came from: 'sensor', 'api', 'rcan', 'local'.

        Empty when nothing is latched. This is the value the sensor-latch rule
        in :meth:`clear_estop` reads, and it survives a restart with the latch.
        """
        return self._estop_source

    @property
    def last_write_denial(self) -> str:
        """Human-readable reason for the most recent write() returning False."""
        return self._last_write_denial

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
