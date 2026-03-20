"""
Protocol 66 Conformance Manifest for OpenCastor.

Machine-readable declaration of which Protocol 66 / RCAN §6 / §16 safety
rules are implemented, at what status, and in which module. Exposed via
GET /api/safety/manifest.

Protocol 66 is ContinuonAI's internal safety protocol for robot runtimes.
OpenCastor implements it as an independent runtime and may deviate where
the physical-layer context differs from ContinuonOS assumptions.

Rule categories (ISO 10218-1 + RCAN §6 alignment):
  motion      — velocity, acceleration, direction dynamics
  force       — contact force limits
  human       — human proximity detection and speed reduction
  workspace   — physical bounds enforcement
  arm         — manipulator-specific: joints, payload, singularity
  thermal     — temperature monitoring
  electrical  — voltage and current monitoring
  software    — watchdog, AI confidence gate, thought log
  emergency   — e-stop availability and response time
  property    — destructive action authorization
  privacy     — sensor consent

Implementation statuses:
  implemented — Rule is enforced in SafetyLayer or SafetyProtocol.
  partial     — Rule logic exists; some inputs may not be wired end-to-end.
  planned     — Rule is defined but not yet enforced.
  hardware    — Requires hardware-level control beyond software (e.g. SIL/PLe).
"""

from __future__ import annotations

import time
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Rule catalogue
# ---------------------------------------------------------------------------

_P66_RULES: list[dict[str, Any]] = [
    # ── Motion ──────────────────────────────────────────────────────────────
    {
        "rule_id": "MOTION_001",
        "category": "motion",
        "description": "Maximum linear velocity — AI-generated commands clamped to configured limit",
        "standard_refs": ["ISO 10218-1:2025 §5.x", "RCAN §6"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_max_linear_velocity",
        "default_params": {"max_velocity_ms": 1.0},
        "notes": "Configurable via /etc/safety/protocol.yaml. Default 1.0 m/s.",
    },
    {
        "rule_id": "MOTION_002",
        "category": "motion",
        "description": "Maximum angular velocity",
        "standard_refs": ["ISO 10218-1:2025 §5.x", "RCAN §6"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_max_angular_velocity",
        "default_params": {"max_angular_velocity_rads": 2.0},
        "notes": "Default 2.0 rad/s (~115 °/s).",
    },
    {
        "rule_id": "MOTION_003",
        "category": "motion",
        "description": "E-stop response time must be < 100ms",
        "standard_refs": ["ISO 10218-1:2025 §5.x — emergency stop timing"],
        "severity": "critical",
        "status": "partial",
        "module": "castor.safety.protocol._check_estop_response",
        "notes": (
            "Protocol rule implemented. End-to-end timing depends on actuator driver "
            "latency — hardware watchdog recommended for SIL/PLe compliance."
        ),
    },
    {
        "rule_id": "MOTION_004",
        "category": "motion",
        "description": "Prevent sudden direction reversal above threshold speed",
        "standard_refs": ["RCAN §6", "ContinuonOS Protocol 66"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_direction_reversal",
        "default_params": {"min_speed_for_reversal_check_ms": 0.3},
        "notes": "Requires prev_linear_velocity in action dict from motion controller.",
    },
    # ── Force ────────────────────────────────────────────────────────────────
    {
        "rule_id": "FORCE_001",
        "category": "force",
        "description": "Maximum contact force — 50N general, 10N when human nearby",
        "standard_refs": [
            "ISO 10218-1:2025 §5.x — power and force limiting (PFL)",
            "ISO/TS 15066 — collaborative robot force limits",
        ],
        "severity": "critical",
        "status": "partial",
        "module": "castor.safety.protocol._check_contact_force",
        "default_params": {"max_force_n": 50.0, "max_force_human_n": 10.0},
        "notes": (
            "Rule implemented. Requires contact_force and human_nearby fields from "
            "force-torque sensor or collision detection. Not applicable to robots "
            "without force sensing."
        ),
    },
    # ── Human proximity ──────────────────────────────────────────────────────
    {
        "rule_id": "HUMAN_001",
        "category": "human",
        "description": "Immediate ESTOP if human within hard-stop distance (default 0.3m)",
        "standard_refs": [
            "ISO 10218-1:2025 §5.x — speed and separation monitoring (SSM)",
            "ISO/TS 15066 §5.4",
            "ContinuonOS Protocol 66",
        ],
        "severity": "critical",
        "status": "implemented",
        "module": "castor.safety.protocol._check_human_proximity_estop",
        "default_params": {"estop_distance_m": 0.3},
        "notes": (
            "Requires human_distance_m in action dict from proximity sensor "
            "(LiDAR, depth camera, UWB, etc.). Not self-activating without sensor input."
        ),
    },
    {
        "rule_id": "HUMAN_002",
        "category": "human",
        "description": "Speed reduction required when human in slowdown zone (1.5m, max 0.25 m/s)",
        "standard_refs": [
            "ISO 10218-1:2025 §5.x — speed and separation monitoring",
            "ISO/TS 15066 §5.4.3",
        ],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_human_proximity_slowdown",
        "default_params": {"slowdown_distance_m": 1.5, "max_velocity_in_zone_ms": 0.25},
    },
    # ── Workspace / bounds ────────────────────────────────────────────────────
    {
        "rule_id": "WORKSPACE_001",
        "category": "workspace",
        "description": "Physical workspace bounds enforcement",
        "standard_refs": ["ISO 10218-1:2025 §5.x — restricted space"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_workspace_bounds",
        "notes": "Bounds loaded from /etc/safety/bounds or robot.rcan.yaml safety block.",
    },
    # ── Arm / manipulator ─────────────────────────────────────────────────────
    {
        "rule_id": "ARM_001",
        "category": "arm",
        "description": "Per-joint velocity limit (default π rad/s)",
        "standard_refs": ["ISO 10218-1:2025 §5.x", "ContinuonOS Protocol 66"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_arm_joint_velocity",
        "default_params": {"max_joint_velocity_rads": 3.14159},
        "notes": "Requires joint_velocities list/dict in action dict.",
    },
    {
        "rule_id": "ARM_002",
        "category": "arm",
        "description": "Payload mass limit (default 5 kg for SO-ARM101)",
        "standard_refs": ["ISO 10218-1:2025 §5.x — rated load"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_arm_payload",
        "default_params": {"max_payload_kg": 5.0},
    },
    {
        "rule_id": "ARM_003",
        "category": "arm",
        "description": "Kinematic singularity proximity warning and motion block",
        "standard_refs": ["ContinuonOS Protocol 66"],
        "severity": "warning",
        "status": "implemented",
        "module": "castor.safety.protocol._check_arm_singularity",
        "notes": (
            "Requires singularity_metric (0.0 = at singularity) from kinematics solver. "
            "Not wired to kinematics solver by default — robot-specific integration needed."
        ),
    },
    # ── Thermal ──────────────────────────────────────────────────────────────
    {
        "rule_id": "THERMAL_001",
        "category": "thermal",
        "description": "Motor/CPU temperature limits (warn 80°C, critical 90°C)",
        "standard_refs": ["ISO 10218-1:2025 §5.x — thermal safety"],
        "severity": "critical",
        "status": "partial",
        "module": "castor.safety.protocol._check_thermal",
        "default_params": {"warn_temp_c": 80.0, "critical_temp_c": 90.0},
        "notes": "Requires temperature_c in action dict from motor driver or CPU sensor.",
    },
    # ── Electrical / power ────────────────────────────────────────────────────
    {
        "rule_id": "ELECTRICAL_001",
        "category": "electrical",
        "description": "Motor supply voltage must stay within safe range (9–16.8V)",
        "standard_refs": [
            "IEC 62443 — industrial automation security",
            "ContinuonOS Protocol 66",
        ],
        "severity": "critical",
        "status": "implemented",
        "module": "castor.safety.protocol._check_motor_voltage",
        "default_params": {"min_voltage_v": 9.0, "max_voltage_v": 16.8},
        "notes": "Range covers 3S–4S LiPo. Requires motor_voltage_v in action dict.",
    },
    {
        "rule_id": "ELECTRICAL_002",
        "category": "electrical",
        "description": "Motor current draw limit (10A warning, 15A critical)",
        "standard_refs": ["ContinuonOS Protocol 66"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_motor_current",
        "default_params": {"max_current_a": 10.0, "critical_current_a": 15.0},
        "notes": "Requires motor_current_a in action dict from motor controller telemetry.",
    },
    # ── Software / AI accountability ─────────────────────────────────────────
    {
        "rule_id": "SOFTWARE_001",
        "category": "software",
        "description": "Brain watchdog — motors stopped if AI unresponsive beyond timeout",
        "standard_refs": [
            "ISO 10218-1:2025 §5.x — control system reliability",
            "RCAN §6 — safe-stop on network/brain loss",
        ],
        "severity": "critical",
        "status": "implemented",
        "module": "castor.watchdog.BrainWatchdog",
        "default_params": {"timeout_s": 10.0},
        "notes": "Configurable via watchdog.timeout_s in rcan.yaml. Enabled by default.",
    },
    {
        "rule_id": "SOFTWARE_002",
        "category": "software",
        "description": "AI confidence gate — block actuation below per-scope threshold",
        "standard_refs": ["RCAN §16.2 — confidence gates"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_ai_confidence + castor.confidence_gate",
        "default_params": {"min_confidence": 0.7},
        "notes": (
            "Dual enforcement: confidence_gate.py at dispatch time + "
            "protocol rule for action-level check. Threshold configurable per action scope."
        ),
    },
    {
        "rule_id": "SOFTWARE_003",
        "category": "software",
        "description": "AI-generated commands must carry thought_id for audit (RCAN §16.4)",
        "standard_refs": ["RCAN §16.4 — thought log", "EU AI Act Art. 12 — record keeping"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_thought_log_required + castor.thought_log",
    },
    # ── Emergency ────────────────────────────────────────────────────────────
    {
        "rule_id": "EMERGENCY_001",
        "category": "emergency",
        "description": "E-stop must always be available regardless of system state",
        "standard_refs": [
            "ISO 10218-1:2025 §5.x — emergency stop function",
            "RCAN §6 — ESTOP bypasses all queues",
        ],
        "severity": "critical",
        "status": "implemented",
        "module": "castor.fs.safety.SafetyLayer.estop",
        "notes": (
            "POST /api/estop and RCAN MessageType.SAFETY ESTOP events bypass all "
            "HiTL gates and confidence queues. clear_estop requires CAP_SAFETY_OVERRIDE "
            "or root + optional OPENCASTOR_ESTOP_AUTH env code."
        ),
    },
    {
        "rule_id": "EMERGENCY_002",
        "category": "emergency",
        "description": "STOP (controlled decel) vs ESTOP (immediate cut) distinction",
        "standard_refs": ["RCAN §6 — SAFETY message safety_event field"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.fs.safety.SafetyLayer.controlled_stop",
        "notes": "POST /api/safety/rcan with safety_event=STOP triggers controlled_stop().",
    },
    # ── Property / authorization ──────────────────────────────────────────────
    {
        "rule_id": "PROPERTY_001",
        "category": "property",
        "description": "Destructive actions require HiTL authorization",
        "standard_refs": ["RCAN §16.3 — HiTL gates", "EU AI Act Art. 14 — human oversight"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.hitl_gate.HiTLGateManager + castor.safety.protocol._check_destructive_auth",
    },
    # ── Privacy ──────────────────────────────────────────────────────────────
    {
        "rule_id": "PRIVACY_001",
        "category": "privacy",
        "description": "Sensor consent required before activating cameras/microphones",
        "standard_refs": ["GDPR", "EU AI Act Art. 9", "ContinuonOS Protocol 66"],
        "severity": "violation",
        "status": "implemented",
        "module": "castor.safety.protocol._check_sensor_consent",
    },
    # ── Planned / hardware-dependent ──────────────────────────────────────────
    {
        "rule_id": "HARDWARE_001",
        "category": "hardware",
        "description": "Hardware-level watchdog MCU — independent of software runtime",
        "standard_refs": [
            "ISO 10218-1:2025 §5.x — safety function hardware architecture",
            "IEC 61508 SIL 2 / ISO 13849 PLd",
        ],
        "severity": "critical",
        "status": "hardware",
        "module": "N/A — requires dedicated safety MCU",
        "notes": (
            "OpenCastor's software watchdog (SOFTWARE_001) stops motors via the OS "
            "if the brain is unresponsive, but cannot protect against OS crash or "
            "power failure. A dedicated hardware watchdog MCU (e.g. STM32 safety core) "
            "is required for SIL 2 / PLd compliance."
        ),
    },
    {
        "rule_id": "HARDWARE_002",
        "category": "hardware",
        "description": "Physical e-stop button — hardware-level actuator cut independent of software",
        "standard_refs": ["ISO 10218-1:2025 §5.x — emergency stop category 0/1"],
        "severity": "critical",
        "status": "hardware",
        "module": "N/A — hardware requirement",
        "notes": (
            "Software ESTOP (EMERGENCY_001) cuts motor commands at the OS layer. "
            "ISO 10218-1:2025 requires a physical hardware e-stop that cuts power "
            "to actuators independently of any software path."
        ),
    },
]


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest(safety_layer: Any = None, hardware_caps: Optional[dict] = None) -> dict:
    """Build the Protocol 66 conformance manifest.

    Args:
        safety_layer: Optional SafetyLayer instance to include live state
                      (e-stop status, active policies, violation counts).
        hardware_caps: Optional dict of hardware capability flags from the
                       robot's rcan-config.json ``hardware_safety`` block.
                       When provided, HARDWARE_001 and HARDWARE_002 rules are
                       annotated with status="implemented" if the corresponding
                       capabilities are declared as present.

    Returns:
        Dict suitable for JSON serialisation.
    """
    # Optionally annotate hardware rules based on declared hardware capabilities
    _hw_caps = hardware_caps or {}
    _rules_with_hw = []
    for rule in _P66_RULES:
        r = dict(rule)
        if _hw_caps:
            if r["rule_id"] == "HARDWARE_001" and _hw_caps.get("hardware_watchdog_mcu"):
                r = {
                    **r,
                    "status": "implemented",
                    "notes": (
                        r.get("notes", "")
                        + " [Declared implemented via hardware_safety.hardware_watchdog_mcu]"
                    ).strip(),
                }
            elif r["rule_id"] == "HARDWARE_002" and _hw_caps.get("physical_estop"):
                r = {
                    **r,
                    "status": "implemented",
                    "notes": (
                        r.get("notes", "")
                        + " [Declared implemented via hardware_safety.physical_estop]"
                    ).strip(),
                }
        _rules_with_hw.append(r)

    total = len(_rules_with_hw)
    by_status: dict[str, int] = {}
    by_category: dict[str, list[str]] = {}

    for rule in _rules_with_hw:
        s = rule["status"]
        by_status[s] = by_status.get(s, 0) + 1
        cat = rule["category"]
        by_category.setdefault(cat, []).append(rule["rule_id"])

    implemented = by_status.get("implemented", 0)
    partial = by_status.get("partial", 0)
    planned = by_status.get("planned", 0)
    hardware = by_status.get("hardware", 0)

    # v1.5/v1.6 invariants — each counts as 1 "implemented" item for conformance_pct
    # v1.5: replay_cache, sender_type, offline_mode, version_negotiation
    # v1.6: federation_trust, loa_enforcement, multimodal_commands
    V15_INVARIANT_COUNT = 7
    v15_implemented = V15_INVARIANT_COUNT  # all implemented in v1.5/v1.6
    implemented += v15_implemented

    live_state: dict = {}
    if safety_layer is not None:
        try:
            live_state = {
                "estopped": safety_layer.is_estopped,
                "active_policies": {
                    k: v["enabled"] for k, v in safety_layer.ns.read("/etc/safety/policies").items()
                }
                if safety_layer.ns.exists("/etc/safety/policies")
                else {},
                "lockout_count": len(getattr(safety_layer, "_lockouts", {})),
                "violation_counts": dict(getattr(safety_layer, "_violations", {})),
            }
        except Exception:
            live_state = {"error": "Could not read live state"}

    return {
        "manifest_version": "1.0",
        "protocol": "ContinuonOS Protocol 66 (OpenCastor independent implementation)",
        "rcan_spec_version": "1.8",
        "rcan_version": "1.6",  # v1.6: explicit rcan_version field
        "replay_cache_enabled": True,  # v1.5: GAP-03 replay prevention active
        "sender_type_logged": True,  # v1.5: GAP-08 cloud relay audit trail
        "offline_mode_capable": True,  # v1.5: GAP-06 offline operation support
        # v1.6 new fields (GAP-14, GAP-16, GAP-17, GAP-18)
        "federation_enabled": False,
        "supported_transports": ["http"],
        "min_loa_for_control": 1,
        "loa_enforcement": False,
        "multimodal_enabled": True,
        "opencastor_version": __import__("castor").__version__,
        "generated_at": int(time.time() * 1000),
        "summary": {
            "total_rules": total,
            "implemented": implemented,
            "partial": partial,
            "planned": planned,
            "hardware_dependent": hardware,
            "v15_invariants_implemented": v15_implemented,
            "conformance_pct": round(
                100
                * (implemented + partial * 0.5)
                / max(total - hardware + V15_INVARIANT_COUNT, 1),
                1,
            ),
        },
        "by_category": by_category,
        "hardware_capabilities": _hw_caps if _hw_caps else None,
        "rules": _rules_with_hw,
        "invariants": {
            "local_safety_always_wins": {
                "description": (
                    "All commands — local or remote RCAN — pass through SafetyLayer.write() "
                    "which enforces bounds, e-stop, rate limits, and protocol rules regardless "
                    "of command source. Remote commands are tagged source=rcan in audit but "
                    "receive no elevated trust."
                ),
                "status": "enforced",
                "module": "castor.fs.safety.SafetyLayer.write_remote",
            },
            "safety_messages_bypass_queues": {
                "description": (
                    "RCAN MessageType.SAFETY (type 6) messages are dispatched immediately "
                    "via POST /api/safety/rcan without entering the HiTL pending queue or "
                    "confidence gate. Priority.SAFETY messages also skip the internal "
                    "dispatch queue."
                ),
                "status": "enforced",
                "module": "castor.api.rcan_safety_message + castor.rcan.message.Priority.SAFETY",
            },
            "estop_requires_explicit_clear": {
                "description": (
                    "ESTOP cannot be cleared by a RCAN RESUME if the e-stop was triggered "
                    "by a local sensor. Manual clear via POST /api/estop/clear (requires "
                    "CAP_SAFETY_OVERRIDE or root) is always required after an ESTOP."
                ),
                "status": "enforced",
                "module": "castor.fs.safety.SafetyLayer.clear_estop",
            },
            "ai_cannot_override_safety": {
                "description": (
                    "AI-generated commands are subject to the same SafetyLayer checks as "
                    "any other command. Anti-subversion scanning (castor.safety.anti_subversion) "
                    "runs before any AI-generated /dev/ write. AI models cannot disable, "
                    "modify, or bypass safety policies (requires root principal)."
                ),
                "status": "enforced",
                "module": "castor.safety.anti_subversion + castor.fs.safety.SafetyLayer",
            },
            "audit_trail_complete": {
                "description": (
                    "Every write, denial, and safety event is logged to /var/log/actions, "
                    "/var/log/safety, /var/log/access. AI-generated commands include "
                    "model_provider, model_id, inference_confidence, inference_latency_ms, "
                    "thought_id per RCAN §16.1."
                ),
                "status": "enforced",
                "module": "castor.audit + castor.fs.safety.SafetyLayer._audit_action",
            },
            # v1.5 invariants
            "replay_attack_prevention": {
                "description": (
                    "Commands are checked against a sliding-window replay cache "
                    "(30s normal, 10s safety) before R2RAM authorization. Replayed "
                    "commands are rejected with status=replay_rejected. "
                    "Implements RCAN §8.3 (GAP-03)."
                ),
                "status": "enforced",
                "module": "castor.cloud.bridge.CastorBridge._check_replay",
                "rcan_spec": "v1.5 §8.3",
            },
            "sender_type_audit_trail": {
                "description": (
                    "sender_type field is read from every Firestore command doc and "
                    "included in all audit entries. Cloud Function commands are tagged "
                    "with cloud_relay=true. Closes GAP-08 — cloud relay identity audit gap."
                ),
                "status": "enforced",
                "module": "castor.cloud.bridge.CastorBridge._execute_command",
                "rcan_spec": "v1.5 §8.5",
            },
            "offline_mode_restriction": {
                "description": (
                    "Bridge tracks last Firestore contact time. After 300s of no "
                    "connectivity, enters offline mode and restricts to ESTOP-only "
                    "commands. ESTOP is never blocked by offline mode (Protocol 66 invariant). "
                    "Implements RCAN §14 (GAP-06)."
                ),
                "status": "enforced",
                "module": "castor.cloud.bridge.CastorBridge._check_offline_mode",
                "rcan_spec": "v1.5 §14",
            },
            "rcan_version_negotiation": {
                "description": (
                    "Outgoing messages include rcan_version='1.6'. Incoming messages "
                    "with different rcan_version log a WARNING (not error) and are "
                    "processed with forward/backward compatibility rules. "
                    "Implements RCAN §3.5 (GAP-12)."
                ),
                "status": "enforced",
                "module": "castor.rcan.message.RCANMessage.from_dict",
                "rcan_spec": "v1.6 §3.5",
            },
            "federation_trust": {
                "description": (
                    "Cross-registry commands are validated against trust anchors "
                    "via rcan.federation.validate_cross_registry_command(). "
                    "ESTOP commands bypass federation checks (P66 invariant). "
                    "Federation disabled by default; opt-in via federation_enabled flag. "
                    "Implements RCAN v1.6 (GAP-14)."
                ),
                "status": "enforced",
                "module": "castor.cloud.bridge.CastorBridge._check_federation",
                "rcan_spec": "v1.6 §GAP-14",
            },
            "loa_enforcement": {
                "description": (
                    "Level of Assurance (LoA) is extracted from JWT tokens on every command. "
                    "Default: log-only (LoA 1 backward compat). "
                    "Enforce via loa_enforcement=True in config. "
                    "Implements RCAN v1.6 (GAP-16)."
                ),
                "status": "enforced",
                "module": "castor.cloud.bridge.CastorBridge._check_loa",
                "rcan_spec": "v1.6 §GAP-16",
            },
            "multimodal_commands": {
                "description": (
                    "Commands can carry media_chunks (base64 images, audio). "
                    "Chunks are extracted and passed to vision-capable providers. "
                    "TRAINING_DATA commands log chunk SHA-256 hashes to audit trail. "
                    "Implements RCAN v1.6 (GAP-18)."
                ),
                "status": "enforced",
                "module": "castor.cloud.bridge.CastorBridge._handle_media_chunks",
                "rcan_spec": "v1.6 §GAP-18",
            },
        },
        "compliance_refs": {
            "ISO_10218_1_2025": "Aligned (see rcan-spec/docs/compliance/iso-10218-alignment.md)",
            "EU_AI_Act_2024_1689": (
                "Art. 12 (record-keeping), Art. 14 (human oversight), Art. 26 (deployer) "
                "— partial coverage (see rcan-spec/docs/compliance/eu-ai-act-mapping.md)"
            ),
            "IEC_62443": "Informative alignment — see rcan-spec/docs/compliance/iec-62443-alignment.md",
            "NIST_AI_RMF": "Informative alignment — see rcan-spec/docs/compliance/nist-ai-rmf-alignment.md",
        },
        "live_state": live_state,
    }
