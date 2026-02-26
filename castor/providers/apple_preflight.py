"""Preflight checks for Apple Foundation Models provider."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

_REASON_ACTIONS = {
    "APPLE_INTELLIGENCE_NOT_ENABLED": [
        "Open System Settings > Apple Intelligence and enable Apple Intelligence.",
        "Wait for required model assets to finish downloading.",
    ],
    "DEVICE_NOT_ELIGIBLE": [
        "Use an Apple Intelligence compatible Mac.",
        "Or choose MLX/Ollama local stack as fallback.",
    ],
    "MODEL_NOT_READY": [
        "Keep Apple Intelligence enabled and connected until model assets are ready.",
        "Retry setup after a short wait.",
    ],
    "UNKNOWN": [
        "Retry setup and verify macOS + Xcode requirements.",
        "If the issue persists, use the fallback stack chooser.",
    ],
}


def _parse_version_tuple(version_text: str) -> tuple[int, ...]:
    nums = [int(part) for part in re.findall(r"\d+", version_text)]
    return tuple(nums)


def _normalize_unavailable_reason(reason: Any) -> str:
    if reason is None:
        return "UNKNOWN"
    name = getattr(reason, "name", None)
    if isinstance(name, str) and name:
        return name
    text = str(reason).strip().upper()
    if text in _REASON_ACTIONS:
        return text
    return "UNKNOWN"


def _check_xcode() -> tuple[bool, str]:
    if shutil.which("xcodebuild") is None:
        return False, "xcodebuild not found"

    try:
        out = subprocess.check_output(["xcodebuild", "-version"], text=True, timeout=6)
        first = out.splitlines()[0] if out else "xcodebuild available"
        return True, first
    except Exception as exc:
        return False, f"xcodebuild check failed: {exc}"


def _fallback_stacks_for_device(device_info: Dict[str, Any]) -> list[str]:
    stacks = []
    platform_name = str(device_info.get("platform", "")).lower()
    arch = str(device_info.get("architecture", "")).lower()
    if platform_name == "macos" and arch in {"arm64", "aarch64"}:
        stacks.append("mlx_local_vision")
    stacks.append("ollama_universal_local")
    return stacks


def detect_device_info() -> Dict[str, Any]:
    """Detect host properties used by setup stack compatibility checks."""
    platform_name = "macos" if sys.platform == "darwin" else sys.platform
    mac_ver = platform.mac_ver()[0] if platform_name == "macos" else ""
    return {
        "platform": platform_name,
        "architecture": platform.machine().lower(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "macos_version": mac_ver,
    }


def run_apple_preflight(model_profile_id: Optional[str] = None) -> Dict[str, Any]:
    """Run Apple provider readiness checks and return normalized diagnostics."""
    device = detect_device_info()
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    actions: list[str] = []
    available_reason = "UNKNOWN"

    platform_name = device["platform"]
    if platform_name != "macos":
        issues.append("Apple Foundation Models requires macOS.")
        checks.append({"name": "platform", "ok": False, "detail": platform_name})
    else:
        checks.append({"name": "platform", "ok": True, "detail": "macos"})

    arch = device["architecture"]
    if arch not in {"arm64", "aarch64", "x86_64"}:
        issues.append(f"Unsupported architecture: {arch}")
        checks.append({"name": "architecture", "ok": False, "detail": arch})
    else:
        checks.append({"name": "architecture", "ok": True, "detail": arch})

    if platform_name == "macos":
        mac_ver = device.get("macos_version", "")
        if mac_ver:
            ver_tuple = _parse_version_tuple(mac_ver)
            ok_version = ver_tuple >= (26, 0)
            checks.append(
                {
                    "name": "macos_version",
                    "ok": ok_version,
                    "detail": mac_ver,
                }
            )
            if not ok_version:
                issues.append(f"macOS {mac_ver} is below required 26.0+")
        else:
            checks.append({"name": "macos_version", "ok": False, "detail": "unknown"})
            issues.append("Could not detect macOS version")

    xcode_ok, xcode_detail = _check_xcode()
    checks.append({"name": "xcode", "ok": xcode_ok, "detail": xcode_detail})
    if not xcode_ok:
        issues.append("Xcode 26+ is required and xcodebuild must be available")

    sdk = None
    try:
        import apple_fm_sdk as sdk  # type: ignore

        checks.append({"name": "apple_fm_sdk_import", "ok": True, "detail": "imported"})
    except Exception as exc:
        checks.append(
            {
                "name": "apple_fm_sdk_import",
                "ok": False,
                "detail": str(exc),
            }
        )
        issues.append("apple-fm-sdk is not installed in this environment")

    if sdk is not None:
        try:
            use_case = sdk.SystemLanguageModelUseCase.GENERAL
            guardrails = sdk.SystemLanguageModelGuardrails.DEFAULT

            if model_profile_id == "apple-creative":
                guardrails = sdk.SystemLanguageModelGuardrails.PERMISSIVE_CONTENT_TRANSFORMATIONS
            elif model_profile_id == "apple-tagging":
                use_case = sdk.SystemLanguageModelUseCase.CONTENT_TAGGING

            model = sdk.SystemLanguageModel(use_case=use_case, guardrails=guardrails)
            is_available, reason = model.is_available()
            available_reason = _normalize_unavailable_reason(reason)
            checks.append(
                {
                    "name": "system_model_available",
                    "ok": bool(is_available),
                    "detail": available_reason if not is_available else "available",
                }
            )
            if not is_available:
                issues.append(f"Apple model unavailable: {available_reason}")
        except Exception as exc:
            checks.append({"name": "system_model_available", "ok": False, "detail": str(exc)})
            issues.append(f"Failed to query Apple model availability: {exc}")

    if available_reason not in _REASON_ACTIONS:
        available_reason = "UNKNOWN"
    actions.extend(_REASON_ACTIONS.get(available_reason, _REASON_ACTIONS["UNKNOWN"]))

    fallback_stacks = _fallback_stacks_for_device(device)

    ok = len(issues) == 0
    return {
        "ok": ok,
        "provider": "apple",
        "reason": available_reason,
        "issues": issues,
        "actions": actions,
        "checks": checks,
        "device": device,
        "fallback_stacks": fallback_stacks,
        "model_profile": model_profile_id or "apple-balanced",
    }


def is_apple_ready() -> bool:
    """Fast readiness helper used by auth status checks."""
    result = run_apple_preflight(model_profile_id="apple-balanced")
    return bool(result.get("ok", False))
