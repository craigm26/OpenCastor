"""FRIA document generation for EU AI Act compliance (RCAN §22).

Provides:
    check_fria_prerequisite  — conformance gate (score >= 80, 0 safety fails)
    build_fria_document      — assemble unsigned FRIA JSON document
    sign_fria                — add ML-DSA-65 signature
    render_fria_html         — render Jinja2 HTML companion
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from castor.conformance import ConformanceChecker, ConformanceResult
from castor.rcan.message_signing import get_message_signer

logger = logging.getLogger("OpenCastor.FRIA")

FRIA_SCHEMA_VERSION = "rcan-fria-v1"
FRIA_SPEC_REF = "https://rcan.dev/spec/section-22"
CONFORMANCE_SCORE_MIN = 80
MEMORY_CONFIDENCE_MIN = 0.30

ANNEX_III_BASES = frozenset(
    {
        "safety_component",
        "biometric",
        "critical_infrastructure",
        "education",
        "employment",
        "essential_services",
        "law_enforcement",
        "migration",
        "administration_of_justice",
        "general_purpose_ai",
    }
)


def check_fria_prerequisite(
    config: dict,
    annex_iii_strict: bool = False,
) -> tuple[bool, list[ConformanceResult]]:
    """Run conformance checks and return (gate_passed, blocking_results).

    Gate passes when conformance score >= 80 and there are zero safety.* failures.
    When annex_iii_strict=True, Art. 16 checks (SBOM, firmware, authority) are
    promoted from warn to fail, raising the bar for Annex III high-risk systems.
    """
    checker = ConformanceChecker(config, annex_iii_strict=annex_iii_strict)
    results = checker.run_all()
    summary = checker.summary(results)

    safety_failures = [r for r in results if r.category == "safety" and r.status == "fail"]
    score_ok = summary["score"] >= CONFORMANCE_SCORE_MIN
    gate_passed = score_ok and len(safety_failures) == 0

    if not gate_passed:
        blocking = [r for r in results if r.status == "fail"] if not score_ok else safety_failures
    else:
        blocking = []

    return gate_passed, blocking


def _load_benchmark_block(benchmark_path: str | None) -> dict:
    """Load and validate a safety benchmark JSON file for FRIA inlining.

    Returns ``{"safety_benchmarks": {...}}`` when the file exists and has the
    correct schema. Returns ``{}`` when path is None or file missing.
    Raises ``ValueError`` for invalid schema.
    """
    if benchmark_path is None:
        return {}
    if not os.path.exists(benchmark_path):
        return {}

    with open(benchmark_path) as f:
        data = json.load(f)

    if data.get("schema") != "rcan-safety-benchmark-v1":
        raise ValueError(
            f"Invalid safety benchmark schema: {data.get('schema')!r}. "
            "Expected 'rcan-safety-benchmark-v1'."
        )

    return {
        "safety_benchmarks": {
            "ref": os.path.basename(benchmark_path),
            "generated_at": data.get("generated_at", ""),
            "mode": data.get("mode", ""),
            "overall_pass": data.get("overall_pass", False),
            "results": data.get("results", {}),
        }
    }


def build_fria_document(
    config: dict,
    annex_iii_basis: str,
    intended_use: str,
    memory_path: str | None = None,
    prerequisite_waived: bool = False,
    benchmark_path: str | None = None,
    annex_iii_strict: bool = False,
) -> dict:
    """Assemble the unsigned FRIA JSON document dict.

    Args:
        config:              Parsed RCAN config dict.
        annex_iii_basis:     EU AI Act Annex III classification. Must be one of
                             ANNEX_III_BASES.
        intended_use:        Free-text deployment description.
        memory_path:         Path to robot-memory.md. HARDWARE_OBSERVATION entries
                             with confidence >= 0.30 are included.
        prerequisite_waived: True when --force was passed; recorded in the document.
        benchmark_path:      Path to a safety benchmark JSON artifact (rcan-safety-benchmark-v1).
                             Validated and inlined under ``safety_benchmarks`` when provided and valid.
                             Silently omitted when None or file missing.

    Returns:
        Unsigned FRIA document dict (no 'sig' or 'signing_key' fields).
    """
    if annex_iii_basis not in ANNEX_III_BASES:
        raise ValueError(
            f"Invalid annex_iii_basis: {annex_iii_basis!r}. "
            f"Must be one of: {', '.join(sorted(ANNEX_III_BASES))}"
        )

    meta = config.get("metadata", {})
    agent_cfg = config.get("agent", {})

    try:
        import importlib.metadata as _imd

        oc_version = _imd.version("opencastor")
    except Exception:
        oc_version = "unknown"

    # Run conformance
    checker = ConformanceChecker(config, annex_iii_strict=annex_iii_strict)
    results = checker.run_all()
    summary = checker.summary(results)

    # Derive human_oversight from conformance results
    check_map = {r.check_id: r for r in results}

    def _passed(*check_ids: str) -> bool:
        # check_ids are aliases for the same gate — pass if any alias is found passing
        return any(
            check_map.get(cid) is not None and check_map[cid].status == "pass" for cid in check_ids
        )

    human_oversight = {
        "hitl_configured": _passed(
            "safety.hitl_authorization_configured", "safety.hitl_configured"
        ),
        "confidence_gates_configured": _passed("safety.confidence_gates_configured"),
        "estop_configured": _passed("safety.estop_configured", "safety.estop_capable"),
    }

    # Hardware observations from robot memory
    hardware_observations: list[dict[str, Any]] = []
    if memory_path and os.path.exists(memory_path):
        try:
            from castor.brain.memory_schema import EntryType, load_memory

            memory = load_memory(memory_path)
            hardware_observations = [
                {
                    "id": e.id,
                    "text": e.text,
                    "confidence": round(e.confidence, 3),
                    "tags": e.tags,
                }
                for e in memory.entries
                if e.type == EntryType.HARDWARE_OBSERVATION
                and e.confidence >= MEMORY_CONFIDENCE_MIN
            ]
        except Exception as exc:
            logger.warning("Could not load robot memory from %s: %s", memory_path, exc)

    return {
        "schema": FRIA_SCHEMA_VERSION,
        "spec_ref": FRIA_SPEC_REF,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "system": {
            "rrn": meta.get("rrn", ""),
            "rrn_uri": meta.get("rrn_uri", ""),
            "robot_name": meta.get("robot_name", ""),
            "opencastor_version": oc_version,
            "rcan_version": meta.get("rcan_version", config.get("rcan_version", "")),
            "agent_provider": agent_cfg.get("provider", ""),
            "agent_model": agent_cfg.get("model", ""),
        },
        "deployment": {
            "annex_iii_basis": annex_iii_basis,
            "intended_use": intended_use,
            "prerequisite_waived": prerequisite_waived,
        },
        "conformance": {
            "score": summary["score"],
            "pass": summary["pass"],
            "warn": summary["warn"],
            "fail": summary["fail"],
            "checks": [
                {
                    "check_id": r.check_id,
                    "category": r.category,
                    "status": r.status,
                    "detail": r.detail,
                    **({"fix": r.fix} if r.fix else {}),
                }
                for r in results
            ],
        },
        "human_oversight": human_oversight,
        "hardware_observations": hardware_observations,
        **_load_benchmark_block(benchmark_path),
    }


def sign_fria(document: dict, config: dict) -> dict:
    """Add ML-DSA-65 signature to the FRIA document.

    The signature covers the canonical JSON of the document with the 'sig'
    field absent — same algorithm as RCAN message signing (§16.5).
    Returns a new dict with 'signing_key' and 'sig' fields added.
    """
    signer = get_message_signer(config)
    if signer is None:
        raise RuntimeError("No message signer available — check robot key configuration")

    pq_pair = getattr(signer, "_pq_key_pair", None)
    if pq_pair is None:
        raise RuntimeError("ML-DSA-65 keypair not available — cannot sign FRIA")

    pub_bytes = signer.public_key_bytes()
    key_id = getattr(signer, "_pq_key_id", "")

    # Build document with signing_key but without sig
    doc = copy.deepcopy(document)
    doc["signing_key"] = {
        "alg": "ml-dsa-65",
        "kid": key_id,
        "public_key": base64.urlsafe_b64encode(pub_bytes).decode() if pub_bytes else "",
    }

    # Canonical JSON: sort keys, no whitespace, no 'sig' field
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode()

    raw_sig = pq_pair.sign_bytes(canonical)

    doc["sig"] = {
        "alg": "ml-dsa-65",
        "kid": key_id,
        "value": base64.urlsafe_b64encode(raw_sig).decode(),
    }
    return doc


def render_fria_html(document: dict, template_path: str | None = None) -> str:
    """Render the FRIA document to an HTML string using the Jinja2 template.

    Args:
        document:      The FRIA document dict (signed or unsigned).
        template_path: Override path to the Jinja2 template file. Defaults to
                       castor/templates/fria.html.j2 next to this module.

    Returns:
        Rendered HTML string.
    """
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:
        raise ImportError(
            "Jinja2 is required for HTML rendering. Install it with: pip install jinja2"
        ) from exc

    if template_path is None:
        template_path = os.path.join(os.path.dirname(__file__), "templates", "fria.html.j2")

    template_dir = os.path.dirname(os.path.abspath(template_path))
    template_name = os.path.basename(template_path)

    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template(template_name)
    return template.render(doc=document)
