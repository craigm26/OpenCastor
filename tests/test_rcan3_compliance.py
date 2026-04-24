"""Tests for castor.rcan3.compliance — build + sign + submit §22-26 artifacts."""

from __future__ import annotations

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
            conformance={"status": "declared", "date": "2026-04-23"},
        )
    assert out["accepted"] is True


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
        )
    assert out["accepted"] is True
