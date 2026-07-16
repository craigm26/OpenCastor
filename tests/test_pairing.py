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


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    p = tmp_path / "ROBOT.md"
    p.write_text(MANIFEST)
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


def test_existing_key_not_clobbered_without_force(tmp_path, manifest):
    _run(tmp_path, manifest)
    with pytest.raises(FileExistsError):
        _run(tmp_path, manifest)  # same key_file, force defaults False
