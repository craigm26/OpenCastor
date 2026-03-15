"""
Safety Protocol Engine for OpenCastor.

Configurable safety rules inspired by ContinuonOS Protocol 66,
adapted for OpenCastor's robot types. Rules can be loaded from
YAML config files or the virtual filesystem.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

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
    params: dict[str, Any] = field(default_factory=dict)
    check: Callable[[dict[str, Any], dict[str, Any]], Optional[RuleViolation]] = field(
        default=lambda action, params: None
    )

    def evaluate(self, action: dict[str, Any]) -> Optional[RuleViolation]:
        """Run this rule against an action dict. Returns a violation or None."""
        if not self.enabled:
            return None
        return self.check(action, self.params)


# ---------------------------------------------------------------------------
# Default rule check functions
# ---------------------------------------------------------------------------


def _check_max_linear_velocity(
    action: dict[str, Any], params: dict[str, Any]
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
    action: dict[str, Any], params: dict[str, Any]
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
            message=(f"Angular velocity {abs(velocity):.2f} rad/s exceeds limit {limit:.2f} rad/s"),
        )
    return None


def _check_estop_response(
    action: dict[str, Any], params: dict[str, Any]
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


def _check_contact_force(action: dict[str, Any], params: dict[str, Any]) -> Optional[RuleViolation]:
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
    action: dict[str, Any], params: dict[str, Any]
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


def _check_thermal(action: dict[str, Any], params: dict[str, Any]) -> Optional[RuleViolation]:
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


def _check_watchdog(action: dict[str, Any], params: dict[str, Any]) -> Optional[RuleViolation]:
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
    action: dict[str, Any], params: dict[str, Any]
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
    action: dict[str, Any], params: dict[str, Any]
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
    action: dict[str, Any], params: dict[str, Any]
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
# Protocol 66 — Extended rules (Phase 2)
# ---------------------------------------------------------------------------


def _check_human_proximity_estop(
    action: dict[str, Any], params: dict[str, Any]
) -> Optional[RuleViolation]:
    """HUMAN_001 — Immediate ESTOP if human within hard-stop distance."""
    distance_m = action.get("human_distance_m")
    if distance_m is None:
        return None
    limit = params.get("estop_distance_m", 0.3)
    if distance_m < limit:
        return RuleViolation(
            rule_id="HUMAN_001",
            category="human",
            severity="critical",
            message=(
                f"Human within ESTOP distance: {distance_m:.2f}m < {limit:.2f}m — "
                "immediate halt required"
            ),
        )
    return None


def _check_human_proximity_slowdown(
    action: dict[str, Any], params: dict[str, Any]
) -> Optional[RuleViolation]:
    """HUMAN_002 — Reduce speed when human is in the slowdown zone."""
    distance_m = action.get("human_distance_m")
    linear_vel = action.get("linear_velocity", 0.0)
    if distance_m is None:
        return None
    slowdown_dist = params.get("slowdown_distance_m", 1.5)
    max_vel_in_zone = params.get("max_velocity_in_zone_ms", 0.25)
    if distance_m < slowdown_dist and abs(linear_vel) > max_vel_in_zone:
        return RuleViolation(
            rule_id="HUMAN_002",
            category="human",
            severity="violation",
            message=(
                f"Speed {abs(linear_vel):.2f} m/s too high with human at {distance_m:.2f}m — "
                f"max {max_vel_in_zone:.2f} m/s in {slowdown_dist:.1f}m zone"
            ),
        )
    return None


def _check_arm_joint_velocity(
    action: dict[str, Any], params: dict[str, Any]
) -> Optional[RuleViolation]:
    """ARM_001 — Per-joint velocity limits for manipulators."""
    joint_velocities = action.get("joint_velocities")
    if not isinstance(joint_velocities, (list, dict)):
        return None
    max_vel = params.get("max_joint_velocity_rads", 3.14)  # π rad/s default
    if isinstance(joint_velocities, list):
        items = enumerate(joint_velocities)
    else:
        items = joint_velocities.items()
    for idx, vel in items:
        if abs(vel) > max_vel:
            return RuleViolation(
                rule_id="ARM_001",
                category="arm",
                severity="violation",
                message=(
                    f"Joint {idx} velocity {abs(vel):.3f} rad/s exceeds limit {max_vel:.3f} rad/s"
                ),
            )
    return None


def _check_arm_payload(action: dict[str, Any], params: dict[str, Any]) -> Optional[RuleViolation]:
    """ARM_002 — Payload mass limit for manipulators."""
    payload_kg = action.get("payload_kg")
    if payload_kg is None:
        return None
    limit = params.get("max_payload_kg", 5.0)
    if payload_kg > limit:
        return RuleViolation(
            rule_id="ARM_002",
            category="arm",
            severity="violation",
            message=f"Payload {payload_kg:.2f} kg exceeds rated limit {limit:.2f} kg",
        )
    return None


def _check_arm_singularity(
    action: dict[str, Any], params: dict[str, Any]
) -> Optional[RuleViolation]:
    """ARM_003 — Warn when arm is near a kinematic singularity."""
    singularity_metric = action.get("singularity_metric")  # 0.0 = at singularity, 1.0 = far
    if singularity_metric is None:
        return None
    warn_threshold = params.get("singularity_warn_threshold", 0.05)
    critical_threshold = params.get("singularity_critical_threshold", 0.01)
    if singularity_metric < critical_threshold:
        return RuleViolation(
            rule_id="ARM_003",
            category="arm",
            severity="critical",
            message=(
                f"Arm in kinematic singularity (metric={singularity_metric:.4f} < "
                f"{critical_threshold:.4f}) — motion blocked"
            ),
        )
    if singularity_metric < warn_threshold:
        return RuleViolation(
            rule_id="ARM_003",
            category="arm",
            severity="warning",
            message=(
                f"Arm near kinematic singularity (metric={singularity_metric:.4f}) — reduce speed"
            ),
        )
    return None


def _check_motor_voltage(action: dict[str, Any], params: dict[str, Any]) -> Optional[RuleViolation]:
    """ELECTRICAL_001 — Motor supply voltage out of safe range."""
    voltage_v = action.get("motor_voltage_v")
    if voltage_v is None:
        return None
    v_min = params.get("min_voltage_v", 9.0)
    v_max = params.get("max_voltage_v", 16.8)  # 4S LiPo max
    if voltage_v < v_min:
        return RuleViolation(
            rule_id="ELECTRICAL_001",
            category="electrical",
            severity="critical",
            message=f"Motor voltage {voltage_v:.2f}V below minimum {v_min:.2f}V — risk of brownout",
        )
    if voltage_v > v_max:
        return RuleViolation(
            rule_id="ELECTRICAL_001",
            category="electrical",
            severity="critical",
            message=f"Motor voltage {voltage_v:.2f}V above maximum {v_max:.2f}V — risk of damage",
        )
    return None


def _check_motor_current(action: dict[str, Any], params: dict[str, Any]) -> Optional[RuleViolation]:
    """ELECTRICAL_002 — Motor current draw over safe limit."""
    current_a = action.get("motor_current_a")
    if current_a is None:
        return None
    limit = params.get("max_current_a", 10.0)
    critical = params.get("critical_current_a", 15.0)
    severity = "critical" if abs(current_a) > critical else "violation"
    if abs(current_a) > limit:
        return RuleViolation(
            rule_id="ELECTRICAL_002",
            category="electrical",
            severity=severity,
            message=(
                f"Motor current {abs(current_a):.2f}A exceeds {'critical' if severity == 'critical' else 'safe'} "
                f"limit {critical if severity == 'critical' else limit:.2f}A"
            ),
        )
    return None


def _check_direction_reversal(
    action: dict[str, Any], params: dict[str, Any]
) -> Optional[RuleViolation]:
    """MOTION_004 — Prevent sudden direction reversal at high speed.

    A sign flip on linear_velocity when moving above threshold is a sudden
    reversal that can stress drivetrain and destabilise the robot.
    """
    linear_vel = action.get("linear_velocity")
    prev_linear_vel = action.get("prev_linear_velocity")
    if linear_vel is None or prev_linear_vel is None:
        return None
    speed_threshold = params.get("min_speed_for_reversal_check_ms", 0.3)
    if abs(prev_linear_vel) < speed_threshold:
        return None
    # Reversal: signs differ and previous speed was significant
    if (linear_vel * prev_linear_vel) < 0:
        return RuleViolation(
            rule_id="MOTION_004",
            category="motion",
            severity="violation",
            message=(
                f"Sudden direction reversal at {abs(prev_linear_vel):.2f} m/s — "
                "decelerate before reversing"
            ),
        )
    return None


def _check_ai_confidence(action: dict[str, Any], params: dict[str, Any]) -> Optional[RuleViolation]:
    """SOFTWARE_002 — AI confidence must meet per-scope threshold before actuation."""
    confidence = action.get("ai_confidence")
    if confidence is None:
        return None
    threshold = params.get("min_confidence", 0.7)
    if confidence < threshold:
        return RuleViolation(
            rule_id="SOFTWARE_002",
            category="software",
            severity="violation",
            message=(
                f"AI confidence {confidence:.3f} below required threshold {threshold:.3f} — "
                "command blocked per confidence gate"
            ),
        )
    return None


def _check_thought_log_required(
    action: dict[str, Any], params: dict[str, Any]
) -> Optional[RuleViolation]:
    """SOFTWARE_003 — AI-generated actuator commands must carry a thought_id for audit."""
    is_ai_generated = action.get("ai_generated", False)
    if not is_ai_generated:
        return None
    thought_id = action.get("thought_id")
    if not thought_id:
        return RuleViolation(
            rule_id="SOFTWARE_003",
            category="software",
            severity="violation",
            message=(
                "AI-generated actuator command missing thought_id — "
                "cannot audit reasoning chain (RCAN §16.4)"
            ),
        )
    return None


# ---------------------------------------------------------------------------
# Default rules registry
# ---------------------------------------------------------------------------

_DEFAULT_RULES: list[SafetyRule] = [
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
    # ── Human proximity (Protocol 66 Phase 2) ──────────────────────────────
    SafetyRule(
        rule_id="HUMAN_001",
        category="human",
        description="Immediate ESTOP if human within hard-stop distance (default 0.3m)",
        severity="critical",
        params={"estop_distance_m": 0.3},
        check=_check_human_proximity_estop,
    ),
    SafetyRule(
        rule_id="HUMAN_002",
        category="human",
        description="Reduce speed when human in slowdown zone (default 1.5m, max 0.25 m/s)",
        severity="violation",
        params={"slowdown_distance_m": 1.5, "max_velocity_in_zone_ms": 0.25},
        check=_check_human_proximity_slowdown,
    ),
    # ── Manipulator / arm ─────────────────────────────────────────────────
    SafetyRule(
        rule_id="ARM_001",
        category="arm",
        description="Per-joint velocity limit (default π rad/s)",
        severity="violation",
        params={"max_joint_velocity_rads": 3.14159},
        check=_check_arm_joint_velocity,
    ),
    SafetyRule(
        rule_id="ARM_002",
        category="arm",
        description="Payload mass limit (default 5 kg)",
        severity="violation",
        params={"max_payload_kg": 5.0},
        check=_check_arm_payload,
    ),
    SafetyRule(
        rule_id="ARM_003",
        category="arm",
        description="Kinematic singularity proximity warning/block",
        severity="warning",
        params={"singularity_warn_threshold": 0.05, "singularity_critical_threshold": 0.01},
        check=_check_arm_singularity,
    ),
    # ── Electrical / power ────────────────────────────────────────────────
    SafetyRule(
        rule_id="ELECTRICAL_001",
        category="electrical",
        description="Motor supply voltage must stay within safe range (9–16.8V)",
        severity="critical",
        params={"min_voltage_v": 9.0, "max_voltage_v": 16.8},
        check=_check_motor_voltage,
    ),
    SafetyRule(
        rule_id="ELECTRICAL_002",
        category="electrical",
        description="Motor current draw limit (default 10A warning, 15A critical)",
        severity="violation",
        params={"max_current_a": 10.0, "critical_current_a": 15.0},
        check=_check_motor_current,
    ),
    # ── Motion dynamics ───────────────────────────────────────────────────
    SafetyRule(
        rule_id="MOTION_004",
        category="motion",
        description="Prevent sudden direction reversal above threshold speed (0.3 m/s)",
        severity="violation",
        params={"min_speed_for_reversal_check_ms": 0.3},
        check=_check_direction_reversal,
    ),
    # ── AI accountability (RCAN §16) ──────────────────────────────────────
    SafetyRule(
        rule_id="SOFTWARE_002",
        category="software",
        description="AI confidence gate: block actuation below per-scope threshold",
        severity="violation",
        params={"min_confidence": 0.7},
        check=_check_ai_confidence,
    ),
    SafetyRule(
        rule_id="SOFTWARE_003",
        category="software",
        description="AI-generated commands must carry thought_id for audit (RCAN §16.4)",
        severity="violation",
        params={},
        check=_check_thought_log_required,
    ),
]


def _build_default_rules() -> dict[str, SafetyRule]:
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
        self._rules: dict[str, SafetyRule] = _build_default_rules()
        self._audit_log: list[dict[str, Any]] = []
        self._violations: list[RuleViolation] = []

        # Load config overrides
        config = self._load_config(config_path, ns)
        if config:
            self._apply_config(config)

    def _load_config(self, config_path: Optional[str], ns: Any) -> Optional[dict[str, Any]]:
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

    def _apply_config(self, config: dict[str, Any]) -> None:
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

    def check_action(self, action: dict[str, Any]) -> list[RuleViolation]:
        """Run all enabled rules against an action. Returns list of violations."""
        violations: list[RuleViolation] = []
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

    def list_rules(self) -> list[dict[str, Any]]:
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

    def get_violations_summary(self) -> dict[str, int]:
        """Return recent violation counts by category."""
        summary: dict[str, int] = {}
        for v in self._violations:
            summary[v.category] = summary.get(v.category, 0) + 1
        return summary

    def get_audit_log(self) -> list[dict[str, Any]]:
        """Return the audit log."""
        return list(self._audit_log)

    @property
    def rules(self) -> dict[str, SafetyRule]:
        """Direct access to rules dict."""
        return self._rules


# ---------------------------------------------------------------------------
# Integration: check /dev/ writes via SafetyProtocol
# ---------------------------------------------------------------------------


def check_write_protocol(protocol: SafetyProtocol, path: str, data: Any) -> list[RuleViolation]:
    """Translate a /dev/ write into an action dict and check protocol rules.

    This bridges the virtual filesystem write path to the protocol engine.
    """
    if not isinstance(data, dict):
        return []

    action: dict[str, Any] = {}

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
