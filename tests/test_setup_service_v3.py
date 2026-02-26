"""Tests for setup_service v3 session/preflight/metrics behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from castor import setup_service


def test_catalog_includes_version_and_hash():
    payload = setup_service.get_setup_catalog(wizard_context=True)
    assert payload["catalog_version"] == "setup-catalog-v3"
    assert isinstance(payload["catalog_hash"], str)
    assert len(payload["catalog_hash"]) == 16


def test_preflight_is_deterministic_for_same_input():
    with patch(
        "castor.setup_service.detect_device_info",
        return_value={
            "platform": "linux",
            "architecture": "x86_64",
            "python_version": "3.11.0",
            "macos_version": "",
        },
    ):
        with patch("castor.setup_service.urlopen", side_effect=Exception("offline")):
            one = setup_service.run_preflight(
                provider="ollama",
                model_profile="llava:13b",
                stack_id="ollama_universal_local",
            )
            two = setup_service.run_preflight(
                provider="ollama",
                model_profile="llava:13b",
                stack_id="ollama_universal_local",
            )

    assert one["reason_code"] == two["reason_code"]
    assert [c["id"] for c in one["checks"]] == [c["id"] for c in two["checks"]]
    assert one["fallback_stacks"] == two["fallback_stacks"]


def test_verify_config_returns_warnings_and_blocks_by_default():
    with patch("castor.setup_service.generate_setup_config") as mock_gen:
        mock_gen.return_value = {
            "filename": "verifybot.rcan.yaml",
            "agent_config": {"provider": "ollama", "model": "llava:13b", "env_var": None},
            "config": {
                "drivers": [{"protocol": "pca9685_i2c"}],
                "channels": [],
            },
        }
        with patch("castor.setup_service.detect_device_info", return_value={"platform": "macos"}):
            fake_provider = MagicMock()
            fake_provider.health_check.return_value = {"ok": True}
            with patch("castor.providers.get_provider", return_value=fake_provider):
                result = setup_service.verify_setup_config(
                    robot_name="VerifyBot",
                    provider="ollama",
                    model="llava:13b",
                    preset="rpi_rc_car",
                    allow_warnings=False,
                )

    assert result["ok"] is False
    assert any("typically unsupported" in item for item in result["warnings"])


def test_verify_config_rejects_unknown_driver_protocol():
    with patch("castor.setup_service.generate_setup_config") as mock_gen:
        mock_gen.return_value = {
            "filename": "verifybot.rcan.yaml",
            "agent_config": {"provider": "ollama", "model": "llava:13b", "env_var": None},
            "config": {
                "drivers": [{"protocol": "unknown_future_proto"}],
                "channels": [],
            },
        }
        fake_provider = MagicMock()
        fake_provider.health_check.return_value = {"ok": True}
        with patch("castor.providers.get_provider", return_value=fake_provider):
            result = setup_service.verify_setup_config(
                robot_name="VerifyBot",
                provider="ollama",
                model="llava:13b",
                preset="rpi_rc_car",
                allow_warnings=True,
            )

    assert result["ok"] is False
    assert any("unsupported protocol" in item for item in result["blocking_errors"])


def test_metrics_respect_telemetry_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCASTOR_ALLOW_TELEMETRY", "0")
    monkeypatch.setattr(setup_service, "SETUP_METRICS_DB", Path(tmp_path / "setup.db"))

    write = setup_service.record_setup_metric(
        platform_name="linux",
        architecture="x86_64",
        stack_id="ollama_universal_local",
        provider="ollama",
        result="success",
        reason_code="READY",
        duration_ms=12.0,
        time_to_remediation_ms=None,
        used_fallback=False,
    )
    assert write["ok"] is False
    assert write["skipped"] == "telemetry_disabled"

    metrics = setup_service.get_setup_metrics()
    assert metrics["telemetry_enabled"] is False
    assert metrics["total_runs"] == 0
