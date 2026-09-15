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
import os
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
    """The four divergence cases are REFUSED, not approximated.

    A signature that verifies on one side and fails on the other is worse than a filing
    that did not go out, so each of these raises with the reason named. The matching
    half of each pair is pinned on the rail side, in
    apps/platatlas-worker/test/pa18-canonical-conformance.test.ts, which shows what the
    JS implementation does with the same value; neither file proves the pair alone.
    """
    # 1. floats: String(1.5) and repr(1.5) agree here and diverge on precision, and a
    #    float 1.0 becomes the integer 1 over there.
    with pytest.raises(PlatAtlasIncidentError, match="float"):
        canonical_json_string({"n": 1.5})
    with pytest.raises(PlatAtlasIncidentError, match="float"):
        canonical_json_string({"n": 1.0})
    with pytest.raises(PlatAtlasIncidentError, match="float"):
        canonical_json_string({"n": -0.0})
    # 2. non-ASCII object keys: sorted() is by code point here and by UTF-16 code unit
    #    there, and the two orders differ as soon as a key holds an astral character.
    with pytest.raises(PlatAtlasIncidentError, match="non-ASCII object key"):
        canonical_json_string({"clé": "x"})
    with pytest.raises(PlatAtlasIncidentError, match="non-ASCII object key"):
        canonical_json_string({"\U0001F600": "x"})
    # 3. lone surrogates: escaped by JSON.stringify, not encodable as UTF-8 here.
    with pytest.raises(PlatAtlasIncidentError, match="surrogate"):
        canonical_json_string({"s": "\ud800"})
    # 4. integers outside the JS safe range. JSON.parse over there loses 2**53 + 1
    #    before the canonicaliser is reached, and 10**21 comes back as 1e+21, so the
    #    bytes recomputed there are not the bytes signed here.
    with pytest.raises(PlatAtlasIncidentError, match="safe range"):
        canonical_json_string({"n": 2**53})
    with pytest.raises(PlatAtlasIncidentError, match="safe range"):
        canonical_json_string({"n": -(2**53)})
    with pytest.raises(PlatAtlasIncidentError, match="safe range"):
        canonical_json_string({"n": 10**21})
    # MAX_SAFE_INTEGER itself is exact on both sides and is accepted.
    assert canonical_json_string({"n": 2**53 - 1}) == '{"n":9007199254740991}'
    # A bool is NOT an int here, even though Python says isinstance(True, int).
    assert canonical_json_string({"b": True}) == '{"b":true}'
    # And non-ASCII string VALUES are fine on both sides: raw UTF-8, no escaping. An
    # operator with an accent in their name must be able to file.
    assert canonical_json_string({"reporter": "Opérateur 田 🚜"}) == '{"reporter":"Opérateur 田 🚜"}'


def test_a_cap_counts_in_the_units_the_far_side_counts_in():
    """The caps are applied here so the signed bytes ARE the stored bytes, and that only
    holds if both sides cut at the same place.

    Python slices by code point; JavaScript's String.prototype.slice counts UTF-16 code
    units, and an emoji is one code point and two units. A 2000-emoji summary used to
    pass this cap untouched and be cut in half over there, so the stored summary was not
    the summary anybody signed, and an odd boundary left a lone high surrogate in it.
    """
    from castor.platatlas_incident import _cap

    # An emoji is two units, so five units is one ASCII character and two emoji, and the
    # third emoji is dropped WHOLE rather than split into half a surrogate pair.
    assert _cap("a" + "\U0001F600" * 5, 5) == "a\U0001F600\U0001F600"
    assert _cap("a" + "\U0001F600" * 5, 6) == "a\U0001F600\U0001F600"
    assert _cap("hello", 3) == "hel"
    assert _cap("hello", 99) == "hello"
    # And a filing built from an all-astral description is within the far side's cap.
    body = build_incident_report(
        {"id": "i1", "discovered_at": "2026-09-14T00:00:00+00:00", "severity": "serious_harm",
         "description": "\U0001F600" * (SUMMARY_MAX)},
        rrn=FIXTURE_RRN, kid=FIXTURE_KID, ts=FIXTURE_TS,
    )
    summary = body["incident"]["summary"]
    utf16_units = sum(2 if ord(c) > 0xFFFF else 1 for c in summary)
    assert utf16_units <= SUMMARY_MAX


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
    # THE FIXTURE IS NEVER WRITTEN BY THIS TEST. It used to be: a missing file was
    # regenerated and the assertion below then compared the bytes to themselves. That
    # makes the one test whose whole job is to catch cross-repo drift unable to fail -
    # delete the fixture and it heals itself and passes green, while the rail side goes
    # red alone and looks like the side that broke. Regeneration is deliberate and
    # explicit, and it is the operator's job to copy the result into both repos.
    if not FIXTURE_NDJSON.exists():
        if os.environ.get("CASTOR_WRITE_PLATATLAS_FIXTURE") != "1":
            raise AssertionError(
                f"{FIXTURE_NDJSON} is missing. This fixture is the cross-repo drift "
                "detector and is not regenerated silently. To recreate it deliberately, "
                "run this test once with CASTOR_WRITE_PLATATLAS_FIXTURE=1, then copy "
                "BOTH files into rail at "
                "apps/platatlas-worker/test/fixtures/ in the same change."
            )
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
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


# ── 6. the posting path's refusals ──────────────────────────────────────────
# Three failure modes that are not "the server said no": each of them would either send
# the ingest key somewhere this module never chose, or let a filing that did not happen
# be stamped into the local log as one that did.


class _RedirectingResponse(_FakeResponse):
    """What a real urlopen gives back after FOLLOWING a 302: a 200 from somewhere else,
    with no body, because urllib downgraded the POST to a GET on the way."""


def test_a_redirect_is_refused_rather_than_followed():
    """urllib's default redirect handler carries the Authorization header to the new
    host and, on 301/302/303, rewrites the POST to a GET and drops the body. So a
    redirect would send the ingest key to a host nobody chose, file nothing, and return
    a 200 that the caller would stamp into the local log as a successful filing. All
    three are worse than a failed filing, so the opener refuses 3xx outright.
    """
    import urllib.error
    import urllib.request

    from castor.platatlas_incident import _NoRedirects

    handler = _NoRedirects()
    with pytest.raises(urllib.error.HTTPError) as caught:
        handler.redirect_request(
            urllib.request.Request("https://acme.platatlas.com/api/traces?source=rcan"),
            None, 302, "Found", {}, "https://elsewhere.example/collect",
        )
    assert "refusing to follow a redirect" in str(caught.value)
    assert "elsewhere.example" in str(caught.value)


def test_an_org_slug_that_could_move_the_host_is_refused_before_the_key_is_attached():
    """The ingest URL is where the bearer token goes, and org_slug came from the robot's
    env. A slug holding a slash moves the HOST: 'evil.com/' would make the URL
    https://evil.com/.platatlas.com/... and send the key there. One regex closes it.
    """
    for bad in ("evil.com/", "acme/../x", "a b", "ACME", "", "x" * 70, "acme?"):
        with pytest.raises(PlatAtlasIncidentError, match="is not an org slug"):
            ingest_url(bad)
    assert ingest_url("acme-1") == "https://acme-1.platatlas.com/api/traces?source=rcan"


def test_a_response_with_no_readable_status_is_an_operator_line_not_a_traceback():
    """This function's contract is that the caller prints a sentence. int(None) is not a
    sentence."""

    class _NoStatus(_FakeResponse):
        def __init__(self):
            super().__init__(status=None, body=b"")

        def getcode(self):
            return None

    with pytest.raises(PlatAtlasIncidentError, match="no readable status code"):
        submit_incident_ndjson("x\n", "acme", "sk_live_x", opener=_FakeOpener(_NoStatus()))


# ── 7. the CLI: what gets printed, what gets posted, what gets stamped ──────
# The whole command was untested. These four cover the three states an operator can be
# in (no credentials, credentials, both destinations) and the one thing a submission
# must never do, which is disturb the local hash chain.


def _log_with_one_incident(tmp_path):
    from castor.incidents import IncidentLog, IncidentSeverity

    log = IncidentLog(tmp_path / "incidents.jsonl")
    log.record(IncidentSeverity.SERIOUS_HARM, "collision", "arm contacted the bench frame", {})
    return log


def _args(tmp_path, **over):
    import argparse

    base = dict(
        manifest=str(tmp_path / "ROBOT.md"), reporter="Operator on shift",
        disposition="bench halted", notified=["site safety lead"],
        submit=False, platatlas=True,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_with_no_credentials_it_prints_the_filing_posts_nothing_and_stamps_nothing(
    tmp_path, monkeypatch, capsys,
):
    from castor import cli
    from castor import platatlas_incident as pi

    monkeypatch.delenv("PLATATLAS_ORG_SLUG", raising=False)
    monkeypatch.delenv("PLATATLAS_INGEST_KEY", raising=False)
    monkeypatch.setenv("ROBOT_MD_ATTESTATION_KEY_FILE", str(_fixture_key_file(tmp_path)))
    monkeypatch.setenv("ROBOT_MD_ATTESTATION_KID", FIXTURE_KID)

    posted = []
    monkeypatch.setattr(pi, "submit_incident_ndjson", lambda *a, **k: posted.append(a))

    log = _log_with_one_incident(tmp_path)
    rc = cli._submit_incident_report_platatlas(_args(tmp_path), log)

    out = capsys.readouterr()
    assert posted == [], "nothing is sent without credentials"
    assert rc == 1
    # The signed object IS printed: an operator with a robot and no PlatAtlas org can
    # still file by hand, and the record exists either way.
    printed = json.loads(out.out)
    assert printed["action_class"] == INCIDENT_ACTION_CLASS
    assert printed["envelope_signature"]["kid"] == FIXTURE_KID
    assert "PLATATLAS_ORG_SLUG and PLATATLAS_INGEST_KEY not set" in out.err
    assert "nothing was stamped" in out.err
    # THE KEY IS NEVER ECHOED, and here there is not one; the next test is the one that
    # matters for that.
    assert log.unreported_incidents(), "a print-only run stamps nothing: nothing was filed"


def test_with_credentials_it_posts_exactly_one_line_and_never_echoes_the_key(
    tmp_path, monkeypatch, capsys,
):
    from castor import cli
    from castor import platatlas_incident as pi

    SECRET = "sk_live_do_not_print_me"
    monkeypatch.setenv("PLATATLAS_ORG_SLUG", "acme")
    monkeypatch.setenv("PLATATLAS_INGEST_KEY", SECRET)
    monkeypatch.setenv("ROBOT_MD_ATTESTATION_KEY_FILE", str(_fixture_key_file(tmp_path)))
    monkeypatch.setenv("ROBOT_MD_ATTESTATION_KID", FIXTURE_KID)

    seen = {}

    def _fake_submit(body, org_slug, ingest_key, **kw):
        seen.update(body=body, org_slug=org_slug, ingest_key=ingest_key)
        return {"id": "trace-123", "status": "accepted"}

    monkeypatch.setattr(pi, "submit_incident_ndjson", _fake_submit)

    log = _log_with_one_incident(tmp_path)
    rc = cli._submit_incident_report_platatlas(_args(tmp_path), log)
    out = capsys.readouterr()

    assert rc == 0
    assert seen["org_slug"] == "acme"
    assert seen["ingest_key"] == SECRET
    # EXACTLY ONE NDJSON LINE, terminated, one filing.
    assert seen["body"].endswith("\n")
    assert len([ln for ln in seen["body"].split("\n") if ln.strip()]) == 1
    assert json.loads(seen["body"].strip())["event"]["action_class"] == INCIDENT_ACTION_CLASS
    # THE KEY IS NEVER ECHOED, on either stream or into the log.
    assert SECRET not in out.out and SECRET not in out.err
    assert SECRET not in (tmp_path / "incidents.jsonl").read_text()
    # And the operator is told what was filed, where, and what the far side does NOT do.
    assert "Filed 1 incident(s) to acme" in out.out
    assert "it stops nothing" in out.out
    assert not log.unreported_incidents(), "a filing that succeeded IS stamped"


def test_the_local_chain_still_verifies_over_a_platatlas_submission_stamp(
    tmp_path, monkeypatch,
):
    """The stamp is an APPEND, never an edit, so `castor incidents verify` must still
    pass over a log that carries one. If a submission could disturb the chain, filing to
    PlatAtlas would cost the robot the only integrity property its own log has.
    """
    from castor import cli
    from castor import platatlas_incident as pi

    monkeypatch.setenv("PLATATLAS_ORG_SLUG", "acme")
    monkeypatch.setenv("PLATATLAS_INGEST_KEY", "sk_live_x")
    monkeypatch.setenv("ROBOT_MD_ATTESTATION_KEY_FILE", str(_fixture_key_file(tmp_path)))
    monkeypatch.setenv("ROBOT_MD_ATTESTATION_KID", FIXTURE_KID)
    monkeypatch.setattr(pi, "submit_incident_ndjson", lambda *a, **k: {"id": "t1"})

    log = _log_with_one_incident(tmp_path)
    assert log.verify_chain().state == "ok", "the chain holds before the filing"

    assert cli._submit_incident_report_platatlas(_args(tmp_path), log) == 0

    after = log.verify_chain()
    assert after.state == "ok", f"a submission stamp broke the local chain: {after}"
    # And the stamp is a NEW line: the incident record itself was not rewritten.
    lines = [json.loads(ln) for ln in (tmp_path / "incidents.jsonl").read_text().splitlines() if ln.strip()]
    kinds = [ln.get("record_type") for ln in lines]
    assert "incident" in kinds and "report_submission" in kinds


def test_submit_and_platatlas_together_file_to_both(tmp_path, monkeypatch):
    """The bug this guards. --submit stamps the log on success and unreported_incidents()
    re-reads it from disk, so the PlatAtlas leg found nothing pending, posted NOTHING,
    and returned 1 over a registry filing that had worked. Both destinations file the
    same incidents, so the pending list is taken once, before either of them runs.
    """
    from castor import cli
    from castor import platatlas_incident as pi

    monkeypatch.setenv("PLATATLAS_ORG_SLUG", "acme")
    monkeypatch.setenv("PLATATLAS_INGEST_KEY", "sk_live_x")
    monkeypatch.setenv("ROBOT_MD_ATTESTATION_KEY_FILE", str(_fixture_key_file(tmp_path)))
    monkeypatch.setenv("ROBOT_MD_ATTESTATION_KID", FIXTURE_KID)

    posted = []
    monkeypatch.setattr(pi, "submit_incident_ndjson", lambda body, *a, **k: posted.append(body) or {"id": "t1"})

    log = _log_with_one_incident(tmp_path)
    pending_before = log.unreported_incidents()
    assert len(pending_before) == 1

    # Stand in for the registry leg: it stamps the log exactly as _submit_incident_report
    # does on success, which is what used to empty the list out from under this call.
    log.mark_reported([i["id"] for i in pending_before], receipt={"destination": "registry"})
    assert log.unreported_incidents() == [], "the registry filing stamped the log"

    rc = cli._submit_incident_report_platatlas(_args(tmp_path, submit=True), log, pending=pending_before)
    assert rc == 0, "the PlatAtlas leg must not report failure over a registry filing that worked"
    assert len(posted) == 1, "and it must actually post the filing"
    assert log.verify_chain().state == "ok"
