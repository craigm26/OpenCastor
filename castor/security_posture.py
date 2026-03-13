"""Security posture helpers for boot-time attestation checks.

This module reads attestation data from either a kernel-exposed file,
a local agent JSON file, or environment-provided token, then normalises
it into a single structure that can be published into ``/proc/safety``
and surfaced through API status endpoints.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.Security")

_DEFAULT_ATTESTATION_PATHS = (
    "/proc/attestation/opencastor.json",
    "/run/opencastor/attestation.json",
)


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _load_json(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Attestation payload must be a JSON object")
    return payload


def detect_attestation_status() -> dict[str, Any]:
    """Return normalized runtime security posture data.

    Source precedence:
      1) ``OPENCASTOR_ATTESTATION_PATH`` (JSON object file)
      2) default local paths (first existing)
      3) environment fallback values

    The returned object is intentionally compact so it is safe to expose
    in status APIs and lightweight virtual proc nodes.
    """

    source = "none"
    payload: dict[str, Any] = {}

    configured_path = os.getenv("OPENCASTOR_ATTESTATION_PATH")
    candidate_paths = [configured_path] if configured_path else []
    candidate_paths.extend(_DEFAULT_ATTESTATION_PATHS)

    for raw_path in candidate_paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.exists():
            continue
        try:
            payload = _load_json(path)
            source = str(path)
            break
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to parse attestation payload at %s: %s", path, exc)

    # Environment fallback (for minimal profile setups)
    env_token = os.getenv("OPENCASTOR_ATTESTATION_TOKEN")
    env_verified = os.getenv("OPENCASTOR_ATTESTATION_VERIFIED")
    env_profile = os.getenv("OPENCASTOR_SECURITY_PROFILE")

    token = payload.get("token") or env_token
    secure_boot = _coerce_bool(
        payload.get("secure_boot"),
        default=_coerce_bool(os.getenv("OPENCASTOR_SECURE_BOOT"), default=False),
    )
    measured_boot = _coerce_bool(
        payload.get("measured_boot"),
        default=_coerce_bool(os.getenv("OPENCASTOR_MEASURED_BOOT"), default=False),
    )
    update_chain = _coerce_bool(
        payload.get("signed_updates"),
        default=_coerce_bool(os.getenv("OPENCASTOR_SIGNED_UPDATES"), default=False),
    )

    if "verified" in payload:
        verified = _coerce_bool(payload.get("verified"))
    elif env_verified is not None:
        verified = _coerce_bool(env_verified)
    else:
        verified = bool(secure_boot and measured_boot and update_chain)

    profile = str(
        payload.get("profile") or env_profile or ("secure" if verified else "minimum-viable")
    )

    mode = "enforced" if verified else "degraded"
    reasons = []
    if not secure_boot:
        reasons.append("secure_boot_unverified")
    if not measured_boot:
        reasons.append("measured_boot_unavailable")
    if not update_chain:
        reasons.append("signed_update_chain_missing")

    return {
        "mode": mode,
        "verified": verified,
        "profile": profile,
        "token_present": bool(token),
        "token": token,
        "source": source,
        "claims": {
            "secure_boot": secure_boot,
            "measured_boot": measured_boot,
            "signed_updates": update_chain,
        },
        "reasons": reasons,
    }


def publish_attestation(fs: Any) -> Optional[dict[str, Any]]:
    """Publish attestation state into ``/proc/safety`` nodes for a CastorFS instance."""

    if fs is None:
        return None

    posture = detect_attestation_status()
    try:
        fs.ns.mkdir("/proc/safety")
    except Exception:
        pass

    fs.ns.write("/proc/safety", posture)
    fs.ns.write("/proc/safety/mode", posture["mode"])
    fs.ns.write("/proc/safety/attestation", posture)
    fs.ns.write(
        "/proc/safety/attestation_status", "verified" if posture["verified"] else "degraded"
    )
    fs.ns.write("/proc/safety/attestation_token", posture.get("token"))
    return posture
