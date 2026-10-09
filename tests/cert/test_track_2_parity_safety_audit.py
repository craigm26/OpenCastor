"""Track 2 parity — safety state + audit chain (Plan 7 Phase 1 sub-task 4).

Final cert-property slice. Exercises SF-001 (ESTOP precedence), SF-002
(safe-stop on heartbeat staleness), and AC-001 (signed audit bundle
round-trip) via `make_app(...)` TestClient.

Important framing per `docs/open-core-extraction-plan.md`: the gateway's
SafetyMonitor is the binary state machine (READY / SAFE_STOP /
ESTOP_ACTIVE) that runs at envelope-receive time. OpenCastor's runtime
SafetyLayer + BoundsChecker (in `castor/safety/{state,bounds,protocol}.py`)
are *not* replaced — they keep doing real-time bounds + driver-routing
work. This test exercises only the cert side.

Fixture pattern mirrors gateway's
`tests/cert/test_phase_4_safety_wiring.py` and
`tests/cert/test_phase_4_audit_wiring.py`.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from robot_md_gateway.cert import report as cert_report
from robot_md_gateway.cert.audit import AuditChain, verify_audit_bundle
from robot_md_gateway.cert.policy import ToolAllowlist
from robot_md_gateway.cert.safety import GatewayState, SafetyMonitor
from robot_md_gateway.receiver import make_app

FIXTURES = Path(__file__).parent.parent / "fixtures" / "manifests"


class _FakeResolver:
    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._mapping = mapping

    def resolve_public_key_pem(self, kid: str) -> bytes | None:
        return self._mapping.get(kid)


@pytest.fixture(autouse=True)
def _reset_cert_report():
    cert_report.reset()
    yield


def _ed25519_pair() -> tuple[bytes, bytes]:
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def _client(
    *,
    safety_monitor: SafetyMonitor | None = None,
    audit_chain: AuditChain | None = None,
    tool_allowlist: ToolAllowlist | None = None,
) -> tuple[TestClient, object]:
    kid = (FIXTURES / "signing-key.kid").read_text().strip()
    pub = (FIXTURES / "signing-key.pub").read_bytes()
    app = make_app(
        resolver=_FakeResolver({kid: pub}),
        tool_allowlist=tool_allowlist or ToolAllowlist(
            allowed_tools=("mcp__robot__execute_capability", "mcp__robot__render"),
        ),
        bearer_tiers={"actuate-token": "actuate"},
        safety_monitor=safety_monitor,
        audit_chain=audit_chain,
    )
    return TestClient(app), app


def _envelope(**overrides) -> dict:
    base = {
        "msg_id": overrides.pop("msg_id", "msg-default"),
        "type": "INVOKE",
        "ruri": "rcan://opencastor.local/robot/bob/00000003",
        "scope": "MANIPULATE",
        "tool_name": "mcp__robot__execute_capability",
        "tool_args": {},
        "manifest_path": str(FIXTURES / "signed-good.md"),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# SF-001 — ESTOP precedence
# ---------------------------------------------------------------------------


def test_sf_001_estop_wire_trip_denies_invoke():
    sm = SafetyMonitor()
    client, app = _client(safety_monitor=sm)
    app.state.safety_monitor.on_estop_wire(tripped=True, msg_id="hw-trip-1")
    response = client.post(
        "/v1/invoke",
        headers={"Authorization": "Bearer actuate-token"},
        json=_envelope(msg_id="msg-sf-001-trip"),
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["deny"] == "safety_state"
    assert "estop_active" in response.json()["detail"]["reason"]


def test_sf_001_estop_preempts_malformed_envelope():
    """SF-001 precedence: ESTOP fires before schema validation (would normally yield 422)."""
    sm = SafetyMonitor()
    client, app = _client(safety_monitor=sm)
    app.state.safety_monitor.on_estop_wire(tripped=True)
    response = client.post(
        "/v1/invoke",
        headers={"Authorization": "Bearer actuate-token"},
        json={"msg_id": "incomplete"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["deny"] == "safety_state"


# ---------------------------------------------------------------------------
# SF-002 — Safe-stop on heartbeat staleness
# ---------------------------------------------------------------------------


def test_sf_002_heartbeat_staleness_transitions_safe_stop():
    sm = SafetyMonitor(heartbeat_staleness_s=0.05)
    sm.last_heartbeat_at = time.monotonic() - 1.0
    client, _ = _client(safety_monitor=sm)
    response = client.post(
        "/v1/invoke",
        headers={"Authorization": "Bearer actuate-token"},
        json=_envelope(msg_id="msg-sf-002-stale"),
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["deny"] == "safety_state"
    assert sm.state == GatewayState.SAFE_STOP


def test_sf_002_fresh_heartbeat_allows_actuation():
    sm = SafetyMonitor(heartbeat_staleness_s=10.0)
    sm.on_heartbeat()
    client, _ = _client(safety_monitor=sm)
    response = client.post(
        "/v1/invoke",
        headers={"Authorization": "Bearer actuate-token"},
        json=_envelope(msg_id="msg-sf-002-fresh"),
    )
    assert response.status_code == 200, response.text
    assert sm.state == GatewayState.READY


# ---------------------------------------------------------------------------
# AC-001 — Tamper-evident audit chain
# ---------------------------------------------------------------------------


def test_ac_001_allow_records_audit_entry():
    chain = AuditChain()
    client, _ = _client(audit_chain=chain)
    response = client.post(
        "/v1/invoke",
        headers={"Authorization": "Bearer actuate-token"},
        json=_envelope(msg_id="msg-ac-001-allow"),
    )
    assert response.status_code == 200
    # robot-md-gateway 0.5.0a8 (OC-M-04) records an "intent" entry once every gate
    # has passed and before the actuator is called, then the "outcome" entry, so an
    # allow is a linked pair.
    assert [e.entry_kind for e in chain.entries] == ["intent", "outcome"]
    intent, entry = chain.entries
    assert entry.decision == "allow"
    assert entry.msg_id == "msg-ac-001-allow"
    assert entry.envelope_kid is not None
    assert intent.msg_id == entry.msg_id
    assert entry.intent_chain_hash == intent.chain_hash


def test_ac_001_deny_records_audit_entry():
    chain = AuditChain()
    client, _ = _client(
        audit_chain=chain,
        tool_allowlist=ToolAllowlist(allowed_tools=("mcp__robot__render",)),
    )
    response = client.post(
        "/v1/invoke",
        headers={"Authorization": "Bearer actuate-token"},
        json=_envelope(msg_id="msg-ac-001-deny"),
    )
    assert response.status_code == 403
    assert len(chain.entries) == 1
    entry = chain.entries[0]
    assert entry.decision == "deny"
    assert "tool_allowlist" in entry.decision_reason


def test_ac_001_schema_422_does_not_record():
    """Parser errors are not policy decisions — audit chain stays empty."""
    chain = AuditChain()
    client, _ = _client(audit_chain=chain)
    response = client.post(
        "/v1/invoke",
        headers={"Authorization": "Bearer actuate-token"},
        json={"msg_id": "incomplete"},
    )
    assert response.status_code == 422
    assert chain.entries == []


def test_ac_001_signed_bundle_verifies_offline():
    """Multi-decision chain → signed bundle → offline-verifies (the AC-001 claim)."""
    chain = AuditChain()
    client, _ = _client(audit_chain=chain)
    client.post(
        "/v1/invoke",
        headers={"Authorization": "Bearer actuate-token"},
        json=_envelope(msg_id="msg-ac-001-bundle-1"),
    )
    client.post(
        "/v1/invoke",
        headers={"Authorization": "Bearer actuate-token"},
        json=_envelope(msg_id="msg-ac-001-bundle-2", tool_name="mcp__robot__not_allowed"),
    )
    # The allow is an intent and an outcome entry; the deny is one entry.
    assert [e.decision for e in chain.entries] == ["allow", "allow", "deny"]

    priv_pem, pub_pem = _ed25519_pair()
    bundle = chain.export_signed(
        signing_key_pem=priv_pem, kid="opencastor-track-2-test",
    )
    assert verify_audit_bundle(bundle, kid_to_pem={"opencastor-track-2-test": pub_pem})
