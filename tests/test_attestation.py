"""Tests for castor.attestation_generator — software attestation checks."""

import json

from castor.attestation_generator import (
    check_code_integrity,
    check_config_measurement,
    check_update_chain,
    generate_attestation,
)


def test_generate_returns_dict(tmp_path):
    """generate_attestation() returns a dict with all required keys."""
    out = tmp_path / "attestation.json"
    result = generate_attestation(out_path=out)

    assert isinstance(result, dict)
    for key in (
        "secure_boot",
        "measured_boot",
        "signed_updates",
        "verified",
        "profile",
        "token",
        "source",
        "claims_detail",
    ):
        assert key in result, f"Missing key: {key}"

    assert isinstance(result["secure_boot"], bool)
    assert isinstance(result["measured_boot"], bool)
    assert isinstance(result["signed_updates"], bool)
    assert isinstance(result["verified"], bool)
    assert result["source"] == "castor.attestation_generator"


def test_code_integrity_smoke():
    """check_code_integrity() runs without crashing."""
    ok, detail = check_code_integrity()
    assert isinstance(ok, bool)
    assert isinstance(detail, str)
    assert len(detail) > 0


def test_config_measurement_creates_baseline(tmp_path):
    """First run creates a baseline file."""
    config = tmp_path / "config.yaml"
    config.write_text("rcan_version: '1.6.1'\nmetadata:\n  robot_name: test\n")

    # Patch baseline path to tmp
    import castor.attestation_generator as mod

    orig = mod._BASELINE_PATH
    mod._BASELINE_PATH = tmp_path / "baseline.sha256"
    try:
        ok, detail = check_config_measurement(config)
        assert ok is True
        assert detail == "config_measurement_ok"
        assert (tmp_path / "baseline.sha256").exists()
    finally:
        mod._BASELINE_PATH = orig


def test_config_measurement_stable(tmp_path):
    """Second run with same file returns True."""
    config = tmp_path / "config.yaml"
    config.write_text("rcan_version: '1.6.1'\nmetadata:\n  robot_name: test\n")

    import castor.attestation_generator as mod

    orig = mod._BASELINE_PATH
    mod._BASELINE_PATH = tmp_path / "baseline.sha256"
    try:
        ok1, _ = check_config_measurement(config)
        assert ok1 is True

        ok2, detail2 = check_config_measurement(config)
        assert ok2 is True
        assert detail2 == "config_measurement_ok"
    finally:
        mod._BASELINE_PATH = orig


def test_config_measurement_tampered(tmp_path):
    """Modified file between runs returns False."""
    config = tmp_path / "config.yaml"
    config.write_text("rcan_version: '1.6.1'\nmetadata:\n  robot_name: test\n")

    import castor.attestation_generator as mod

    orig = mod._BASELINE_PATH
    mod._BASELINE_PATH = tmp_path / "baseline.sha256"
    try:
        ok1, _ = check_config_measurement(config)
        assert ok1 is True

        # Tamper
        config.write_text("rcan_version: '1.6.1'\nmetadata:\n  robot_name: HACKED\n")

        ok2, detail2 = check_config_measurement(config)
        assert ok2 is False
        assert detail2 == "config_hash_mismatch"
    finally:
        mod._BASELINE_PATH = orig


def test_update_chain_smoke():
    """check_update_chain() runs without crashing."""
    ok, detail = check_update_chain("2026.3.17.13")
    assert isinstance(ok, bool)
    assert isinstance(detail, str)


def test_update_chain_bad_version():
    """Invalid version format returns False."""
    ok, detail = check_update_chain("not-a-version")
    assert ok is False
    assert "invalid_version_format" in detail


def test_attestation_file_written(tmp_path):
    """The JSON file is written to the specified out_path."""
    out = tmp_path / "sub" / "attestation.json"
    result = generate_attestation(out_path=out)

    assert out.exists()
    written = json.loads(out.read_text())
    assert written["source"] == "castor.attestation_generator"
    assert written["verified"] == result["verified"]
