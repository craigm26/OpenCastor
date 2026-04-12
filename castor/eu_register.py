"""castor.eu_register — EU AI Act Art. 49 database submission package generator.

Generates a structured submission package from a signed FRIA artifact + RCAN config.
The package contains all fields required for EU AI Act database registration.

Actual submission requires manual action at the EU AI Act registration portal —
this module generates the data package; it does not submit automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

EU_AI_ACT_REGISTRATION_URL = "https://ec.europa.eu/digital-strategy/en/policies/european-ai-act"
SUBMISSION_SCHEMA_VERSION = "rcan-eu-register-v1"
FRIA_SCHEMA_REQUIRED = "rcan-fria-v1"


def build_submission_package(fria: dict, config: dict) -> dict[str, Any]:
    """Generate an EU AI Act Art. 49 database submission package.

    Args:
        fria:   Signed (or unsigned) FRIA document dict (schema must be 'rcan-fria-v1').
        config: Parsed RCAN config dict.

    Returns:
        Submission package dict ready for JSON serialization.

    Raises:
        ValueError: If ``fria["schema"]`` is not ``"rcan-fria-v1"``.
    """
    if fria.get("schema") != FRIA_SCHEMA_REQUIRED:
        raise ValueError(
            f"FRIA schema must be {FRIA_SCHEMA_REQUIRED!r}, got {fria.get('schema')!r}. "
            "Run `castor fria generate` to produce a valid FRIA."
        )

    meta = config.get("metadata", {}) or {}
    system_info = fria.get("system", {}) or {}
    deployment = fria.get("deployment", {}) or {}
    conformance = fria.get("conformance", {}) or {}
    human_oversight = fria.get("human_oversight", {}) or {}

    return {
        "schema": SUBMISSION_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fria_ref": {
            "generated_at": fria.get("generated_at", ""),
            "schema": fria.get("schema", ""),
        },
        "provider": {
            "name": meta.get("provider_name", ""),
            "contact": meta.get("provider_contact", ""),
            "rrn": system_info.get("rrn", meta.get("rrn", "")),
            "note": (
                "provider.name and provider.contact must be filled in manually. "
                "Add provider_name and provider_contact to your RCAN config metadata."
            ),
        },
        "system": {
            "rrn": system_info.get("rrn", ""),
            "rrn_uri": system_info.get("rrn_uri", ""),
            "robot_name": system_info.get("robot_name", ""),
            "opencastor_version": system_info.get("opencastor_version", ""),
            "rcan_version": system_info.get("rcan_version", ""),
            "agent_provider": system_info.get("agent_provider", ""),
            "agent_model": system_info.get("agent_model", ""),
            "intended_use": deployment.get("intended_use", ""),
        },
        "annex_iii_basis": deployment.get("annex_iii_basis", ""),
        "conformity_status": {
            "fria_overall_pass": fria.get("overall_pass", conformance.get("fail", 1) == 0),
            "conformance_score": conformance.get("score", 0),
            "hitl_configured": human_oversight.get("hitl_configured", False),
            "estop_configured": human_oversight.get("estop_configured", False),
            "fria_generated_at": fria.get("generated_at", ""),
        },
        "submission_instructions": (
            f"Register this system in the EU AI Act database at: {EU_AI_ACT_REGISTRATION_URL}\n"
            "Steps:\n"
            "1. Fill in provider.name and provider.contact in this package.\n"
            "2. Log in with your EU representative credentials.\n"
            "3. Create a new high-risk AI system registration.\n"
            "4. Upload this JSON package and your signed FRIA artifact.\n"
            "5. Await confirmation (typically 2-4 weeks).\n"
            "Deadline: August 2, 2026 for Annex III high-risk AI systems."
        ),
    }
