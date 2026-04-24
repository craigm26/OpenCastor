"""castor.rcan3.compliance — build + sign + submit §22-26 compliance artifacts.

Each helper wraps the corresponding rcan-py builder, signs the body with
the provided :class:`CastorSigner`, and POSTs via
:meth:`RrfClient.submit_compliance`. Callers stay async and dependency-injected.

Builder signatures from rcan-py 3.3.0 are richer than the spec surface;
this module provides convenient defaults where fields are mandatory but
context-derivable (e.g. ``generated_at`` defaults to UTC now).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from rcan import (
    build_eu_register_entry,
    build_ifu,
    build_incident_report,
    build_safety_benchmark,
)

from castor.rcan3.rrf_client import RrfClient
from castor.rcan3.signer import CastorSigner


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def submit_fria(
    *,
    rrf: RrfClient,
    signer: CastorSigner,
    rrn: str,
    conformance: dict[str, Any],
) -> dict[str, Any]:
    """Submit a §22 FRIA (Fundamental Rights Impact Assessment) to RRF intake.

    The FRIA envelope is opaque to this module — callers supply the
    conformance block as a dict. Build the body inline here rather than
    routing through a rcan-py builder (there is no ``build_fria`` helper in
    rcan-py 3.3; the raw dict is the spec surface).
    """
    body = {
        "schema": "rcan-fria-v1",
        "system": {"rrn": rrn},
        "conformance": conformance,
    }
    signed = signer.sign(body)
    return await rrf.submit_compliance("fria", signed)


async def submit_safety_benchmark(
    *,
    rrf: RrfClient,
    signer: CastorSigner,
    rrn: str,
    benchmark_id: str,
    passed: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit a §23 safety-benchmark artifact.

    Wraps ``rcan.build_safety_benchmark`` with sensible defaults.
    The ``benchmark_id`` is placed in ``results`` so it's round-trippable.
    """
    _details = details or {}
    body = build_safety_benchmark(
        iterations=_details.get("iterations", 1),
        thresholds=_details.get("thresholds", {}),
        results={**_details, "benchmark_id": benchmark_id, "rrn": rrn},
        mode=_details.get("mode", "offline"),
        generated_at=_now_iso(),
        overall_pass=passed,
    )
    signed = signer.sign(body)
    return await rrf.submit_compliance("safety-benchmark", signed)


async def submit_ifu(
    *,
    rrf: RrfClient,
    signer: CastorSigner,
    rrn: str,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Submit a §24 Instructions For Use (IFU) artifact."""
    body = build_ifu(
        provider_identity=coverage.get("provider_identity", {"rrn": rrn}),
        intended_purpose=coverage.get("intended_purpose", {}),
        capabilities_and_limitations=coverage.get("capabilities_and_limitations", {}),
        accuracy_and_performance=coverage.get("accuracy_and_performance", {}),
        human_oversight_measures=coverage.get("human_oversight_measures", {}),
        known_risks_and_misuse=coverage.get("known_risks_and_misuse", {}),
        expected_lifetime=coverage.get("expected_lifetime", {}),
        maintenance_requirements=coverage.get("maintenance_requirements", {}),
        generated_at=_now_iso(),
    )
    signed = signer.sign(body)
    return await rrf.submit_compliance("ifu", signed)


async def submit_incident_report(
    *,
    rrf: RrfClient,
    signer: CastorSigner,
    rrn: str,
    incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Submit a §25 incident report artifact."""
    body = build_incident_report(rrn=rrn, incidents=incidents, generated_at=_now_iso())
    signed = signer.sign(body)
    return await rrf.submit_compliance("incident-report", signed)


async def submit_eu_register(
    *,
    rrf: RrfClient,
    signer: CastorSigner,
    rrn: str,
    rmn: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit a §26 EU Register entry artifact."""
    _extra = extra or {}
    body = build_eu_register_entry(
        rmn=rmn,
        fria_ref=_extra.get("fria_ref", f"fria:{rrn}"),
        provider=_extra.get("provider", {"rrn": rrn}),
        system=_extra.get("system", {"rrn": rrn}),
        annex_iii_basis=_extra.get("annex_iii_basis", "article-6"),
        generated_at=_now_iso(),
        conformity_status=_extra.get("conformity_status", "declared"),
    )
    signed = signer.sign(body)
    return await rrf.submit_compliance("eu-register", signed)
