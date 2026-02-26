"""Tests for Apple preflight checks."""

from __future__ import annotations

import types
from unittest.mock import patch

from castor.providers.apple_preflight import run_apple_preflight


def test_preflight_non_macos_returns_fallback_guidance():
    with patch(
        "castor.providers.apple_preflight.detect_device_info",
        return_value={
            "platform": "linux",
            "architecture": "x86_64",
            "python_version": "3.11.0",
            "macos_version": "",
        },
    ):
        with patch("castor.providers.apple_preflight._check_xcode", return_value=(False, "missing")):
            result = run_apple_preflight(model_profile_id="apple-balanced")

    assert result["ok"] is False
    assert "ollama_universal_local" in result["fallback_stacks"]
    assert any("requires macOS" in issue for issue in result["issues"])


def test_preflight_model_not_ready_reason_is_normalized():
    fake_sdk = types.SimpleNamespace()

    class _UseCase:
        GENERAL = object()
        CONTENT_TAGGING = object()

    class _Guardrails:
        DEFAULT = object()
        PERMISSIVE_CONTENT_TRANSFORMATIONS = object()

    class _Reason:
        name = "MODEL_NOT_READY"

    class _Model:
        def __init__(self, use_case=None, guardrails=None):
            self.use_case = use_case
            self.guardrails = guardrails

        def is_available(self):
            return False, _Reason()

    fake_sdk.SystemLanguageModelUseCase = _UseCase
    fake_sdk.SystemLanguageModelGuardrails = _Guardrails
    fake_sdk.SystemLanguageModel = _Model

    with patch(
        "castor.providers.apple_preflight.detect_device_info",
        return_value={
            "platform": "macos",
            "architecture": "arm64",
            "python_version": "3.11.0",
            "macos_version": "26.0",
        },
    ):
        with patch("castor.providers.apple_preflight._check_xcode", return_value=(True, "Xcode 26.0")):
            with patch.dict("sys.modules", {"apple_fm_sdk": fake_sdk}):
                result = run_apple_preflight(model_profile_id="apple-tagging")

    assert result["ok"] is False
    assert result["reason"] == "MODEL_NOT_READY"
    assert "mlx_local_vision" in result["fallback_stacks"]
    assert "ollama_universal_local" in result["fallback_stacks"]
