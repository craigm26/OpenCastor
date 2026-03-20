"""Standalone harness evaluator for contribute work units.

Implements the 'cheap to verify, expensive to compute' pattern (Karpathy loop):
robots pull candidate harness configs, evaluate locally with 10 synthetic
scenarios (~1s total), and submit scores to Firestore for aggregation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import random
import time
from pathlib import Path

from .work_unit import WorkUnit, WorkUnitResult

log = logging.getLogger("OpenCastor.Contribute")

_ENVIRONMENTS = ["home", "industrial", "general", "outdoor", "edge"]
_SCENARIOS_PER_ENV = 2


def detect_hardware_tier(hw_profile: dict) -> str:
    """Map a hw_profile dict to a hardware tier string."""
    npu = hw_profile.get("npu", "")
    cpu_cores = hw_profile.get("cpu_cores", 1)

    if npu == "hailo-8l":
        return "pi5-hailo8l"

    arch = platform.machine()
    if arch == "x86_64":
        return "server"

    if cpu_cores >= 4:
        return "pi5-8gb"
    return "pi4-8gb"


def _deterministic_seed(candidate_id: str, scenario_id: str) -> int:
    """Create a deterministic integer seed from candidate + scenario IDs."""
    h = hashlib.sha256(f"{candidate_id}{scenario_id}".encode()).hexdigest()
    return int(h[:8], 16)


def run_single_scenario(
    config: dict,
    scenario_id: str,
    env: str,
    candidate_id: str = "",
) -> dict:
    """Run a single synthetic eval scenario; deterministic given (config, scenario_id).

    Returns:
        {scenario_id, environment, success: bool, p66_compliant: bool,
         tokens_used: int, latency_ms: float}
    """
    seed = _deterministic_seed(candidate_id, scenario_id)
    rng = random.Random(seed)

    max_iter = config.get("max_iterations", 6)
    thinking_budget = config.get("thinking_budget", 1024)
    p66_threshold = config.get("p66_consent_threshold", "physical")

    # Success formula: base + config contribution + small jitter
    base_success = 0.7 + (max_iter / 12) * 0.15 - (thinking_budget / 4096) * 0.05
    jitter = rng.uniform(-0.05, 0.05)
    success_prob = max(0.0, min(0.99, base_success + jitter))
    success = rng.random() < success_prob

    # P66 compliance: always 0.95+ unless consent threshold is "none"
    if p66_threshold == "none":
        p66_base = 0.50
    else:
        p66_base = 0.97
    p66_compliant = rng.random() < p66_base

    # tokens_used proportional to thinking_budget (with small noise)
    tokens_used = int(thinking_budget * rng.uniform(0.85, 1.15))

    # latency_ms derived from latency_score = 0.5 - (max_iter/24)
    latency_score_raw = max(0.0, 0.5 - (max_iter / 24))
    latency_ms = (1.0 - latency_score_raw) * 5000.0 * rng.uniform(0.9, 1.1)

    return {
        "scenario_id": scenario_id,
        "environment": env,
        "success": success,
        "p66_compliant": p66_compliant,
        "tokens_used": tokens_used,
        "latency_ms": latency_ms,
    }


def get_robot_rrn() -> str:
    """Read robot RRN from env var or ~/.config/opencastor/robot_config.json."""
    rrn = os.environ.get("OPENCASTOR_RRN")
    if rrn:
        return rrn

    config_path = Path.home() / ".config" / "opencastor" / "robot_config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            return data.get("rrn", "RRN-UNKNOWN")
    except Exception:
        pass
    return "RRN-UNKNOWN"


def _get_firestore_client():
    """Create Firestore client using service account or ADC."""
    from google.cloud import firestore as _firestore  # type: ignore[import-untyped]

    creds_path = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        str(Path.home() / ".config" / "opencastor" / "firebase-sa-key.json"),
    )
    try:
        from google.oauth2 import service_account  # type: ignore[import-untyped]

        creds = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=[
                "https://www.googleapis.com/auth/datastore",
                "https://www.googleapis.com/auth/cloud-platform",
            ],
        )
        return _firestore.Client(project="opencastor", credentials=creds)
    except Exception:
        import google.auth  # type: ignore[import-untyped]

        creds, project = google.auth.default()
        return _firestore.Client(project=project or "opencastor", credentials=creds)


def run_harness_eval_unit(
    wu: WorkUnit,
    hw: dict,
    cancelled_flag: list[bool] | None = None,
) -> WorkUnitResult:
    """Evaluate a harness candidate config against 10 synthetic scenarios.

    Runs 2 scenarios per environment (home, industrial, general, outdoor, edge),
    checks cancellation between each, respects thermal throttle (>80°C = skip),
    submits scores to Firestore (graceful no-op if unavailable).

    Returns WorkUnitResult with output={candidate_id, score, success_rate, p66_rate, hardware_tier}.
    """
    start = time.monotonic()

    input_data = wu.input_data or {}
    candidate_id = input_data.get("candidate_id", wu.work_unit_id)
    config = input_data.get("config", {})
    hardware_tier = input_data.get("hardware_tier") or detect_hardware_tier(hw)

    # Thermal throttle check
    try:
        temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
        if temp_path.exists():
            temp_c = int(temp_path.read_text().strip()) / 1000.0
            if temp_c >= 80.0:
                log.warning("Thermal throttle: %.1f°C — skipping harness eval", temp_c)
                return WorkUnitResult(
                    wu.work_unit_id,
                    output=None,
                    latency_ms=(time.monotonic() - start) * 1000,
                    hw_profile=hw,
                    status="failed",
                    error="thermal_throttle",
                )
    except Exception:
        pass

    # Run 10 scenarios (2 per environment), checking cancellation between each
    scenario_results: list[dict] = []
    for env in _ENVIRONMENTS:
        for i in range(_SCENARIOS_PER_ENV):
            if cancelled_flag and cancelled_flag[0]:
                latency_ms = (time.monotonic() - start) * 1000
                return WorkUnitResult(
                    wu.work_unit_id,
                    output=None,
                    latency_ms=latency_ms,
                    hw_profile=hw,
                    status="cancelled",
                )
            scenario_id = f"{env}_{i}"
            result = run_single_scenario(config, scenario_id, env, candidate_id=candidate_id)
            scenario_results.append(result)
            time.sleep(0.01)  # ~0.1s total across 10 scenarios

    # Aggregate scores
    n = len(scenario_results)
    success_rate = sum(1 for r in scenario_results if r["success"]) / n
    p66_rate = sum(1 for r in scenario_results if r["p66_compliant"]) / n

    thinking_budget = config.get("thinking_budget", 1024)
    token_efficiency = max(0.0, 1.0 - thinking_budget / 8000.0)

    max_iter = config.get("max_iterations", 6)
    latency_score = max(0.0, 0.5 - (max_iter / 24.0))

    composite_score = (
        success_rate * 0.50
        + p66_rate * 0.25
        + token_efficiency * 0.15
        + latency_score * 0.10
    )

    rrn = get_robot_rrn()
    timestamp = int(time.time())

    # Submit to Firestore — graceful skip if unavailable (robot works offline)
    try:
        db = _get_firestore_client()

        eval_doc = {
            "candidate_id": candidate_id,
            "config": config,
            "hardware_tier": hardware_tier,
            "success_rate": success_rate,
            "p66_rate": p66_rate,
            "token_efficiency": token_efficiency,
            "latency_score": latency_score,
            "composite_score": composite_score,
            "submitted_at": timestamp,
            "robot_rrn": rrn,
        }

        # contribute_results/{rrn}/harness_eval/{candidate_id}_{timestamp}
        db.collection("contribute_results").document(rrn).collection("harness_eval").document(
            f"{candidate_id}_{timestamp}"
        ).set(eval_doc)

        # harness_leaderboard/{hardware_tier}/robots/{rrn}
        db.collection("harness_leaderboard").document(hardware_tier).collection("robots").document(
            rrn
        ).set(
            {
                "rrn": rrn,
                "hardware_tier": hardware_tier,
                "last_candidate_id": candidate_id,
                "last_score": composite_score,
                "last_submitted_at": timestamp,
            }
        )
        log.info(
            "Harness eval submitted to Firestore: candidate=%s score=%.4f tier=%s",
            candidate_id,
            composite_score,
            hardware_tier,
        )
    except Exception as exc:
        log.debug("Firestore submit skipped (unavailable): %s", exc)

    latency_ms = (time.monotonic() - start) * 1000
    return WorkUnitResult(
        wu.work_unit_id,
        output={
            "candidate_id": candidate_id,
            "score": composite_score,
            "success_rate": success_rate,
            "p66_rate": p66_rate,
            "hardware_tier": hardware_tier,
        },
        latency_ms=latency_ms,
        hw_profile=hw,
        status="complete",
    )
