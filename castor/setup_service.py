"""Shared setup orchestration used by CLI wizard and web setup APIs."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import urlopen

import yaml

from castor.providers.apple_preflight import detect_device_info, run_apple_preflight
from castor.setup_catalog import (
    get_catalog_schema_info,
    get_hardware_presets,
    get_model_profiles,
    get_provider_auth_map,
    get_provider_models,
    get_provider_order,
    get_provider_specs,
    get_stack_profiles,
)

APPLE_SDK_GIT_REF = "3204b7ee892131a5d2c940d95caaabc90b4a40c9"
APPLE_SDK_GIT_URL = "git+https://github.com/apple/python-apple-fm-sdk.git@" + APPLE_SDK_GIT_REF

SETUP_STAGE_ORDER = (
    "probe",
    "stack",
    "profile",
    "preflight",
    "remediation",
    "verify",
    "save",
)

SETUP_SESSION_DIR = Path.home() / ".castor" / "setup_sessions"
SETUP_METRICS_DB = Path.home() / ".castor" / "setup.db"
SETUP_METRICS_TABLE = "setup_metrics_v1"

REASON_ACTIONS: Dict[str, list[str]] = {
    "READY": [],
    "SDK_MISSING": [
        "Install the required SDK dependency for the selected stack.",
        "Re-run preflight after installation.",
    ],
    "RUNTIME_UNAVAILABLE": [
        "Start the required local runtime/service for this stack.",
        "Re-run preflight after the service is reachable.",
    ],
    "MODEL_NOT_READY": [
        "Ensure required model assets are present (or pull the model).",
        "Retry setup after model warmup/download completes.",
    ],
    "DEVICE_NOT_ELIGIBLE": [
        "Choose a compatible stack for this device class.",
        "Use guided fallback recommendations in setup.",
    ],
    "UNKNOWN": [
        "Re-run preflight and review detailed check evidence.",
        "Use fallback stack and continue setup.",
    ],
}


@dataclass
class PreflightCheck:
    """Typed check result used by setup preflight responses."""

    id: str
    category: str
    severity: str
    ok: bool
    reason_code: str
    evidence: str
    remediation_id: Optional[str] = None
    retryable: bool = True
    name: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["name"] = self.name or self.id
        payload["detail"] = self.detail if self.detail is not None else self.evidence
        return payload


@dataclass
class RemediationAction:
    """Describes one remediation action exposed by setup APIs."""

    id: str
    action_type: str
    label: str
    command: Optional[list[str]] = None
    help_url: Optional[str] = None
    check_id: Optional[str] = None
    consent_required: bool = True


@dataclass
class SetupSession:
    """Resumable setup session persisted to disk."""

    session_id: str
    stage: str
    status: str
    created_at: str
    updated_at: str
    device: Dict[str, Any]
    selections: Dict[str, Any] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SetupMetrics:
    """Aggregated local setup reliability metrics."""

    telemetry_enabled: bool
    total_runs: int
    first_run_success_rate: float
    median_time_to_remediation_ms: float
    fallback_success_rate: float
    setup_abandonment_rate: float
    top_reason_codes: Dict[str, int]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_REMEDIATION_REGISTRY: Dict[str, RemediationAction] = {
    "install_apple_sdk": RemediationAction(
        id="install_apple_sdk",
        action_type="install_dependency",
        label="Install Apple Foundation Models SDK",
        command=[sys.executable, "-m", "pip", "install", APPLE_SDK_GIT_URL],
        check_id="apple_fm_sdk_import",
    ),
    "install_mlx_lm": RemediationAction(
        id="install_mlx_lm",
        action_type="install_dependency",
        label="Install MLX runtime (mlx-lm)",
        command=[sys.executable, "-m", "pip", "install", "mlx-lm"],
        check_id="mlx_import",
    ),
    "start_ollama": RemediationAction(
        id="start_ollama",
        action_type="start_service",
        label="Start Ollama local service",
        command=["ollama", "serve"],
        check_id="ollama_daemon",
    ),
    "open_apple_help": RemediationAction(
        id="open_apple_help",
        action_type="open_help",
        label="Open Apple Intelligence troubleshooting help",
        help_url="https://support.apple.com/guide/mac-help/use-apple-intelligence-mchl04f1f5f3/mac",
        consent_required=False,
    ),
    "open_ollama_help": RemediationAction(
        id="open_ollama_help",
        action_type="open_help",
        label="Open Ollama setup guide",
        help_url="https://ollama.com/download",
        consent_required=False,
    ),
    "retry_preflight": RemediationAction(
        id="retry_preflight",
        action_type="retry_check",
        label="Retry setup preflight checks",
        consent_required=False,
    ),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def setup_v3_enabled(config: Optional[dict[str, Any]] = None, wizard_context: bool = False) -> bool:
    """Feature gate for setup-v3 rollout.

    Defaults:
    - wizard context: enabled
    - non-wizard context: disabled
    """
    env = os.getenv("OPENCASTOR_SETUP_V3")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}

    if config and isinstance(config.get("setup_v3"), dict):
        value = config["setup_v3"].get("enabled")
        if isinstance(value, bool):
            return value

    return wizard_context


def _telemetry_enabled(config: Optional[dict[str, Any]] = None) -> bool:
    if os.getenv("OPENCASTOR_ALLOW_TELEMETRY") is not None:
        return _bool_env("OPENCASTOR_ALLOW_TELEMETRY", True)
    if config and isinstance(config.get("privacy"), dict):
        privacy = config["privacy"]
        if "telemetry_collection" in privacy:
            return bool(privacy.get("telemetry_collection", True))
    return True


def get_setup_catalog(
    *,
    config: Optional[dict[str, Any]] = None,
    wizard_context: bool = False,
) -> Dict[str, Any]:
    """Return setup choices used by both CLI and web setup flows."""
    device = detect_device_info()
    providers = get_provider_specs(include_hidden=False)
    stacks = get_stack_profiles(device)
    info = get_catalog_schema_info()

    return {
        "device": device,
        "providers": [
            {
                "key": p.key,
                "label": p.label,
                "desc": p.desc,
                "env_var": p.env_var,
                "local": p.local,
            }
            for p in providers.values()
        ],
        "provider_order": get_provider_order(),
        "models": get_provider_models(),
        "stack_profiles": [asdict(stack) for stack in stacks],
        "hardware_presets": [asdict(preset) for preset in get_hardware_presets()],
        "apple_profiles": [asdict(profile) for profile in get_model_profiles("apple")],
        "setup_v3": {"enabled": setup_v3_enabled(config=config, wizard_context=wizard_context)},
        **info,
    }


def _session_path(session_id: str) -> Path:
    return SETUP_SESSION_DIR / f"{session_id}.json"


def _save_session(session: SetupSession) -> SetupSession:
    path = _session_path(session.session_id)
    _ensure_parent(path)
    path.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")
    return session


def _load_session(session_id: str) -> SetupSession:
    path = _session_path(session_id)
    if not path.exists():
        raise ValueError(f"Unknown setup session: {session_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SetupSession(**payload)


def _record_timeline(session: SetupSession, event: str, payload: Optional[dict[str, Any]] = None) -> None:
    session.timeline.append(
        {
            "ts": _now_iso(),
            "event": event,
            "payload": payload or {},
        }
    )
    session.updated_at = _now_iso()


def start_setup_session(
    *,
    robot_name: Optional[str] = None,
    wizard_context: bool = False,
) -> Dict[str, Any]:
    """Create a new setup session persisted under ~/.castor/setup_sessions."""
    session = SetupSession(
        session_id=str(uuid.uuid4()),
        stage="probe",
        status="in_progress",
        created_at=_now_iso(),
        updated_at=_now_iso(),
        device=detect_device_info(),
        selections={"robot_name": robot_name} if robot_name else {},
        timeline=[],
    )
    _record_timeline(
        session,
        "session_started",
        {"setup_v3_enabled": setup_v3_enabled(wizard_context=wizard_context)},
    )
    return _save_session(session).to_dict()


def get_setup_session(session_id: str) -> Dict[str, Any]:
    """Return an existing setup session by id."""
    return _load_session(session_id).to_dict()


def resume_setup_session(session_id: str) -> Dict[str, Any]:
    """Resume an existing setup session."""
    session = _load_session(session_id)
    _record_timeline(session, "session_resumed")
    return _save_session(session).to_dict()


def find_resumable_setup_session(max_age_hours: int = 24) -> Optional[Dict[str, Any]]:
    """Return the latest in-progress setup session if one is available."""
    if not SETUP_SESSION_DIR.exists():
        return None

    cutoff = time.time() - float(max_age_hours * 3600)
    candidates = sorted(SETUP_SESSION_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        if path.stat().st_mtime < cutoff:
            continue
        with contextlib.suppress(Exception):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") == "in_progress":
                return payload
    return None


def select_setup_session(session_id: str, stage: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """Update session selections and move the active stage."""
    if stage not in SETUP_STAGE_ORDER:
        raise ValueError(f"Unknown setup stage: {stage}")
    session = _load_session(session_id)
    session.stage = stage
    session.selections.update(values or {})
    _record_timeline(session, "stage_selected", {"stage": stage, "values": values or {}})
    return _save_session(session).to_dict()


def finalize_setup_session(
    session_id: str,
    *,
    success: bool,
    reason_code: str = "READY",
    config: Optional[dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Mark session completed/failed and emit setup metrics row."""
    session = _load_session(session_id)
    session.stage = "save"
    session.status = "completed" if success else "failed"
    _record_timeline(session, "session_finalized", {"success": success, "reason_code": reason_code})
    saved = _save_session(session)

    created = datetime.fromisoformat(saved.created_at)
    duration_ms = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() * 1000.0)
    remediation_events = [e for e in saved.timeline if e.get("event") == "remediation_attempted"]
    first_remediation_ms = None
    if remediation_events:
        rem_ts = datetime.fromisoformat(remediation_events[0]["ts"])
        first_remediation_ms = max(0.0, (rem_ts - created).total_seconds() * 1000.0)

    selection = saved.selections
    stack_id = str(selection.get("stack_id") or "")
    used_fallback = bool(selection.get("used_fallback", False))
    provider = str(selection.get("provider") or "")
    record_setup_metric(
        platform_name=str(saved.device.get("platform", "unknown")),
        architecture=str(saved.device.get("architecture", "unknown")),
        stack_id=stack_id,
        provider=provider,
        result="success" if success else "failed",
        reason_code=reason_code,
        duration_ms=duration_ms,
        time_to_remediation_ms=first_remediation_ms,
        used_fallback=used_fallback,
        config=config,
    )
    return saved.to_dict()


def _install_apple_sdk() -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pip", "install", APPLE_SDK_GIT_URL]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
        ok = proc.returncode == 0
        detail = proc.stdout.strip() if ok else proc.stderr.strip() or proc.stdout.strip()
        return ok, detail
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)


def _stack_fallbacks(stack_id: Optional[str], device: Dict[str, Any]) -> list[str]:
    if not stack_id:
        return []
    stacks = {stack.id: stack for stack in get_stack_profiles(device)}
    stack = stacks.get(stack_id)
    if not stack:
        return []
    return list(stack.fallback_stack_ids)


def _preflight_check(
    *,
    check_id: str,
    category: str,
    severity: str,
    ok: bool,
    reason_code: str,
    evidence: str,
    remediation_id: Optional[str] = None,
    retryable: bool = True,
) -> PreflightCheck:
    return PreflightCheck(
        id=check_id,
        category=category,
        severity=severity,
        ok=ok,
        reason_code=reason_code,
        evidence=evidence,
        remediation_id=remediation_id,
        retryable=retryable,
        name=check_id,
        detail=evidence,
    )


def _apple_stack_preflight(model_profile: Optional[str]) -> Dict[str, Any]:
    raw = run_apple_preflight(model_profile_id=model_profile)
    reason_code = str(raw.get("reason") or "UNKNOWN")
    checks: list[PreflightCheck] = []
    for item in raw.get("checks", []):
        name = str(item.get("name", "unknown"))
        ok = bool(item.get("ok", False))
        detail = str(item.get("detail", ""))
        remediation = None
        check_reason = "READY"
        category = "runtime"
        severity = "warning"

        if not ok:
            if name == "apple_fm_sdk_import":
                check_reason = "SDK_MISSING"
                remediation = "install_apple_sdk"
                category = "dependency"
            elif name in {"platform", "architecture", "macos_version"}:
                check_reason = "DEVICE_NOT_ELIGIBLE"
                remediation = "open_apple_help"
                category = "compatibility"
            elif name == "xcode":
                check_reason = "RUNTIME_UNAVAILABLE"
                remediation = "open_apple_help"
                category = "runtime"
            elif name == "system_model_available":
                check_reason = reason_code if reason_code in REASON_ACTIONS else "MODEL_NOT_READY"
                remediation = "retry_preflight"
                category = "model"
            else:
                check_reason = "UNKNOWN"
                remediation = "retry_preflight"

        checks.append(
            _preflight_check(
                check_id=name,
                category=category,
                severity=severity,
                ok=ok,
                reason_code=check_reason,
                evidence=detail,
                remediation_id=remediation,
                retryable=True,
            )
        )

    if reason_code not in REASON_ACTIONS:
        reason_code = "UNKNOWN"
    return {
        "ok": bool(raw.get("ok", False)),
        "reason_code": reason_code,
        "issues": list(raw.get("issues", [])),
        "actions": list(raw.get("actions") or REASON_ACTIONS.get(reason_code, REASON_ACTIONS["UNKNOWN"])),
        "checks": checks,
        "device": raw.get("device") or detect_device_info(),
        "fallback_stacks": list(raw.get("fallback_stacks", [])),
    }


def _mlx_stack_preflight(model_profile: Optional[str]) -> Dict[str, Any]:
    device = detect_device_info()
    checks: list[PreflightCheck] = []

    platform_ok = str(device.get("platform", "")).lower() == "macos"
    checks.append(
        _preflight_check(
            check_id="mlx_platform",
            category="compatibility",
            severity="warning",
            ok=platform_ok,
            reason_code="READY" if platform_ok else "DEVICE_NOT_ELIGIBLE",
            evidence=f"platform={device.get('platform', 'unknown')}",
            remediation_id=None if platform_ok else "retry_preflight",
            retryable=True,
        )
    )

    arch = str(device.get("architecture", "")).lower()
    arch_ok = arch in {"arm64", "aarch64"}
    checks.append(
        _preflight_check(
            check_id="mlx_architecture",
            category="compatibility",
            severity="warning",
            ok=arch_ok,
            reason_code="READY" if arch_ok else "DEVICE_NOT_ELIGIBLE",
            evidence=f"arch={arch}",
            remediation_id=None if arch_ok else "retry_preflight",
            retryable=False,
        )
    )

    mlx_ok = True
    mlx_detail = "mlx runtime import ok"
    try:
        __import__("mlx_lm")
    except Exception:
        try:
            __import__("mlx")
        except Exception as exc:
            mlx_ok = False
            mlx_detail = str(exc)
    checks.append(
        _preflight_check(
            check_id="mlx_import",
            category="dependency",
            severity="warning",
            ok=mlx_ok,
            reason_code="READY" if mlx_ok else "SDK_MISSING",
            evidence=mlx_detail,
            remediation_id=None if mlx_ok else "install_mlx_lm",
            retryable=True,
        )
    )

    model_ok = bool(model_profile)
    checks.append(
        _preflight_check(
            check_id="mlx_model_profile",
            category="model",
            severity="warning",
            ok=model_ok,
            reason_code="READY" if model_ok else "MODEL_NOT_READY",
            evidence=f"model_profile={model_profile or ''}",
            remediation_id=None if model_ok else "retry_preflight",
            retryable=True,
        )
    )

    failed = [c for c in checks if not c.ok]
    if not failed:
        reason_code = "READY"
    else:
        priority = ["DEVICE_NOT_ELIGIBLE", "SDK_MISSING", "MODEL_NOT_READY", "RUNTIME_UNAVAILABLE", "UNKNOWN"]
        reason_code = next((rc for rc in priority if any(c.reason_code == rc for c in failed)), "UNKNOWN")
    return {
        "ok": len(failed) == 0,
        "reason_code": reason_code,
        "issues": [c.evidence for c in failed],
        "actions": REASON_ACTIONS.get(reason_code, REASON_ACTIONS["UNKNOWN"]),
        "checks": checks,
        "device": device,
        "fallback_stacks": ["ollama_universal_local"] if failed else [],
    }


def _ollama_stack_preflight(model_profile: Optional[str]) -> Dict[str, Any]:
    device = detect_device_info()
    checks: list[PreflightCheck] = []

    daemon_ok = False
    daemon_detail = "Ollama daemon not reachable"
    tags: list[dict[str, Any]] = []
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=2.5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            tags = list(payload.get("models", []))
            daemon_ok = True
            daemon_detail = "daemon reachable"
    except URLError as exc:
        daemon_detail = str(exc)
    except Exception as exc:
        daemon_detail = str(exc)

    checks.append(
        _preflight_check(
            check_id="ollama_daemon",
            category="runtime",
            severity="warning",
            ok=daemon_ok,
            reason_code="READY" if daemon_ok else "RUNTIME_UNAVAILABLE",
            evidence=daemon_detail,
            remediation_id=None if daemon_ok else "start_ollama",
            retryable=True,
        )
    )

    model_ok = True
    model_detail = "no model profile supplied"
    if model_profile:
        found = any(str(item.get("name", "")).startswith(model_profile) for item in tags)
        model_ok = found if daemon_ok else False
        model_detail = f"model={model_profile} present={found}"
    checks.append(
        _preflight_check(
            check_id="ollama_model",
            category="model",
            severity="warning",
            ok=model_ok,
            reason_code="READY" if model_ok else "MODEL_NOT_READY",
            evidence=model_detail,
            remediation_id=None if model_ok else "open_ollama_help",
            retryable=True,
        )
    )

    failed = [c for c in checks if not c.ok]
    if not failed:
        reason_code = "READY"
    else:
        priority = ["RUNTIME_UNAVAILABLE", "MODEL_NOT_READY", "UNKNOWN"]
        reason_code = next((rc for rc in priority if any(c.reason_code == rc for c in failed)), "UNKNOWN")
    return {
        "ok": len(failed) == 0,
        "reason_code": reason_code,
        "issues": [c.evidence for c in failed],
        "actions": REASON_ACTIONS.get(reason_code, REASON_ACTIONS["UNKNOWN"]),
        "checks": checks,
        "device": device,
        "fallback_stacks": [],
    }


def run_preflight(
    provider: str,
    model_profile: Optional[str] = None,
    auto_install: bool = False,
    *,
    stack_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run provider/stack setup preflight checks.

    Returns the old fields (`ok`, `reason`, `issues`, `actions`, `fallback_stacks`)
    and extends `checks` with typed fields for setup-v3 compatibility.
    """
    provider_name = provider.lower().strip()
    stack = (stack_id or "").strip()

    if stack == "apple_native" or (not stack and provider_name == "apple"):
        adapter = _apple_stack_preflight(model_profile)
    elif stack == "mlx_local_vision":
        adapter = _mlx_stack_preflight(model_profile)
    elif stack == "ollama_universal_local":
        adapter = _ollama_stack_preflight(model_profile)
    else:
        adapter = {
            "ok": True,
            "reason_code": "READY",
            "issues": [],
            "actions": [],
            "checks": [
                _preflight_check(
                    check_id="generic",
                    category="generic",
                    severity="info",
                    ok=True,
                    reason_code="READY",
                    evidence="No preflight required",
                    remediation_id=None,
                    retryable=False,
                )
            ],
            "device": detect_device_info(),
            "fallback_stacks": _stack_fallbacks(stack, detect_device_info()),
        }

    # Optional auto-install flow for Apple SDK.
    auto_install_result: Optional[dict[str, Any]] = None
    if auto_install and not adapter["ok"]:
        missing_sdk = any(
            (not check.ok) and check.reason_code == "SDK_MISSING"
            for check in adapter["checks"]
        )
        if missing_sdk and (stack == "apple_native" or provider_name == "apple"):
            install_ok, install_detail = _install_apple_sdk()
            auto_install_result = {
                "attempted": True,
                "ok": install_ok,
                "detail": install_detail,
            }
            if install_ok:
                adapter = _apple_stack_preflight(model_profile)

    checks_payload = [check.to_dict() for check in sorted(adapter["checks"], key=lambda c: c.id)]
    payload: Dict[str, Any] = {
        "ok": bool(adapter["ok"]),
        "provider": provider_name,
        "stack_id": stack or None,
        "reason": adapter["reason_code"],  # compatibility alias
        "reason_code": adapter["reason_code"],
        "issues": list(adapter.get("issues", [])),
        "actions": list(adapter.get("actions", [])),
        "checks": checks_payload,
        "device": adapter.get("device") or detect_device_info(),
        "fallback_stacks": list(adapter.get("fallback_stacks") or _stack_fallbacks(stack, detect_device_info())),
        "model_profile": model_profile,
    }
    if auto_install_result is not None:
        payload["auto_install"] = auto_install_result

    if session_id:
        with contextlib.suppress(Exception):
            session = _load_session(session_id)
            session.stage = "preflight"
            session.checks = checks_payload
            _record_timeline(
                session,
                "preflight_ran",
                {
                    "provider": provider_name,
                    "stack_id": stack or None,
                    "ok": payload["ok"],
                    "reason_code": payload["reason_code"],
                },
            )
            _save_session(session)
    return payload


def run_remediation(
    remediation_id: str,
    *,
    consent: bool,
    session_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute one remediation action from the registry."""
    action = _REMEDIATION_REGISTRY.get(remediation_id)
    if not action:
        raise ValueError(f"Unknown remediation_id: {remediation_id}")

    if action.consent_required and not consent:
        return {
            "ok": False,
            "remediation_id": remediation_id,
            "action_type": action.action_type,
            "requires_consent": True,
            "message": "Explicit consent is required before executing this remediation.",
        }

    result: Dict[str, Any] = {
        "ok": True,
        "remediation_id": remediation_id,
        "action_type": action.action_type,
        "label": action.label,
        "requires_consent": action.consent_required,
    }

    ctx = context or {}
    if action.action_type in {"install_dependency", "start_service"} and action.command:
        command = [item.format(**ctx) for item in action.command]
        timeout = 300 if action.action_type == "install_dependency" else 30
        proc = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        result["command"] = command
        result["exit_code"] = proc.returncode
        result["output"] = (proc.stdout or proc.stderr or "").strip()
        result["ok"] = proc.returncode == 0
    elif action.action_type == "open_help":
        result["help_url"] = action.help_url
        result["message"] = f"Open help URL: {action.help_url}"
    elif action.action_type == "retry_check":
        result["message"] = "Re-run setup preflight for the target check."
    else:
        result["ok"] = False
        result["message"] = f"Unsupported remediation action: {action.action_type}"

    if session_id:
        with contextlib.suppress(Exception):
            session = _load_session(session_id)
            session.stage = "remediation"
            _record_timeline(
                session,
                "remediation_attempted",
                {
                    "remediation_id": remediation_id,
                    "ok": bool(result.get("ok", False)),
                    "action_type": action.action_type,
                },
            )
            _save_session(session)
    return result


def _build_agent_config(provider_key: str, model_id: str) -> Dict[str, Any]:
    auth_map = get_provider_auth_map()
    info = auth_map.get(provider_key)
    if info is None:
        raise ValueError(f"Unknown provider: {provider_key}")

    actual_provider = "openai" if info.get("openai_compat") else provider_key
    config = {
        "provider": actual_provider,
        "model": model_id,
        "label": f"{info['label']} {model_id}",
        "env_var": info.get("env_var"),
    }
    if info.get("base_url"):
        config["base_url"] = info["base_url"]
    if provider_key == "apple":
        config["apple_profile"] = model_id
    return config


def generate_preset_config(
    preset_name: str,
    robot_name: str,
    agent_config: Dict[str, Any],
    secondary_models: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Generate RCAN config from a preset and setup selections."""
    preset_path = Path(__file__).resolve().parent.parent / "config" / "presets" / f"{preset_name}.rcan.yaml"

    if preset_path.exists():
        config = yaml.safe_load(preset_path.read_text()) or {}
        config.setdefault("metadata", {})
        config["metadata"]["robot_name"] = robot_name
        config["metadata"]["robot_uuid"] = str(uuid.uuid4())
        config["metadata"]["created_at"] = _now_iso()
    else:
        config = {
            "rcan_version": "1.0.0-alpha",
            "metadata": {
                "robot_name": robot_name,
                "robot_uuid": str(uuid.uuid4()),
                "created_at": _now_iso(),
                "author": "OpenCastor Setup",
                "license": "Apache-2.0",
            },
            "agent": {},
            "physics": {"type": "differential_drive", "dof": 2},
            "drivers": [],
            "network": {"telemetry_stream": True},
            "rcan_protocol": {"port": 8000, "capabilities": ["status", "nav", "teleop", "chat"]},
        }

    config.setdefault("agent", {})
    config["agent"]["provider"] = agent_config["provider"]
    config["agent"]["model"] = agent_config["model"]
    if agent_config.get("base_url"):
        config["agent"]["base_url"] = agent_config["base_url"]
    if agent_config.get("apple_profile"):
        config["agent"]["apple_profile"] = agent_config["apple_profile"]

    if secondary_models:
        config["agent"]["secondary_models"] = [
            {
                "provider": item["provider"],
                "model": item["model"],
                "tags": item.get("tags", []),
            }
            for item in secondary_models
        ]
    return config


def generate_setup_config(
    robot_name: str,
    provider: str,
    model: str,
    preset: str,
    secondary_models: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build setup output payload from setup selections."""
    agent_config = _build_agent_config(provider, model)
    config = generate_preset_config(
        preset_name=preset,
        robot_name=robot_name,
        agent_config=agent_config,
        secondary_models=secondary_models,
    )
    filename = f"{robot_name.lower().replace(' ', '_')}.rcan.yaml"
    return {
        "filename": filename,
        "agent_config": agent_config,
        "config": config,
    }


def verify_setup_config(
    *,
    robot_name: str,
    provider: str,
    model: str,
    preset: str,
    stack_id: Optional[str] = None,
    api_key: Optional[str] = None,
    allow_warnings: bool = False,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Dry-run setup verification before writing config to disk."""
    payload = generate_setup_config(
        robot_name=robot_name,
        provider=provider,
        model=model,
        preset=preset,
    )
    config = payload["config"]
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    # Provider init + health check.
    env_var = payload["agent_config"].get("env_var")
    old_env = os.getenv(env_var) if env_var else None
    try:
        if env_var and api_key:
            os.environ[env_var] = api_key
        try:
            from castor.providers import get_provider

            provider_inst = get_provider(
                {
                    "provider": provider,
                    "model": model,
                    **({"api_key": api_key} if api_key else {}),
                }
            )
            health = provider_inst.health_check()
            provider_ok = bool(health.get("ok", False))
            checks.append(
                {
                    "id": "provider_health",
                    "ok": provider_ok,
                    "severity": "error" if not provider_ok else "info",
                    "evidence": health,
                }
            )
            if not provider_ok:
                errors.append(f"Provider health check failed: {health.get('error') or health}")
        except Exception as exc:
            errors.append(f"Provider initialization failed: {exc}")
            checks.append(
                {
                    "id": "provider_health",
                    "ok": False,
                    "severity": "error",
                    "evidence": str(exc),
                }
            )
    finally:
        if env_var:
            if old_env is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = old_env

    # Driver viability checks.
    drivers = config.get("drivers") or []
    if not drivers:
        errors.append("Generated config has no drivers configured.")
    else:
        host = detect_device_info().get("platform", "")
        for idx, drv in enumerate(drivers):
            protocol = str(drv.get("protocol", ""))
            if "i2c" in protocol.lower() and host not in {"linux"}:
                warnings.append(
                    f"Driver {idx} uses {protocol}, which is typically unsupported on {host}."
                )

    # Channel credential sanity.
    channels = config.get("channels")
    if channels:
        refs: list[str] = []
        if isinstance(channels, dict):
            refs = [json.dumps(channels)]
        elif isinstance(channels, list):
            refs = [json.dumps(item) for item in channels]
        for blob in refs:
            for token in [part for part in blob.split("${") if "}" in part]:
                env_name = token.split("}", 1)[0]
                if not os.getenv(env_name):
                    warnings.append(f"Missing channel env var: {env_name}")

    for msg in errors:
        checks.append({"id": "verify_error", "ok": False, "severity": "error", "evidence": msg})
    for msg in warnings:
        checks.append({"id": "verify_warning", "ok": False, "severity": "warning", "evidence": msg})

    ok = len(errors) == 0 and (allow_warnings or len(warnings) == 0)
    result = {
        "ok": ok,
        "allow_warnings": allow_warnings,
        "blocking_errors": errors,
        "warnings": warnings,
        "checks": checks,
        "preview_filename": payload["filename"],
        "stack_id": stack_id,
    }

    if session_id:
        with contextlib.suppress(Exception):
            session = _load_session(session_id)
            session.stage = "verify"
            _record_timeline(
                session,
                "verify_ran",
                {
                    "ok": ok,
                    "error_count": len(errors),
                    "warning_count": len(warnings),
                },
            )
            _save_session(session)
    return result


def save_env_vars(env_vars: Dict[str, str]) -> None:
    """Persist env vars to local .env file, upserting keys."""
    env_path = Path(".env")
    existing = env_path.read_text() if env_path.exists() else ""
    lines = existing.splitlines()

    for key, value in env_vars.items():
        updated = False
        for idx, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[idx] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n")


def save_config_file(config: Dict[str, Any], filename: str) -> str:
    """Write generated RCAN config to disk."""
    Path(filename).write_text(yaml.dump(config, sort_keys=False, default_flow_style=False))
    return filename


def resolve_provider_env_var(provider: str) -> Optional[str]:
    """Return env var for provider setup key entry, if any."""
    auth_map = get_provider_auth_map()
    info = auth_map.get(provider.lower())
    return info.get("env_var") if info else None


def _metrics_conn() -> sqlite3.Connection:
    _ensure_parent(SETUP_METRICS_DB)
    conn = sqlite3.connect(str(SETUP_METRICS_DB))
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SETUP_METRICS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            platform TEXT NOT NULL,
            arch TEXT NOT NULL,
            stack_id TEXT,
            provider TEXT,
            result TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            duration_ms REAL NOT NULL,
            time_to_remediation_ms REAL,
            used_fallback INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn


def record_setup_metric(
    *,
    platform_name: str,
    architecture: str,
    stack_id: str,
    provider: str,
    result: str,
    reason_code: str,
    duration_ms: float,
    time_to_remediation_ms: Optional[float],
    used_fallback: bool,
    config: Optional[dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist one anonymized local setup metric row."""
    if not _telemetry_enabled(config):
        return {"ok": False, "skipped": "telemetry_disabled"}

    with _metrics_conn() as conn:
        conn.execute(
            f"""
            INSERT INTO {SETUP_METRICS_TABLE}
                (ts, platform, arch, stack_id, provider, result, reason_code, duration_ms, time_to_remediation_ms, used_fallback)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_iso(),
                platform_name,
                architecture,
                stack_id or None,
                provider or None,
                result,
                reason_code,
                float(duration_ms),
                float(time_to_remediation_ms) if time_to_remediation_ms is not None else None,
                1 if used_fallback else 0,
            ),
        )
        conn.commit()
    return {"ok": True}


def get_setup_metrics(config: Optional[dict[str, Any]] = None) -> Dict[str, Any]:
    """Return aggregated setup reliability metrics."""
    enabled = _telemetry_enabled(config)
    if not enabled:
        return SetupMetrics(
            telemetry_enabled=False,
            total_runs=0,
            first_run_success_rate=0.0,
            median_time_to_remediation_ms=0.0,
            fallback_success_rate=0.0,
            setup_abandonment_rate=0.0,
            top_reason_codes={},
            generated_at=_now_iso(),
        ).to_dict()

    with _metrics_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT result, reason_code, duration_ms, time_to_remediation_ms, used_fallback
            FROM {SETUP_METRICS_TABLE}
            """
        ).fetchall()

    total = len(rows)
    if total == 0:
        return SetupMetrics(
            telemetry_enabled=True,
            total_runs=0,
            first_run_success_rate=0.0,
            median_time_to_remediation_ms=0.0,
            fallback_success_rate=0.0,
            setup_abandonment_rate=0.0,
            top_reason_codes={},
            generated_at=_now_iso(),
        ).to_dict()

    success_count = sum(1 for row in rows if row[0] == "success")
    abandoned_count = sum(1 for row in rows if row[0] == "abandoned")

    remediation_values = [float(row[3]) for row in rows if row[3] is not None]
    fallback_total = sum(1 for row in rows if int(row[4]) == 1)
    fallback_success = sum(1 for row in rows if int(row[4]) == 1 and row[0] == "success")

    reason_counts: Dict[str, int] = dict(Counter(str(row[1]) for row in rows).most_common(10))
    metrics = SetupMetrics(
        telemetry_enabled=True,
        total_runs=total,
        first_run_success_rate=float(success_count / total),
        median_time_to_remediation_ms=float(
            (sorted(remediation_values)[len(remediation_values) // 2]) if remediation_values else 0.0
        ),
        fallback_success_rate=float((fallback_success / fallback_total) if fallback_total else 0.0),
        setup_abandonment_rate=float(abandoned_count / total),
        top_reason_codes=reason_counts,
        generated_at=_now_iso(),
    )
    return metrics.to_dict()
