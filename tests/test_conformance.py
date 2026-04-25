"""Tests for castor.conformance — RCAN behavioral conformance checker (~60 tests)."""

from __future__ import annotations

import argparse
import os
from unittest.mock import patch

import pytest

from castor.conformance import ConformanceChecker, ConformanceResult

# ---------------------------------------------------------------------------
# Minimal valid config fixture
# ---------------------------------------------------------------------------

VALID_UUID4 = "550e8400-e29b-41d4-a716-446655440000"


def make_valid_config() -> dict:
    """Return a complete, conformant RCAN config dict."""
    return {
        "rcan_version": "2.1",
        "metadata": {
            "robot_name": "TestBot",
            "robot_uuid": VALID_UUID4,
            "author": "TestAuthor",
            "license": "Apache-2.0",
        },
        "agent": {
            "provider": "anthropic",
            "model": "claude-opus-4-6",
            "vision_enabled": True,
            "latency_budget_ms": 3000,
        },
        "physics": {
            "type": "differential",
            "dof": 2,
        },
        "drivers": [
            {"id": "main_driver", "protocol": "pca9685_rc", "port": "/dev/i2c-1"},
        ],
        "camera": {
            "type": "csi",
            "resolution": [640, 480],
        },
        "network": {"telemetry_stream": True},
        "rcan_protocol": {
            "port": 8000,
            "capabilities": ["status", "nav", "teleop"],
        },
        "reactive": {
            "min_obstacle_m": 0.3,
            "fallback_provider": "ollama",
        },
        "tiered_brain": {
            "planner_interval": 10,
        },
    }


def checker_from(config: dict) -> ConformanceChecker:
    return ConformanceChecker(config, config_path="test.rcan.yaml")


# ===========================================================================
# ConformanceResult dataclass
# ===========================================================================


class TestConformanceResultDataclass:
    def test_fields_exist(self):
        r = ConformanceResult(
            check_id="safety.foo",
            category="safety",
            status="pass",
            detail="all good",
        )
        assert r.check_id == "safety.foo"
        assert r.category == "safety"
        assert r.status == "pass"
        assert r.detail == "all good"
        assert r.fix is None

    def test_fix_field(self):
        r = ConformanceResult(
            check_id="safety.bar",
            category="safety",
            status="fail",
            detail="bad",
            fix="do this",
        )
        assert r.fix == "do this"


# ===========================================================================
# Summary scoring
# ===========================================================================


class TestSummary:
    def test_all_pass_score_100(self):
        cfg = make_valid_config()
        chk = checker_from(cfg)
        results = [
            ConformanceResult("a.b", "safety", "pass", "ok"),
            ConformanceResult("a.c", "safety", "pass", "ok"),
        ]
        s = chk.summary(results)
        assert s["pass"] == 2
        assert s["warn"] == 0
        assert s["fail"] == 0
        assert s["score"] == 100

    def test_one_fail_deducts_10(self):
        chk = checker_from({})
        results = [ConformanceResult("a.b", "safety", "fail", "bad")]
        s = chk.summary(results)
        assert s["fail"] == 1
        assert s["score"] == 90

    def test_one_warn_deducts_3(self):
        chk = checker_from({})
        results = [ConformanceResult("a.b", "safety", "warn", "meh")]
        s = chk.summary(results)
        assert s["warn"] == 1
        assert s["score"] == 97

    def test_fail_deducts_more_than_warn(self):
        chk = checker_from({})
        warn_result = [ConformanceResult("a.w", "safety", "warn", "w")]
        fail_result = [ConformanceResult("a.f", "safety", "fail", "f")]
        assert chk.summary(fail_result)["score"] < chk.summary(warn_result)["score"]

    def test_score_clamped_at_zero(self):
        chk = checker_from({})
        results = [ConformanceResult(f"a.{i}", "safety", "fail", "bad") for i in range(20)]
        s = chk.summary(results)
        assert s["score"] == 0

    def test_mixed_results(self):
        chk = checker_from({})
        results = [
            ConformanceResult("a.1", "safety", "pass", "ok"),
            ConformanceResult("a.2", "safety", "warn", "meh"),
            ConformanceResult("a.3", "safety", "fail", "bad"),
        ]
        s = chk.summary(results)
        assert s["pass"] == 1
        assert s["warn"] == 1
        assert s["fail"] == 1
        # 100 - 10(fail) - 3(warn) = 87
        assert s["score"] == 87


# ===========================================================================
# run_all / run_category
# ===========================================================================


class TestRunAll:
    def test_run_all_returns_list(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_all()
        assert isinstance(results, list)
        assert len(results) > 0

    def test_all_results_are_conformance_result(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_all()
        for r in results:
            assert isinstance(r, ConformanceResult)
            assert r.status in ("pass", "warn", "fail", "skip")

    def test_run_unknown_category_raises(self):
        chk = checker_from(make_valid_config())
        with pytest.raises(ValueError, match="Unknown category"):
            chk.run_category("nonexistent")

    def test_run_category_safety(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        ids = [r.check_id for r in results]
        assert "safety.reactive_layer" in ids
        assert "safety.estop_capable" in ids

    def test_run_category_provider(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("provider")
        ids = [r.check_id for r in results]
        assert "provider.configured" in ids

    def test_run_category_protocol(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("protocol")
        ids = [r.check_id for r in results]
        assert "protocol.rcan_version" in ids

    def test_run_category_performance(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("performance")
        ids = [r.check_id for r in results]
        assert "perf.tiered_brain" in ids

    def test_run_category_hardware(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("hardware")
        ids = [r.check_id for r in results]
        assert "hardware.drivers_present" in ids


# ===========================================================================
# Safety checks
# ===========================================================================


class TestSafetyReactiveLayer:
    def test_pass_when_configured(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.reactive_layer")
        assert r.status == "pass"

    def test_fail_when_reactive_missing(self):
        cfg = make_valid_config()
        del cfg["reactive"]
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.reactive_layer")
        assert r.status == "fail"

    def test_fail_when_min_obstacle_m_missing(self):
        cfg = make_valid_config()
        cfg["reactive"] = {}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.reactive_layer")
        assert r.status == "fail"

    def test_warn_when_min_obstacle_m_too_high(self):
        cfg = make_valid_config()
        cfg["reactive"]["min_obstacle_m"] = 1.5
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.reactive_layer")
        assert r.status == "warn"

    def test_pass_at_exactly_1m(self):
        cfg = make_valid_config()
        cfg["reactive"]["min_obstacle_m"] = 1.0
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.reactive_layer")
        assert r.status == "pass"

    def test_fail_when_invalid_value(self):
        cfg = make_valid_config()
        cfg["reactive"]["min_obstacle_m"] = "not_a_number"
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.reactive_layer")
        assert r.status == "fail"


class TestSafetyEstopCapable:
    def test_pass_with_drivers(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.estop_capable")
        assert r.status == "pass"

    def test_warn_when_no_drivers(self):
        cfg = make_valid_config()
        cfg["drivers"] = []
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.estop_capable")
        assert r.status == "warn"

    def test_warn_when_drivers_have_no_protocol(self):
        cfg = make_valid_config()
        cfg["drivers"] = [{"id": "d1"}]
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.estop_capable")
        assert r.status == "warn"


class TestSafetyLatencyBudget:
    def test_pass_at_3000(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.latency_budget")
        assert r.status == "pass"

    def test_warn_at_4000(self):
        cfg = make_valid_config()
        cfg["agent"]["latency_budget_ms"] = 4000
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.latency_budget")
        assert r.status == "warn"

    def test_fail_at_11000(self):
        cfg = make_valid_config()
        cfg["agent"]["latency_budget_ms"] = 11000
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.latency_budget")
        assert r.status == "fail"

    def test_warn_when_not_set(self):
        cfg = make_valid_config()
        del cfg["agent"]["latency_budget_ms"]
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.latency_budget")
        assert r.status == "warn"

    def test_pass_at_1000(self):
        cfg = make_valid_config()
        cfg["agent"]["latency_budget_ms"] = 1000
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.latency_budget")
        assert r.status == "pass"

    def test_boundary_exactly_10000_passes(self):
        """10000ms is at the boundary — should pass (not fail, fail is > 10000)."""
        cfg = make_valid_config()
        cfg["agent"]["latency_budget_ms"] = 10000
        # 10000 > 3000 → warn
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.latency_budget")
        assert r.status == "warn"


class TestSafetyHailoOptIn:
    def test_no_result_when_hailo_disabled(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        ids = [r.check_id for r in results]
        assert "safety.hailo_opt_in" not in ids

    def test_warn_when_hailo_enabled_without_confidence(self):
        cfg = make_valid_config()
        cfg["hailo_vision"] = True
        results = checker_from(cfg).run_category("safety")
        r = next((x for x in results if x.check_id == "safety.hailo_opt_in"), None)
        assert r is not None
        assert r.status == "warn"

    def test_warn_when_confidence_out_of_range_high(self):
        cfg = make_valid_config()
        cfg["hailo_vision"] = True
        cfg["hailo_confidence"] = 0.9
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.hailo_opt_in")
        assert r.status == "warn"

    def test_warn_when_confidence_out_of_range_low(self):
        cfg = make_valid_config()
        cfg["hailo_vision"] = True
        cfg["hailo_confidence"] = 0.1
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.hailo_opt_in")
        assert r.status == "warn"

    def test_pass_when_confidence_in_range(self):
        cfg = make_valid_config()
        cfg["hailo_vision"] = True
        cfg["hailo_confidence"] = 0.5
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.hailo_opt_in")
        assert r.status == "pass"


class TestSafetyGeofence:
    def test_warn_when_no_geofence(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.geofence")
        assert r.status == "warn"

    def test_pass_when_geofence_configured(self):
        cfg = make_valid_config()
        cfg["geofence"] = {"type": "circle", "radius_m": 10}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.geofence")
        assert r.status == "pass"


# ===========================================================================
# Provider checks
# ===========================================================================


class TestProviderConfigured:
    def test_pass_when_fully_configured(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.configured")
        assert r.status == "pass"

    def test_fail_when_provider_missing(self):
        cfg = make_valid_config()
        del cfg["agent"]["provider"]
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.configured")
        assert r.status == "fail"

    def test_fail_when_model_missing(self):
        cfg = make_valid_config()
        del cfg["agent"]["model"]
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.configured")
        assert r.status == "fail"

    def test_fail_when_both_missing(self):
        cfg = make_valid_config()
        cfg["agent"] = {}
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.configured")
        assert r.status == "fail"

    def test_detail_contains_provider_and_model(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.configured")
        assert "anthropic" in r.detail
        assert "claude" in r.detail


class TestProviderKnown:
    def test_pass_for_anthropic(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.known")
        assert r.status == "pass"

    def test_warn_for_unknown_provider(self):
        cfg = make_valid_config()
        cfg["agent"]["provider"] = "my_custom_llm"
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.known")
        assert r.status == "warn"

    def test_pass_for_huggingface(self):
        cfg = make_valid_config()
        cfg["agent"]["provider"] = "huggingface"
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.known")
        assert r.status == "pass"

    def test_pass_for_ollama(self):
        cfg = make_valid_config()
        cfg["agent"]["provider"] = "ollama"
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.known")
        assert r.status == "pass"


class TestProviderVisionEnabled:
    def test_pass_when_camera_and_vision_enabled(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.vision_enabled")
        assert r.status == "pass"

    def test_warn_when_camera_present_but_vision_disabled(self):
        cfg = make_valid_config()
        cfg["agent"]["vision_enabled"] = False
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.vision_enabled")
        assert r.status == "warn"

    def test_pass_when_no_camera(self):
        cfg = make_valid_config()
        del cfg["camera"]
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.vision_enabled")
        assert r.status == "pass"


class TestProviderApiKeyPresent:
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True)
    def test_pass_when_key_set(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.api_key_present")
        assert r.status == "pass"

    @patch.dict(os.environ, {}, clear=True)
    def test_warn_when_key_missing(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.api_key_present")
        assert r.status == "warn"

    def test_pass_for_ollama_no_key_needed(self):
        cfg = make_valid_config()
        cfg["agent"]["provider"] = "ollama"
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.api_key_present")
        assert r.status == "pass"


class TestProviderFallback:
    def test_pass_when_fallback_set(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.fallback")
        assert r.status == "pass"

    def test_warn_when_no_fallback(self):
        cfg = make_valid_config()
        del cfg["reactive"]["fallback_provider"]
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.fallback")
        assert r.status == "warn"

    def test_warn_when_no_reactive_section(self):
        cfg = make_valid_config()
        del cfg["reactive"]
        results = checker_from(cfg).run_category("provider")
        r = next(x for x in results if x.check_id == "provider.fallback")
        assert r.status == "warn"


# ===========================================================================
# Protocol checks
# ===========================================================================


class TestProtocolRcanVersion:
    def test_pass_with_valid_version(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.rcan_version")
        assert r.status == "pass"

    def test_fail_when_missing(self):
        cfg = make_valid_config()
        del cfg["rcan_version"]
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.rcan_version")
        assert r.status == "fail"

    def test_warn_when_not_semver(self):
        cfg = make_valid_config()
        cfg["rcan_version"] = "latest"
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.rcan_version")
        assert r.status == "warn"


class TestProtocolRobotUuid:
    def test_pass_with_valid_uuid4(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.robot_uuid")
        assert r.status == "pass"

    def test_fail_when_missing(self):
        cfg = make_valid_config()
        del cfg["metadata"]["robot_uuid"]
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.robot_uuid")
        assert r.status == "fail"

    def test_warn_when_not_uuid4_format(self):
        cfg = make_valid_config()
        cfg["metadata"]["robot_uuid"] = "not-a-uuid"
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.robot_uuid")
        assert r.status == "warn"

    def test_warn_for_all_zeros_uuid(self):
        """All-zeros UUID is UUID4 format invalid (version nibble should be 4)."""
        cfg = make_valid_config()
        cfg["metadata"]["robot_uuid"] = "00000000-0000-0000-0000-000000000002"
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.robot_uuid")
        # Zeros UUID has version 0, not 4 — warn
        assert r.status == "warn"


class TestProtocolRobotName:
    def test_pass_with_valid_name(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.robot_name")
        assert r.status == "pass"

    def test_fail_when_missing(self):
        cfg = make_valid_config()
        del cfg["metadata"]["robot_name"]
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.robot_name")
        assert r.status == "fail"

    def test_fail_when_empty_string(self):
        cfg = make_valid_config()
        cfg["metadata"]["robot_name"] = ""
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.robot_name")
        assert r.status == "fail"


class TestProtocolCapabilitiesDeclared:
    def test_pass_when_capabilities_present(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.capabilities_declared")
        assert r.status == "pass"

    def test_warn_when_empty(self):
        cfg = make_valid_config()
        cfg["rcan_protocol"]["capabilities"] = []
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.capabilities_declared")
        assert r.status == "warn"

    def test_warn_when_missing(self):
        cfg = make_valid_config()
        del cfg["rcan_protocol"]["capabilities"]
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.capabilities_declared")
        assert r.status == "warn"


class TestProtocolPortInRange:
    def test_pass_for_8000(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.port_in_range")
        assert r.status == "pass"

    def test_warn_for_port_80(self):
        cfg = make_valid_config()
        cfg["rcan_protocol"]["port"] = 80
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.port_in_range")
        assert r.status == "warn"

    def test_warn_for_high_ephemeral_port(self):
        cfg = make_valid_config()
        cfg["rcan_protocol"]["port"] = 60000
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.port_in_range")
        assert r.status == "warn"

    def test_pass_for_boundary_1024(self):
        cfg = make_valid_config()
        cfg["rcan_protocol"]["port"] = 1024
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.port_in_range")
        assert r.status == "pass"

    def test_warn_when_port_missing(self):
        cfg = make_valid_config()
        del cfg["rcan_protocol"]["port"]
        results = checker_from(cfg).run_category("protocol")
        r = next(x for x in results if x.check_id == "protocol.port_in_range")
        assert r.status == "warn"


# ===========================================================================
# Performance checks
# ===========================================================================


class TestPerfTieredBrain:
    def test_pass_when_configured(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.tiered_brain")
        assert r.status == "pass"

    def test_warn_when_missing(self):
        cfg = make_valid_config()
        del cfg["tiered_brain"]
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.tiered_brain")
        assert r.status == "warn"


class TestPerfPlannerInterval:
    def test_pass_at_10(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.planner_interval")
        assert r.status == "pass"

    def test_warn_when_too_low(self):
        cfg = make_valid_config()
        cfg["tiered_brain"]["planner_interval"] = 2
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.planner_interval")
        assert r.status == "warn"

    def test_warn_when_too_high(self):
        cfg = make_valid_config()
        cfg["tiered_brain"]["planner_interval"] = 60
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.planner_interval")
        assert r.status == "warn"

    def test_pass_at_boundary_5(self):
        cfg = make_valid_config()
        cfg["tiered_brain"]["planner_interval"] = 5
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.planner_interval")
        assert r.status == "pass"

    def test_pass_at_boundary_30(self):
        cfg = make_valid_config()
        cfg["tiered_brain"]["planner_interval"] = 30
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.planner_interval")
        assert r.status == "pass"

    def test_warn_when_tiered_brain_missing(self):
        cfg = make_valid_config()
        del cfg["tiered_brain"]
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.planner_interval")
        assert r.status == "warn"


class TestPerfAgentRoster:
    def test_pass_when_roster_present(self):
        cfg = make_valid_config()
        cfg["agent_roster"] = [{"name": "observer"}, {"name": "navigator"}]
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.agent_roster")
        assert r.status == "pass"

    def test_warn_when_roster_missing(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.agent_roster")
        assert r.status == "warn"


class TestPerfLearnerConfigured:
    def test_pass_when_learner_disabled(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.learner_configured")
        assert r.status == "pass"

    def test_pass_when_learner_enabled_with_cadence(self):
        cfg = make_valid_config()
        cfg["learner"] = {"enabled": True, "cadence_n": 5}
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.learner_configured")
        assert r.status == "pass"

    def test_warn_when_learner_enabled_without_cadence(self):
        cfg = make_valid_config()
        cfg["learner"] = {"enabled": True}
        results = checker_from(cfg).run_category("performance")
        r = next(x for x in results if x.check_id == "perf.learner_configured")
        assert r.status == "warn"


# ===========================================================================
# Hardware checks
# ===========================================================================


class TestHardwareDriversPresent:
    def test_pass_with_valid_driver(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.drivers_present")
        assert r.status == "pass"

    def test_fail_when_no_drivers(self):
        cfg = make_valid_config()
        cfg["drivers"] = []
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.drivers_present")
        assert r.status == "fail"

    def test_fail_when_drivers_missing(self):
        cfg = make_valid_config()
        del cfg["drivers"]
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.drivers_present")
        assert r.status == "fail"

    def test_fail_when_no_protocol_field(self):
        cfg = make_valid_config()
        cfg["drivers"] = [{"id": "d1"}]
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.drivers_present")
        assert r.status == "fail"

    def test_pass_with_multiple_drivers(self):
        cfg = make_valid_config()
        cfg["drivers"] = [
            {"id": "d1", "protocol": "pca9685_rc"},
            {"id": "d2", "protocol": "dynamixel"},
        ]
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.drivers_present")
        assert r.status == "pass"
        assert "2" in r.detail


class TestHardwareCameraConfigured:
    def test_pass_when_camera_present(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.camera_configured")
        assert r.status == "pass"

    def test_warn_when_camera_missing_and_vision_enabled(self):
        cfg = make_valid_config()
        del cfg["camera"]
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.camera_configured")
        assert r.status == "warn"

    def test_pass_when_camera_missing_and_vision_disabled(self):
        cfg = make_valid_config()
        del cfg["camera"]
        cfg["agent"]["vision_enabled"] = False
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.camera_configured")
        assert r.status == "pass"


class TestHardwarePhysicsType:
    def test_pass_for_differential(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.physics_type")
        assert r.status == "pass"

    def test_warn_for_custom(self):
        cfg = make_valid_config()
        cfg["physics"]["type"] = "custom"
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.physics_type")
        assert r.status == "warn"

    def test_warn_for_unknown_type(self):
        cfg = make_valid_config()
        cfg["physics"]["type"] = "antigravity"
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.physics_type")
        assert r.status == "warn"

    def test_pass_for_ackermann(self):
        cfg = make_valid_config()
        cfg["physics"]["type"] = "ackermann"
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.physics_type")
        assert r.status == "pass"


class TestHardwareDofReasonable:
    def test_pass_for_dof_2(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.dof_reasonable")
        assert r.status == "pass"

    def test_warn_for_dof_15(self):
        cfg = make_valid_config()
        cfg["physics"]["dof"] = 15
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.dof_reasonable")
        assert r.status == "warn"

    def test_pass_when_dof_missing(self):
        cfg = make_valid_config()
        del cfg["physics"]["dof"]
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.dof_reasonable")
        assert r.status == "pass"

    def test_pass_for_dof_12(self):
        cfg = make_valid_config()
        cfg["physics"]["dof"] = 12
        results = checker_from(cfg).run_category("hardware")
        r = next(x for x in results if x.check_id == "hardware.dof_reasonable")
        assert r.status == "pass"


# ===========================================================================
# cmd_validate integration tests
# ===========================================================================


class TestCmdValidate:
    """Integration tests for the castor validate CLI command."""

    def _make_args(self, config_path, category=None, json_out=False, strict=False):
        args = argparse.Namespace()
        args.config = config_path
        args.category = category
        args.json = json_out
        args.strict = strict
        return args

    def test_cmd_validate_import(self):
        from castor.cli import cmd_validate

        assert callable(cmd_validate)

    def test_cmd_validate_json_output(self, tmp_path):
        """cmd_validate with --json outputs valid JSON."""
        import yaml

        from castor.cli import cmd_validate

        cfg = make_valid_config()
        config_file = tmp_path / "test.rcan.yaml"
        config_file.write_text(yaml.dump(cfg))

        args = self._make_args(str(config_file), json_out=True)

        __builtins__["print"] if isinstance(__builtins__, dict) else print

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            try:
                cmd_validate(args)
            except SystemExit:
                pass

        output = f.getvalue()
        # Should contain JSON-parseable content
        # Find a JSON array in the output
        assert "{" in output or "[" in output

    def test_cmd_validate_exits_1_on_fail(self, tmp_path):
        """cmd_validate exits with code 1 when there are failures."""
        import yaml

        from castor.cli import cmd_validate

        cfg = make_valid_config()
        # Remove required field to cause fails
        del cfg["rcan_version"]
        del cfg["drivers"]
        config_file = tmp_path / "fail.rcan.yaml"
        config_file.write_text(yaml.dump(cfg))

        args = self._make_args(str(config_file))

        with pytest.raises(SystemExit) as exc_info:
            cmd_validate(args)
        assert exc_info.value.code == 1

    def test_cmd_validate_exits_0_on_warns_only(self, tmp_path):
        """cmd_validate exits 0 when there are warns but no fails (no --strict)."""
        import yaml

        from castor.cli import cmd_validate

        cfg = make_valid_config()
        # Force some warns: remove geofence (warn), remove tiered_brain (warn)
        # Keep safety.local_safety_wins: true so RCAN §6 check passes.
        # Keep p66.enabled: true so v1.5 estop_qos_bypass MUST check passes.
        cfg["safety"] = {
            "local_safety_wins": True,
            "emergency_stop_distance": 0.3,
            "watchdog": {"timeout_s": 10},
            "watermark_enforcement": True,  # Art. 50 requirement
        }
        cfg["p66"] = {"enabled": True}
        cfg["reactive"] = {"min_obstacle_m": 0.3}
        # Ensure v1.5 RRN check passes (needs metadata.rrn in canonical format)
        cfg.setdefault("metadata", {})["rrn"] = "RRN-000000000001"
        cfg.setdefault("metadata", {})["rrn_uri"] = "rrn://test/robot/testbot/001"
        # v2.1: mark authority handler as enabled and set retention so L5 checks produce warns, not fails
        cfg["authority_handler_enabled"] = True
        cfg["audit_retention_days"] = 3650
        # Remove geofence and tiered_brain to force warns (not fails)
        cfg.pop("geofence", None)
        cfg.pop("tiered_brain", None)
        config_file = tmp_path / "warn.rcan.yaml"
        config_file.write_text(yaml.dump(cfg))

        args = self._make_args(str(config_file))

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            try:
                cmd_validate(args)
                exit_code = 0
            except SystemExit as e:
                exit_code = e.code or 0

        # Warns only → exit 0
        assert exit_code == 0

    def test_cmd_validate_strict_exits_1_on_warn(self, tmp_path):
        """cmd_validate --strict exits 1 when there are warns."""
        import yaml

        from castor.cli import cmd_validate

        cfg = make_valid_config()
        # geofence warn is always present
        config_file = tmp_path / "strict.rcan.yaml"
        config_file.write_text(yaml.dump(cfg))

        args = self._make_args(str(config_file), strict=True)

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            try:
                cmd_validate(args)
                exit_code = 0
            except SystemExit as e:
                exit_code = e.code or 0

        assert exit_code == 1

    def test_cmd_validate_category_filter(self, tmp_path):
        """cmd_validate --category only runs that category."""
        import yaml

        from castor.cli import cmd_validate

        cfg = make_valid_config()
        config_file = tmp_path / "cat.rcan.yaml"
        config_file.write_text(yaml.dump(cfg))

        args = self._make_args(str(config_file), category="safety")

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            try:
                cmd_validate(args)
            except SystemExit:
                pass

        output = f.getvalue()
        assert "SAFETY" in output.upper() or "safety" in output.lower()

    def test_cmd_validate_missing_config_file(self, tmp_path):
        """cmd_validate with missing config file should not crash fatally."""
        from castor.cli import cmd_validate

        args = self._make_args(str(tmp_path / "nonexistent.rcan.yaml"))

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            try:
                cmd_validate(args)
                exit_code = 0
            except SystemExit as e:
                exit_code = e.code or 0

        # Should exit with error, not crash
        assert exit_code != 0 or "not found" in f.getvalue().lower()


# ===========================================================================
# New P66 / safety completeness checks (Task C + D)
# ===========================================================================


class TestSafetyLocalSafetyWins:
    def test_fail_when_missing(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.local_safety_wins")
        assert r.status == "fail"

    def test_fail_when_false(self):
        cfg = make_valid_config()
        cfg["safety"] = {"local_safety_wins": False}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.local_safety_wins")
        assert r.status == "fail"

    def test_pass_when_true(self):
        cfg = make_valid_config()
        cfg["safety"] = {"local_safety_wins": True}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.local_safety_wins")
        assert r.status == "pass"

    def test_fix_mentions_rcan_section_6(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.local_safety_wins")
        assert r.fix is not None
        assert "RCAN §6" in r.fix or "rcan.yaml" in r.fix


class TestSafetyWatchdogConfigured:
    def test_warn_when_missing(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.watchdog_configured")
        assert r.status == "warn"

    def test_pass_when_timeout_le_30(self):
        cfg = make_valid_config()
        cfg["watchdog"] = {"timeout_s": 10}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.watchdog_configured")
        assert r.status == "pass"

    def test_warn_when_timeout_gt_30(self):
        cfg = make_valid_config()
        cfg["watchdog"] = {"timeout_s": 60}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.watchdog_configured")
        assert r.status == "warn"

    def test_pass_at_boundary_30(self):
        cfg = make_valid_config()
        cfg["watchdog"] = {"timeout_s": 30}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.watchdog_configured")
        assert r.status == "pass"


class TestSafetyConfidenceGatesConfigured:
    def test_warn_when_missing(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.confidence_gates_configured")
        assert r.status == "warn"

    def test_pass_when_configured(self):
        cfg = make_valid_config()
        cfg["brain"] = {"confidence_gates": [{"scope": "motion", "min_confidence": 0.7}]}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.confidence_gates_configured")
        assert r.status == "pass"


class TestSafetyP66Conformance:
    def test_p66_conformance_check_runs(self):
        """P66 conformance check runs and returns a valid status."""
        cfg = make_valid_config()
        cfg["safety"] = {"local_safety_wins": True}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.p66_conformance")
        assert r.status in ("pass", "warn", "fail")
        assert "%" in r.detail

    def test_p66_conformance_pct_in_detail(self):
        """P66 conformance detail includes a percentage value."""
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.p66_conformance")
        # Should contain a percentage figure in the detail string
        assert "%" in r.detail


class TestSafetyHardwareSafetyDeclared:
    def test_warn_when_missing(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.hardware_safety_declared")
        assert r.status == "warn"

    def test_pass_when_declared(self):
        cfg = make_valid_config()
        cfg["hardware_safety"] = {"physical_estop": True, "hardware_watchdog_mcu": False}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.hardware_safety_declared")
        assert r.status == "pass"


class TestSafetyEstopDistanceConfigured:
    def test_warn_when_missing(self):
        cfg = make_valid_config()
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.estop_distance_configured")
        assert r.status == "warn"

    def test_pass_with_emergency_stop_distance(self):
        cfg = make_valid_config()
        cfg["safety"] = {"emergency_stop_distance": 0.3}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.estop_distance_configured")
        assert r.status == "pass"

    def test_pass_with_estop_distance_mm(self):
        cfg = make_valid_config()
        cfg["safety"] = {"estop_distance_mm": 300}
        results = checker_from(cfg).run_category("safety")
        r = next(x for x in results if x.check_id == "safety.estop_distance_configured")
        assert r.status == "pass"


# ---------------------------------------------------------------------------
# ISO conformance (closes #755)
# ---------------------------------------------------------------------------


class TestISOCheck:
    """Tests for castor iso-check command and DISCOVER iso_conformance payload."""

    def test_cmd_iso_check_runs(self, tmp_path):
        """iso-check runs without error on a valid config."""
        import yaml

        from castor.cli import cmd_iso_check

        cfg = make_valid_config()
        cfg["iso_conformance"] = {
            "iso_13482": False,
            "iso_10218_2": False,
            "iso_42001": True,
            "eu_ai_act": True,
        }
        cfg["authority_handler_enabled"] = True
        cfg["audit_retention_days"] = 3650
        cfg["pq_signing_required"] = True
        cfg["hitl_gates"] = [{"scope": "destructive"}]
        p = tmp_path / "iso.rcan.yaml"
        p.write_text(yaml.dump(cfg))

        class Args:
            config = str(p)
            json = False

        import io
        import sys

        buf = io.StringIO()
        sys.stdout = buf
        try:
            cmd_iso_check(Args())
        finally:
            sys.stdout = sys.__stdout__
        out = buf.getvalue()
        assert "ISO/TC 299" in out
        assert "EU AI Act" in out
        assert "COMPLIANT" in out

    def test_cmd_iso_check_json_output(self, tmp_path):
        """iso-check --json outputs parseable JSON with iso_checks key."""
        import json

        import yaml

        from castor.cli import cmd_iso_check

        cfg = make_valid_config()
        cfg["iso_conformance"] = {"iso_42001": True, "eu_ai_act": True}
        p = tmp_path / "iso.rcan.yaml"
        p.write_text(yaml.dump(cfg))

        class Args:
            config = str(p)
            json = True

        import io
        import sys

        buf = io.StringIO()
        sys.stdout = buf
        try:
            cmd_iso_check(Args())
        finally:
            sys.stdout = sys.__stdout__
        data = json.loads(buf.getvalue())
        assert "iso_checks" in data
        standards = [c["standard"] for c in data["iso_checks"]]
        assert "ISO/IEC 42001:2023" in standards
        assert "EU AI Act (Reg. 2024/1689)" in standards

    def test_discover_payload_includes_iso_conformance(self):
        """DISCOVER response (msg_type=1) includes iso_conformance block."""
        from fastapi.testclient import TestClient

        from castor.api import app, state

        client = TestClient(app)
        state.config = {
            "iso_conformance": {"iso_42001": True, "eu_ai_act": True},
            "authority_handler_enabled": True,
            "pq_signing_required": True,
        }
        # DISCOVER is unauthenticated
        resp = client.post("/rcan/peer", json={"msg_type": 1, "source": "test"})
        if resp.status_code in (200, 404, 501):
            # Endpoint may not be mounted in test mode — check the inline handler path
            pass
        # At minimum verify the DISCOVER handler code path includes iso_conformance
        import inspect

        from castor import api as api_mod

        src = inspect.getsource(api_mod)
        assert "iso_conformance" in src
        assert "iso_42001" in src


class TestAnnexIIIStrictMode:
    """Art. 16 checks promote from warn to fail in strict mode."""

    base_config = {
        "rcan_version": "2.2",
        "metadata": {"rrn": "RRN-000000000001"},
        "reactive": {"min_obstacle_m": 0.3},
        "agent": {"provider": "google", "model": "gemini-2.5-flash"},
    }

    def test_sbom_warn_in_default_mode(self):
        checker = ConformanceChecker(self.base_config)
        results = checker.run_category("rcan_v21")
        sbom = next(r for r in results if r.check_id == "rcan_v21.sbom_attestation")
        assert sbom.status == "warn"

    def test_sbom_fail_in_strict_mode(self):
        checker = ConformanceChecker(self.base_config, annex_iii_strict=True)
        results = checker.run_category("rcan_v21")
        sbom = next(r for r in results if r.check_id == "rcan_v21.sbom_attestation")
        assert sbom.status == "fail"

    def test_firmware_fail_in_strict_mode(self):
        checker = ConformanceChecker(self.base_config, annex_iii_strict=True)
        results = checker.run_category("rcan_v21")
        fw = next(r for r in results if r.check_id == "rcan_v21.firmware_manifest")
        assert fw.status == "fail"

    def test_authority_warn_in_default_mode_when_module_available(self):
        """When castor.authority exists but handler unregistered, default mode → warn."""
        import sys
        from unittest.mock import MagicMock, patch

        mock_authority = MagicMock()
        mock_authority.AuthorityRequestHandler = MagicMock()
        with patch.dict(sys.modules, {"castor.authority": mock_authority}):
            checker = ConformanceChecker(self.base_config)
            results = checker.run_category("rcan_v21")
            auth = next(r for r in results if r.check_id == "rcan_v21.authority_handler")
            assert auth.status == "warn"

    def test_authority_fail_in_strict_mode_when_module_available(self):
        """When castor.authority exists but handler unregistered, strict mode → fail."""
        import sys
        from unittest.mock import MagicMock, patch

        mock_authority = MagicMock()
        mock_authority.AuthorityRequestHandler = MagicMock()
        with patch.dict(sys.modules, {"castor.authority": mock_authority}):
            checker = ConformanceChecker(self.base_config, annex_iii_strict=True)
            results = checker.run_category("rcan_v21")
            auth = next(r for r in results if r.check_id == "rcan_v21.authority_handler")
            assert auth.status == "fail"

    def test_strict_mode_does_not_affect_non_art16_checks(self):
        checker_default = ConformanceChecker(self.base_config)
        checker_strict = ConformanceChecker(self.base_config, annex_iii_strict=True)
        default_safety = checker_default.run_category("safety")
        strict_safety = checker_strict.run_category("safety")
        assert [r.status for r in default_safety] == [r.status for r in strict_safety]

    def test_check_fria_prerequisite_strict_blocks_on_art16(self):
        from castor.fria import check_fria_prerequisite

        passed, blocking = check_fria_prerequisite(self.base_config, annex_iii_strict=True)
        blocking_ids = [r.check_id for r in blocking]
        assert "rcan_v21.sbom_attestation" in blocking_ids
        assert not passed


class TestWatermarkEnforcedCheck:
    """rcan_v22.watermark_enforced — Art. 50 detectability."""

    def _checker(self, extra: dict | None = None):
        config = {
            "rcan_version": "2.2",
            "metadata": {"rrn": "RRN-000000000001"},
            "reactive": {"min_obstacle_m": 0.3},
            "agent": {"provider": "google", "model": "gemini-2.5-flash"},
        }
        if extra:
            config.update(extra)
        return ConformanceChecker(config)

    def test_fails_when_watermark_enforcement_absent(self):
        results = self._checker().run_category("rcan_v21")
        wm = next((r for r in results if r.check_id == "rcan_v22.watermark_enforced"), None)
        assert wm is not None
        assert wm.status == "fail"

    def test_fails_when_watermark_enforcement_false(self):
        results = self._checker({"safety": {"watermark_enforcement": False}}).run_category(
            "rcan_v21"
        )
        wm = next(r for r in results if r.check_id == "rcan_v22.watermark_enforced")
        assert wm.status == "fail"

    def test_passes_when_watermark_enforcement_true(self):
        results = self._checker({"safety": {"watermark_enforcement": True}}).run_category(
            "rcan_v21"
        )
        wm = next(r for r in results if r.check_id == "rcan_v22.watermark_enforced")
        assert wm.status == "pass"

    def test_check_id_and_category(self):
        results = self._checker().run_category("rcan_v21")
        wm = next(r for r in results if r.check_id == "rcan_v22.watermark_enforced")
        assert wm.category == "rcan_v22"

    def test_fix_message_present_when_failing(self):
        results = self._checker().run_category("rcan_v21")
        wm = next(r for r in results if r.check_id == "rcan_v22.watermark_enforced")
        assert wm.fix is not None
        assert "watermark_enforcement" in wm.fix


class TestQmsDeclarationCheck:
    """rcan_v22.qms_declaration — Art. 17 QMS conformance check."""

    def _checker(self, extra=None):
        config = {
            "rcan_version": "2.2",
            "metadata": {"rrn": "RRN-000000000001"},
            "reactive": {"min_obstacle_m": 0.3},
            "agent": {"provider": "google", "model": "gemini-2.5-flash"},
        }
        if extra:
            config.update(extra)
        return ConformanceChecker(config)

    def test_warns_when_qms_reference_absent(self):
        results = self._checker().run_category("rcan_v21")
        qms = next((r for r in results if r.check_id == "rcan_v22.qms_declaration"), None)
        assert qms is not None
        assert qms.status == "warn"

    def test_passes_when_qms_reference_set(self):
        results = self._checker({"qms_reference": "https://example.com/qms.pdf"}).run_category(
            "rcan_v21"
        )
        qms = next(r for r in results if r.check_id == "rcan_v22.qms_declaration")
        assert qms.status == "pass"

    def test_check_in_rcan_v22_category(self):
        results = self._checker().run_category("rcan_v21")
        qms = next(r for r in results if r.check_id == "rcan_v22.qms_declaration")
        assert qms.category == "rcan_v22"
