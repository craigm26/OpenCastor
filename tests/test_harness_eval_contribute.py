"""Tests for harness_eval contribute work unit integration."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# 1. detect_hardware_tier — Hailo-8L NPU
# ---------------------------------------------------------------------------


def test_detect_hardware_tier_hailo():
    from castor.contribute.harness_eval import detect_hardware_tier

    hw_profile = {"npu": "hailo-8l", "tops": 26, "cpu_cores": 4}
    assert detect_hardware_tier(hw_profile) == "pi5-hailo8l"


# ---------------------------------------------------------------------------
# 2. detect_hardware_tier — no NPU, 4 cores (aarch64 / default non-x86)
# ---------------------------------------------------------------------------


def test_detect_hardware_tier_no_npu():
    from castor.contribute.harness_eval import detect_hardware_tier

    hw_profile = {"cpu_cores": 4}
    # On non-x86 machine this should be pi5-8gb; on x86 it's "server"
    result = detect_hardware_tier(hw_profile)
    assert result in ("pi5-8gb", "pi4-8gb", "server")


# ---------------------------------------------------------------------------
# 3. detect_hardware_tier — server (x86_64)
# ---------------------------------------------------------------------------


def test_detect_hardware_tier_server():
    from castor.contribute.harness_eval import detect_hardware_tier

    hw_profile = {"cpu_cores": 16}
    with patch("castor.contribute.harness_eval.platform.machine", return_value="x86_64"):
        result = detect_hardware_tier(hw_profile)
    assert result == "server"


# ---------------------------------------------------------------------------
# 4. run_single_scenario — deterministic with same seed
# ---------------------------------------------------------------------------


def test_run_single_scenario_deterministic():
    from castor.contribute.harness_eval import run_single_scenario

    config = {
        "max_iterations": 6,
        "thinking_budget": 1024,
        "context_budget": 8192,
        "p66_consent_threshold": "physical",
    }
    r1 = run_single_scenario(config, "general_0", "general", candidate_id="cand-abc")
    r2 = run_single_scenario(config, "general_0", "general", candidate_id="cand-abc")

    assert r1["success"] == r2["success"]
    assert r1["p66_compliant"] == r2["p66_compliant"]
    assert r1["tokens_used"] == r2["tokens_used"]
    assert abs(r1["latency_ms"] - r2["latency_ms"]) < 1e-6
    assert r1["scenario_id"] == "general_0"
    assert r1["environment"] == "general"
    assert isinstance(r1["success"], bool)
    assert isinstance(r1["p66_compliant"], bool)


# ---------------------------------------------------------------------------
# 5. run_harness_eval_unit — Firestore unavailable, graceful skip
# ---------------------------------------------------------------------------


def test_run_harness_eval_unit_no_firestore():
    from castor.contribute.harness_eval import run_harness_eval_unit
    from castor.contribute.work_unit import WorkUnit

    wu = WorkUnit(
        work_unit_id="test-wu-001",
        project="harness_research",
        coordinator_url="synthetic://localhost",
        model_format="harness_eval",
        input_data={
            "candidate_id": "cand-test-001",
            "config": {
                "max_iterations": 6,
                "thinking_budget": 1024,
                "context_budget": 8192,
                "p66_consent_threshold": "physical",
                "retry_on_error": True,
                "drift_detection": True,
                "cost_gate_usd": 0.01,
            },
            "hardware_tier": "pi5-8gb",
        },
        timeout_seconds=35,
    )
    hw = {"cpu_cores": 4}

    # Firestore import raises — should be gracefully skipped
    with patch("castor.contribute.harness_eval._get_firestore_client", side_effect=Exception("no firestore")):
        result = run_harness_eval_unit(wu, hw)

    assert result.status == "complete"
    assert result.output is not None
    assert result.output["candidate_id"] == "cand-test-001"
    assert 0.0 <= result.output["score"] <= 1.0
    assert "success_rate" in result.output
    assert "p66_rate" in result.output
    assert result.output["hardware_tier"] == "pi5-8gb"


# ---------------------------------------------------------------------------
# 6. HarnessEvalCoordinator — Firestore unavailable, synthetic fallback
# ---------------------------------------------------------------------------


def test_harness_eval_coordinator_fetch_synthetic():
    from castor.contribute.coordinator import HarnessEvalCoordinator

    coord = HarnessEvalCoordinator()
    hw_profile = {"cpu_cores": 4}

    with patch.object(
        coord,
        "_get_firestore_client",
        side_effect=Exception("no firestore"),
    ):
        wu = coord.fetch_work_unit(hw_profile, ["harness_research"])

    assert wu is not None
    assert wu.model_format == "harness_eval"
    assert wu.project == "harness_research"
    assert "candidate_id" in wu.input_data
    assert "config" in wu.input_data


# ---------------------------------------------------------------------------
# 7. WorkUnit has hardware_tier field
# ---------------------------------------------------------------------------


def test_work_unit_has_hardware_tier():
    from castor.contribute.work_unit import WorkUnit

    wu = WorkUnit(
        work_unit_id="wu-tier-test",
        project="harness_research",
        coordinator_url="synthetic://localhost",
        model_format="harness_eval",
        input_data={},
        hardware_tier="pi5-hailo8l",
    )
    assert wu.hardware_tier == "pi5-hailo8l"

    # Default is None
    wu2 = WorkUnit(
        work_unit_id="wu-no-tier",
        project="test",
        coordinator_url="synthetic://localhost",
        model_format="simulated",
        input_data={},
    )
    assert wu2.hardware_tier is None
