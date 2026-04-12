"""castor.instructions_for_use — Art. 13 Instructions for Use document generator.

Generates an EU AI Act Art. 13-structured Instructions for Use document from
RCAN config and deployment context. The document covers all fields required
by Art. 13(3) of the EU AI Act for high-risk AI systems.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from castor.fria import ANNEX_III_BASES

IFU_SCHEMA_VERSION = "rcan-ifu-v1"

# EU AI Act Art. 13(3) required fields
ART13_FIELDS = [
    "provider_identity",
    "intended_purpose",
    "capabilities_and_limitations",
    "accuracy_and_performance",
    "human_oversight_measures",
    "known_risks_and_misuse",
    "expected_lifetime",
    "maintenance_requirements",
]


def build_ifu_document(
    config: dict,
    annex_iii_basis: str,
    intended_use: str,
) -> dict[str, Any]:
    """Build an Art. 13-structured Instructions for Use document.

    Args:
        config:          Parsed RCAN config dict.
        annex_iii_basis: EU AI Act Annex III classification.
        intended_use:    Deployment description.

    Returns:
        IFU document dict ready for JSON serialization or HTML rendering.
    """
    if annex_iii_basis not in ANNEX_III_BASES:
        raise ValueError(
            f"Invalid annex_iii_basis: {annex_iii_basis!r}. "
            f"Must be one of: {', '.join(sorted(ANNEX_III_BASES))}"
        )
    meta = config.get("metadata", {}) or {}
    agent_cfg = config.get("agent", {}) or {}

    return {
        "schema": IFU_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "art13_coverage": ART13_FIELDS,
        # Art. 13(3)(a) — provider identity
        "provider_identity": {
            "rrn": meta.get("rrn", ""),
            "rrn_uri": meta.get("rrn_uri", ""),
            "robot_name": meta.get("robot_name", ""),
            "provider_name": meta.get("provider_name", ""),
            "provider_contact": meta.get("provider_contact", ""),
            "rcan_version": config.get("rcan_version", ""),
            "agent_provider": agent_cfg.get("provider", ""),
            "agent_model": agent_cfg.get("model", ""),
        },
        # Art. 13(3)(b) — intended purpose
        "intended_purpose": {
            "description": intended_use,
            "annex_iii_basis": annex_iii_basis,
            "deployment_context": ("High-risk AI system under EU AI Act Annex III"),
        },
        # Art. 13(3)(c) — capabilities and limitations
        "capabilities_and_limitations": {
            "summary": (
                "OpenCastor is a universal robot runtime that connects LLM AI "
                "providers to physical robot hardware. It enforces safety "
                "constraints via SafetyLayer, BoundsChecker, and HiTL "
                "authorization gates."
            ),
            "known_limitations": [
                "AI provider responses are subject to model confidence thresholds",
                "Physical hardware limits enforced by BoundsChecker configuration",
                "HiTL authorization required for high-risk actions",
                "Wireless connectivity required for remote monitoring",
            ],
        },
        # Art. 13(3)(d) — accuracy and performance
        "accuracy_and_performance": {
            "note": (
                "Quantified performance evidence is available via `castor safety "
                "benchmark`. Safety path P95 latency thresholds: ESTOP 100ms, "
                "BoundsCheck 5ms, ConfidenceGate 2ms, FullPipeline 50ms."
            ),
        },
        # Art. 13(3)(e) — human oversight measures
        "human_oversight_measures": {
            "hitl_gates": (
                "Human-in-the-loop authorization gates (RCAN §8) prevent "
                "autonomous high-risk actions"
            ),
            "estop": ("Emergency stop (ESTOP) halts all motion; P95 latency ≤ 100ms"),
            "confidence_gates": (
                "AI commands below confidence threshold are blocked automatically"
            ),
            "override": ("Operators can override or halt the system at any time via ESTOP"),
        },
        # Art. 13(3)(f) — foreseeable misuse
        "known_risks_and_misuse": {
            "foreseeable_misuse": [
                "Deployment in environments outside the declared intended use",
                "Operating beyond configured physical bounds",
                "Disabling HiTL gates without risk assessment",
                "Using uncertified AI providers without Art. 10 documentation",
            ],
            "mitigations": [
                "BoundsChecker enforces hard physical limits",
                "Conformance checks detect disabled safety features",
                "Anti-subversion module defends against prompt injection",
            ],
        },
        # Art. 13(3)(g) — expected lifetime
        "expected_lifetime": {
            "software_support": ("Subject to OpenCastor release lifecycle (YYYY.MM.DD versioning)"),
            "hardware_dependent": True,
            "note": ("Deployer is responsible for post-market monitoring per Art. 72"),
        },
        # Art. 13(3)(h) — maintenance
        "maintenance_requirements": {
            "software_updates": ("Run `castor upgrade` for runtime updates; monitor CHANGELOG.md"),
            "conformance_checks": "Run `castor validate` before each deployment",
            "incident_logging": ("Run `castor incidents record` for any safety-relevant events"),
        },
    }
