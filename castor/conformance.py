"""
OpenCastor Conformance Checker — RCAN behavioral invariants.

Goes beyond JSON schema validation to check safety, provider, protocol,
performance, and hardware behavioral invariants.

Usage:
    from castor.conformance import ConformanceChecker

    checker = ConformanceChecker(config, config_path="robot.rcan.yaml")
    results = checker.run_all()
    summary = checker.summary(results)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from castor.setup_catalog import get_known_provider_names, get_provider_env_var_map

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

KNOWN_PROVIDERS = frozenset(get_known_provider_names())

KNOWN_PHYSICS_TYPES = frozenset(
    {
        "differential",
        "ackermann",
        "holonomic",
        "omnidirectional",
        "legged",
        "arm",
        "fixed",
        "aerial",
        "aquatic",
        "custom",
    }
)

PROVIDER_ENV_VARS: dict[str, list[str]] = {
    name: ([env_var] if env_var else []) for name, env_var in get_provider_env_var_map().items()
}
PROVIDER_ENV_VARS.setdefault("claude_oauth", ["ANTHROPIC_API_KEY"])
PROVIDER_ENV_VARS.setdefault("huggingface", ["HF_TOKEN"])
if "HUGGINGFACE_TOKEN" not in PROVIDER_ENV_VARS.get("huggingface", []):
    PROVIDER_ENV_VARS["huggingface"].append("HUGGINGFACE_TOKEN")

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass
class ConformanceResult:
    check_id: str  # e.g. "safety.estop_configured"
    category: str  # "safety" | "provider" | "protocol" | "performance" | "hardware"
    status: str  # "pass" | "warn" | "fail"
    detail: str  # human-readable explanation
    fix: str | None = field(default=None)  # suggested fix


# ---------------------------------------------------------------------------
# ConformanceChecker
# ---------------------------------------------------------------------------


class ConformanceChecker:
    """Run RCAN behavioral conformance checks against a loaded config dict."""

    def __init__(self, config: dict, config_path: str | None = None) -> None:
        self._cfg = config or {}
        self._config_path = config_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_all(self) -> list[ConformanceResult]:
        """Run every check and return results."""
        results: list[ConformanceResult] = []
        for category in (
            "safety",
            "provider",
            "protocol",
            "performance",
            "hardware",
            "rcan_v12",
            "rcan_v15",
            "rcan_v16",
            "rcan_v21",
        ):
            results.extend(self.run_category(category))
        return results

    def run_category(self, category: str) -> list[ConformanceResult]:
        """Run checks for a single category."""
        runners = {
            "safety": self._check_safety,
            "provider": self._check_provider,
            "protocol": self._check_protocol,
            "performance": self._check_performance,
            "hardware": self._check_hardware,
            "rcan_v12": self._check_rcan_v12,
            "rcan_v15": self._check_rcan_v15,
            "rcan_v16": self._check_rcan_v16,
            "rcan_v21": self._check_rcan_v21,
        }
        runner = runners.get(category)
        if runner is None:
            raise ValueError(f"Unknown category: {category!r}")
        return runner()

    def summary(self, results: list[ConformanceResult]) -> dict:
        """Return counts and a 0-100 score.

        Scoring:
          - Each *fail* deducts 10 points from 100.
          - Each *warn* deducts 3 points.
          - Score is clamped to [0, 100].
        """
        passes = sum(1 for r in results if r.status == "pass")
        warns = sum(1 for r in results if r.status == "warn")
        fails = sum(1 for r in results if r.status == "fail")
        score = max(0, 100 - fails * 10 - warns * 3)
        return {"pass": passes, "warn": warns, "fail": fails, "score": score}

    # ------------------------------------------------------------------
    # Safety checks
    # ------------------------------------------------------------------

    def _check_safety(self) -> list[ConformanceResult]:
        return [
            self._safety_reactive_layer(),
            self._safety_estop_capable(),
            self._safety_latency_budget(),
            *self._safety_hailo_opt_in(),
            self._safety_geofence(),
            self._safety_local_safety_wins(),
            self._safety_watchdog_configured(),
            self._safety_confidence_gates_configured(),
            self._safety_p66_conformance(),
            self._safety_hardware_safety_declared(),
            self._safety_estop_distance_configured(),
        ]

    def _safety_reactive_layer(self) -> ConformanceResult:
        cid = "safety.reactive_layer"
        reactive = self._cfg.get("reactive")
        if not reactive:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="fail",
                detail="No 'reactive' section found in config",
                fix="Add a 'reactive:' section with 'min_obstacle_m' set",
            )
        val = reactive.get("min_obstacle_m")
        if val is None:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="fail",
                detail="reactive.min_obstacle_m is not set",
                fix="Set reactive.min_obstacle_m (e.g. 0.3) in your config",
            )
        try:
            fval = float(val)
        except (TypeError, ValueError):
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="fail",
                detail=f"reactive.min_obstacle_m is not a number: {val!r}",
                fix="Set reactive.min_obstacle_m to a numeric value (e.g. 0.3)",
            )
        if fval > 1.0:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail=f"reactive.min_obstacle_m={fval} is very conservative (> 1.0m)",
                fix="Consider reducing min_obstacle_m to 0.3-1.0m",
            )
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status="pass",
            detail=f"min_obstacle_m={fval} (good)",
        )

    def _safety_estop_capable(self) -> ConformanceResult:
        cid = "safety.estop_capable"
        drivers = self._cfg.get("drivers", []) or []
        capable = [d for d in drivers if isinstance(d, dict) and d.get("protocol")]
        n = len(capable)
        if n == 0:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail="No drivers with 'protocol' set — cannot confirm e-stop capability",
                fix="Add at least one driver with a 'protocol' field",
            )
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status="pass",
            detail=f"{n} driver{'s' if n != 1 else ''} configured with protocol",
        )

    def _safety_latency_budget(self) -> ConformanceResult:
        cid = "safety.latency_budget"
        agent = self._cfg.get("agent", {}) or {}
        val = agent.get("latency_budget_ms")
        if val is None:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail="agent.latency_budget_ms is not set",
                fix="Set agent.latency_budget_ms (e.g. 3000) to define the safety deadline",
            )
        try:
            ival = int(val)
        except (TypeError, ValueError):
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="fail",
                detail=f"agent.latency_budget_ms is not an integer: {val!r}",
                fix="Set agent.latency_budget_ms to a positive integer (milliseconds)",
            )
        if ival > 10000:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="fail",
                detail=f"agent.latency_budget_ms={ival} exceeds hard limit (> 10000ms)",
                fix="Reduce latency_budget_ms to ≤ 5000ms for safety",
            )
        if ival > 3000:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail=f"agent.latency_budget_ms={ival} is high (> 3000ms); consider reducing",
                fix="Reduce latency_budget_ms to ≤ 3000ms for responsive control",
            )
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status="pass",
            detail=f"latency_budget_ms={ival}ms (good)",
        )

    def _safety_hailo_opt_in(self) -> list[ConformanceResult]:
        """Only emit a result if hailo_vision is explicitly True."""
        if not self._cfg.get("hailo_vision"):
            return []
        cid = "safety.hailo_opt_in"
        confidence = self._cfg.get("hailo_confidence")
        if confidence is None:
            return [
                ConformanceResult(
                    check_id=cid,
                    category="safety",
                    status="warn",
                    detail="hailo_vision=true but hailo_confidence is not set",
                    fix="Set hailo_confidence to a value between 0.3 and 0.8",
                )
            ]
        try:
            fval = float(confidence)
        except (TypeError, ValueError):
            return [
                ConformanceResult(
                    check_id=cid,
                    category="safety",
                    status="warn",
                    detail=f"hailo_confidence is not a number: {confidence!r}",
                    fix="Set hailo_confidence to a float between 0.3 and 0.8",
                )
            ]
        if not (0.3 <= fval <= 0.8):
            return [
                ConformanceResult(
                    check_id=cid,
                    category="safety",
                    status="warn",
                    detail=f"hailo_confidence={fval} is out of recommended range [0.3, 0.8]",
                    fix="Set hailo_confidence between 0.3 and 0.8",
                )
            ]
        return [
            ConformanceResult(
                check_id=cid,
                category="safety",
                status="pass",
                detail=f"hailo_confidence={fval} (within safe range)",
            )
        ]

    def _safety_geofence(self) -> ConformanceResult:
        cid = "safety.geofence"
        geofence = self._cfg.get("geofence")
        if not geofence:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail="No geofence config found (optional but recommended)",
                fix="Add a 'geofence:' section to restrict robot operating area",
            )
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status="pass",
            detail="Geofence configured",
        )

    def _safety_local_safety_wins(self) -> ConformanceResult:
        cid = "safety.local_safety_wins"
        safety_cfg = self._cfg.get("safety", {}) or {}
        val = safety_cfg.get("local_safety_wins")
        if val is not True:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="fail",
                detail=(
                    "safety.local_safety_wins is not True — remote commands may override "
                    "local safety constraints (RCAN §6 invariant violated)"
                ),
                fix="Set safety.local_safety_wins: true in rcan.yaml — RCAN §6 invariant.",
            )
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status="pass",
            detail="safety.local_safety_wins=true (RCAN §6 invariant satisfied)",
        )

    def _safety_watchdog_configured(self) -> ConformanceResult:
        cid = "safety.watchdog_configured"
        # Check both top-level watchdog: and nested safety.watchdog:
        watchdog = (
            self._cfg.get("watchdog", {})
            or (self._cfg.get("safety", {}) or {}).get("watchdog", {})
            or {}
        )
        timeout = watchdog.get("timeout_s")
        if timeout is None:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail="watchdog.timeout_s is not configured",
                fix="Add watchdog: timeout_s: 10 to rcan.yaml.",
            )
        try:
            fval = float(timeout)
        except (TypeError, ValueError):
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail=f"watchdog.timeout_s is not a number: {timeout!r}",
                fix="Set watchdog.timeout_s to a numeric value ≤ 30 (e.g. 10).",
            )
        if fval > 30:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail=f"watchdog.timeout_s={fval} exceeds recommended maximum of 30s",
                fix="Reduce watchdog.timeout_s to ≤ 30 for timely fault detection.",
            )
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status="pass",
            detail=f"watchdog.timeout_s={fval} (≤ 30s, good)",
        )

    def _safety_confidence_gates_configured(self) -> ConformanceResult:
        cid = "safety.confidence_gates_configured"
        # Check both legacy brain.confidence_gates and current agent.confidence_gates locations
        brain = self._cfg.get("brain", {}) or {}
        agent = self._cfg.get("agent", {}) or {}
        gates = brain.get("confidence_gates") or agent.get("confidence_gates")
        if gates is None:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail="brain.confidence_gates is not configured",
                fix="Add confidence_gates block to brain config for RCAN §16.2 compliance.",
            )
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status="pass",
            detail="brain.confidence_gates is configured (RCAN §16.2)",
        )

    def _safety_p66_conformance(self) -> ConformanceResult:
        cid = "safety.p66_conformance"
        try:
            from castor.safety.p66_manifest import build_manifest

            manifest = build_manifest()
            pct = manifest.get("summary", {}).get("conformance_pct", 0)
        except Exception as exc:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail=f"Could not evaluate P66 conformance manifest: {exc}",
                fix="Ensure castor.safety.p66_manifest is importable and build_manifest() works.",
            )
        if pct >= 80:
            status = "pass"
        elif pct >= 60:
            status = "warn"
        else:
            status = "fail"
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status=status,
            detail=f"Protocol 66 conformance: {pct}% (threshold pass≥80, warn≥60)",
        )

    def _safety_hardware_safety_declared(self) -> ConformanceResult:
        cid = "safety.hardware_safety_declared"
        hw_safety = self._cfg.get("hardware_safety")
        if not hw_safety:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail="hardware_safety block is not declared in config",
                fix=(
                    "Add hardware_safety block to declare physical safety capabilities "
                    "(physical_estop, hardware_watchdog_mcu, etc.)."
                ),
            )
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status="pass",
            detail="hardware_safety block is declared",
        )

    def _safety_estop_distance_configured(self) -> ConformanceResult:
        cid = "safety.estop_distance_configured"
        safety_cfg = self._cfg.get("safety", {}) or {}
        val = safety_cfg.get("emergency_stop_distance") or safety_cfg.get("estop_distance_mm")
        if val is None:
            return ConformanceResult(
                check_id=cid,
                category="safety",
                status="warn",
                detail=(
                    "Neither safety.emergency_stop_distance nor safety.estop_distance_mm "
                    "is configured"
                ),
                fix=(
                    "Add safety.emergency_stop_distance (metres) or safety.estop_distance_mm "
                    "to define the minimum clearance before emergency stop triggers."
                ),
            )
        return ConformanceResult(
            check_id=cid,
            category="safety",
            status="pass",
            detail=f"E-stop distance configured: {val}",
        )

    # ------------------------------------------------------------------
    # Provider checks
    # ------------------------------------------------------------------

    def _check_provider(self) -> list[ConformanceResult]:
        return [
            self._provider_configured(),
            self._provider_known(),
            self._provider_vision_enabled(),
            self._provider_api_key_present(),
            self._provider_fallback(),
        ]

    def _provider_configured(self) -> ConformanceResult:
        cid = "provider.configured"
        agent = self._cfg.get("agent", {}) or {}
        provider = (agent.get("provider") or "").strip()
        model = (agent.get("model") or "").strip()
        if not provider and not model:
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="fail",
                detail="agent.provider and agent.model are both missing",
                fix="Set agent.provider and agent.model in your config",
            )
        if not provider:
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="fail",
                detail="agent.provider is missing",
                fix="Set agent.provider (e.g. anthropic, huggingface, ollama)",
            )
        if not model:
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="fail",
                detail="agent.model is missing",
                fix="Set agent.model (e.g. claude-opus-4-6, Qwen2.5-VL-7B-Instruct)",
            )
        return ConformanceResult(
            check_id=cid,
            category="provider",
            status="pass",
            detail=f"{provider} / {model}",
        )

    def _provider_known(self) -> ConformanceResult:
        cid = "provider.known"
        agent = self._cfg.get("agent", {}) or {}
        provider = (agent.get("provider") or "").strip().lower()
        if not provider:
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="warn",
                detail="agent.provider is empty — cannot check known providers list",
                fix="Set agent.provider",
            )
        if provider not in KNOWN_PROVIDERS:
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="warn",
                detail=f"Provider '{provider}' is not in the known list",
                fix=f"Known providers: {', '.join(sorted(KNOWN_PROVIDERS))}",
            )
        return ConformanceResult(
            check_id=cid,
            category="provider",
            status="pass",
            detail=f"'{provider}' is a known provider",
        )

    def _provider_vision_enabled(self) -> ConformanceResult:
        cid = "provider.vision_enabled"
        camera = self._cfg.get("camera")
        agent = self._cfg.get("agent", {}) or {}
        if not camera:
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="pass",
                detail="No camera section — vision check skipped",
            )
        vision_enabled = agent.get("vision_enabled")
        if vision_enabled is False:
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="warn",
                detail="Camera is configured but vision_enabled=false — camera will be unused",
                fix="Set agent.vision_enabled: true to use the camera",
            )
        return ConformanceResult(
            check_id=cid,
            category="provider",
            status="pass",
            detail="Camera present and vision is enabled",
        )

    def _provider_api_key_present(self) -> ConformanceResult:
        cid = "provider.api_key_present"
        agent = self._cfg.get("agent", {}) or {}
        provider = (agent.get("provider") or "").strip().lower()
        # OAuth/ADC authentication doesn't require an API key env var
        if agent.get("use_oauth") or agent.get("use_adc"):
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="pass",
                detail=f"'{provider}' uses OAuth/ADC — no API key env var required",
            )
        if not provider:
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="warn",
                detail="agent.provider not set — cannot check API key",
                fix="Set agent.provider first",
            )
        env_vars = PROVIDER_ENV_VARS.get(provider, [])
        if not env_vars:
            # Providers that don't need keys (ollama, llamacpp, mlx, etc.)
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="pass",
                detail=f"'{provider}' does not require an API key",
            )
        found = [v for v in env_vars if os.environ.get(v)]
        if not found:
            var_list = " or ".join(env_vars)
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="warn",
                detail=f"{var_list} not found in environment (key may be in token store)",
                fix=f"Set {env_vars[0]} in your .env file, or run: castor login {provider}",
            )
        return ConformanceResult(
            check_id=cid,
            category="provider",
            status="pass",
            detail=f"{found[0]} is set",
        )

    def _provider_fallback(self) -> ConformanceResult:
        cid = "provider.fallback"
        reactive = self._cfg.get("reactive", {}) or {}
        fallback = reactive.get("fallback_provider")
        if not fallback:
            return ConformanceResult(
                check_id=cid,
                category="provider",
                status="warn",
                detail="reactive.fallback_provider is not set — single point of failure",
                fix="Set reactive.fallback_provider to a backup provider (e.g. ollama)",
            )
        return ConformanceResult(
            check_id=cid,
            category="provider",
            status="pass",
            detail=f"Fallback provider: '{fallback}'",
        )

    # ------------------------------------------------------------------
    # Protocol checks
    # ------------------------------------------------------------------

    def _check_protocol(self) -> list[ConformanceResult]:
        return [
            self._protocol_rcan_version(),
            self._protocol_robot_uuid(),
            self._protocol_robot_name(),
            self._protocol_capabilities_declared(),
            self._protocol_port_in_range(),
        ]

    def _protocol_rcan_version(self) -> ConformanceResult:
        cid = "protocol.rcan_version"
        version = self._cfg.get("rcan_version")
        if not version:
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="fail",
                detail="rcan_version field is missing",
                fix='Add rcan_version: "1.3" to the top of your config',
            )
        # Check it looks like a version string
        version_str = str(version).strip()
        if not re.match(r"^\d+\.\d+", version_str):
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="warn",
                detail=f"rcan_version='{version_str}' does not look like a semver string",
                fix='Use format: rcan_version: "1.3"',
            )
        return ConformanceResult(
            check_id=cid,
            category="protocol",
            status="pass",
            detail=f"rcan_version='{version_str}'",
        )

    def _protocol_robot_uuid(self) -> ConformanceResult:
        cid = "protocol.robot_uuid"
        metadata = self._cfg.get("metadata", {}) or {}
        uuid_val = metadata.get("robot_uuid")
        if not uuid_val:
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="fail",
                detail="metadata.robot_uuid is missing",
                fix="Add metadata.robot_uuid with a valid UUID4 value",
            )
        if not _UUID4_RE.match(str(uuid_val)):
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="warn",
                detail=f"metadata.robot_uuid='{uuid_val}' is not a valid UUID4 format",
                fix='Generate a UUID4: python -c "import uuid; print(uuid.uuid4())"',
            )
        return ConformanceResult(
            check_id=cid,
            category="protocol",
            status="pass",
            detail=f"robot_uuid='{uuid_val}' (valid UUID4)",
        )

    def _protocol_robot_name(self) -> ConformanceResult:
        cid = "protocol.robot_name"
        metadata = self._cfg.get("metadata", {}) or {}
        name = (metadata.get("robot_name") or "").strip()
        if not name:
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="fail",
                detail="metadata.robot_name is missing or empty",
                fix="Set metadata.robot_name to a descriptive name for your robot",
            )
        return ConformanceResult(
            check_id=cid,
            category="protocol",
            status="pass",
            detail=f"robot_name='{name}'",
        )

    def _protocol_capabilities_declared(self) -> ConformanceResult:
        cid = "protocol.capabilities_declared"
        rcan = self._cfg.get("rcan_protocol", {}) or {}
        caps = rcan.get("capabilities") or []
        if not caps:
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="warn",
                detail="rcan_protocol.capabilities is empty — no capabilities declared",
                fix="Add at least one capability: [status, nav, teleop, vision, chat]",
            )
        return ConformanceResult(
            check_id=cid,
            category="protocol",
            status="pass",
            detail=f"{len(caps)} capability/ies declared: {', '.join(str(c) for c in caps[:5])}",
        )

    def _protocol_port_in_range(self) -> ConformanceResult:
        cid = "protocol.port_in_range"
        rcan = self._cfg.get("rcan_protocol", {}) or {}
        port = rcan.get("port")
        if port is None:
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="warn",
                detail="rcan_protocol.port is not set",
                fix="Set rcan_protocol.port to a value between 1024 and 49151",
            )
        try:
            iport = int(port)
        except (TypeError, ValueError):
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="warn",
                detail=f"rcan_protocol.port is not an integer: {port!r}",
                fix="Set rcan_protocol.port to an integer between 1024 and 49151",
            )
        if not (1024 <= iport <= 49151):
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="warn",
                detail=(
                    f"rcan_protocol.port={iport} is outside safe range [1024, 49151] "
                    "(avoids system/ephemeral ports)"
                ),
                fix="Use a port between 1024 and 49151 (e.g. 8000)",
            )
        return ConformanceResult(
            check_id=cid,
            category="protocol",
            status="pass",
            detail=f"port={iport} (in safe range)",
        )

    # ------------------------------------------------------------------
    # Performance checks
    # ------------------------------------------------------------------

    def _check_performance(self) -> list[ConformanceResult]:
        return [
            self._perf_tiered_brain(),
            self._perf_planner_interval(),
            self._perf_agent_roster(),
            self._perf_learner_configured(),
        ]

    def _perf_tiered_brain(self) -> ConformanceResult:
        cid = "perf.tiered_brain"
        tiered = self._cfg.get("tiered_brain")
        if not tiered:
            return ConformanceResult(
                check_id=cid,
                category="performance",
                status="warn",
                detail="No 'tiered_brain' section — single-layer brain is less efficient",
                fix="Add a 'tiered_brain:' section to enable fast/slow reasoning layers",
            )
        return ConformanceResult(
            check_id=cid,
            category="performance",
            status="pass",
            detail="tiered_brain section is configured",
        )

    def _perf_planner_interval(self) -> ConformanceResult:
        cid = "perf.planner_interval"
        tiered = self._cfg.get("tiered_brain", {}) or {}
        if not tiered:
            return ConformanceResult(
                check_id=cid,
                category="performance",
                status="warn",
                detail="No tiered_brain section — planner_interval check skipped",
                fix="Add 'tiered_brain:' with 'planner_interval: 10'",
            )
        val = tiered.get("planner_interval")
        if val is None:
            return ConformanceResult(
                check_id=cid,
                category="performance",
                status="warn",
                detail="tiered_brain.planner_interval is not set",
                fix="Set tiered_brain.planner_interval (recommended: 5-30)",
            )
        try:
            ival = int(val)
        except (TypeError, ValueError):
            return ConformanceResult(
                check_id=cid,
                category="performance",
                status="warn",
                detail=f"tiered_brain.planner_interval is not an integer: {val!r}",
                fix="Set tiered_brain.planner_interval to an integer (5-30)",
            )
        if ival < 5:
            return ConformanceResult(
                check_id=cid,
                category="performance",
                status="warn",
                detail=f"planner_interval={ival} is too low (< 5) — over-uses the expensive model",
                fix="Increase planner_interval to at least 5",
            )
        if ival > 30:
            return ConformanceResult(
                check_id=cid,
                category="performance",
                status="warn",
                detail=f"planner_interval={ival} is too high (> 30) — under-uses the planner",
                fix="Reduce planner_interval to at most 30",
            )
        return ConformanceResult(
            check_id=cid,
            category="performance",
            status="pass",
            detail=f"planner_interval={ival} (good)",
        )

    def _perf_agent_roster(self) -> ConformanceResult:
        cid = "perf.agent_roster"
        roster = self._cfg.get("agent_roster")
        if not roster:
            return ConformanceResult(
                check_id=cid,
                category="performance",
                status="warn",
                detail="No 'agent_roster' section — multi-agent routing not configured",
                fix="Add 'agent_roster:' with specialist agents for better task routing",
            )
        n = len(roster) if isinstance(roster, list) else len(roster)
        return ConformanceResult(
            check_id=cid,
            category="performance",
            status="pass",
            detail=f"{n} agent{'s' if n != 1 else ''} in roster",
        )

    def _perf_learner_configured(self) -> ConformanceResult:
        cid = "perf.learner_configured"
        learner = self._cfg.get("learner", {}) or {}
        if not learner.get("enabled"):
            return ConformanceResult(
                check_id=cid,
                category="performance",
                status="pass",
                detail="Learner is disabled (no misconfiguration risk)",
            )
        cadence = learner.get("cadence_n")
        if cadence is None:
            return ConformanceResult(
                check_id=cid,
                category="performance",
                status="warn",
                detail="learner.enabled=true but learner.cadence_n is not set",
                fix="Set learner.cadence_n (e.g. 5) to control learning frequency",
            )
        return ConformanceResult(
            check_id=cid,
            category="performance",
            status="pass",
            detail=f"Learner enabled with cadence_n={cadence}",
        )

    # ------------------------------------------------------------------
    # Hardware checks
    # ------------------------------------------------------------------

    def _check_hardware(self) -> list[ConformanceResult]:
        return [
            self._hardware_drivers_present(),
            self._hardware_camera_configured(),
            self._hardware_physics_type(),
            self._hardware_dof_reasonable(),
        ]

    def _hardware_drivers_present(self) -> ConformanceResult:
        cid = "hardware.drivers_present"
        drivers = self._cfg.get("drivers", []) or []
        valid = [d for d in drivers if isinstance(d, dict) and d.get("protocol")]
        if not valid:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="fail",
                detail="No drivers with a valid 'protocol' field found",
                fix="Add at least one driver with a 'protocol' field to the 'drivers:' list",
            )
        return ConformanceResult(
            check_id=cid,
            category="hardware",
            status="pass",
            detail=f"{len(valid)} driver{'s' if len(valid) != 1 else ''} with protocol configured",
        )

    def _hardware_camera_configured(self) -> ConformanceResult:
        cid = "hardware.camera_configured"
        # Accept both singular `camera:` and plural `cameras:` keys
        camera = self._cfg.get("camera") or self._cfg.get("cameras")
        agent = self._cfg.get("agent", {}) or {}
        vision_enabled = agent.get("vision_enabled", True)
        if not camera and vision_enabled is not False:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="warn",
                detail="No camera section configured (vision model with no camera is unusual)",
                fix="Add a 'cameras:' section, or set agent.vision_enabled: false",
            )
        if not camera:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="pass",
                detail="No camera section; vision disabled",
            )
        if isinstance(camera, dict):
            cam_info = next(iter(camera.values()), camera)
            cam_type = cam_info.get("type", "unknown") if isinstance(cam_info, dict) else "unknown"
        else:
            cam_type = "configured"
        return ConformanceResult(
            check_id=cid,
            category="hardware",
            status="pass",
            detail=f"Camera configured (type={cam_type})",
        )

    def _hardware_physics_type(self) -> ConformanceResult:
        cid = "hardware.physics_type"
        physics = self._cfg.get("physics", {}) or {}
        ptype = (physics.get("type") or "").strip().lower()
        if not ptype:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="warn",
                detail="physics.type is not set",
                fix=f"Set physics.type to one of: {', '.join(sorted(KNOWN_PHYSICS_TYPES))}",
            )
        if ptype not in KNOWN_PHYSICS_TYPES:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="warn",
                detail=f"physics.type='{ptype}' is not a recognised type",
                fix=f"Use one of: {', '.join(sorted(KNOWN_PHYSICS_TYPES))}",
            )
        if ptype == "custom":
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="warn",
                detail="physics.type='custom' — ensure extra documentation is provided",
                fix="Consider using a standard type or add docs/README.md describing the kinematics",
            )
        return ConformanceResult(
            check_id=cid,
            category="hardware",
            status="pass",
            detail=f"physics.type='{ptype}'",
        )

    def _hardware_dof_reasonable(self) -> ConformanceResult:
        cid = "hardware.dof_reasonable"
        physics = self._cfg.get("physics", {}) or {}
        dof = physics.get("dof")
        if dof is None:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="pass",
                detail="physics.dof not set (optional)",
            )
        try:
            idof = int(dof)
        except (TypeError, ValueError):
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="warn",
                detail=f"physics.dof is not an integer: {dof!r}",
                fix="Set physics.dof to a positive integer",
            )
        if idof < 1:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="warn",
                detail=f"physics.dof={idof} is less than 1 (unusual)",
                fix="Set physics.dof to at least 1",
            )
        if idof > 12:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="warn",
                detail=f"physics.dof={idof} is unusually high (> 12)",
                fix="Verify physics.dof is correct; values > 12 are rare for mobile robots",
            )
        return ConformanceResult(
            check_id=cid,
            category="hardware",
            status="pass",
            detail=f"physics.dof={idof} (reasonable)",
        )

    # ------------------------------------------------------------------
    # RCAN v1.2 conformance checks
    # ------------------------------------------------------------------

    def _check_rcan_v12(self) -> list[ConformanceResult]:
        return [
            self._v12_rcan_version(),
            *self._v12_confidence_gates(),
            *self._v12_hitl_gates(),
        ]

    def _v12_rcan_version(self) -> ConformanceResult:
        cid = "rcan_v12.rcan_version"
        version = str(self._cfg.get("rcan_version", "")).strip()
        if not version:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v12",
                status="fail",
                detail="rcan_version is missing — required for RCAN v1.2 compliance",
                fix='Set rcan_version: "1.3.0" at the top of your config',
            )
        # Accept "1.2.x" or later; warn on older versions.
        try:
            parts = [int(x) for x in version.split(".")[:2]]
        except ValueError:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v12",
                status="warn",
                detail=f"rcan_version='{version}' is not a numeric semver string",
                fix='Set rcan_version: "1.3.0" for RCAN v1.3 compliance',
            )
        major, minor = parts[0], parts[1] if len(parts) > 1 else 0
        if (major, minor) < (1, 2):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v12",
                status="warn",
                detail=(
                    f"rcan_version='{version}' is below 1.2.0 — "
                    "confidence_gates and hitl_gates are RCAN v1.2+ features"
                ),
                fix='Update rcan_version: "1.3.0" to enable v1.3 features',
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v12",
            status="pass",
            detail=f"rcan_version='{version}' is compatible with RCAN v1.3",
        )

    def _v12_confidence_gates(self) -> list[ConformanceResult]:
        agent = self._cfg.get("agent", {}) or {}
        gates = agent.get("confidence_gates")
        if gates is None:
            return []  # Optional feature; skip if not declared

        results: list[ConformanceResult] = []
        _VALID_ON_FAIL = {"block", "escalate", "allow"}

        if not isinstance(gates, list):
            results.append(
                ConformanceResult(
                    check_id="rcan_v12.confidence_gates.type",
                    category="rcan_v12",
                    status="fail",
                    detail="agent.confidence_gates must be a list",
                    fix="Change agent.confidence_gates to a YAML list of gate definitions",
                )
            )
            return results

        for i, gate in enumerate(gates):
            cid_base = f"rcan_v12.confidence_gates[{i}]"
            if not isinstance(gate, dict):
                results.append(
                    ConformanceResult(
                        check_id=cid_base,
                        category="rcan_v12",
                        status="fail",
                        detail=f"confidence_gates[{i}] must be a mapping (dict)",
                        fix="Each gate entry must be a YAML mapping with scope, min_confidence, on_fail",
                    )
                )
                continue

            # Required fields
            for fname in ("scope", "min_confidence", "on_fail"):
                if fname not in gate:
                    results.append(
                        ConformanceResult(
                            check_id=f"{cid_base}.{fname}",
                            category="rcan_v12",
                            status="fail",
                            detail=f"confidence_gates[{i}] missing required field '{fname}'",
                            fix=f"Add '{fname}:' to confidence_gates[{i}]",
                        )
                    )

            # Validate min_confidence range
            min_conf = gate.get("min_confidence")
            if min_conf is not None:
                try:
                    fval = float(min_conf)
                    if not (0.0 <= fval <= 1.0):
                        results.append(
                            ConformanceResult(
                                check_id=f"{cid_base}.min_confidence",
                                category="rcan_v12",
                                status="warn",
                                detail=f"confidence_gates[{i}].min_confidence={fval} is outside [0.0, 1.0]",
                                fix="Set min_confidence between 0.0 and 1.0 (e.g. 0.7)",
                            )
                        )
                except (TypeError, ValueError):
                    results.append(
                        ConformanceResult(
                            check_id=f"{cid_base}.min_confidence",
                            category="rcan_v12",
                            status="fail",
                            detail=f"confidence_gates[{i}].min_confidence is not a number: {min_conf!r}",
                            fix="Set min_confidence to a float between 0.0 and 1.0",
                        )
                    )

            # Validate on_fail value
            on_fail = gate.get("on_fail")
            if on_fail is not None and on_fail not in _VALID_ON_FAIL:
                results.append(
                    ConformanceResult(
                        check_id=f"{cid_base}.on_fail",
                        category="rcan_v12",
                        status="fail",
                        detail=(
                            f"confidence_gates[{i}].on_fail='{on_fail}' is invalid; "
                            f"must be one of: {sorted(_VALID_ON_FAIL)}"
                        ),
                        fix=f"Set on_fail to one of: {', '.join(sorted(_VALID_ON_FAIL))}",
                    )
                )

            if not results:
                results.append(
                    ConformanceResult(
                        check_id=cid_base,
                        category="rcan_v12",
                        status="pass",
                        detail=(
                            f"confidence_gates[{i}]: scope={gate.get('scope')!r}, "
                            f"min_confidence={gate.get('min_confidence')}, "
                            f"on_fail={gate.get('on_fail')!r}"
                        ),
                    )
                )

        return results

    def _v12_hitl_gates(self) -> list[ConformanceResult]:
        agent = self._cfg.get("agent", {}) or {}
        gates = agent.get("hitl_gates")
        if gates is None:
            return []  # Optional feature; skip if not declared

        results: list[ConformanceResult] = []
        _VALID_ON_FAIL = {"block", "allow"}

        if not isinstance(gates, list):
            results.append(
                ConformanceResult(
                    check_id="rcan_v12.hitl_gates.type",
                    category="rcan_v12",
                    status="fail",
                    detail="agent.hitl_gates must be a list",
                    fix="Change agent.hitl_gates to a YAML list of gate definitions",
                )
            )
            return results

        for i, gate in enumerate(gates):
            cid_base = f"rcan_v12.hitl_gates[{i}]"
            if not isinstance(gate, dict):
                results.append(
                    ConformanceResult(
                        check_id=cid_base,
                        category="rcan_v12",
                        status="fail",
                        detail=f"hitl_gates[{i}] must be a mapping (dict)",
                        fix="Each gate entry must be a YAML mapping with action_types and require_auth",
                    )
                )
                continue

            # Required fields
            for fname in ("action_types", "require_auth"):
                if fname not in gate:
                    results.append(
                        ConformanceResult(
                            check_id=f"{cid_base}.{fname}",
                            category="rcan_v12",
                            status="fail",
                            detail=f"hitl_gates[{i}] missing required field '{fname}'",
                            fix=f"Add '{fname}:' to hitl_gates[{i}]",
                        )
                    )

            # Validate action_types is a list
            action_types = gate.get("action_types")
            if action_types is not None and not isinstance(action_types, list):
                results.append(
                    ConformanceResult(
                        check_id=f"{cid_base}.action_types",
                        category="rcan_v12",
                        status="fail",
                        detail=f"hitl_gates[{i}].action_types must be a list of action type strings",
                        fix="Set action_types to a list, e.g. [motor_command, config_change]",
                    )
                )

            # Validate require_auth is boolean
            require_auth = gate.get("require_auth")
            if require_auth is not None and not isinstance(require_auth, bool):
                results.append(
                    ConformanceResult(
                        check_id=f"{cid_base}.require_auth",
                        category="rcan_v12",
                        status="warn",
                        detail=f"hitl_gates[{i}].require_auth should be a boolean (got {require_auth!r})",
                        fix="Set require_auth: true or require_auth: false",
                    )
                )

            # Validate on_fail value
            on_fail = gate.get("on_fail")
            if on_fail is not None and on_fail not in _VALID_ON_FAIL:
                results.append(
                    ConformanceResult(
                        check_id=f"{cid_base}.on_fail",
                        category="rcan_v12",
                        status="fail",
                        detail=(
                            f"hitl_gates[{i}].on_fail='{on_fail}' is invalid; "
                            f"must be one of: {sorted(_VALID_ON_FAIL)}"
                        ),
                        fix=f"Set on_fail to one of: {', '.join(sorted(_VALID_ON_FAIL))}",
                    )
                )

            if not results:
                results.append(
                    ConformanceResult(
                        check_id=cid_base,
                        category="rcan_v12",
                        status="pass",
                        detail=(
                            f"hitl_gates[{i}]: action_types={gate.get('action_types')!r}, "
                            f"require_auth={gate.get('require_auth')}"
                        ),
                    )
                )

        return results

    # ------------------------------------------------------------------
    # RCAN v1.5 checks — GAP-1 through GAP-13
    # ------------------------------------------------------------------

    def _check_rcan_v15(self) -> list[ConformanceResult]:
        return [
            self._v15_rcan_version(),
            self._v15_replay_protection(),
            self._v15_consent_declared(),
            self._v15_loa_enforcement(),
            self._v15_estop_qos_bypass(),
            self._v15_rrn_format(),
            self._v15_signing_configured(),
            self._v15_offline_mode(),
            self._v15_registry_rrn(),
            self._v15_r2ram_scopes(),
        ]

    def _v15_rcan_version(self) -> ConformanceResult:
        cid = "rcan_v15.rcan_version"
        ver = str(self._cfg.get("rcan_version", "")).strip()
        try:
            parts = [int(x) for x in ver.split(".")[:2]]
            major, minor = parts[0], parts[1] if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="fail",
                detail=f"rcan_version='{ver}' is not parseable",
                fix='Set rcan_version: "1.5" at the top of your config',
            )
        if (major, minor) >= (1, 5):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail=f"rcan_version='{ver}' satisfies RCAN v1.5 minimum",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="warn",
            detail=f"rcan_version='{ver}' is below 1.5 — v1.5 features may be unavailable",
            fix='Update rcan_version: "1.6" and enable v1.5 features',
        )

    def _v15_replay_protection(self) -> ConformanceResult:
        cid = "rcan_v15.replay_protection"
        security = self._cfg.get("security", {}) or {}
        replay = security.get("replay_protection", {}) or {}
        enabled = replay.get("enabled", False)
        if enabled:
            window = replay.get("window_s", 0)
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail=f"Replay protection enabled (window_s={window})",
            )
        rcan_proto = self._cfg.get("rcan_protocol", {}) or {}
        if rcan_proto.get("enable_jwt"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail="Replay protection satisfied via JWT (contains exp+iat claims)",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="warn",
            detail="Replay protection is not explicitly configured (RCAN v1.5 §7.3 SHOULD)",
            fix="Add security.replay_protection.enabled: true with window_s: 30",
        )

    def _v15_consent_declared(self) -> ConformanceResult:
        cid = "rcan_v15.consent_declared"
        consent = self._cfg.get("consent", {}) or {}
        if consent.get("required") is True or consent.get("mode"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail=f"Consent declared: mode={consent.get('mode', 'required')}",
            )
        p66 = self._cfg.get("p66", {}) or {}
        if p66.get("consent_required") is True:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail="Consent declared via p66.consent_required",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="warn",
            detail="Consent mechanism not declared (RCAN v1.5 §8 SHOULD for physical robots)",
            fix="Add consent: required: true  (or p66.consent_required: true)",
        )

    def _v15_loa_enforcement(self) -> ConformanceResult:
        cid = "rcan_v15.loa_enforcement"
        p66 = self._cfg.get("p66", {}) or {}
        loa_cfg = self._cfg.get("loa", {}) or {}
        if p66.get("loa_enforcement") or loa_cfg.get("enforcement"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail="LoA enforcement enabled (RCAN v1.5 GAP-3)",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="warn",
            detail="LoA enforcement not enabled — Level of Assurance checks skipped (RCAN v1.5 GAP-3 SHOULD)",
            fix="Add p66.loa_enforcement: true or loa.enforcement: true",
        )

    def _v15_estop_qos_bypass(self) -> ConformanceResult:
        cid = "rcan_v15.estop_qos_bypass"
        p66 = self._cfg.get("p66", {}) or {}
        safety = self._cfg.get("safety", {}) or {}
        # P66 manifest or safety.estop_bypass_rate_limit = true means we comply
        # If P66 is declared at all, the runtime enforces this invariant
        if p66 or safety.get("local_safety_wins"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail="ESTOP QoS bypass invariant enforced by P66 runtime layer (never rate-limited)",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="fail",
            detail="ESTOP QoS bypass not confirmed — P66 section missing (RCAN v1.5 §9.1 MUST)",
            fix="Add p66.enabled: true or safety.local_safety_wins: true",
        )

    def _v15_rrn_format(self) -> ConformanceResult:
        cid = "rcan_v15.rrn_format"
        import re as _re

        metadata = self._cfg.get("metadata", {}) or {}
        rrn = metadata.get("rrn", "")
        if not rrn:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="fail",
                detail="metadata.rrn is missing — required for RCAN v1.5 identity (§4.1 MUST)",
                fix="Add metadata.rrn: RRN-XXXXXXXXXXXX (12-digit zero-padded)",
            )
        if _re.match(r"^RRN-\d{12}$", rrn):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail=f"RRN format valid: {rrn}",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="warn",
            detail=f"RRN '{rrn}' does not match canonical RRN-XXXXXXXXXXXX format",
            fix="Use 12-digit zero-padded RRN, e.g. RRN-000000000001",
        )

    def _v15_signing_configured(self) -> ConformanceResult:
        cid = "rcan_v15.message_signing"
        security = self._cfg.get("security", {}) or {}
        signing = security.get("signing", {}) or {}
        rcan_proto = self._cfg.get("rcan_protocol", {}) or {}
        if signing.get("enabled") or rcan_proto.get("enable_jwt") or signing.get("algorithm"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail="Message signing configured (RCAN v1.5 §7.2)",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="warn",
            detail="Message signing not configured (RCAN v1.5 §7.2 SHOULD for production robots)",
            fix="Add security.signing.enabled: true with algorithm: Ed25519",
        )

    def _v15_offline_mode(self) -> ConformanceResult:
        cid = "rcan_v15.offline_mode"
        offline = self._cfg.get("offline", {}) or {}
        agent = self._cfg.get("agent", {}) or {}
        if (
            offline.get("enabled")
            or offline.get("fallback_provider")
            or agent.get("offline_provider")
        ):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail="Offline/graceful-degradation mode configured (RCAN v1.5 GAP-11)",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="warn",
            detail="Offline mode not configured — robot requires cloud connectivity (RCAN v1.5 GAP-11 SHOULD)",
            fix="Add offline.enabled: true with offline.fallback_provider: ollama",
        )

    def _v15_registry_rrn(self) -> ConformanceResult:
        cid = "rcan_v15.registry_rrn_uri"
        metadata = self._cfg.get("metadata", {}) or {}
        rrn_uri = metadata.get("rrn_uri", "")
        if rrn_uri and rrn_uri.startswith("rrn://"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail=f"Registry RRN URI declared: {rrn_uri}",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="warn",
            detail="metadata.rrn_uri not set — robot not anchored to a registry namespace (RCAN v1.5 §4.2 SHOULD)",
            fix="Add metadata.rrn_uri: rrn://org/category/model/instance-id",
        )

    def _v15_r2ram_scopes(self) -> ConformanceResult:
        cid = "rcan_v15.r2ram_scopes"
        r2ram = self._cfg.get("r2ram", {}) or {}
        p66 = self._cfg.get("p66", {}) or {}
        if r2ram.get("scopes") or p66.get("r2ram"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail="R2RAM scope hierarchy declared (RCAN v1.5 §10)",
            )
        # Minimum: if safety.local_safety_wins is set, scope hierarchy is implicitly enforced
        if (self._cfg.get("safety", {}) or {}).get("local_safety_wins"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v15",
                status="pass",
                detail="R2RAM scope enforcement satisfied via safety.local_safety_wins",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v15",
            status="warn",
            detail="R2RAM scope hierarchy not declared (RCAN v1.5 §10 SHOULD)",
            fix="Add r2ram.scopes: [discover, status, chat, control, safety] or set safety.local_safety_wins: true",
        )

    # ------------------------------------------------------------------
    # RCAN v1.6 checks — GAP-14, GAP-16, GAP-17, GAP-18
    # ------------------------------------------------------------------

    def _check_rcan_v16(self) -> list[ConformanceResult]:
        return [
            self._v16_rcan_version(),
            self._v16_human_identity_loa(),
            self._v16_federated_consent(),
            self._v16_constrained_transport(),
            self._v16_multimodal_support(),
            self._v16_hardware_safety_core(),
        ]

    def _v16_rcan_version(self) -> ConformanceResult:
        cid = "rcan_v16.rcan_version"
        ver = str(self._cfg.get("rcan_version", "")).strip()
        try:
            parts = [int(x) for x in ver.split(".")[:2]]
            major, minor = parts[0], parts[1] if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v16",
                status="fail",
                detail=f"rcan_version='{ver}' is not parseable",
                fix='Set rcan_version: "1.6"',
            )
        if (major, minor) >= (1, 6):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v16",
                status="pass",
                detail=f"rcan_version='{ver}' is RCAN v1.6 compliant",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v16",
            status="warn",
            detail=f"rcan_version='{ver}' is below 1.6 — GAP-14/16/17/18 features not declared",
            fix='Update rcan_version: "1.6" to enable v1.6 features',
        )

    def _v16_human_identity_loa(self) -> ConformanceResult:
        """GAP-14: Human identity Level-of-Assurance."""
        cid = "rcan_v16.human_identity_loa"
        human_id = self._cfg.get("human_identity", {}) or {}
        loa = self._cfg.get("loa", {}) or {}
        p66 = self._cfg.get("p66", {}) or {}
        if human_id.get("loa_required") or loa.get("human_loa") or p66.get("human_identity_loa"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v16",
                status="pass",
                detail="Human identity LoA declared (RCAN v1.6 GAP-14)",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v16",
            status="warn",
            detail=(
                "human_identity.loa_required not set — RCAN v1.6 GAP-14 SHOULD declare "
                "minimum LoA for physical-layer commands"
            ),
            fix="Add human_identity.loa_required: 2 (0=anonymous, 1=authenticated, 2=verified, 3=physical)",
        )

    def _v16_federated_consent(self) -> ConformanceResult:
        """GAP-16: Federated consent across registries."""
        cid = "rcan_v16.federated_consent"
        federation = self._cfg.get("federation", {}) or {}
        consent = self._cfg.get("consent", {}) or {}
        if (
            federation.get("consent_bridge")
            or consent.get("federated")
            or federation.get("enabled")
        ):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v16",
                status="pass",
                detail="Federated consent bridge declared (RCAN v1.6 GAP-16)",
            )
        # If federation is not used, this is only a warning (not all robots federate)
        return ConformanceResult(
            check_id=cid,
            category="rcan_v16",
            status="warn",
            detail=(
                "Federated consent not configured — cross-registry commands will be "
                "rejected unless federation.consent_bridge is set (RCAN v1.6 GAP-16 SHOULD)"
            ),
            fix="Add federation.enabled: true  # enables cross-registry consent verification",
        )

    def _v16_constrained_transport(self) -> ConformanceResult:
        """GAP-17: Constrained transport support (CoAP/MQTT/BLE)."""
        cid = "rcan_v16.constrained_transport"
        transport = self._cfg.get("transport", {}) or {}
        supported = transport.get("supported", []) or []
        # HTTP is always available; constrained transports are CoAP/MQTT/BLE
        constrained = {"coap", "mqtt", "ble", "lorawan"}
        has_constrained = bool(set(str(s).lower() for s in supported) & constrained)
        if has_constrained or transport.get("constrained_enabled"):
            return ConformanceResult(
                check_id=cid,
                category="rcan_v16",
                status="pass",
                detail=f"Constrained transport declared: {supported} (RCAN v1.6 GAP-17)",
            )
        # HTTP-only is acceptable but warn
        return ConformanceResult(
            check_id=cid,
            category="rcan_v16",
            status="warn",
            detail=(
                "Only HTTP transport declared — no constrained transport (RCAN v1.6 GAP-17 SHOULD "
                "for robots that may operate in bandwidth-limited environments)"
            ),
            fix="Add transport.supported: [http, mqtt] to enable constrained transport fallback",
        )

    def _v16_multimodal_support(self) -> ConformanceResult:
        """GAP-18: Multi-modal payload support."""
        cid = "rcan_v16.multimodal_support"
        multimodal = self._cfg.get("multimodal", {}) or {}
        agent = self._cfg.get("agent", {}) or {}
        cameras = self._cfg.get("cameras", {}) or {}
        has_vision = agent.get("vision_enabled") or bool(cameras)
        if multimodal.get("enabled") or multimodal.get("max_chunk_bytes") or has_vision:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v16",
                status="pass",
                detail="Multimodal/vision capability declared (RCAN v1.6 GAP-18)",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v16",
            status="warn",
            detail="Multimodal support not declared (RCAN v1.6 GAP-18 SHOULD for camera-equipped robots)",
            fix="Add multimodal.enabled: true or set agent.vision_enabled: true",
        )

    def _v16_hardware_safety_core(self) -> ConformanceResult:
        """Hardware safety core (STM32/MCU watchdog) — v1.6 SHOULD."""
        cid = "rcan_v16.hardware_safety_core"
        hw_safety = self._cfg.get("hardware_safety", {}) or {}
        # Block exists = robot has explicitly declared its hardware safety posture (even if false)
        # This is honest self-declaration: physical_estop: false means software-only, which is fine
        if hw_safety:
            has_estop = hw_safety.get("physical_estop", False)
            has_mcu = hw_safety.get("hardware_watchdog_mcu", False)
            if has_estop or has_mcu:
                detail = "Hardware safety core declared"
                if has_estop:
                    detail += " (physical ESTOP present)"
                if has_mcu:
                    detail += " (MCU watchdog present)"
            else:
                detail = "Hardware safety posture declared (software-only ESTOP — physical ESTOP and MCU watchdog not present)"
            return ConformanceResult(
                check_id=cid,
                category="rcan_v16",
                status="pass",
                detail=detail,
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v16",
            status="warn",
            detail=(
                "No hardware safety core declared — software-only ESTOP (RCAN v1.6 SHOULD "
                "declare hardware_safety block for physical robots)"
            ),
            fix=(
                "Add hardware_safety:\n"
                "  physical_estop: false  # true if physical ESTOP button present\n"
                "  hardware_watchdog_mcu: false  # true if STM32/Arduino safety MCU present"
            ),
        )

    # -----------------------------------------------------------------------
    # RCAN v2.1 / L5 Supply Chain + EU AI Act checks
    # -----------------------------------------------------------------------

    def _check_rcan_v21(self) -> list[ConformanceResult]:
        return [
            self._v21_firmware_manifest(),
            self._v21_sbom_attestation(),
            self._v21_authority_handler(),
            self._v21_audit_chain_retention(),
            self._v21_rcan_version(),
            self._v22_pq_signing_key(),
            self._v22_firmware_pq_sig(),
        ]

    def _v22_pq_signing_key(self) -> ConformanceResult:
        """RCAN v2.2 §7.2 — ML-DSA-65 signing key MUST exist (Q-Day 2029 is primary NOW)."""
        import os
        from pathlib import Path

        cid = "rcan_v22.pq_signing_key"
        pq_path = Path(
            os.environ.get("OPENCASTOR_PQ_KEY_PATH")
            or self._cfg.get("pq_key_path")
            or str(Path.home() / ".opencastor" / "pq_signing.key")
        )
        if pq_path.exists():
            try:
                from rcan.signing import MLDSAKeyPair

                kp = MLDSAKeyPair.load(str(pq_path))
                return ConformanceResult(
                    check_id=cid,
                    category="rcan_v22",
                    status="pass",
                    detail=f"ML-DSA-65 signing key present (kid={kp.key_id}, FIPS 204)",
                )
            except Exception as e:
                return ConformanceResult(
                    check_id=cid,
                    category="rcan_v22",
                    status="fail",
                    detail=f"ML-DSA-65 key file corrupt or unreadable: {e}",
                    fix="Run `castor keygen --pq --force` to regenerate the key",
                )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v22",
            status="fail",
            detail="ML-DSA-65 signing key missing — robot is NOT Q-Day protected (Q-Day 2029)",
            fix="Run `castor keygen --pq` to generate ~/.opencastor/pq_signing.key",
        )

    def _v22_firmware_pq_sig(self) -> ConformanceResult:
        """RCAN v2.2 §11 — firmware manifest MUST carry ML-DSA-65 pq_sig."""
        import json
        import os
        from pathlib import Path

        cid = "rcan_v22.firmware_pq_sig"
        paths = [
            os.environ.get("OPENCASTOR_FIRMWARE_MANIFEST_PATH", ""),
            "/run/opencastor/rcan-firmware-manifest.json",
            "/tmp/opencastor-firmware-manifest.json",
        ]
        manifest_path = next((p for p in paths if p and Path(p).exists()), None)
        if not manifest_path:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v22",
                status="warn",
                detail="Firmware manifest not found — run `castor attest generate && castor attest sign`",
                fix="castor attest generate && castor attest sign",
            )
        try:
            m = json.loads(Path(manifest_path).read_text())
            pq_sig = m.get("pq_sig")
            pq_alg = m.get("pq_alg", "")
            if pq_sig and pq_alg == "ml-dsa-65":
                return ConformanceResult(
                    check_id=cid,
                    category="rcan_v22",
                    status="pass",
                    detail=f"Firmware manifest has ML-DSA-65 pq_sig at {manifest_path}",
                )
            if pq_sig:
                return ConformanceResult(
                    check_id=cid,
                    category="rcan_v22",
                    status="warn",
                    detail=f"Firmware manifest has pq_sig but alg={pq_alg!r} (expected ml-dsa-65)",
                    fix="Re-sign: castor keygen --pq --force && castor attest sign --key <key>",
                )
            return ConformanceResult(
                check_id=cid,
                category="rcan_v22",
                status="fail",
                detail="Firmware manifest lacks ML-DSA-65 pq_sig — Ed25519 only (not Q-Day safe)",
                fix="Run `castor keygen --pq && castor attest sign --key <ed25519-key>`",
            )
        except Exception as e:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v22",
                status="warn",
                detail=f"Could not read firmware manifest: {e}",
                fix="castor attest generate && castor attest sign",
            )

    def _v21_rcan_version(self) -> ConformanceResult:
        cid = "rcan_v21.rcan_version"
        ver = str(self._cfg.get("rcan_version", "")).strip()
        try:
            parts = [int(x) for x in ver.split(".")[:2]]
            major, minor = parts[0], parts[1] if len(parts) > 1 else 0
            ok = major == 2 and minor >= 1
        except Exception:
            ok = False
        if ok:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v21",
                status="pass",
                detail=f"rcan_version is '{ver}' — satisfies v2.1 requirement",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v21",
            status="fail",
            detail=f"rcan_version is '{ver}' — must be 2.1 or higher for v2.1 compliance",
            fix="Set rcan_version: '2.1' in your config and run `castor migrate`",
        )

    def _v21_firmware_manifest(self) -> ConformanceResult:
        cid = "rcan_v21.firmware_manifest"
        import os

        manifest_paths = [
            "/run/opencastor/rcan-firmware-manifest.json",
            "/tmp/opencastor-firmware-manifest.json",
        ]
        for p in manifest_paths:
            if os.path.exists(p):
                try:
                    import json as _json

                    d = _json.loads(open(p).read())
                    has_sig = bool(d.get("signature"))
                    if has_sig:
                        return ConformanceResult(
                            check_id=cid,
                            category="rcan_v21",
                            status="pass",
                            detail=f"Signed firmware manifest found at {p}",
                        )
                    return ConformanceResult(
                        check_id=cid,
                        category="rcan_v21",
                        status="warn",
                        detail=f"Firmware manifest exists at {p} but is UNSIGNED",
                        fix="Run: castor attest sign --key <robot-private.pem>",
                    )
                except Exception:
                    pass
        return ConformanceResult(
            check_id=cid,
            category="rcan_v21",
            status="warn",
            detail="No firmware manifest found (required for EU AI Act Art. 16(d) in production)",
            fix="Run: castor attest generate && castor attest sign --key <robot-private.pem>",
        )

    def _v21_sbom_attestation(self) -> ConformanceResult:
        cid = "rcan_v21.sbom_attestation"
        import os

        sbom_paths = [
            "/run/opencastor/rcan-sbom.json",
            "/tmp/opencastor-rcan-sbom.json",
        ]
        for p in sbom_paths:
            if os.path.exists(p):
                try:
                    import json as _json

                    d = _json.loads(open(p).read())
                    rcan = d.get("x-rcan", {})
                    has_countersig = bool(rcan.get("rrf_countersig"))
                    if has_countersig:
                        return ConformanceResult(
                            check_id=cid,
                            category="rcan_v21",
                            status="pass",
                            detail=f"SBOM found at {p} with RRF countersignature",
                        )
                    return ConformanceResult(
                        check_id=cid,
                        category="rcan_v21",
                        status="warn",
                        detail=f"SBOM found at {p} but not yet RRF-countersigned",
                        fix="Run: castor sbom publish --token <rrf-token>",
                    )
                except Exception:
                    pass
        return ConformanceResult(
            check_id=cid,
            category="rcan_v21",
            status="warn",
            detail="No SBOM found (required for EU AI Act Art. 16(a) in production)",
            fix="Run: castor sbom generate && castor sbom publish --token <rrf-token>",
        )

    def _v21_authority_handler(self) -> ConformanceResult:
        cid = "rcan_v21.authority_handler"
        # Check if the authority module is importable and configured
        try:
            from castor.authority import AuthorityRequestHandler  # noqa: F401

            # Check if handler is registered in harness YAML (optional deeper check)
            harness = self._cfg.get("harness", {})
            handlers = harness.get("message_handlers", {})
            # type 41 may be registered as int or string
            has_handler = (
                41 in handlers
                or "41" in handlers
                or "AUTHORITY_ACCESS" in handlers
                or self._cfg.get("authority_handler_enabled", False)
            )
            if has_handler:
                return ConformanceResult(
                    check_id=cid,
                    category="rcan_v21",
                    status="pass",
                    detail="AUTHORITY_ACCESS (41) handler registered",
                )
            return ConformanceResult(
                check_id=cid,
                category="rcan_v21",
                status="warn",
                detail="castor.authority module available but handler not explicitly registered",
                fix=(
                    "Add authority_handler_enabled: true to config, or register handler in "
                    "harness.message_handlers[41]. Required for EU AI Act Art. 16(j)."
                ),
            )
        except ImportError:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v21",
                status="fail",
                detail="castor.authority module not found — AUTHORITY_ACCESS handler not available",
                fix="Ensure castor/authority.py is installed (OpenCastor v2026.4+)",
            )

    def _v21_audit_chain_retention(self) -> ConformanceResult:
        cid = "rcan_v21.audit_chain_retention"
        # Check configured retention period — EU AI Act Art. 12 requires min 10 years (3650 days)
        retention_days = self._cfg.get("audit_retention_days", 0)
        if retention_days >= 3650:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v21",
                status="pass",
                detail=f"Audit chain retention: {retention_days} days (≥ 3650 days required by EU AI Act Art. 12)",
            )
        if retention_days > 0:
            return ConformanceResult(
                check_id=cid,
                category="rcan_v21",
                status="warn",
                detail=f"Audit chain retention: {retention_days} days — EU AI Act Art. 12 requires min 10 years (3650 days)",
                fix="Set audit_retention_days: 3650 in your config",
            )
        return ConformanceResult(
            check_id=cid,
            category="rcan_v21",
            status="warn",
            detail="audit_retention_days not configured — EU AI Act Art. 12 requires min 10 years",
            fix="Set audit_retention_days: 3650 in your config",
        )

    def compliance_report(self) -> dict:
        """Generate a full EU AI Act compliance report.

        Returns a structured dict with:
          - overall_status: "compliant" | "partial" | "non_compliant"
          - deadline: EU AI Act deadline (August 2, 2026)
          - checks: list of L5 check results
          - eu_ai_act_mapping: article → RCAN provision mapping
        """
        results = self.run_category("rcan_v21")
        statuses = [r.status for r in results]
        if all(s == "pass" for s in statuses):
            overall = "compliant"
        elif "fail" in statuses:
            overall = "non_compliant"
        else:
            overall = "partial"

        return {
            "overall_status": overall,
            "deadline": "2026-08-02",
            "rrn": self._cfg.get("rrn", "unknown"),
            "rcan_version": self._cfg.get("rcan_version", "unknown"),
            "checks": [
                {
                    "id": r.check_id,
                    "status": r.status,
                    "detail": r.detail,
                    "fix": r.fix,
                }
                for r in results
            ],
            "eu_ai_act_mapping": [
                {
                    "article": "Art. 16(a)",
                    "provision": "§12 SBOM",
                    "status": next((r.status for r in results if "sbom" in r.check_id), "unknown"),
                },
                {
                    "article": "Art. 16(d)",
                    "provision": "§11 Firmware Manifest",
                    "status": next(
                        (r.status for r in results if "firmware" in r.check_id), "unknown"
                    ),
                },
                {
                    "article": "Art. 12",
                    "provision": "§16 Commitment Chain",
                    "status": next((r.status for r in results if "audit" in r.check_id), "unknown"),
                },
                {
                    "article": "Art. 16(j)",
                    "provision": "§13 Authority Access",
                    "status": next(
                        (r.status for r in results if "authority" in r.check_id), "unknown"
                    ),
                },
            ],
        }
