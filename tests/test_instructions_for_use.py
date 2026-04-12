"""Tests for castor.instructions_for_use — EU AI Act Art. 13 IFU document."""
import json

import pytest

from castor.instructions_for_use import build_ifu_document, IFU_SCHEMA_VERSION


SAMPLE_CONFIG = {
    "rcan_version": "2.2",
    "metadata": {
        "rrn": "RRN-000000000001",
        "robot_name": "test-robot",
        "provider_name": "Test Corp",
        "provider_contact": "safety@testcorp.com",
    },
    "agent": {"provider": "google", "model": "gemini-2.5-flash"},
}


class TestBuildIfuDocument:
    def test_returns_dict_with_required_art13_fields(self):
        doc = build_ifu_document(SAMPLE_CONFIG, "safety_component", "Indoor navigation")
        assert "schema" in doc
        assert "provider_identity" in doc
        assert "intended_purpose" in doc
        assert "capabilities_and_limitations" in doc
        assert "human_oversight_measures" in doc
        assert "known_risks_and_misuse" in doc
        assert "expected_lifetime" in doc

    def test_schema_value(self):
        doc = build_ifu_document(SAMPLE_CONFIG, "safety_component", "Indoor navigation")
        assert doc["schema"] == IFU_SCHEMA_VERSION

    def test_provider_identity_from_config(self):
        doc = build_ifu_document(SAMPLE_CONFIG, "safety_component", "Indoor navigation")
        assert doc["provider_identity"]["rrn"] == "RRN-000000000001"
        assert doc["provider_identity"]["robot_name"] == "test-robot"

    def test_intended_purpose_in_output(self):
        doc = build_ifu_document(
            SAMPLE_CONFIG, "safety_component", "Indoor navigation for warehouse"
        )
        assert doc["intended_purpose"]["description"] == "Indoor navigation for warehouse"
        assert doc["intended_purpose"]["annex_iii_basis"] == "safety_component"

    def test_json_serializable(self):
        doc = build_ifu_document(SAMPLE_CONFIG, "safety_component", "Indoor navigation")
        serialized = json.dumps(doc)
        assert len(serialized) > 0

    def test_human_oversight_section_present(self):
        doc = build_ifu_document(SAMPLE_CONFIG, "safety_component", "nav")
        ho = doc["human_oversight_measures"]
        assert "hitl_gates" in ho
        assert "estop" in ho
        assert "confidence_gates" in ho
