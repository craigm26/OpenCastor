"""
Safety Protocol Engine for OpenCastor.

Configurable safety rules inspired by ContinuonOS Protocol 66,
adapted for OpenCastor's robot types. Rules can be loaded from
YAML config files or the virtual filesystem.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Safety.Protocol")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RuleViolation:
    """A single rule violation."""

    rule_id: str
    category: str
    severity: str  # "warning" | "violation" | "critical"
    message: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class SafetyRule:
    """A configurable safety rule."""

    rule_id: str
    category: str  # motion, force, workspace, human, thermal, electrical,
    #                 software, emergency, property, privacy
    description: str
    severity: str  # "warning" | "violation" | "critical"
    enabled: bool = True
    params: Dict[str, Any] = field(default_factory=dict)
    check: Callable[[Dict[str, Any], Dict[str, Any]], Optional[RuleViolation]] = field(
        default=lambda action, params: None
    )

    def evaluate(self, action: Dict[str, Any]) -> Optional[RuleViolation]:
        """Run this rule against an action dict. Returns a violation or None."""
        if not self.enabled:
            return None
        return self.check(action, self.params)


# ---------------------------------------------------------------------------
# Default rule check functions
# ---------------------------------------------------------------------------


def _check_max_linear_velocity(
    action: Dict[str, Any], params: Dict[str, Any]
) -> Optional[RuleViolation]:
    velocity = action.get("linear_velocity")
    if velocity is None:
        return None
    limit = params.get("max_velocity_ms", 1.0)
    if abs(velocity) > limit:
        return RuleViolation(
            rule_id="MOTION_001",
            category="motion",
            severity="violation",
            message=f"Linear velocity {abs(velocity):.2f} m/s exceeds limit {limit:.2f} m/s",
        )
    return None


def _check_max_angular_velocity(
    action: Dict[str, Any], params: Dict[str, Any]
) -> Optional[RuleViolation]:
    velocity = action.get("angular_velocity")
    if velocity is None:
        return None
    limit = params.get("max_angular_velocity_rads", 2.0)
    if abs(velocity) > limit:
        return RuleViolation(
            rule_id="MOTION_002",
            category="motion",
            severity="violation",
            message=(
                f"Angular velocity {abs(velocity):.2f} rad/s exceeds limit {limit:.2f} rad/s"
            ),
        )
    return None


def _check_estop_response(
    action: Dict[str, Any], params: Dict[str, Any]
) -> Optional[RuleViolation]:
    response_ms = action.get("estop_response_ms")
    if response_ms is None:
        return None
    limit = params.get("max_response_ms", 100.0)
    if response_ms > limit:
        return RuleViolation(
            rule_id="MOTION_003",
            category="motion",
            severity="critical",
            message=f"E-stop response {response_ms:.1f}ms exceeds limit {limit:.1f}ms",
        )
    return None


def _check_contact_force(
    action: Dict[str, Any], params: Dict[str, Any]
) -> Optional[RuleViolation]:
    force = action.get("contact_force")
    if force is None:
        return None
    human_nearby = action.get("human_nearby", False)
    if human_nearby:
        limit = params.get("max_force_human_n", 10.0)
    else:
        limit = params.get("max_force_n", 50.0)
    if abs(force) > limit:
        ctx = " (human nearby)" if human_nearby else ""
        return RuleViolation(
            rule_id="FORCE_001",
            category="force",
            severity="critical" if human_nearby else "violation",
            message=f"Contact force {abs(force):.1f}N exceeds limit {limit:.1f}N{ctx}",
        )
    return None


def _check_workspace_bounds(
    action: Dict[str, Any], params: Dict[str, Any]
) -> Optional[RuleViolation]:
    position = action.get("position")
    if position is None:
        return None
    bounds = params.get("bounds", {})
    for axis, idx in [("x", 0), ("y", 1), ("z", 2)]:
        if idx >= len(position):
            continue
        lo = bounds.get(f"{axis}_min")
        hi = bounds.get(f"{axis}_max")
        if lo is not None and position[idx] < lo:
            return RuleViolation(
                rule_id="WORKSPACE_001",
                category="workspace",
                severity="violation",
                message=f"Position {axis}={position[idx]:.3f} below minimum {lo}",
            )
        if hi is not None and position[idx] > hi:
            return RuleViolation(
                rule_id="WORKSPACE_001",
                category="workspace",
                severity="violation",
                message=f"Position {axis}={position[idx]:.3f} above maximum {hi}",
            )
    return None


def _check_thermal(action: Dict[str, Any], params: Dict[str, Any]) -> Optional[RuleViolation]:
    temp = action.get("temperature_c")
    if temp is None:
        return None
    critical = params.get("critical_temp_c", 90.0)
    warn = params.get("warn_temp_c", 80.0)
    if temp >= critical:
        return RuleViolation(
            rule_id="THERMAL_001",
            category="thermal",
            severity="critical",
            message=f"Temperature {temp:.1f}°C exceeds critical limit {critical:.1f}°C",
        )
    if temp >= warn:
        return RuleViolation(
            rule_id="THERMAL_001",
            category="thermal",
            severity="warning",
            message=f"Temperature {temp:.1f}°C exceeds warning limit {warn:.1f}°C",
        )
    return None


def _check_watchdog(action: Dict[str, Any], params: Dict[str, Any]) -> Optional[RuleViolation]:
    last_heartbeat_ms = action.get("watchdog_elapsed_ms")
    if last_heartbeat_ms is None:
        return None
    limit = params.get("timeout_ms", 100.0)
    if last_heartbeat_ms > limit:
        return RuleViolation(
            rule_id="SOFTWARE_001",
            category="software",
            severity="critical",
            message=f"Watchdog timeout: {last_heartbeat_ms:.1f}ms > {limit:.1f}ms limit",
        )
    return None


def _check_estop_available(
    action: Dict[str, Any], params: Dict[str, Any]
) -> Optional[RuleViolation]:
    estop_available = action.get("estop_available")
    if estop_available is None:
        return None
    if not estop_available:
        return RuleViolation(
            rule_id="EMERGENCY_001",
            category="emergency",
            severity="critical",
            message="E-stop is not available",
        )
    return None


def _check_destructive_auth(
    action: Dict[str, Any], params: Dict[str, Any]
) -> Optional[RuleViolation]:
    if not action.get("destructive", False):
        return None
    if not action.get("authorized", False):
        return RuleViolation(
            rule_id="PROPERTY_001",
            category="property",
            severity="violation",
            message="Destructive action requires authorization",
        )
    return None


def _check_sensor_consent(
    action: Dict[str, Any], params: Dict[str, Any]
) -> Optional[RuleViolation]:
    if not action.get("sensor_active", False):
        return None
    if not action.get("consent_granted", False):
        required = params.get("require_consent", True)
        if required:
            return RuleViolation(
                rule_id="PRIVACY_001",
                category="privacy",
                severity="violation",
                message="Sensor activation requires consent",
            )
    return None


# ---------------------------------------------------------------------------
# Default rules registry
# ---------------------------------------------------------------------------

_DEFAULT_RULES: List[SafetyRule] = [
    SafetyRule(
        rule_id="MOTION_001",
        category="motion",
        description="Maximum linear velocity",
        severity="violation",
        params={"max_velocity_ms": 1.0},
        check=_check_max_linear_velocity,
    ),
    SafetyRule(
        rule_id="MOTION_002",
        category="motion",
        description="Maximum angular velocity",
        severity="violation",
        params={"max_angular_velocity_rads": 2.0},
        check=_check_max_angular_velocity,
    ),
    SafetyRule(
        rule_id="MOTION_003",
        category="motion",
        description="E-stop response time < 100ms",
        severity="critical",
        params={"max_response_ms": 100.0},
        check=_check_estop_response,
    ),
    SafetyRule(
        rule_id="FORCE_001",
        category="force",
        description="Maximum contact force (50N normal, 10N with human)",
        severity="violation",
        params={"max_force_n": 50.0, "max_force_human_n": 10.0},
        check=_check_contact_force,
    ),
    SafetyRule(
        rule_id="WORKSPACE_001",
        category="workspace",
        description="Workspace bounds check",
        severity="violation",
        params={"bounds": {}},
        check=_check_workspace_bounds,
    ),
    SafetyRule(
        rule_id="THERMAL_001",
        category="thermal",
        description="Motor/CPU temperature limits",
        severity="critical",
        params={"warn_temp_c": 80.0, "critical_temp_c": 90.0},
        check=_check_thermal,
    ),
    SafetyRule(
        rule_id="SOFTWARE_001",
        category="software",
        description="Watchdog timeout",
        severity="critical",
        params={"timeout_ms": 100.0},
        check=_check_watchdog,
    ),
    SafetyRule(
        rule_id="EMERGENCY_001",
        category="emergency",
        description="E-stop must always be available",
        severity="critical",
        params={},
        check=_check_estop_available,
    ),
    SafetyRule(
        rule_id="PROPERTY_001",
        category="property",
        description="Destructive actions need authorization",
        severity="violation",
        params={},
        check=_check_destructive_auth,
    ),
    SafetyRule(
        rule_id="PRIVACY_001",
        category="privacy",
        description="Sensor consent required",
        severity="violation",
        params={"require_consent": True},
        check=_check_sensor_consent,
    ),
]


def _build_default_rules() -> Dict[str, SafetyRule]:
    """Return a fresh copy of default rules keyed by rule_id."""
    import copy

    return {r.rule_id: copy.deepcopy(r) for r in _DEFAULT_RULES}


# ---------------------------------------------------------------------------
# SafetyProtocol
# ---------------------------------------------------------------------------


class SafetyProtocol:
    """Configurable safety protocol engine.

    Loads default rules and optionally overrides them from a YAML config file
    or virtual filesystem path.
    """

    def __init__(self, config_path: Optional[str] = None, ns: Any = None):
        self._rules: Dict[str, SafetyRule] = _build_default_rules()
        self._audit_log: List[Dict[str, Any]] = []
        self._violations: List[RuleViolation] = []

        # Load config overrides
        config = self._load_config(config_path, ns)
        if config:
            self._apply_config(config)

    def _load_config(
        self, config_path: Optional[str], ns: Any
    ) -> Optional[Dict[str, Any]]:
        """Load YAML config from file path or virtual FS."""
        # Try virtual FS first
        if ns is not None:
            try:
                data = ns.read("/etc/safety/protocol.yaml")
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

        # Try file path
        if config_path:
            path = Path(config_path)
            if path.is_file():
                try:
                    import yaml

                    with open(path) as f:
                        return yaml.safe_load(f)
                except ImportError:
                    logger.warning("PyYAML not installed; cannot load %s", config_path)
                except Exception as exc:
                    logger.warning("Failed to load config %s: %s", config_path, exc)

        return None

    def _apply_config(self, config: Dict[str, Any]) -> None:
        """Apply YAML config overrides to rules."""
        protocol = config.get("safety_protocol", config)
        rules_cfg = protocol.get("rules", {})
        for rule_id, overrides in rules_cfg.items():
            if rule_id not in self._rules:
                logger.warning("Config references unknown rule: %s", rule_id)
                continue
            rule = self._rules[rule_id]
            if "enabled" in overrides:
                rule.enabled = bool(overrides["enabled"])
            if "params" in overrides and isinstance(overrides["params"], dict):
                rule.params.update(overrides["params"])
            if "severity" in overrides:
                rule.severity = overrides["severity"]

    def _audit(self, event: str, **kwargs: Any) -> None:
        entry = {"event": event, "timestamp": time.time(), **kwargs}
        self._audit_log.append(entry)
        logger.debug("Protocol audit: %s", entry)

    # -- Public API --

    def check_action(self, action: Dict[str, Any]) -> List[RuleViolation]:
        """Run all enabled rules against an action. Returns list of violations."""
        violations: List[RuleViolation] = []
        for rule in self._rules.values():
            result = rule.evaluate(action)
            if result is not None:
                violations.append(result)
                self._violations.append(result)
                self._audit(
                    "violation",
                    rule_id=result.rule_id,
                    severity=result.severity,
                    message=result.message,
                )
        return violations

    def enable_rule(self, rule_id: str) -> bool:
        """Enable a rule by ID. Returns True if found."""
        rule = self._rules.get(rule_id)
        if rule is None:
            return False
        rule.enabled = True
        self._audit("enable_rule", rule_id=rule_id)
        return True

    def disable_rule(self, rule_id: str) -> bool:
        """Disable a rule by ID. Returns True if found."""
        rule = self._rules.get(rule_id)
        if rule is None:
            return False
        rule.enabled = False
        self._audit("disable_rule", rule_id=rule_id)
        return True

    def get_rule(self, rule_id: str) -> Optional[SafetyRule]:
        """Get a rule by ID."""
        return self._rules.get(rule_id)

    def list_rules(self) -> List[Dict[str, Any]]:
        """Return a table-friendly list of all rules with status."""
        return [
            {
                "rule_id": r.rule_id,
                "category": r.category,
                "description": r.description,
                "severity": r.severity,
                "enabled": r.enabled,
                "params": dict(r.params),
            }
            for r in self._rules.values()
        ]

    def get_violations_summary(self) -> Dict[str, int]:
        """Return recent violation counts by category."""
        summary: Dict[str, int] = {}
        for v in self._violations:
            summary[v.category] = summary.get(v.category, 0) + 1
        return summary

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return the audit log."""
        return list(self._audit_log)

    @property
    def rules(self) -> Dict[str, SafetyRule]:
        """Direct access to rules dict."""
        return self._rules


# ---------------------------------------------------------------------------
# Integration: check /dev/ writes via SafetyProtocol
# ---------------------------------------------------------------------------


def check_write_protocol(
    protocol: SafetyProtocol, path: str, data: Any
) -> List[RuleViolation]:
    """Translate a /dev/ write into an action dict and check protocol rules.

    This bridges the virtual filesystem write path to the protocol engine.
    """
    if not isinstance(data, dict):
        return []

    action: Dict[str, Any] = {}

    if path.startswith("/dev/motor"):
        if "velocity" in data:
            action["linear_velocity"] = data["velocity"]
        if "angular_velocity" in data:
            action["angular_velocity"] = data["angular_velocity"]

    if path.startswith("/dev/arm"):
        if "position" in data:
            action["position"] = data["position"]
        if "force" in data or "contact_force" in data:
            action["contact_force"] = data.get("contact_force", data.get("force"))
        if "human_nearby" in data:
            action["human_nearby"] = data["human_nearby"]

    if path.startswith("/dev/sensor"):
        action["sensor_active"] = True
        action["consent_granted"] = data.get("consent", False)

    if path.startswith("/dev/gpio"):
        action["destructive"] = True
        action["authorized"] = data.get("authorized", False)

    if not action:
        return []

    return protocol.check_action(action)
