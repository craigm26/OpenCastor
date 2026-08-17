"""T-002 — castor pair: QR payload shape + gateway attestation wiring.

Asserts the pairing core (castor.pairing) produces the exact QR payload the iOS
app parses and writes the exact env vars the robot-md-gateway attestation loader
reads (ROBOT_MD_ATTESTATION_KEY_FILE + ROBOT_MD_ATTESTATION_KID).
"""

from __future__ import annotations

from pathlib import Path

import json
import pytest

from castor import pairing

MANIFEST = """---
rcan_version: "3.2"
metadata:
  robot_name: bob
  rrn: RRN-000000000011
---

# Bob

A test robot manifest.
"""

# A drive robot — the case the capability surface exists for. A phone that has
# only ever seen an arm must not render this one as an arm.
DRIVE_MANIFEST = """---
rcan_version: "3.0"
metadata:
  robot_name: rover-spec-a-drive
  rrn: RRN-000000000012
capabilities:
  - drive.set
  - drive.stop
  - status.report
capability_contracts:
  drive.set:
    args:
      throttle:
        kind: float
        default: 0
      steering:
        kind: float
        default: 0
    preconditions:
      - kind: envelope_open
      - kind: backend_resolved
  drive.envelope.open:
    args:
      motion_budget_s:
        kind: float
        required: true
  drive.stop:
    returns:
      stopped:
        kind: bool
vision:
  object_descriptors:
    - id: orange_cone
      detector: hsv
    - detector: hsv
safety:
  estop:
    software: true
  hitl_gates:
    - scope: destructive
      require_auth: true
---

# rover

A test drive manifest.
"""


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    p = tmp_path / "ROBOT.md"
    p.write_text(MANIFEST)
    return p


@pytest.fixture
def drive_manifest(tmp_path: Path) -> Path:
    p = tmp_path / "DRIVE.md"
    p.write_text(DRIVE_MANIFEST)
    return p


def _run(tmp_path: Path, manifest: Path, **overrides) -> pairing.PairResult:
    kwargs = dict(
        manifest_path=manifest,
        gateway_url="http://192.168.1.50:8080",
        bearer="tok-actuate",
        rrn="RRN-000000000011",
        key_file=tmp_path / "attn" / "gw.pem",
        env_file=tmp_path / "gw.env",
    )
    kwargs.update(overrides)
    return pairing.run_pair(**kwargs)


def test_qr_payload_has_the_five_required_keys(tmp_path, manifest):
    result = _run(tmp_path, manifest)
    payload = result.payload
    assert set(payload) >= {"v", "gateway_url", "bearer", "manifest_path", "rrn"}
    assert payload["v"] == 1
    assert payload["gateway_url"] == "http://192.168.1.50:8080"
    assert payload["bearer"] == "tok-actuate"
    assert payload["rrn"] == "RRN-000000000011"
    # manifest_path rides in the QR as an absolute gateway-host-local path.
    assert payload["manifest_path"] == str(manifest.resolve())
    assert Path(payload["manifest_path"]).is_absolute()


def test_estop_url_included_only_when_provided(tmp_path, manifest):
    without = _run(tmp_path, manifest)
    assert "estop_url" not in without.payload

    with_estop = _run(
        tmp_path,
        manifest,
        key_file=tmp_path / "attn2" / "gw.pem",
        env_file=tmp_path / "gw2.env",
        estop_url="http://192.168.1.50:8001/api/stop",
    )
    assert with_estop.payload["estop_url"] == "http://192.168.1.50:8001/api/stop"


def test_attestation_keypair_written_and_loadable(tmp_path, manifest):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    result = _run(tmp_path, manifest)
    key_file = result.identity.key_file
    assert key_file.exists()
    # The gateway loads this via load_pem_private_key — must be an Ed25519 PKCS8 PEM.
    priv = load_pem_private_key(key_file.read_bytes(), password=None)
    assert isinstance(priv, Ed25519PrivateKey)
    # A sibling public key PEM is written for kid resolution.
    assert result.identity.pub_file.exists()
    assert result.identity.kid.startswith("gw-")
    # Private key is not world-readable.
    assert (key_file.stat().st_mode & 0o077) == 0


def test_env_file_sets_exact_gateway_attestation_vars(tmp_path, manifest):
    result = _run(tmp_path, manifest)
    env_text = result.env_file.read_text()
    # The exact vars robot_md_gateway.attestation.load_signing_identity_from_env reads.
    assert f"{pairing.ATTESTATION_KEY_FILE_ENV}={result.identity.key_file}" in env_text
    assert f"{pairing.ATTESTATION_KID_ENV}={result.identity.kid}" in env_text


def test_set_env_var_preserves_and_updates(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ROBOT_MD_PATH=./ROBOT.md\nROBOT_MD_BEARERS_FILE=./bearers.yaml\n")
    pairing.set_env_var(env, pairing.ATTESTATION_KID_ENV, "gw-abc")
    pairing.set_env_var(env, pairing.ATTESTATION_KID_ENV, "gw-def")  # idempotent replace
    text = env.read_text()
    assert "ROBOT_MD_PATH=./ROBOT.md" in text  # untouched
    assert "ROBOT_MD_BEARERS_FILE=./bearers.yaml" in text  # untouched
    assert text.count("ROBOT_MD_ATTESTATION_KID=") == 1  # replaced in place, not duplicated
    assert "ROBOT_MD_ATTESTATION_KID=gw-def" in text


def test_rrn_parsed_from_manifest_frontmatter(manifest):
    assert pairing.read_rrn_from_manifest(manifest) == "RRN-000000000011"


def test_bearer_read_prefers_actuate_tier(tmp_path):
    bearers = tmp_path / "bearers.yaml"
    bearers.write_text(
        "- token: read-tok\n  tier: read\n"
        "- token: actuate-tok\n  tier: actuate\n"
    )
    assert pairing.read_bearer_from_bearers_yaml(bearers) == "actuate-tok"


def test_bearer_read_accepts_the_shape_the_GATEWAY_WIZARD_writes(tmp_path):
    """`robot-md-gateway init` writes a mapping, not a bare list.

    This reader only accepted a bare list, so the file the gateway's own wizard
    generates could not be read by the pairing command documented as the next
    step — it failed with "expected a list of bearer entries" about a file that
    plainly contains one. Found on the bench trying to re-pair a live robot.
    """
    bearers = tmp_path / "bearers.yaml"
    bearers.write_text(
        "bearers:\n"
        "  - token: read-tok\n    tier: read\n"
        "  - token: actuate-tok\n    tier: actuate\n"
    )
    assert pairing.read_bearer_from_bearers_yaml(bearers) == "actuate-tok"


def test_bearer_read_ignores_other_keys_in_the_mapping(tmp_path):
    # A robot with several actuators has an `actuators:` list beside the
    # bearers. That is not this function's business and must not confuse it.
    bearers = tmp_path / "bearers.yaml"
    bearers.write_text(
        "bearers:\n  - token: actuate-tok\n    tier: actuate\n"
        "actuators:\n  - name: so-arm101\n    config: {}\n"
        "  - name: host\n    config: {}\n"
    )
    assert pairing.read_bearer_from_bearers_yaml(bearers) == "actuate-tok"


def test_bearer_read_still_reports_a_file_it_genuinely_cannot_use(tmp_path):
    bearers = tmp_path / "bearers.yaml"
    bearers.write_text("just-a-string\n")
    with pytest.raises(ValueError, match="bearers"):
        pairing.read_bearer_from_bearers_yaml(bearers)

    empty = tmp_path / "empty.yaml"
    empty.write_text("bearers: []\n")
    with pytest.raises(ValueError, match="no bearer entries"):
        pairing.read_bearer_from_bearers_yaml(empty)


def test_existing_key_not_clobbered_without_force(tmp_path, manifest):
    # CONTRACT CHANGED DELIBERATELY: rerun now REUSES the identity instead of
    # refusing. Refusing pushed operators toward the two wrong paths — hand-
    # assembling the payload (stale-IP QRs) or --force (which rotated a live
    # signing key to change an IP address, and once destroyed the only copy of
    # one). What this test protects is what it always protected: the key BYTES
    # must survive a rerun untouched.
    first = _run(tmp_path, manifest)
    key_bytes = first.identity.key_file.read_bytes()
    again = _run(tmp_path, manifest)  # same key_file, force defaults False
    assert again.identity.key_file.read_bytes() == key_bytes
    assert again.identity.kid == first.identity.kid
    # And the payload is re-emitted with the SAME verify key, so receipts
    # signed yesterday still verify against the QR scanned today.
    assert again.payload["attest_pub"] == first.payload["attest_pub"]


# ---------------------------------------------------------------------------
# capability_surface — the robot's OWN capabilities ride in the QR
# ---------------------------------------------------------------------------


def test_drive_robot_surface_carries_its_own_capabilities(drive_manifest):
    surface = pairing.capability_surface_from_manifest(drive_manifest)
    assert surface["robot_name"] == "rover-spec-a-drive"
    assert surface["capabilities"] == ["drive.set", "drive.stop", "status.report"]
    assert surface["software_stop"] is True
    assert surface["gates"] == [{"scope": "destructive", "require_auth": True}]
    # A descriptor with no id is skipped, not carried as a nameless target.
    assert surface["object_descriptors"] == ["orange_cone"]


def test_surface_contracts_project_kind_required_and_default_presence(drive_manifest):
    contracts = pairing.capability_surface_from_manifest(drive_manifest)["contracts"]
    # A declared default travels as `has_default` — the client needs to know a
    # missing arg is still well-formed, not what the value would be.
    assert contracts["drive.set"]["args"]["throttle"] == {"kind": "float", "has_default": True}
    assert contracts["drive.set"]["preconditions"] == ["envelope_open", "backend_resolved"]
    assert contracts["drive.envelope.open"]["args"]["motion_budget_s"] == {
        "kind": "float",
        "required": True,
    }
    # A contract with only `returns` still exists, with no declared args.
    assert contracts["drive.stop"] == {}


def test_surface_is_none_when_the_manifest_declares_no_capabilities(manifest):
    # An empty surface would itself be a claim ("this robot can do nothing").
    assert pairing.capability_surface_from_manifest(manifest) is None


def test_surface_is_none_rather_than_raising_on_an_unreadable_manifest(tmp_path):
    assert pairing.capability_surface_from_manifest(tmp_path / "nope.md") is None
    bad = tmp_path / "bad.md"
    bad.write_text("---\n:\n  - [unclosed\n---\n")
    assert pairing.capability_surface_from_manifest(bad) is None


def test_run_pair_puts_the_drive_surface_in_the_qr(tmp_path, drive_manifest):
    result = _run(tmp_path, drive_manifest, rrn="RRN-000000000012")
    surface = result.payload["capability_surface"]
    assert surface["capabilities"] == ["drive.set", "drive.stop", "status.report"]
    assert surface["robot_name"] == "rover-spec-a-drive"


def test_capability_surface_absent_when_the_manifest_declares_none(tmp_path, manifest):
    # The pre-surface QR shape, unchanged — an old robot pairs exactly as before.
    result = _run(tmp_path, manifest)
    assert "capability_surface" not in result.payload


def test_surface_is_trimmed_rather_than_making_the_qr_unscannable():
    import json

    fat = {
        "capabilities": ["arm.pick", "arm.place"],
        "software_stop": True,
        "gates": [{"scope": "destructive", "require_auth": True}],
        "contracts": {
            f"arm.step{i}": {"args": {f"arg{j}": {"kind": "float"} for j in range(20)}}
            for i in range(20)
        },
    }
    payload = pairing.build_pair_payload(
        gateway_url="http://192.168.1.50:8080",
        bearer="tok",
        manifest_path="/etc/opencastor/ROBOT.md",
        rrn="RRN-1",
        capability_surface=fat,
    )
    encoded = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    assert encoded <= pairing.PAIR_QR_BYTE_BUDGET
    # Detail is dropped, never invented: the capability list survives.
    assert payload["capability_surface"]["capabilities"] == ["arm.pick", "arm.place"]
    assert "contracts" not in payload["capability_surface"]


def test_force_rotation_keeps_a_copy_of_the_old_key(tmp_path):
    """`--force` rotates a LIVE signing identity, so it must not be the only copy.

    Written after --force destroyed the only copy of a robot's live attestation
    key on this bench. Everything behaved as documented; it still cost an
    unrecoverable key, because "rotate" and "delete the only copy of the thing
    that signs your receipts" are the same operation when nothing keeps a
    backup. Receipts already signed with the old key stay verifiable only if the
    key survives somewhere.
    """
    key = tmp_path / "attest.pem"
    first = pairing.generate_attestation_identity(key, kid="k1")
    original = key.read_bytes()

    pairing.generate_attestation_identity(key, kid="k2", force=True)
    assert key.read_bytes() != original, "force must actually rotate"

    backups = sorted(tmp_path.glob("attest.pem.rotated-*"))
    assert len(backups) == 1, f"expected one backup, found {backups}"
    assert backups[0].read_bytes() == original
    # 0600: it is still a private key, and a backup with looser permissions
    # would be a worse outcome than no backup at all.
    assert (backups[0].stat().st_mode & 0o777) == 0o600
    assert first.key_file == key


def test_a_second_rotation_does_not_eat_the_first_backup(tmp_path):
    key = tmp_path / "attest.pem"
    pairing.generate_attestation_identity(key, kid="k1")
    pairing.generate_attestation_identity(key, kid="k2", force=True)
    pairing.generate_attestation_identity(key, kid="k3", force=True)
    assert len(sorted(tmp_path.glob("attest.pem.rotated-*"))) >= 1


# ---------------------------------------------------------------------------
# The QR is a universal link — one QR, both audiences
# ---------------------------------------------------------------------------


#: A payload with the shape and the secrets a real one has. The tokens are
#: fake, and named so that a grep of a failing test's output says what leaked.
LIVE_PAYLOAD = {
    "v": 1,
    "gateway_url": "http://192.0.2.7:8081",
    "bearer": "rmg_live_actuate_NEVER_IN_A_URL_PATH",
    "manifest_path": "/home/pi/rover/ROBOT.md",
    "rrn": "RRN-000000000012",
    "estop_url": "http://192.0.2.7:8082/api/stop",
    "console_token": "rmg_view_NEVER_IN_A_URL_PATH",
    "capability_surface": {"capabilities": ["drive.set", "drive.stop"],
                           "software_stop": True},
}


def _every_string_in(value) -> list[str]:
    """Every string anywhere in the payload — what must not appear in a URL path."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _every_string_in(k) + _every_string_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in _every_string_in(v)]
    return []


def test_the_link_prefix_is_exactly_the_pair_url():
    # Pinned as a literal: this exact origin+path is what opencastor.com's
    # apple-app-site-association claims for WYGG3JXWMG.com.opencastor.ios. Move
    # it and every QR already printed and taped to a robot stops opening the app.
    assert pairing.PAIR_LINK_BASE == "https://opencastor.com/pair"
    assert pairing.pair_link(LIVE_PAYLOAD).startswith("https://opencastor.com/pair#")


def test_THERULE_not_one_payload_byte_rides_before_the_hash():
    """The payload holds a live actuate bearer, so it lives in the fragment.

    Fragments are not sent to servers: no access log, no CDN cache key, no
    analytics pixel ever sees this. A path or query segment would publish a
    credential that can move a robot to every hop between the phone and
    opencastor.com, and to whoever reads those logs afterwards.
    """
    link = pairing.pair_link(LIVE_PAYLOAD)
    before, sep, fragment = link.partition("#")

    assert sep == "#" and link.count("#") == 1
    assert before == pairing.PAIR_LINK_BASE
    assert "?" not in before, "a query string IS sent to the server"
    for value in _every_string_in(LIVE_PAYLOAD):
        assert value not in before, f"{value!r} rode in front of the '#'"
    # All of it went into the fragment, whole.
    assert pairing.decode_pair_link(link) == LIVE_PAYLOAD


def test_the_fragment_carries_a_version_tag_the_app_can_parse_on():
    fragment = pairing.encode_pair_fragment(LIVE_PAYLOAD)
    tag, dot, body = fragment.partition(".")
    assert (tag, dot) == ("v1", "."), fragment
    assert body, "the tag must be followed by the encoded payload"
    assert pairing.PAIR_LINK_SCHEMA == "v1"


def test_the_body_is_unpadded_url_safe_base64():
    # '+' and '/' would need percent-encoding to survive a URL, and '=' is legal
    # but noisy in a QR and in every log line a human pastes it into.
    body = pairing.encode_pair_fragment(LIVE_PAYLOAD).split(".", 1)[1]
    assert set("+/=").isdisjoint(body), body


def test_round_trip_is_byte_exact():
    # The runtime has to be able to verify its own output, and the firmware and
    # app parsers are written against this function.
    payload = dict(LIVE_PAYLOAD, robot_name="rover-ünïcode", nested={"n": [1, 2, {"b": False}]})
    link = pairing.pair_link(payload)
    back = pairing.decode_pair_link(link)
    assert back == payload
    assert pairing.compact_payload_json(back) == pairing.compact_payload_json(payload)
    assert pairing.pair_link(back) == link


def test_an_unknown_version_tag_is_refused_rather_than_guessed_at():
    with pytest.raises(ValueError, match="unknown pairing fragment version"):
        pairing.decode_pair_fragment("v2.eyJ2IjoxfQ")
    with pytest.raises(ValueError, match="unknown pairing fragment version"):
        pairing.decode_pair_fragment("eyJ2IjoxfQ")  # no tag at all


def test_a_link_without_a_fragment_is_not_a_pairing_link():
    with pytest.raises(ValueError, match="no '#' fragment"):
        pairing.decode_pair_link("https://opencastor.com/pair")


def test_a_leading_hash_is_tolerated_because_that_is_how_a_browser_hands_it_over():
    # `location.hash` includes the '#'; so does anything a person copies out of
    # an address bar. Refusing it would be a parser being right and useless.
    fragment = pairing.encode_pair_fragment(LIVE_PAYLOAD)
    assert pairing.decode_pair_fragment("#" + fragment) == LIVE_PAYLOAD


def test_a_self_hosted_pair_page_still_pairs():
    # The fragment IS the pairing; what precedes the '#' only decides which
    # explainer page a phone WITHOUT the app lands on.
    fragment = pairing.encode_pair_fragment(LIVE_PAYLOAD)
    assert pairing.decode_pair_link(f"https://robot.local/pair#{fragment}") == LIVE_PAYLOAD


# ---------------------------------------------------------------------------
# The byte budget is measured against whatever the camera actually reads
# ---------------------------------------------------------------------------


def _fat_surface(n_contracts: int) -> dict:
    return {
        "capabilities": ["arm.pick", "arm.place"],
        "contracts": {f"arm.step{i}": {"args": {"x": {"kind": "float"}}}
                      for i in range(n_contracts)},
    }


_BASE = dict(gateway_url="http://192.168.1.50:8080", bearer="rmg_live_actuate_x",
             manifest_path="/home/pi/rover/ROBOT.md", rrn="RRN-000000000012")


def test_THEBUG_the_fit_is_computed_on_the_link_length_in_link_mode():
    """base64url costs ~33%, so a surface that fits the JSON can bust the QR.

    A surface trimmed against the raw payload and then shipped as a link passes
    its own fit check and still comes out as a 177-module grid nobody can scan
    at arm's length — the fit was measured on a string that never reached a
    camera. Same budget, longer string: link mode drops the contracts sooner.
    """
    surface = _fat_surface(22)

    raw = pairing.build_pair_payload(**_BASE, capability_surface=surface)
    assert "contracts" in raw["capability_surface"], "precondition: this fits as raw JSON"

    linked = pairing.build_pair_payload(**_BASE, capability_surface=surface, for_link=True)
    assert "contracts" not in linked["capability_surface"]
    # Detail is dropped, never invented: the capability list survives.
    assert linked["capability_surface"]["capabilities"] == ["arm.pick", "arm.place"]
    assert len(pairing.pair_link(linked).encode("utf-8")) <= pairing.PAIR_QR_BYTE_BUDGET


def test_link_mode_keeps_the_whole_link_under_the_budget():
    linked = pairing.build_pair_payload(**_BASE, capability_surface=_fat_surface(40),
                                        for_link=True)
    assert len(pairing.pair_link(linked).encode("utf-8")) <= pairing.PAIR_QR_BYTE_BUDGET


def test_no_link_mode_budgets_exactly_as_it_always_did():
    # --no-link must restore today's behaviour byte for byte, including how much
    # of the surface survives.
    surface = _fat_surface(22)
    default = pairing.build_pair_payload(**_BASE, capability_surface=surface)
    explicit = pairing.build_pair_payload(**_BASE, capability_surface=surface, for_link=False)
    assert default == explicit
    assert len(pairing.compact_payload_json(default).encode()) <= pairing.PAIR_QR_BYTE_BUDGET


def test_a_small_robot_pairs_with_the_same_payload_either_way():
    # Nothing about linking changes the payload itself; only how much of a big
    # capability surface fits. The overwhelmingly common robot is unaffected.
    small = {"capabilities": ["drive.set", "drive.stop"]}
    assert (pairing.build_pair_payload(**_BASE, capability_surface=small)
            == pairing.build_pair_payload(**_BASE, capability_surface=small, for_link=True))


class TestWritePairArtifacts:
    """One command puts the QR where the runbooks say it lives.

    Every robot on this bench ended up with a hand-assembled payload plus a
    qrcode two-liner pasted from session notes — and hand assembly is how the
    rover shipped a QR pinned to a DHCP address the Pi no longer held.
    """

    PAYLOAD = {"v": 1, "gateway_url": "http://192.0.2.7:8081", "bearer": "rmg_live_x",
               "manifest_path": "/home/pi/ROBOT.md", "rrn": "RRN-000000000012"}

    def test_writes_payload_json_and_qr_png(self, tmp_path):
        from castor.pairing import write_pair_artifacts

        written = write_pair_artifacts(self.PAYLOAD, tmp_path / "robot")
        payload_file = written["payload"]
        assert json.loads(payload_file.read_text()) == self.PAYLOAD
        # Owner-only: the payload carries a live actuate bearer.
        assert (payload_file.stat().st_mode & 0o777) == 0o600
        assert written["qr"].exists()
        assert (written["qr"].stat().st_mode & 0o777) == 0o600

    def test_the_png_decodes_back_to_the_compact_payload(self, tmp_path):
        # The QR is only worth writing if a camera gets the same bytes back.
        pytest.importorskip("qrcode")
        try:
            from PIL import Image
            from pyzbar.pyzbar import decode
        except ImportError:
            pytest.skip("pyzbar not installed")
        from castor.pairing import write_pair_artifacts

        written = write_pair_artifacts(self.PAYLOAD, tmp_path)
        got = decode(Image.open(written["qr"]))
        assert json.loads(got[0].data) == self.PAYLOAD

    def test_link_mode_writes_pair_link_txt_and_points_the_qr_at_it(self, tmp_path):
        import qrcode

        from castor.pairing import decode_pair_link, pair_link, write_pair_artifacts

        written = write_pair_artifacts(self.PAYLOAD, tmp_path / "robot", link=True)
        link = pair_link(self.PAYLOAD)

        assert written["link"].name == "pair-link.txt"
        assert written["link"].read_text() == link + "\n"
        # The fragment carries the same live bearer the JSON does.
        assert (written["link"].stat().st_mode & 0o777) == 0o600
        assert decode_pair_link(written["link"].read_text().strip()) == self.PAYLOAD

        # The PNG is the QR of the LINK, not of the JSON. Byte-compared against
        # the library's own output for the link: no decoder needed to prove it.
        reference = tmp_path / "reference.png"
        qrcode.make(link).save(str(reference))
        assert written["qr"].read_bytes() == reference.read_bytes()

        # The payload of record is unchanged — it is still what gets pasted.
        assert json.loads(written["payload"].read_text()) == self.PAYLOAD

    def test_no_link_is_byte_identical_to_the_raw_json_qr(self, tmp_path):
        import qrcode

        from castor.pairing import compact_payload_json, write_pair_artifacts

        written = write_pair_artifacts(self.PAYLOAD, tmp_path / "robot", link=False)
        reference = tmp_path / "reference.png"
        qrcode.make(compact_payload_json(self.PAYLOAD)).save(str(reference))

        assert written["qr"].read_bytes() == reference.read_bytes()
        assert "link" not in written
        assert not (tmp_path / "robot" / "pair-link.txt").exists()

    def test_the_link_file_is_written_even_without_qrcode(self, tmp_path, monkeypatch):
        # A link you can paste into a phone is MORE useful than the PNG when
        # there is no qrcode package and no scannable screen, so it must not be
        # collateral damage of the optional dependency being absent.
        import builtins

        from castor import pairing

        real_import = builtins.__import__

        def no_qrcode(name, *a, **k):
            if name == "qrcode":
                raise ImportError("not installed")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_qrcode)
        written = pairing.write_pair_artifacts(self.PAYLOAD, tmp_path, link=True)
        assert set(written) == {"payload", "link"}
        assert written["link"].read_text().startswith("https://opencastor.com/pair#v1.")

    def test_missing_qrcode_degrades_to_json_only(self, tmp_path, monkeypatch):
        # A missing nicety must not block a pairing: the JSON is the payload of
        # record and pasting it is the documented fallback.
        import builtins

        from castor import pairing

        real_import = builtins.__import__

        def no_qrcode(name, *a, **k):
            if name == "qrcode":
                raise ImportError("not installed")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_qrcode)
        written = pairing.write_pair_artifacts(self.PAYLOAD, tmp_path)
        assert "payload" in written and "qr" not in written


class TestCastorPairLinkFlag:
    """`castor pair` links by default; --no-link is the escape hatch.

    The QR used to encode raw JSON, which only the app's in-app scanner
    understood — a phone camera showed a wall of gibberish to someone who had no
    way to know what it was or what to install.
    """

    def _args(self, tmp_path, out_dir, **over):
        import types

        manifest = tmp_path / "ROBOT.md"
        manifest.write_text(MANIFEST)
        bearers = tmp_path / "bearers.yaml"
        bearers.write_text("- token: rmg_live_actuate_x\n  tier: actuate\n")
        kwargs = dict(
            manifest_path=str(manifest),
            gateway_url="http://192.0.2.7:8081", port=8081,
            bearer=None, bearers=str(bearers), rrn=None,
            estop_url=None,
            console_url=None, console_port=None, console_token=None,
            out_dir=str(out_dir),
            key_file=str(tmp_path / "attest.pem"),
            env_file=str(tmp_path / "attest.env"),
            kid=None, force=False,
        )
        kwargs.update(over)
        return types.SimpleNamespace(**kwargs)

    def test_the_default_qr_is_the_universal_link(self, tmp_path, monkeypatch, capsys):
        import qrcode

        from castor.cli import cmd_pair

        monkeypatch.delenv("CONSOLE_TOKEN", raising=False)
        out = tmp_path / "out"
        assert cmd_pair(self._args(tmp_path, out, link=True)) == 0

        payload = json.loads((out / "pair-payload.json").read_text())
        link = (out / "pair-link.txt").read_text().strip()
        assert link.startswith("https://opencastor.com/pair#v1.")
        assert pairing.decode_pair_link(link) == payload

        reference = tmp_path / "ref.png"
        qrcode.make(link).save(str(reference))
        assert (out / "pair-qr.png").read_bytes() == reference.read_bytes()
        # The bearer is printed in the decoded payload as it always was, but the
        # link the operator is told to hand around keeps it after the '#'.
        assert "pair#v1." in capsys.readouterr().out

    def test_no_link_restores_the_raw_json_qr(self, tmp_path, monkeypatch):
        import qrcode

        from castor.cli import cmd_pair

        monkeypatch.delenv("CONSOLE_TOKEN", raising=False)
        out = tmp_path / "out"
        assert cmd_pair(self._args(tmp_path, out, link=False)) == 0

        payload = json.loads((out / "pair-payload.json").read_text())
        assert not (out / "pair-link.txt").exists()

        reference = tmp_path / "ref.png"
        qrcode.make(pairing.compact_payload_json(payload)).save(str(reference))
        assert (out / "pair-qr.png").read_bytes() == reference.read_bytes()

    def test_an_args_namespace_without_the_flag_still_links(self, tmp_path, monkeypatch):
        # Older callers (and the hand-built namespaces in the other test modules)
        # must land on the default, not silently opt out of it.
        from castor.cli import cmd_pair

        monkeypatch.delenv("CONSOLE_TOKEN", raising=False)
        out = tmp_path / "out"
        assert cmd_pair(self._args(tmp_path, out)) == 0
        assert (out / "pair-link.txt").exists()


class TestRunPairConsoleFields:
    def test_console_fields_ride_when_both_are_given(self, tmp_path):
        from castor.pairing import run_pair

        manifest = tmp_path / "ROBOT.md"
        manifest.write_text("---\nmetadata:\n  rrn: RRN-000000000012\n---\n")
        result = run_pair(
            manifest_path=manifest,
            gateway_url="http://192.0.2.7:8081",
            bearer="rmg_live_x",
            rrn="RRN-000000000012",
            key_file=tmp_path / "attest.pem",
            env_file=tmp_path / "attest.env",
            console_url="http://192.0.2.7:8004",
            console_token="rmg_view_y",
        )
        assert result.payload["console_url"] == "http://192.0.2.7:8004"
        assert result.payload["console_token"] == "rmg_view_y"
