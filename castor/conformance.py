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

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

KNOWN_PROVIDERS = frozenset(
    {
        "anthropic",
        "google",
        "openai",
        "huggingface",
        "ollama",
        "llamacpp",
        "llama.cpp",
        "llama-cpp",
        "mlx",
        "mlx-lm",
        "claude_oauth",
        "openrouter",
    }
)

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
    "anthropic": ["ANTHROPIC_API_KEY"],
    "claude_oauth": ["ANTHROPIC_API_KEY"],
    "google": ["GOOGLE_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "huggingface": ["HF_TOKEN", "HUGGINGFACE_TOKEN"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "ollama": [],  # no key needed
    "llamacpp": [],
    "llama.cpp": [],
    "llama-cpp": [],
    "mlx": [],
    "mlx-lm": [],
}

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
        for category in ("safety", "provider", "protocol", "performance", "hardware"):
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
                fix='Add rcan_version: "1.0.0-alpha" to the top of your config',
            )
        # Check it looks like a version string
        version_str = str(version).strip()
        if not re.match(r"^\d+\.\d+", version_str):
            return ConformanceResult(
                check_id=cid,
                category="protocol",
                status="warn",
                detail=f"rcan_version='{version_str}' does not look like a semver string",
                fix='Use format: rcan_version: "1.0.0-alpha"',
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
        camera = self._cfg.get("camera")
        agent = self._cfg.get("agent", {}) or {}
        vision_enabled = agent.get("vision_enabled", True)
        if not camera and vision_enabled is not False:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="warn",
                detail="No camera section configured (vision model with no camera is unusual)",
                fix="Add a 'camera:' section, or set agent.vision_enabled: false",
            )
        if not camera:
            return ConformanceResult(
                check_id=cid,
                category="hardware",
                status="pass",
                detail="No camera section; vision disabled",
            )
        return ConformanceResult(
            check_id=cid,
            category="hardware",
            status="pass",
            detail=f"Camera configured (type={camera.get('type', 'unknown')})",
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
