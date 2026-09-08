"""castor up — the ten-minute bring-up, tested at its seams.

Every test here pins something the first LIVE run of `up` got wrong on the
bench, which is the strongest argument for their existence.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

import castor.up as up
from castor.up import (
    DRIVE_VARS,
    UpPlan,
    apply_real_wheels,
    decide_real_wheels,
    derive_identity,
    pick_archetype,
    policy_names_real_wheels,
    real_wheels_question,
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


def test_THERULE_generated_policy_drives_simulated_wheels_unless_asked():
    # The rule, restated. The template was cut from a live robot AFTER its
    # wheels went real, and quietly carried that decision to every future robot:
    # the first scratch bring-up constructed PCA9685Drive out of the box. The
    # rule is not "the drive lines are always commented" — that made the flip a
    # file nobody mentions — it is that NOTHING BUT AN EXPLICIT ANSWER can
    # produce a config that moves hardware. UpPlan.real_wheels defaults False,
    # so every path that does not carry an answer renders the safe file.
    text = render("gateway-policy.env.tmpl", plan())
    live = [l for l in text.splitlines() if l.startswith("OPENCASTOR_DRIVE")]
    assert live == [], f"template enables real drive by default: {live}"


def test_an_answered_yes_renders_the_drive_block_live():
    text = render("gateway-policy.env.tmpl", plan(real_wheels=True))
    live = [l.split("=")[0] for l in text.splitlines() if l.startswith("OPENCASTOR_DRIVE")]
    assert live == list(DRIVE_VARS)
    assert "OPENCASTOR_DRIVE=pca9685" in text.splitlines()


def test_THEBUG_template_channels_match_both_real_vehicles():
    # The template shipped throttle 0 / steering 1. The live rover runs throttle
    # 1 / steering 0 and keeps a backup file named `.bak-channelswap` to prove
    # what that cost; carbot agrees with the rover. A cross-plugged harness is
    # invisible to every bench test ("the steering command produced a pulse on
    # the steering channel" is true whatever is in that pin), so the default has
    # to be the one both real cars actually use.
    text = render("gateway-policy.env.tmpl", plan(real_wheels=True))
    assert "OPENCASTOR_DRIVE_THROTTLE_CHANNEL=1" in text.splitlines()
    assert "OPENCASTOR_DRIVE_STEERING_CHANNEL=0" in text.splitlines()


def test_the_policy_template_points_at_a_checklist_that_exists():
    # `gateway-policy.env.tmpl` used to say "the PCA9685 bring-up checklist" with
    # no such file in the repository — a dead reference at the exact moment the
    # user needs it, since this is the file they are editing to make the car move.
    text = render("gateway-policy.env.tmpl", plan())
    assert "docs/hardware/pca9685-bringup.md" in text
    doc = Path(__file__).resolve().parents[1] / "docs" / "hardware" / "pca9685-bringup.md"
    assert doc.is_file(), "the checklist the template names must be in the repo"


def test_the_template_does_not_leak_the_donor_robots_name():
    text = render("gateway-policy.env.tmpl", plan())
    assert "Rover" not in text and "rover" not in text


# ---------------------------------------------------------------------------
# The one question: real wheels
# ---------------------------------------------------------------------------


def test_an_explicit_flag_beats_everything_including_a_bare_bus():
    # --real-wheels on a host where the chip has not answered is honoured (it
    # may be unpowered while the operator wires it) — `up` warns, the gateway
    # refuses to start until the board is there.
    assert decide_real_wheels(requested=True, detected_pwm=False, interactive=False)[0] is True
    assert decide_real_wheels(requested=False, detected_pwm=True, interactive=True)[0] is False


def test_no_chip_means_no_question_and_no_real_wheels():
    asked = []
    choice, why = decide_real_wheels(
        requested=None, detected_pwm=False, interactive=True,
        ask=lambda prompt: asked.append(prompt) or "y",
    )
    assert choice is False and asked == [], "nothing to drive, nothing to ask"
    assert "no PWM controller" in why


def test_THERULE_a_machine_that_cannot_be_asked_never_says_yes():
    # The image's firstboot runs `up` from a unit with no stdin. Reading a
    # prompt from EOF, or treating "could not ask" as consent, would make every
    # flashed card a car that can move on first boot.
    choice, why = decide_real_wheels(requested=None, detected_pwm=True, interactive=False)
    assert choice is False
    assert "--real-wheels" in why, "and it must say how to change that"


@pytest.mark.parametrize("answer,expected", [
    ("y", True), ("Y", True), ("yes", True), (" YES \n", True),
    ("", False), ("n", False), ("no", False), ("sure", False), ("1", False),
])
def test_only_an_actual_yes_is_a_yes(answer, expected):
    choice, _ = decide_real_wheels(
        requested=None, detected_pwm=True, interactive=True, ask=lambda _: answer,
    )
    assert choice is expected


def test_the_question_names_the_chip_that_was_actually_found():
    # The evidence for the question has to travel with the question: a prompt
    # that says "a PCA9685" reads as boilerplate, one that quotes the bus scan
    # is the machine telling you what it saw.
    seen = []
    decide_real_wheels(
        requested=None, detected_pwm=True, interactive=True,
        detected=["PCA9685 PWM controller at 0x40 (i2c)"],
        ask=lambda prompt: seen.append(prompt) or "n",
    )
    assert "0x40" in seen[0]


def test_the_question_carries_the_wheels_off_the_ground_warning():
    q = real_wheels_question(["PCA9685 PWM controller at 0x40 (i2c)"])
    assert "WHEELS OFF THE GROUND" in q
    assert "docs/hardware/pca9685-bringup.md" in q
    assert q.rstrip().endswith("[y/N]"), "the default must read as No"


# ---------------------------------------------------------------------------
# Flipping an ALREADY-WRITTEN policy file
# ---------------------------------------------------------------------------


LIVE_POLICY = (
    'ROBOT_MD_TOOL_ALLOWLIST="drive.set,drive.stop"\n'
    "#OPENCASTOR_DRIVE=pca9685\n"
    "#OPENCASTOR_DRIVE_I2C_BUS=1\n"
    "#OPENCASTOR_DRIVE_I2C_ADDRESS=0x40\n"
    "#OPENCASTOR_DRIVE_THROTTLE_CHANNEL=1\n"
    "#OPENCASTOR_DRIVE_STEERING_CHANNEL=0\n"
    "OPENCASTOR_DRIVE_THROTTLE_NEUTRAL_US=1487\n"
)


def test_flipping_the_drive_block_touches_nothing_else():
    # gateway-policy.env carries hand-MEASURED trims. A flip that rewrote the
    # file from the template would silently discard an afternoon on a stand.
    on = apply_real_wheels(LIVE_POLICY, True)
    assert "OPENCASTOR_DRIVE_THROTTLE_NEUTRAL_US=1487" in on, "measured trim survived"
    assert 'ROBOT_MD_TOOL_ALLOWLIST="drive.set,drive.stop"' in on
    assert apply_real_wheels(on, False) == LIVE_POLICY, "and the flip is reversible"


def test_enabling_replaces_an_explicit_simulated_rather_than_uncommenting_it():
    # A robot whose file says OPENCASTOR_DRIVE=simulated out loud: uncommenting
    # is not enough, the value itself is the switch.
    on = apply_real_wheels("OPENCASTOR_DRIVE=simulated\n", True)
    assert on == "OPENCASTOR_DRIVE=pca9685\n"


def test_enabling_leaves_another_real_backend_alone():
    # `maestro` is also real wheels, and is chosen for its hardware failsafe.
    # --real-wheels must not overrule a controller somebody picked on purpose.
    assert apply_real_wheels("#OPENCASTOR_DRIVE=maestro\n", True) == "OPENCASTOR_DRIVE=maestro\n"


def test_up_reports_what_the_gateway_will_read_not_what_it_wrote_last_time():
    assert policy_names_real_wheels(apply_real_wheels(LIVE_POLICY, True)) is True
    assert policy_names_real_wheels(LIVE_POLICY) is False
    assert policy_names_real_wheels("OPENCASTOR_DRIVE=simulated\n") is False


# ---------------------------------------------------------------------------
# The manifest's own contract (trap 7): a schema that cannot draft a stop
# ---------------------------------------------------------------------------


def _contract(tool: str) -> dict:
    import yaml

    front = render("ROBOT.md.tmpl", plan()).split("---")[1]
    return yaml.safe_load(front)["capability_contracts"][tool]


def test_THETRAP_drive_set_duration_has_no_default_a_model_could_copy():
    # The generated manifest is the document a drafting model reads. It used to
    # say `duration_s: {kind: float, default: 0}` while its own prose fifty
    # lines later promised 400 ms — and 0 is a zero-length lease, which the
    # actuator treats as a STOP. A model that filled in the schema default wrote
    # a stop and reported a drive. There is no safe default for this field, so
    # it has none, and it is required instead.
    duration = _contract("drive.set")["args"]["duration_s"]
    assert "default" not in duration, "a default here is a default STOP"
    assert duration["required"] is True


def test_the_manifest_prose_and_the_schema_agree_about_duration():
    text = render("ROBOT.md.tmpl", plan())
    assert "**required, and has no default**" in text
    assert "A command that states no duration gets 400 ms." not in text, (
        "the old prose contradicted the schema; both now say the same thing"
    )


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
                          "testbot-console.service", "testbot-rrf-stub.service",
                          "testbot-discovery.service"}
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


def test_THEFIFTHUNIT_up_writes_an_advertiser_and_it_is_mandatory():
    # `up` wrote four units and no advertiser, so nothing it produced was ever
    # findable on the network. The unit that fixes it must also not be able to
    # start empty: `EnvironmentFile=-` would leave a service reporting
    # "active (running)" while publishing nothing, which is the same failure in
    # a costume the operator cannot see through.
    units = unit_files(plan(), python="/venv/bin/python",
                       gateway_bin="/venv/bin/robot-md-gateway")
    unit = units["testbot-discovery.service"]
    assert "EnvironmentFile=/home/pi/testbot/discovery.env" in unit
    assert "EnvironmentFile=-" not in unit
    assert "ExecStart=/venv/bin/python -m castor.discovery" in unit
    assert "WantedBy=default.target" in unit
    # An absent zeroconf is not fixable by restarting; looping on it every five
    # seconds buries the one log line that says what to install.
    from castor.discovery import EX_UNFIXABLE

    assert f"RestartPreventExitStatus={EX_UNFIXABLE}" in unit


def test_the_advertiser_env_carries_every_key_the_record_needs(tmp_path):
    from castor.discovery import record_from_env

    p = plan(home=tmp_path, base_port=8000)  # any base; the record must follow it
    env = dict(
        line.split("=", 1)
        for line in up.discovery_env(p).splitlines()
        if line and not line.startswith("#")
    )
    # Parsed back through the real reader: a key renamed on either side fails
    # here rather than on a LAN nobody is watching.
    got = record_from_env(env)
    assert got.rrn == p.rrn
    assert got.name == "testbot" or got.name == tmp_path.name
    assert (got.gateway_port, got.castor_port, got.console_port) == (8000, 8001, 8002)
    assert got.manifest_path == str(tmp_path / "ROBOT.md")


def test_the_advertiser_env_holds_no_credential():
    # The record answers "where is this RRN now?" and nothing else. Pairing
    # still happens through the QR, and this file is world-readable by design.
    text = up.discovery_env(plan())
    for secret in ("TOKEN", "BEARER", "KEY", "SECRET"):
        assert secret not in text.upper().replace("ROBOT_RRN", "")


def test_the_runtime_unit_knows_the_console_port_so_its_own_record_is_whole():
    # The runtime publishes the same record from its own process (enable_mdns).
    # Without this it would guess the console port and advertise a wrong one on
    # any robot not using the default base.
    unit = unit_files(plan(base_port=8300), python="/venv/bin/python",
                      gateway_bin="/gw")["testbot-castor.service"]
    assert "Environment=ROBOT_CONSOLE_PORT=8302" in unit


# ---------------------------------------------------------------------------
# Ports — the half of "cannot find the robot" that mDNS does not fix
# ---------------------------------------------------------------------------


def test_THEDEFAULT_base_port_did_not_move_and_the_app_owns_the_sweep():
    # The mDNS-blocked fallback sweeps a fixed list of ports, and it did not
    # include any of these three, which is the second half of "cannot find the
    # robot". It was fixed on the app side — CastorKit's `RobotPorts` derives
    # the sweep from `upBases = [8080, 8110]` through this file's base + 0/1/2
    # layout — because no single default here can cover BOTH robots on a host.
    # This test exists so that a later "helpful" nudge of the default has to
    # go and change the Swift that cites it.
    assert up.DEFAULT_BASE_PORT == 8080
    assert up.SECOND_BASE_PORT == 8110
    p = plan(base_port=up.DEFAULT_BASE_PORT)
    assert (p.gateway_port, p.runtime_port, p.console_port) == (8080, 8081, 8082)


def test_an_existing_robot_keeps_the_ports_its_QR_pinned():
    # Reuse-don't-refuse, the contract identity already follows: moving the
    # default must not relocate the services behind a QR somebody scanned.
    assert up.resolve_base_port(None, 8080) == 8080
    assert up.resolve_base_port(None, None) == up.DEFAULT_BASE_PORT
    assert up.resolve_base_port(8110, 8080) == 8110, "an explicit flag still wins"


def test_a_new_robot_notices_a_port_that_is_already_taken():
    assert up.occupied_ports(8000, lambda port: port == 8001) == [8001]
    assert up.occupied_ports(8000, lambda port: False) == []


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


def test_up_on_a_bare_host_writes_a_policy_that_cannot_move(tmp_path, monkeypatch):
    import sys

    import castor.up as up

    _stub_the_host(monkeypatch, tmp_path)  # scan_i2c returns []
    home = tmp_path / "testbot"
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False)
    policy = (home / "gateway-policy.env").read_text()
    assert [l for l in policy.splitlines() if l.startswith("OPENCASTOR_DRIVE")] == []


def test_up_real_wheels_writes_the_drive_block_live(tmp_path, monkeypatch):
    import sys

    import castor.up as up

    _stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "testbot"
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False,
              real_wheels=True)
    policy = (home / "gateway-policy.env").read_text()
    assert "OPENCASTOR_DRIVE=pca9685" in policy.splitlines()
    assert "OPENCASTOR_DRIVE_THROTTLE_CHANNEL=1" in policy.splitlines()


def test_a_rerun_never_re_asks_and_never_reverses_the_decision(tmp_path, monkeypatch):
    # Idempotence, and it is a safety property in both directions: a rerun must
    # not turn a simulated robot real, and must not turn a trimmed real robot
    # back to simulated behind the operator's back.
    import sys

    import castor.up as up

    _stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "testbot"
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False,
              real_wheels=True)
    (home / "gateway-policy.env").write_text(
        (home / "gateway-policy.env").read_text() + "\n# hand note\n"
    )
    monkeypatch.setattr(up, "_stdin_is_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("a rerun must not ask"))
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False)
    policy = (home / "gateway-policy.env").read_text()
    assert "OPENCASTOR_DRIVE=pca9685" in policy.splitlines(), "decision survived the rerun"
    assert "# hand note" in policy, "and so did the hand edit"


def test_an_explicit_flag_flips_an_existing_robot(tmp_path, monkeypatch):
    # The answer to "I already ran up, how do I turn the wheels on now?" has to
    # be a command, not "open a file nobody told you about".
    import sys

    import castor.up as up

    _stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "testbot"
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False)
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False,
              real_wheels=True)
    assert "OPENCASTOR_DRIVE=pca9685" in (home / "gateway-policy.env").read_text().splitlines()
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False,
              real_wheels=False)
    live = [l for l in (home / "gateway-policy.env").read_text().splitlines()
            if l.startswith("OPENCASTOR_DRIVE=")]
    assert live == [], "--simulated-wheels puts it back"


def test_up_prints_the_wheels_off_the_ground_warning_when_it_goes_real(
    tmp_path, monkeypatch, capsys
):
    import sys

    import castor.up as up

    _stub_the_host(monkeypatch, tmp_path)
    up.run_up(home=tmp_path / "testbot", base_port=8300, python=sys.executable,
              start_services=False, real_wheels=True)
    out = capsys.readouterr().out
    assert "WHEELS OFF THE GROUND" in out
    assert "pca9685-bringup.md" in out


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
