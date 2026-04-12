"""Tests for castor.eu_register — EU AI Act Art. 49 database submission package."""
import json

import pytest

from castor.eu_register import build_submission_package, EU_AI_ACT_REGISTRATION_URL


SAMPLE_FRIA = {
    "schema": "rcan-fria-v1",
    "generated_at": "2026-04-11T09:00:00+00:00",
    "system": {
        "rrn": "RRN-000000000001",
        "rrn_uri": "rrn://org/robot/model/id",
        "robot_name": "test-robot",
        "opencastor_version": "2026.3.21.1",
        "rcan_version": "2.2",
        "agent_provider": "google",
        "agent_model": "gemini-2.5-flash",
    },
    "deployment": {
        "annex_iii_basis": "safety_component",
        "intended_use": "Indoor navigation assistance",
        "prerequisite_waived": False,
    },
    "conformance": {
        "score": 85,
        "pass": 20,
        "warn": 5,
        "fail": 0,
    },
    "human_oversight": {
        "hitl_configured": True,
        "confidence_gates_configured": True,
        "estop_configured": True,
    },
    "overall_pass": True,
}

SAMPLE_CONFIG = {
    "rcan_version": "2.2",
    "metadata": {
        "rrn": "RRN-000000000001",
        "robot_name": "test-robot",
    },
}


class TestBuildSubmissionPackage:
    def test_returns_dict_with_required_fields(self):
        pkg = build_submission_package(SAMPLE_FRIA, SAMPLE_CONFIG)
        assert "schema" in pkg
        assert "generated_at" in pkg
        assert "provider" in pkg
        assert "system" in pkg
        assert "annex_iii_basis" in pkg
        assert "conformity_status" in pkg
        assert "submission_instructions" in pkg

    def test_schema_value(self):
        pkg = build_submission_package(SAMPLE_FRIA, SAMPLE_CONFIG)
        assert pkg["schema"] == "rcan-eu-register-v1"

    def test_system_fields_populated_from_fria(self):
        pkg = build_submission_package(SAMPLE_FRIA, SAMPLE_CONFIG)
        assert pkg["system"]["rrn"] == "RRN-000000000001"
        assert pkg["system"]["robot_name"] == "test-robot"
        assert pkg["system"]["intended_use"] == "Indoor navigation assistance"

    def test_annex_iii_basis_from_fria(self):
        pkg = build_submission_package(SAMPLE_FRIA, SAMPLE_CONFIG)
        assert pkg["annex_iii_basis"] == "safety_component"

    def test_conformity_status_pass_when_fria_passes(self):
        pkg = build_submission_package(SAMPLE_FRIA, SAMPLE_CONFIG)
        assert pkg["conformity_status"]["fria_overall_pass"] is True
        assert pkg["conformity_status"]["conformance_score"] == 85

    def test_submission_instructions_contains_url(self):
        pkg = build_submission_package(SAMPLE_FRIA, SAMPLE_CONFIG)
        assert EU_AI_ACT_REGISTRATION_URL in pkg["submission_instructions"]

    def test_raises_on_wrong_fria_schema(self):
        bad_fria = {**SAMPLE_FRIA, "schema": "wrong-schema"}
        with pytest.raises(ValueError, match="rcan-fria-v1"):
            build_submission_package(bad_fria, SAMPLE_CONFIG)

    def test_json_serializable(self):
        pkg = build_submission_package(SAMPLE_FRIA, SAMPLE_CONFIG)
        serialized = json.dumps(pkg)
        assert len(serialized) > 0
