"""castor.rcan3.compliance — build + sign + submit §22-26 compliance artifacts.

Each helper wraps the corresponding rcan-py builder, signs the body with
the provided :class:`CastorSigner`, and POSTs via
:meth:`RrfClient.submit_compliance`. Callers stay async and dependency-injected.

Builder signatures from rcan-py 3.3.0 are richer than the spec surface;
this module provides convenient defaults where fields are mandatory but
context-derivable (e.g. ``generated_at`` defaults to UTC now).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any

from rcan import (
    FriaConformance,
    FriaDocument,
    FriaSigningKey,
    build_eu_register_entry,
    build_ifu,
    build_incident_report,
    build_safety_benchmark,
)

from castor.rcan3.rrf_client import RrfClient
from castor.rcan3.signer import CastorSigner

_FRIA_FIELDS = {f.name for f in dataclasses.fields(FriaDocument)}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def submit_fria(
    *,
    rrf: RrfClient,
    signer: CastorSigner,
    rrn: str,
    deployment: dict[str, Any] | None = None,
    conformance: FriaConformance | None = None,
) -> dict[str, Any]:
    """Submit a §22 FRIA (Fundamental Rights Impact Assessment) to RRF intake.

    Builds a fully-compliant :class:`rcan.FriaDocument` using the real
    dataclass constructor so the emitted body satisfies RRF ingress validation.

    Parameters
    ----------
    deployment:
        Deployment block for the FRIA.  Defaults to an empty dict when not
        provided; callers should pass real deployment metadata for production
        submissions.
    conformance:
        Optional :class:`rcan.FriaConformance` dataclass.  Omit to leave the
        conformance section null.
    """
    pub = signer.public_key_jwk
    signing_key = FriaSigningKey(
        alg=pub["alg"],
        kid=pub["kid"],
        public_key=pub["x"],
    )

    pre_sig_body: dict[str, Any] = {
        "schema": "rcan-fria-v1",
        "generated_at": _now_iso(),
        "system": {"rrn": rrn},
        "deployment": deployment or {},
        "signing_key": dataclasses.asdict(signing_key),
    }
    signed = signer.sign(pre_sig_body)

    # Validate round-trip: construct FriaDocument from the signed fields to
    # confirm structural compliance before sending (pq_signing_pub / pq_kid
    # are rcan-py transport fields — filter to FriaDocument's own fields).
    # signing_key is stored as a plain dict in the signed body; pass the
    # FriaSigningKey object directly to avoid duplicate-keyword errors.
    _round_trip_fields = {
        k: v for k, v in signed.items() if k in _FRIA_FIELDS and k != "signing_key"
    }
    FriaDocument(
        **_round_trip_fields,
        signing_key=signing_key,
        conformance=conformance,
    )

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
    """Submit a §26 EU Register entry artifact.

    Every legally loaded field must be stated explicitly. There is no default
    Annex III basis and no default conformity status: a register entry that
    nobody chose is a claim nobody made. Refuses a submission whose provider
    name or contact is empty (OC-13).
    """
    _extra = extra or {}

    provider = _extra.get("provider") or {}
    missing = [k for k in ("name", "contact") if not str(provider.get(k, "")).strip()]
    if missing:
        raise ValueError(
            "eu-register refused: provider."
            + " and provider.".join(missing)
            + " is empty. An EU register entry names a real provider; supply "
            "provider.name and provider.contact in --data."
        )

    annex_iii_basis = _extra.get("annex_iii_basis")
    if not annex_iii_basis:
        raise ValueError(
            "eu-register refused: annex_iii_basis is required and has no default. "
            "Pass --annex-iii-basis, or set annex_iii_basis in --data."
        )
    conformity_status = _extra.get("conformity_status")
    if not conformity_status:
        raise ValueError(
            "eu-register refused: conformity_status is required and has no default. "
            "Pass --conformity-status, or set conformity_status in --data."
        )

    body = build_eu_register_entry(
        rmn=rmn,
        fria_ref=_extra.get("fria_ref", f"fria:{rrn}"),
        provider=provider,
        system=_extra.get("system", {"rrn": rrn}),
        annex_iii_basis=annex_iii_basis,
        generated_at=_now_iso(),
        conformity_status=conformity_status,
    )
    signed = signer.sign(body)
    return await rrf.submit_compliance("eu-register", signed)
