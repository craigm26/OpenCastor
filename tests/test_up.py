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
                          "testbot-console.service", "testbot-rrf-stub.service"}
    for content in units.values():
        assert "/home/pi/testbot" in content
        assert "craigm26" not in content and "rover" not in content


def test_the_gateway_binary_is_the_one_passed_not_a_guess():
    units = unit_files(plan(), python="/venv/bin/python", gateway_bin="/right/one")
    assert "ExecStart=/right/one serve" in units["testbot-gateway.service"]


def test_THEBUG_console_env_is_applied_after_the_generated_defaults():
    # systemd applies Environment= and EnvironmentFile= in the order they are
    # written, last assignment winning. console.env was listed FIRST, so the
    # generated Environment=CONSOLE_PORT overrode it — and console.env is the
    # one file here whose own header invites hand edits. An operator who moved
    # the port there watched the console keep answering on the old one, with
    # nothing anywhere saying why.
    unit = unit_files(plan(), python="/venv/bin/python",
                      gateway_bin="/venv/bin/robot-md-gateway")["testbot-console.service"]
    assert unit.index("Environment=CONSOLE_PORT=8082") < unit.index(
        "EnvironmentFile=/home/pi/testbot/console.env"), (
        "a hand-set CONSOLE_PORT in console.env must beat the generated default")
    assert unit.index("Environment=ROBOT_HOME=/home/pi/testbot") < unit.index(
        "EnvironmentFile=/home/pi/testbot/console.env")


def test_port_layout_is_adjacent_and_derived():
    p = plan(base_port=9000)
    assert (p.gateway_port, p.runtime_port, p.console_port) == (9000, 9001, 9002)


# ---------------------------------------------------------------------------
# Fresh-host degradation — the parts pip does not bring
# ---------------------------------------------------------------------------


def test_a_host_without_the_rc_car_actuator_falls_back_to_noop(monkeypatch):
    # rc-car-actuator is a separate package and not (yet) on PyPI: a fresh
    # `pip install opencastor` does not have it. Writing `actuator: rc-car`
    # anyway crash-loops the gateway on an entry-point error at minute two.
    import castor.up as up

    class EP:
        name = "noop"

    monkeypatch.setattr("importlib.metadata.entry_points",
                        lambda group: [EP()] if group == "robot_md_gateway.actuators" else [])
    name, note = up.resolve_actuator()
    assert name == "noop"
    assert "pip install rc-car-actuator" in note


def test_with_the_actuator_installed_rc_car_is_chosen(monkeypatch):
    # rc-car-actuator is an opt-in extra (it is not on PyPI), so this cannot
    # assume the host has it — stub the registry the way the fallback test does.
    import castor.up as up

    class EP:
        name = "rc-car"

    monkeypatch.setattr("importlib.metadata.entry_points",
                        lambda group: [EP()] if group == "robot_md_gateway.actuators" else [])
    name, note = up.resolve_actuator()
    assert name == "rc-car"
    assert note is None


# ---------------------------------------------------------------------------
# The pairing QR — a universal link by default
# ---------------------------------------------------------------------------


def _stub_the_host(monkeypatch, tmp_path) -> None:
    """Let `up` run for real without touching this machine.

    HOME is redirected (so systemd units land in the scratch tree), the bus scan
    and the port probes are stubbed out, and gaps collection is skipped. Twin of
    the helper in test_console.py: a test must not enumerate the operator's I2C
    bus or poke a live service to prove that a file got written.
    """
    import castor.up as up

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(up, "detect_brain", lambda: ("ollama", ""))
    monkeypatch.setattr(up, "_port_answers", lambda port: False)
    monkeypatch.setattr("castor.peripherals.scan_i2c", lambda: [])
    monkeypatch.setattr("castor.gaps.collect", lambda **kwargs: [])


def test_the_up_qr_opens_the_app_from_any_phone_camera(tmp_path, monkeypatch):
    """`up` ends in a QR, and the person holding the phone is often a beginner.

    Encoding raw JSON meant only the app's own in-app scanner understood it — a
    camera showed a wall of gibberish to someone with no way to know what it was
    or what to install. `up` follows `castor pair`: the QR is a universal link.
    """
    import json
    import sys

    import qrcode

    import castor.up as up
    from castor.pairing import PAIR_QR_BYTE_BUDGET, decode_pair_link, pair_link

    _stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "testbot"
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False)

    payload = json.loads((home / "pair-payload.json").read_text())
    link = (home / "pair-link.txt").read_text().strip()
    assert link.startswith("https://opencastor.com/pair#v1.")
    assert decode_pair_link(link) == payload
    # The QR is the link's, not the JSON's.
    reference = tmp_path / "ref.png"
    qrcode.make(pair_link(payload)).save(str(reference))
    assert (home / "pair-qr.png").read_bytes() == reference.read_bytes()
    # And the whole thing still fits the budget a camera can resolve.
    assert len(link.encode("utf-8")) <= PAIR_QR_BYTE_BUDGET


def test_up_no_link_writes_the_raw_json_qr(tmp_path, monkeypatch):
    import json
    import sys

    import qrcode

    import castor.up as up
    from castor.pairing import compact_payload_json

    _stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "testbot"
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False,
              link=False)

    assert not (home / "pair-link.txt").exists()
    payload = json.loads((home / "pair-payload.json").read_text())
    reference = tmp_path / "ref.png"
    qrcode.make(compact_payload_json(payload)).save(str(reference))
    assert (home / "pair-qr.png").read_bytes() == reference.read_bytes()


# ---------------------------------------------------------------------------
# Gaps — missing pieces as data, never self-closing
# ---------------------------------------------------------------------------


def test_gaps_are_written_as_structured_data(tmp_path):
    from castor.gaps import Gap, write
    import json

    gap = Gap(id="x", kind="missing-package", evidence="e", suggestion="s")
    path = write([gap], tmp_path)
    data = json.loads(path.read_text())
    assert data["gaps"][0]["kind"] == "missing-package"
    assert data["v"] == 1


def test_a_closed_gap_disappears_on_rewrite(tmp_path):
    # Rewritten whole each run: plugging the missing package in must make the
    # gap vanish, not linger as stale advice.
    from castor.gaps import Gap, write
    import json

    write([Gap(id="x", kind="missing-package", evidence="e", suggestion="s")], tmp_path)
    write([], tmp_path)
    assert json.loads((tmp_path / "gaps.json").read_text())["gaps"] == []


def test_collect_survives_a_bare_host(tmp_path, monkeypatch):
    # No bus, no ollama, no manifest: gaps degrade to "fewer gaps", never to a
    # crash — `up` must succeed on the barest machine.
    from castor import gaps as gaps_mod

    monkeypatch.setattr("castor.up.detect_brain", lambda: ("ollama", ""))
    result = gaps_mod.collect(home=tmp_path / "nonexistent")
    assert isinstance(result, list)
    assert any(g.kind == "no-brain" for g in result)


def test_no_gap_carries_an_imperative_to_an_ai():
    # The suggestion field speaks to the OPERATOR. The rail's consent model
    # (docs/SKILL-GAPS.md) has drafting happen only after a human allows it,
    # so a gap must never be phrased as an instruction an agent should follow
    # on sight.
    from castor.gaps import Gap

    g = Gap(id="x", kind="unclaimed-peripheral", evidence="e",
            suggestion="declare a capability in ROBOT.md (operator-signed)")
    assert "operator" in g.suggestion
