"""Track 2 parity — gateway smoke (Plan 7 Phase 1 sub-task 1).

Exercises GW-002 (tool allowlist) and GW-003 (tier policy) via
`robot_md_gateway.receiver.make_app(...)` running through
`fastapi.testclient.TestClient`. This is the first slice of the
runtime-level integration described in `docs/open-core-extraction-plan.md`
"Revised Phase 1 schedule".

The fixture pattern mirrors the gateway's own
`tests/cert/test_gw_002_tool_allowlist.py` so that any drift in
`make_app(...)`'s constructor is caught by both suites simultaneously.

Gate ordering (issue #942, gateway >= 0.5.0a6)
----------------------------------------------
The gateway's T-003 anon-fail-open fix moved the **tier** gate ahead of the
**tool-allowlist** gate in `receiver.invoke()`, and made `anon` (no bearer,
or an unknown one) a non-actuating tier. An unauthenticated MANIPULATE
envelope therefore now stops at `deny: tier_policy` and never reaches the
allowlist — it records GW-003, not GW-002.

Nothing in the Track 2 contract pins the relative order of those two gates:
`docs/open-core-extraction-plan.md` and `docs/cert-enforcement-architecture.md`
require both properties to be *exercised* at envelope receive, not to fire in
a particular sequence. So this file asserts the new behaviour as correct and
keeps both properties covered, rather than pinning the old order:

* `test_gateway_smoke_anon_tier_denied_before_allowlist` asserts tier-first
  for an under-tiered principal (GW-003), including the negative half — the
  allowlist gate genuinely did not run.
* the remaining tests present an actuate-tier bearer so the envelope clears
  the tier gate and the allowlist gate is still reached and proven (GW-002),
  exactly as the gateway's own GW-002 suite does.

No source code in `castor/` is touched by this test — Phase 1 is a
test-only integration. Production wiring of `make_app(...)` into
`castor/api.py` is Phase 2.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from robot_md_gateway.cert import report as cert_report
from robot_md_gateway.cert.policy import ToolAllowlist
from robot_md_gateway.receiver import make_app

FIXTURES = Path(__file__).parent.parent / "fixtures" / "manifests"

# Same bearer/tier convention as the sibling Track 2 parity files
# (test_track_2_parity.py, ..._envelope_verification.py, ..._gates_envelope.py,
# ..._safety_audit.py). Incidental to what the allowlist tests assert — it only
# buys passage through the tier gate that now runs first.
ACTUATE_TOKEN = "actuate-token"
ACTUATE_HEADERS = {"Authorization": f"Bearer {ACTUATE_TOKEN}"}


class _FakeResolver:
    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._mapping = mapping

    def resolve_public_key_pem(self, kid: str) -> bytes | None:
        return self._mapping.get(kid)


@pytest.fixture(autouse=True)
def _reset_cert_report():
    cert_report.reset()
    yield


def _client(allowed: tuple[str, ...]) -> TestClient:
    kid = (FIXTURES / "signing-key.kid").read_text().strip()
    pub = (FIXTURES / "signing-key.pub").read_bytes()
    app = make_app(
        resolver=_FakeResolver({kid: pub}),
        tool_allowlist=ToolAllowlist(allowed_tools=allowed),
        bearer_tiers={ACTUATE_TOKEN: "actuate"},
    )
    return TestClient(app)


def _envelope(*, msg_id: str, tool_name: str, scope: str = "MANIPULATE") -> dict:
    return {
        "msg_id": msg_id,
        "type": "INVOKE",
        "ruri": "rcan://opencastor.local/robot/bob/00000003",
        "scope": scope,
        "tool_name": tool_name,
        "tool_args": {},
        "manifest_path": str(FIXTURES / "signed-good.md"),
    }


def test_gateway_smoke_unallowlisted_tool_denied():
    client = _client(allowed=("mcp__robot__render", "mcp__robot__validate"))
    response = client.post("/v1/invoke", headers=ACTUATE_HEADERS, json=_envelope(
        msg_id="msg-smoke-1", tool_name="mcp__robot__execute_capability",
    ))
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["deny"] == "tool_allowlist"


def test_gateway_smoke_allowlisted_tool_accepted():
    client = _client(allowed=("mcp__robot__render", "mcp__robot__execute_capability"))
    response = client.post("/v1/invoke", headers=ACTUATE_HEADERS, json=_envelope(
        msg_id="msg-smoke-2", tool_name="mcp__robot__execute_capability",
    ))
    assert response.status_code == 200, response.text


def test_gateway_smoke_cert_report_records_property():
    client = _client(allowed=("mcp__robot__render",))
    client.post("/v1/invoke", headers=ACTUATE_HEADERS, json=_envelope(
        msg_id="msg-smoke-3", tool_name="mcp__robot__execute_capability",
    ))
    serialized = cert_report.serialize(repo="opencastor-track-2", sha="HEAD")
    property_ids = {p["property_id"] for p in serialized["properties"]}
    assert "GW-002" in property_ids, (
        f"expected GW-002 in cert_report after deny; got {property_ids}"
    )


def test_gateway_smoke_anon_tier_denied_before_allowlist():
    """An anon principal is denied MANIPULATE by tier, ahead of the allowlist.

    The tool is deliberately ON the allowlist, so the only gate that can refuse
    this envelope is the tier gate — and GW-002's absence from the cert report
    proves the allowlist gate was never reached. Issue #942.
    """
    client = _client(allowed=("mcp__robot__render", "mcp__robot__execute_capability"))
    response = client.post("/v1/invoke", json=_envelope(
        msg_id="msg-smoke-4", tool_name="mcp__robot__execute_capability",
    ))
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["deny"] == "tier_policy", detail
    assert detail["reason"] == "anon-tier principal cannot invoke MANIPULATE", detail

    serialized = cert_report.serialize(repo="opencastor-track-2", sha="HEAD")
    property_ids = {p["property_id"] for p in serialized["properties"]}
    assert "GW-003" in property_ids, (
        f"expected GW-003 in cert_report after tier deny; got {property_ids}"
    )
    assert "GW-002" not in property_ids, (
        "tier gate must short-circuit before the tool allowlist for an "
        f"under-tiered principal; got {property_ids}"
    )
