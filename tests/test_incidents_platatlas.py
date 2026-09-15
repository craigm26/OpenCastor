"""PA-18 - `castor incidents report --platatlas`: the incident-report/1 filing.

WHAT THIS FILE IS FOR. One record class crosses two repositories, and the only thing
holding the two implementations together is that they canonicalise and sign the same
bytes. So the load-bearing test here is not "the function returns a dict": it is the
CROSS-REPO FIXTURE at the bottom, a signed object written to ``tests/fixtures/`` by this
side and ingested, verified and rendered by a test on the rail side
(``apps/platatlas-worker/test/incident-report-pack.test.ts``). If either side drifts, one
of the two tests goes red, and neither can drift quietly.

The fixture is generated from a FIXED key seed and FIXED timestamps so it is stable byte
for byte across runs and machines. The seed is a test seed and nothing else: it is in the
file, it signs one fixture, and it is not any robot's identity.

Nothing here files anything anywhere. Every network path is exercised through an injected
opener.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from castor.platatlas_incident import (
    ID_MAX,
    INCIDENT_ACTION_CLASS,
    INCIDENT_REPORT_SCHEMA,
    SUMMARY_MAX,
    PlatAtlasIncidentError,
    build_incident_report,
    build_signed_lines,
    canonical_json_string,
    ingest_url,
    ndjson_line,
    reporting_window_days,
    sign_incident_report,
    submit_incident_ndjson,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_NDJSON = FIXTURE_DIR / "platatlas-incident-report-1.ndjson"
FIXTURE_PUBKEY = FIXTURE_DIR / "platatlas-incident-report-1.pub.pem"

#: A TEST key seed. 32 bytes, fixed, committed on purpose so the fixture regenerates
#: identically anywhere. It is not a robot identity and never was.
FIXTURE_SEED = bytes(range(32))
FIXTURE_KID = "fixture-robot-gw-attest"
FIXTURE_TS = "2026-09-15T12:00:00+00:00"
FIXTURE_RRN = "rrn://craigm26/robot/opencastor-rpi5/bob-001"

#: The incident-log record the fixture is built from. Shaped exactly as
#: ``IncidentLog.record`` writes one, so the builder is exercised against a real row
#: rather than against a dict written to suit it.
FIXTURE_INCIDENT = {
    "id": "d0a1f2b3-4c5d-6e7f-8091-a2b3c4d5e6f7",
    "record_type": "incident",
    "timestamp": "2026-09-14T09:15:00+00:00",
    "discovered_at": "2026-09-14T11:30:00+00:00",
    "unknown_discovery": False,
    "severity": "serious_harm",
    "category": "estop",
    "description": "Arm contacted the bench frame during a paint run; the run was stopped and the operator was not in the cell.",
    "system_state": {"mode": "bench", "joint_6_deg": 12.5},
    "source": "estop",
    "reported": False,
}


def _fixture_key_file(tmp_path: Path) -> Path:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.from_private_bytes(FIXTURE_SEED)
    key_file = tmp_path / "attestation-ed25519-private.pem"
    key_file.write_bytes(
        priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return key_file


def _fixture_public_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.from_private_bytes(FIXTURE_SEED)
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


# ── 1. the shared canonicalisation ──────────────────────────────────────────


def test_canonical_json_matches_the_js_rules():
    """Sorted keys, recursively, no whitespace, and `exclude` drops one TOP-LEVEL key.

    These literals are what @rail/rcan's canonicalJsonString produces for the same
    inputs. They are written out rather than computed so a change to this
    implementation cannot silently redefine what "canonical" means on this side.
    """
    assert canonical_json_string({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert canonical_json_string({"z": {"y": True, "x": None}}) == '{"z":{"x":null,"y":true}}'
    assert canonical_json_string({"a": ["c", "b"]}) == '{"a":["c","b"]}'
    assert canonical_json_string({"a": 1, "sig": 2}, exclude="sig") == '{"a":1}'
    # exclude is TOP LEVEL only: a nested key of the same name survives, exactly as the
    # JS implementation leaves it, because the signature covers the nested one.
    assert canonical_json_string({"a": {"sig": 3}, "sig": 2}, exclude="sig") == '{"a":{"sig":3}}'
    # String escaping: the short escapes, and \\u00XX for other control characters.
    assert canonical_json_string({"s": 'a"b\\\\c\nd\te'}) == '{"s":"a\\"b\\\\\\\\c\\nd\\te"}'
    assert canonical_json_string({"s": "\x01"}) == '{"s":"\\u0001"}'
    # Non-ASCII VALUES are left literal on both sides (ensure_ascii=False mirrors
    # JSON.stringify), so a summary in any language signs and verifies.
    assert canonical_json_string({"s": "café"}) == '{"s":"café"}'


def test_canonical_json_refuses_what_the_two_sides_would_disagree_about():
    """The three divergence cases are REFUSED, not approximated.

    A signature that verifies on one side and fails on the other is worse than a filing
    that did not go out, so each of these raises with the reason named.
    """
    with pytest.raises(PlatAtlasIncidentError, match="float"):
        canonical_json_string({"n": 1.5})
    with pytest.raises(PlatAtlasIncidentError, match="non-ASCII object key"):
        canonical_json_string({"clé": "x"})
    with pytest.raises(PlatAtlasIncidentError, match="surrogate"):
        canonical_json_string({"s": "\ud800"})
    # A bool is NOT an int here, even though Python says isinstance(True, int).
    assert canonical_json_string({"b": True}) == '{"b":true}'


# ── 2. the record ───────────────────────────────────────────────────────────


def test_build_incident_report_shape_and_taxonomy():
    body = build_incident_report(
        FIXTURE_INCIDENT, rrn=FIXTURE_RRN, kid=FIXTURE_KID, ts=FIXTURE_TS, rmn="rmn://x/y"
    )
    assert body["type"] == "agent_action_attestation"
    assert body["schema"] == INCIDENT_REPORT_SCHEMA
    assert body["action_class"] == INCIDENT_ACTION_CLASS
    # 'service', never 'human'. Claiming 'human' would put this record in the human
    # column of the receiving side's oversight counts.
    assert body["actor_class"] == "service"
    assert body["attestation_id"] == FIXTURE_INCIDENT["id"]
    inc = body["incident"]
    assert inc["incident_id"] == FIXTURE_INCIDENT["id"]
    # The two clocks stay apart: the reporting window runs from discovery.
    assert inc["discovered_at"] == FIXTURE_INCIDENT["discovered_at"]
    assert inc["occurred_at"] == FIXTURE_INCIDENT["timestamp"]
    assert inc["discovered_at"] != inc["occurred_at"]
    assert inc["severity"] == "serious_harm"
    assert reporting_window_days("serious_harm") == 15
    assert reporting_window_days("death") == 10
    assert reporting_window_days("critical_infrastructure") == 2
    # Every key the rail parser reads is present, so a field cannot go missing silently.
    for k in (
        "incident_id", "discovered_at", "occurred_at", "severity", "affected_rrn",
        "affected_rmn", "affected_actor_ids", "summary", "evidence_refs", "reporter",
        "disposition", "notified",
    ):
        assert k in inc, k
    # No float reaches the preimage: system_state (which holds one) is deliberately not
    # carried, and the whole object canonicalises.
    canonical_json_string(body)


def test_build_incident_report_normalises_a_legacy_severity_and_defaults_the_unknown():
    legacy = dict(FIXTURE_INCIDENT, severity="life_health")
    assert build_incident_report(legacy, rrn="r", kid="k", ts=FIXTURE_TS)["incident"]["severity"] == "serious_harm"
    # An unrecognised category falls to the LONGEST window, never a shorter one: this
    # side must not shorten somebody's deadline by failing to recognise their string.
    weird = dict(FIXTURE_INCIDENT, severity="whatever")
    assert build_incident_report(weird, rrn="r", kid="k", ts=FIXTURE_TS)["incident"]["severity"] == "serious_harm"


def test_caps_are_applied_before_signing_not_after():
    """The bytes that are signed are the bytes the far side stores.

    A cap applied on the receiving side instead would leave a stored summary that nobody
    signed, which is the one thing a signed record must never allow.
    """
    long_desc = "x" * (SUMMARY_MAX + 500)
    body = build_incident_report(
        dict(FIXTURE_INCIDENT, description=long_desc),
        rrn="r" * (ID_MAX + 100), kid="k", ts=FIXTURE_TS,
        notified=["n" * (ID_MAX + 10)] + [f"p{i}" for i in range(80)],
    )
    assert len(body["incident"]["summary"]) == SUMMARY_MAX
    assert len(body["incident"]["affected_rrn"]) == ID_MAX
    assert len(body["incident"]["notified"]) == 50
    assert len(body["incident"]["notified"][0]) == ID_MAX


# ── 3. signing ──────────────────────────────────────────────────────────────


def test_signature_covers_the_body_without_its_own_block(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key_file = _fixture_key_file(tmp_path)
    body = build_incident_report(FIXTURE_INCIDENT, rrn=FIXTURE_RRN, kid=FIXTURE_KID, ts=FIXTURE_TS)
    signed = sign_incident_report(body, key_file, FIXTURE_KID)
    assert signed["envelope_signature"]["alg"] == "Ed25519"
    assert signed["envelope_signature"]["kid"] == FIXTURE_KID

    from cryptography.hazmat.primitives import serialization

    pub = serialization.load_pem_public_key(_fixture_public_pem())
    assert isinstance(pub, Ed25519PublicKey)
    preimage = canonical_json_string(signed, exclude="envelope_signature").encode()
    pub.verify(base64.b64decode(signed["envelope_signature"]["sig"]), preimage)

    # And the signature does NOT hold over an edited body: the whole point.
    tampered = json.loads(json.dumps(signed))
    tampered["incident"]["severity"] = "death"
    with pytest.raises(Exception):
        pub.verify(
            base64.b64decode(tampered["envelope_signature"]["sig"]),
            canonical_json_string(tampered, exclude="envelope_signature").encode(),
        )


def test_ndjson_line_is_the_event_wrapper():
    signed = dict(build_incident_report(FIXTURE_INCIDENT, rrn="r", kid="k", ts=FIXTURE_TS))
    signed["envelope_signature"] = {"alg": "Ed25519", "kid": "k", "sig": "AAAA"}
    line = ndjson_line(signed)
    assert "\n" not in line
    assert json.loads(line)["event"]["action_class"] == INCIDENT_ACTION_CLASS


# ── 4. posting ──────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status=200, body=b'{"ok":true}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    def __init__(self, response=None):
        self.response = response or _FakeResponse()
        self.request = None

    def urlopen(self, request, timeout=None):
        self.request = request
        return self.response


def test_submit_posts_ndjson_with_a_bearer_key_to_the_org_ingest():
    opener = _FakeOpener()
    out = submit_incident_ndjson('{"event":{}}\n', "acme", "sk_live_x", opener=opener)
    assert out == {"ok": True}
    req = opener.request
    # source=rcan is REQUIRED: the record-class parse on the far side runs only for that
    # source, so a body posted without it is stored as a trace and never becomes a record.
    assert req.full_url == "https://acme.platatlas.com/api/traces?source=rcan"
    assert ingest_url("acme") == req.full_url
    assert req.get_header("Authorization") == "Bearer sk_live_x"
    assert req.get_header("Content-type") == "application/x-ndjson"
    assert req.data == b'{"event":{}}\n'


def test_submit_turns_a_non_2xx_into_an_operator_line_not_a_traceback():
    opener = _FakeOpener(_FakeResponse(status=403, body=b"forbidden"))
    with pytest.raises(PlatAtlasIncidentError, match="403"):
        submit_incident_ndjson("x\n", "acme", "sk_live_x", opener=opener)


# ── 5. THE CROSS-REPO FIXTURE ───────────────────────────────────────────────
# This is the test that matters. The file it writes is copied, verbatim, into the rail
# repo at apps/platatlas-worker/test/fixtures/, where a test ingests it through the REAL
# POST /api/traces handler, verifies the signature against this public key, and renders
# the pack section. Two repos, one set of bytes.


def test_the_cross_repo_fixture_regenerates_byte_for_byte(tmp_path):
    key_file = _fixture_key_file(tmp_path)
    _objects, body = build_signed_lines(
        [FIXTURE_INCIDENT],
        rrn=FIXTURE_RRN,
        ts=FIXTURE_TS,
        key_file=key_file,
        kid=FIXTURE_KID,
        rmn="rmn://craigm26/module/arm/soarm101-001",
        reporter="Operator on shift (declared, verified by nobody)",
        disposition="bench halted; arm parked; cause under review",
        notified=["site safety lead", "platform operator"],
    )
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    if not FIXTURE_NDJSON.exists():
        FIXTURE_NDJSON.write_text(body)
        FIXTURE_PUBKEY.write_bytes(_fixture_public_pem())

    # Ed25519 is deterministic, and every input above is fixed, so this is an equality
    # and not an approximation. A change to the builder, the caps, the field names, the
    # canonicalisation or the signing preimage moves these bytes and fails here, which is
    # exactly when the rail-side test would start failing too.
    assert body == FIXTURE_NDJSON.read_text(), (
        "the signed incident-report/1 fixture changed. If that is intended, update BOTH "
        "copies in the same change: this file and "
        "apps/platatlas-worker/test/fixtures/platatlas-incident-report-1.ndjson in rail"
    )
    assert FIXTURE_PUBKEY.read_bytes() == _fixture_public_pem()

    # And the fixture is a real, well-formed filing rather than a blob that happens to
    # match: one line, one event, the class the far side recognises, and a signature over
    # exactly the body beside it.
    lines = [ln for ln in body.split("\n") if ln.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])["event"]
    assert event["action_class"] == INCIDENT_ACTION_CLASS
    assert event["schema"] == INCIDENT_REPORT_SCHEMA
    assert event["incident"]["severity"] == "serious_harm"
    assert event["incident"]["reporter"].startswith("Operator on shift")
    assert event["envelope_signature"]["kid"] == FIXTURE_KID

    from cryptography.hazmat.primitives import serialization

    pub = serialization.load_pem_public_key(FIXTURE_PUBKEY.read_bytes())
    pub.verify(
        base64.b64decode(event["envelope_signature"]["sig"]),
        canonical_json_string(event, exclude="envelope_signature").encode(),
    )
