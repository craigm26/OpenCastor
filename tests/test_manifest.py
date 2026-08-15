"""castor.manifest — one-command capability edits, gateway-exact signing."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from castor import manifest
from castor.up import sign_manifest

BODY = """---
metadata:
  rrn: RRN-000000000012
capabilities:
  - drive.set
  - drive.stop
  # a load-bearing comment about stopping
  - status.report
capability_contracts:
  drive.set:
    args: {}
---

The prose below the frontmatter is SAFETY DOCUMENTATION and every byte of it
must survive an edit untouched.
"""


@pytest.fixture
def signed(tmp_path):
    key = tmp_path / "manifest.pem"
    path = tmp_path / "ROBOT.md"
    path.write_text(sign_manifest(BODY, key, "test-manifest"))
    pub = tmp_path / "pub.pem"
    from cryptography.hazmat.primitives import serialization

    priv = serialization.load_pem_private_key(key.read_bytes(), password=None)
    pub.write_bytes(priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))
    return path, key, pub


def test_list_reads_declarations_in_order(signed):
    path, _, _ = signed
    assert manifest.capabilities(path) == ["drive.set", "drive.stop", "status.report"]


def test_add_declares_resigns_and_the_gateway_math_verifies(signed):
    path, key, pub = signed
    assert manifest.add_capability(path, "sensor.battery", key_file=key,
                                   comment="MAX1704x fuel gauge") is True
    assert "sensor.battery" in manifest.capabilities(path)
    ok, why = manifest.verify(path, pub_pem=pub.read_bytes())
    assert ok, why


def test_add_is_idempotent(signed):
    path, key, _ = signed
    assert manifest.add_capability(path, "drive.set", key_file=key) is False


def test_every_other_byte_survives_an_edit(signed):
    # Text-surgical, not a YAML round-trip: comments and safety prose are
    # load-bearing, and a reformat would destroy the document's audit trail.
    path, key, _ = signed
    before = path.read_text()
    manifest.add_capability(path, "sensor.battery", key_file=key)
    after = path.read_text()
    assert "# a load-bearing comment about stopping" in after
    assert "SAFETY DOCUMENTATION and every byte" in after
    # Everything except the new line and the new signature is unchanged.
    stripped_before = re.sub(r"<!-- ROBOT-MD-SIG.*-->\n", "", before)
    stripped_after = re.sub(r"<!-- ROBOT-MD-SIG.*-->\n", "", after)
    assert stripped_after.replace("  - sensor.battery\n", "") == stripped_before


def test_remove_withdraws_and_still_verifies(signed):
    path, key, pub = signed
    assert manifest.remove_capability(path, "drive.stop", key_file=key) is True
    assert "drive.stop" not in manifest.capabilities(path)
    ok, why = manifest.verify(path, pub_pem=pub.read_bytes())
    assert ok, why
    # The comment above the removed entry survives: prose explaining what WAS
    # declared is history worth keeping in an auditable document.
    assert "# a load-bearing comment about stopping" in path.read_text()


def test_remove_of_undeclared_is_a_no(signed):
    path, key, _ = signed
    assert manifest.remove_capability(path, "arm.home", key_file=key) is False


def test_sign_blesses_a_hand_edit(signed):
    # The operator rewrote prose in an editor; `sign` must make it verify
    # again without touching their words.
    path, key, pub = signed
    text = path.read_text()
    text = re.sub(r"<!-- ROBOT-MD-SIG.*-->\n", "", text)
    text = text.replace("SAFETY DOCUMENTATION", "SAFETY DOCUMENTATION (edited by hand)")
    path.write_text(text)
    manifest.sign(path, key_file=key, kid="test-manifest")
    ok, why = manifest.verify(path, pub_pem=pub.read_bytes())
    assert ok, why
    assert "(edited by hand)" in path.read_text()


def test_verify_catches_tampering(signed):
    path, _, pub = signed
    tampered = path.read_text().replace("drive.set", "drive.everything")
    path.write_text(tampered)
    ok, why = manifest.verify(path, pub_pem=pub.read_bytes())
    assert not ok
    assert "did not verify" in why


def test_the_footer_matches_the_gateways_own_regex(signed):
    path, key, _ = signed
    manifest.add_capability(path, "sensor.battery", key_file=key)
    assert manifest.SIG_RE.search(path.read_text())
