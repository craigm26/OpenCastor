"""Tests for castor.rcan3.compliance — build + sign + submit §22-26 artifacts."""

from __future__ import annotations

import dataclasses

import pytest
import respx
from httpx import Response


@pytest.mark.asyncio
@respx.mock
async def test_submit_fria_end_to_end(tmp_path):
    from castor.rcan3.compliance import submit_fria
    from castor.rcan3.identity import load_or_generate_identity
    from castor.rcan3.rrf_client import RrfClient
    from castor.rcan3.signer import CastorSigner

    respx.post("https://rcan.dev/v2/compliance/fria").mock(
        return_value=Response(202, json={"accepted": True, "artifact_id": "fria-001"})
    )

    ident = load_or_generate_identity(keydir=tmp_path)
    signer = CastorSigner(ident)
    async with RrfClient(base_url="https://rcan.dev") as rrf:
        out = await submit_fria(
            rrf=rrf,
            signer=signer,
            rrn="RRN-000000000001",
        )
    assert out["accepted"] is True


@pytest.mark.asyncio
@respx.mock
async def test_submit_fria_body_is_fria_document_compliant(tmp_path):
    """Emitted body must round-trip through FriaDocument (structural compliance)."""
    import json

    from rcan import FriaConformance, FriaDocument, FriaSigningKey

    from castor.rcan3.compliance import submit_fria
    from castor.rcan3.identity import load_or_generate_identity
    from castor.rcan3.rrf_client import RrfClient
    from castor.rcan3.signer import CastorSigner

    captured: list[bytes] = []

    def _capture(request):
        captured.append(request.read())
        return Response(202, json={"accepted": True})

    respx.post("https://rcan.dev/v2/compliance/fria").mock(side_effect=_capture)

    ident = load_or_generate_identity(keydir=tmp_path)
    signer = CastorSigner(ident)
    fria_conformance = FriaConformance(score=1.0, pass_count=5, warn_count=0, fail_count=0)
    async with RrfClient(base_url="https://rcan.dev") as rrf:
        await submit_fria(
            rrf=rrf,
            signer=signer,
            rrn="RRN-000000000001",
            deployment={"env": "test"},
            conformance=fria_conformance,
        )

    assert len(captured) == 1
    body = json.loads(captured[0])

    # Verify required fields are present
    assert body.get("schema") == "rcan-fria-v1"
    assert "generated_at" in body
    assert "system" in body
    assert "deployment" in body
    assert "sig" in body

    # Round-trip through FriaDocument must succeed.
    # signing_key is serialised as a plain dict on the wire; pass the
    # FriaSigningKey object directly so there's no duplicate-kwarg collision.
    fria_fields = {f.name for f in dataclasses.fields(FriaDocument)}
    pub = ident.public_key_jwk
    FriaDocument(
        **{k: v for k, v in body.items() if k in fria_fields and k != "signing_key"},
        signing_key=FriaSigningKey(alg=pub["alg"], kid=pub["kid"], public_key=pub["x"]),
        conformance=fria_conformance,
    )


@pytest.mark.asyncio
@respx.mock
async def test_submit_safety_benchmark_signs_and_posts(tmp_path):
    from castor.rcan3.compliance import submit_safety_benchmark
    from castor.rcan3.identity import load_or_generate_identity
    from castor.rcan3.rrf_client import RrfClient
    from castor.rcan3.signer import CastorSigner

    captured: list[bytes] = []

    def _capture(request):
        captured.append(request.read())
        return Response(202, json={"accepted": True})

    respx.post("https://rcan.dev/v2/compliance/safety-benchmark").mock(side_effect=_capture)

    ident = load_or_generate_identity(keydir=tmp_path)
    signer = CastorSigner(ident)
    async with RrfClient(base_url="https://rcan.dev") as rrf:
        await submit_safety_benchmark(
            rrf=rrf,
            signer=signer,
            rrn="RRN-000000000001",
            benchmark_id="iso-10218-1",
            passed=True,
        )
    assert len(captured) == 1


@pytest.mark.asyncio
@respx.mock
async def test_submit_eu_register_requires_rmn(tmp_path):
    """§26 EU Register requires rmn per rcan-spec 3.1."""
    from castor.rcan3.compliance import submit_eu_register
    from castor.rcan3.identity import load_or_generate_identity
    from castor.rcan3.rrf_client import RrfClient
    from castor.rcan3.signer import CastorSigner

    respx.post("https://rcan.dev/v2/compliance/eu-register").mock(
        return_value=Response(202, json={"accepted": True})
    )

    ident = load_or_generate_identity(keydir=tmp_path)
    signer = CastorSigner(ident)
    async with RrfClient(base_url="https://rcan.dev") as rrf:
        out = await submit_eu_register(
            rrf=rrf,
            signer=signer,
            rrn="RRN-000000000001",
            rmn="craigm26/so-arm101/1-0-0",
            # Since OC-13 there are no silent defaults: provider identity,
            # Annex III basis and conformity status must all be stated.
            extra={
                "provider": {"name": "Test Provider", "contact": "ops@example.invalid"},
                "annex_iii_basis": "article-6",
                "conformity_status": "declared",
            },
        )
    assert out["accepted"] is True


@pytest.mark.asyncio
async def test_submit_eu_register_refuses_empty_provider(tmp_path):
    """OC-13: an eu-register submit with no provider identity is refused."""
    from castor.rcan3.compliance import submit_eu_register

    with pytest.raises(ValueError, match="provider"):
        await submit_eu_register(
            rrf=None,
            signer=None,
            rrn="RRN-000000000001",
            rmn="craigm26/so-arm101/1-0-0",
            extra={"annex_iii_basis": "article-6", "conformity_status": "declared"},
        )


@pytest.mark.asyncio
async def test_submit_eu_register_requires_explicit_annex_iii_basis(tmp_path):
    """OC-13: annex_iii_basis and conformity_status no longer have defaults."""
    from castor.rcan3.compliance import submit_eu_register

    provider = {"name": "Test Provider", "contact": "ops@example.invalid"}
    with pytest.raises(ValueError, match="annex_iii_basis"):
        await submit_eu_register(
            rrf=None,
            signer=None,
            rrn="RRN-000000000001",
            rmn="craigm26/so-arm101/1-0-0",
            extra={"provider": provider, "conformity_status": "declared"},
        )
    with pytest.raises(ValueError, match="conformity_status"):
        await submit_eu_register(
            rrf=None,
            signer=None,
            rrn="RRN-000000000001",
            rmn="craigm26/so-arm101/1-0-0",
            extra={"provider": provider, "annex_iii_basis": "article-6"},
        )
