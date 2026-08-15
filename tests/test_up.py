"""castor up — the ten-minute bring-up, tested at its seams.

Every test here pins something the first LIVE run of `up` got wrong on the
bench, which is the strongest argument for their existence.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from castor.up import (
    UpPlan,
    derive_identity,
    pick_archetype,
    render,
    sign_manifest,
    unit_files,
)


def plan(**over) -> UpPlan:
    defaults = dict(name="testbot", home=Path("/home/pi/testbot"), archetype="rc-car",
                    rrn="RRN-LOCAL-abc123", robot_uuid="u-1", base_port=8080)
    defaults.update(over)
    return UpPlan(**defaults)


# ---------------------------------------------------------------------------
# Archetype detection
# ---------------------------------------------------------------------------


def test_a_pca9685_on_the_bus_means_rc_car():
    archetype, found = pick_archetype({0x40, 0x36})
    assert archetype == "rc-car"
    assert any("PCA9685" in f for f in found)


def test_an_empty_bus_means_sim_not_an_error():
    # A dead drive battery takes the PCA off the bus (its supply is the ESC's
    # BEC) — `up` run at that moment must still produce a working robot.
    assert pick_archetype(set())[0] == "sim"


def test_local_identity_is_marked_local_and_unique():
    a, b = derive_identity("robot"), derive_identity("robot")
    assert a[0].startswith("RRN-LOCAL-")
    assert a != b, "two robots both named 'robot' must not collide"


# ---------------------------------------------------------------------------
# Manifest signing — the one-newline frame
# ---------------------------------------------------------------------------


GATEWAY_SIG_RE = re.compile(  # copied verbatim from robot-md-gateway
    r"\n<!--\s*ROBOT-MD-SIG\s+kid=(?P<kid>\S+)\s+sig=(?P<sig>[A-Za-z0-9+/=]+)\s*-->\s*\Z"
)


def test_THEBUG_signature_covers_exactly_what_the_gateway_verifies(tmp_path):
    # The gateway's footer regex starts at the newline BEFORE the comment and
    # verifies text[:match.start()] — the body WITHOUT that newline. The first
    # live run signed the body WITH it: the signature verified in a bare test
    # and the gateway denied `manifest_provenance`. One byte of framing.
    from cryptography.hazmat.primitives import serialization

    key = tmp_path / "k.pem"
    signed = sign_manifest("---\nbody: yes\n", key, "test-kid")

    m = GATEWAY_SIG_RE.search(signed)
    assert m, "footer must match the gateway's own regex"
    verified_body = signed[: m.start()].encode()

    priv = serialization.load_pem_private_key(key.read_bytes(), password=None)
    priv.public_key().verify(base64.b64decode(m.group("sig")), verified_body)


def test_resigning_reuses_the_key(tmp_path):
    key = tmp_path / "k.pem"
    sign_manifest("a\n", key, "kid")
    first = key.read_bytes()
    sign_manifest("b\n", key, "kid")
    assert key.read_bytes() == first, "a rerun must not rotate the manifest key"


# ---------------------------------------------------------------------------
# Safe-by-default templates
# ---------------------------------------------------------------------------


def test_THERULE_generated_policy_drives_simulated_wheels():
    # The template was cut from a live robot AFTER its wheels went real, and
    # quietly carried that decision to every future robot: the first scratch
    # bring-up constructed PCA9685Drive out of the box. `up` must never
    # generate a config that can move hardware.
    text = render("gateway-policy.env.tmpl", plan())
    live = [l for l in text.splitlines() if l.startswith("OPENCASTOR_DRIVE")]
    assert live == [], f"template enables real drive: {live}"


def test_the_manifest_template_carries_the_substituted_identity():
    text = render("ROBOT.md.tmpl", plan())
    assert "testbot" in text
    assert "RRN-LOCAL-abc123" in text
    assert "rover" not in text, "template leaks the donor robot's name"


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_units_reference_only_the_robots_own_home():
    units = unit_files(plan(), python="/venv/bin/python",
                       gateway_bin="/venv/bin/robot-md-gateway")
    assert set(units) == {"testbot-gateway.service", "testbot-castor.service",
                          "testbot-rrf-stub.service"}
    for content in units.values():
        assert "/home/pi/testbot" in content
        assert "craigm26" not in content and "rover" not in content


def test_the_gateway_binary_is_the_one_passed_not_a_guess():
    units = unit_files(plan(), python="/venv/bin/python", gateway_bin="/right/one")
    assert "ExecStart=/right/one serve" in units["testbot-gateway.service"]


def test_port_layout_is_adjacent_and_derived():
    p = plan(base_port=9000)
    assert (p.gateway_port, p.runtime_port, p.console_port) == (9000, 9001, 9002)
