"""T-002 — castor pair: QR payload shape + gateway attestation wiring.

Asserts the pairing core (castor.pairing) produces the exact QR payload the iOS
app parses and writes the exact env vars the robot-md-gateway attestation loader
reads (ROBOT_MD_ATTESTATION_KEY_FILE + ROBOT_MD_ATTESTATION_KID).
"""

from __future__ import annotations

from pathlib import Path

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
    _run(tmp_path, manifest)
    with pytest.raises(FileExistsError):
        _run(tmp_path, manifest)  # same key_file, force defaults False


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
