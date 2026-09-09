"""`castor up --archetype microduck` — the sixth unit, the token, and the hello.

Every test here pins something that would otherwise fail silently on a robot
nobody in this project has on the desk. The Microduck review's finding was that
`castor duck` had never been run against a real robotd and four wire keys were
wrong for weeks; the answer to that is not more care, it is more fixtures.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
import threading
from pathlib import Path

import pytest

import castor.up as up
from castor.up import (
    BRIDGE_TOKEN_FILE,
    DUCKBRIDGE_ENV,
    MICRODUCK,
    UpPlan,
    detect_microduck,
    duckbridge_env,
    pick_archetype,
    render,
    resolve_actuator,
    unit_files,
)


def plan(**over) -> UpPlan:
    defaults = dict(
        name="huey",
        home=Path("/home/pi/huey"),
        archetype=MICRODUCK,
        rrn="RRN-LOCAL-abc123",
        robot_uuid="u-1",
        base_port=8080,
        duck_host="192.168.1.42",
        duck_user="radxa",
        bridge_token_file="/home/pi/.microduck-bridge-token",
    )
    defaults.update(over)
    return UpPlan(**defaults)


# ---------------------------------------------------------------------------
# Detection — over the network, never a bus
# ---------------------------------------------------------------------------


def test_a_duck_beats_a_pwm_chip_on_the_same_host():
    """A Pi with a PWM hat AND a duck on the network is a Pi pointed at the duck.

    The two pieces of evidence are not comparable: 0x40 is a chip that MIGHT be
    wired to a vehicle; a robotd answering is a whole robot already standing
    there. There is no reading of `castor up --host <duck>` that means
    "make an rc-car".
    """
    archetype, found = pick_archetype({0x40}, duck_evidence="microduck bridge at duck:7788")
    assert archetype == MICRODUCK
    assert found == ["microduck bridge at duck:7788"]


def test_without_duck_evidence_nothing_about_the_bus_scan_changed():
    assert pick_archetype({0x40, 0x36})[0] == "rc-car"
    assert pick_archetype(set())[0] == "sim"
    assert pick_archetype(set(), duck_evidence=None)[0] == "sim"


def test_a_local_robotd_socket_means_this_machine_is_the_duck():
    found = detect_microduck(
        None, exists=lambda p: p == "/run/robotd.sock", probe=lambda h, p: False
    )
    assert found is not None and "this machine is the duck" in found


def test_a_bridge_answering_on_7788_is_the_evidence():
    found = detect_microduck(
        "192.168.1.42", exists=lambda p: False, probe=lambda h, p: (h, p) == ("192.168.1.42", 7788)
    )
    assert found == "microduck bridge answering at 192.168.1.42:7788"


def test_the_hostname_ladder_is_the_drivers_own_not_a_second_list():
    """One list of names, in castor/microduck.py, used by both.

    A second copy here is how `duck-01.local` ends up in one file and not the
    other, and the symptom is "nothing found" on a duck two metres away.
    """
    from castor.microduck import CANDIDATE_HOSTNAMES

    tried: list[str] = []

    def probe(host, port):
        tried.append(host)
        return host == CANDIDATE_HOSTNAMES[-1]

    found = detect_microduck(None, exists=lambda p: False, probe=probe)
    assert tried == list(CANDIDATE_HOSTNAMES)
    assert found is not None and CANDIDATE_HOSTNAMES[-1] in found


def test_a_host_that_answers_nothing_is_still_a_duck_and_says_which():
    """`--host` is a duck-only flag, so a duck that is merely switched off must
    still be configurable — and the line must not claim something answered."""
    found = detect_microduck("10.0.0.9", exists=lambda p: False, probe=lambda h, p: False)
    assert found == "--host 10.0.0.9 (nothing answered on 7788 yet)"


def test_no_duck_anywhere_is_none_not_an_error():
    assert detect_microduck(None, exists=lambda p: False, probe=lambda h, p: False) is None


# ---------------------------------------------------------------------------
# The six units
# ---------------------------------------------------------------------------


def test_a_duck_gets_six_units_and_a_car_still_gets_five():
    duck = unit_files(plan(), python="/usr/bin/python3", gateway_bin="/usr/bin/robot-md-gateway")
    car = unit_files(
        plan(archetype="rc-car"), python="/usr/bin/python3", gateway_bin="/usr/bin/rmg"
    )
    assert set(duck) - set(car) == {"huey-duckbridge.service"}
    assert sorted(duck) == [
        "huey-castor.service",
        "huey-console.service",
        "huey-discovery.service",
        "huey-duckbridge.service",
        "huey-gateway.service",
        "huey-rrf-stub.service",
    ]


def test_THEUNIT_the_bridge_unit_cannot_start_without_its_settings():
    """EnvironmentFile with NO leading dash.

    A `-` would make duckbridge.env optional, and a bridge started with no
    settings binds 0.0.0.0:7788 pointed at a /run/robotd.sock that is not there
    — a service that reports `active (running)` and relays nothing. That is the
    exact shape of the failure this unit exists to end.
    """
    unit = unit_files(plan(), python="/usr/bin/python3", gateway_bin="/x")[
        "huey-duckbridge.service"
    ]
    assert "EnvironmentFile=/home/pi/huey/duckbridge.env" in unit
    assert "EnvironmentFile=-" not in unit
    assert "ExecStart=/usr/bin/python3 -m castor.microduck_bridge" in unit
    # Restart=always, unlike the gateway's on-failure: the deadman cannot act if
    # this process dies, so a bridge that exited because the duck was rebooting
    # must come back when the duck does.
    assert "Restart=always" in unit
    # ...but not on the bridge's own refusals (exit 2: no token, a token file
    # others can read, an ssh forward that will not come up). None of those is
    # fixed by trying again in five seconds, and looping buries the one line
    # that says what to do.
    assert "RestartPreventExitStatus=2" in unit


def test_the_bridge_unit_carries_no_token_and_no_command_line_settings():
    unit = unit_files(plan(), python="/usr/bin/python3", gateway_bin="/x")[
        "huey-duckbridge.service"
    ]
    assert "--token" not in unit and "--deadman" not in unit and "--ssh" not in unit
    assert "MICRODUCK_BRIDGE" not in unit, "settings live in the EnvironmentFile"


# ---------------------------------------------------------------------------
# duckbridge.env
# ---------------------------------------------------------------------------


def test_THESECRET_the_env_file_holds_the_path_to_the_token_and_never_the_token(tmp_path):
    """A robot home somebody tars up for a bug report must carry no secret out."""
    text = duckbridge_env(plan())
    assert "MICRODUCK_BRIDGE_TOKEN_FILE=/home/pi/.microduck-bridge-token" in text
    assert "MICRODUCK_BRIDGE_TOKEN=" not in text
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        assert "TOKEN=" not in line or line.endswith("-token"), line


def test_a_networked_duck_gets_an_ssh_forward_and_a_local_one_does_not():
    networked = duckbridge_env(plan())
    assert "MICRODUCK_BRIDGE_SSH=radxa@192.168.1.42" in networked
    # The forward's local end is NOT the listen port: forwarding onto our own
    # listener is a loop that looks like a hang.
    assert "MICRODUCK_BRIDGE_FORWARD_PORT=7789" in networked
    assert "MICRODUCK_BRIDGE_PORT=7788" in networked

    local = duckbridge_env(plan(duck_host=None, duck_user=None))
    assert "MICRODUCK_BRIDGE_SSH=" not in local
    assert "FORWARD_PORT" not in local


def test_the_env_file_states_all_three_deadmen_not_only_its_own():
    """700 ms means nothing without 1500 and 500 written next to it."""
    text = duckbridge_env(plan())
    assert "MICRODUCK_BRIDGE_DEADMAN_MS=700" in text
    assert "1500 ms" in text and "500 ms" in text
    assert "robotd" in text


def test_policy_install_is_off_unless_somebody_says_where():
    text = duckbridge_env(plan())
    assert "#MICRODUCK_BRIDGE_POLICY_DIR=" in text
    live = [ln for ln in text.splitlines() if ln.startswith("MICRODUCK_BRIDGE_POLICY_DIR")]
    assert live == [], "a phone that can write to the robot's disk is a deliberate act"


# ---------------------------------------------------------------------------
# The templates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template", ["ROBOT.md.tmpl", "robot.rcan.yaml.tmpl", "gateway-policy.env.tmpl",
                 "runtime.py.tmpl"]
)
def test_every_duck_template_renders_with_no_placeholder_left(template):
    text = render(template, plan())
    for token in ("{name}", "{rrn}", "{uuid}", "{bridge_port}", "{bridge_token_file}",
                  "{duck_socket}", "{duck_host}", "{bridge_host}", "{connection_type}",
                  "{port_runtime}"):
        assert token not in text, f"{template} left {token} unfilled"


def test_THELINK_the_generated_config_dials_the_bridge_not_the_duck():
    """127.0.0.1:7788, and it looks wrong until you see why.

    The driver dials the BRIDGE, and the bridge is on this machine holding one
    connection to the duck. Two processes dialling the duck directly is the
    two-relays problem this archetype exists to end: two deadmen with different
    numbers, two tokens, and a duck driven by two clients neither of which
    knows the other exists.
    """
    import yaml

    config = yaml.safe_load(render("robot.rcan.yaml.tmpl", plan()))
    duck = config["drivers"][0]
    assert duck["protocol"] == "microduck"
    assert duck["transport"] == "tcp"
    assert (duck["host"], duck["port"]) == ("127.0.0.1", 7788)
    assert duck["bridge_token_file"] == "/home/pi/.microduck-bridge-token"
    assert "bridge_token" not in duck, "the token itself never lands in a config"
    assert duck["auto_init"] is False, "a service restart must not stand the duck up"


def test_THEHARNESS_a_generated_duck_can_be_sequenced_not_only_chatted_at():
    """Fix 5 of the review, made a default rather than a sentence.

    `duck_vocabulary` and `duck_perform` register inside a block gated on
    `agent.harness.enabled`, which no shipped duck profile set — so the guide's
    claim that they register "automatically whenever a Microduck is the
    attached robot" was true of the function and false of the product.
    """
    import yaml

    config = yaml.safe_load(render("robot.rcan.yaml.tmpl", plan()))
    assert config["agent"]["harness"]["enabled"] is True


def test_THEHONESTY_the_duck_policy_names_no_motion_tool():
    """A `duck.move` on this gateway would be accepted, signed, receipted and
    delivered to nothing: there is no duck actuator. That is trap 7 in a third
    costume, so it is not in the allowlist and the file says why."""
    text = render("gateway-policy.env.tmpl", plan())
    allowlist = [ln for ln in text.splitlines() if ln.startswith("ROBOT_MD_TOOL_ALLOWLIST")][0]
    assert "duck.move" not in allowlist
    assert "drive.set" not in allowlist
    assert "duck.stop" in allowlist and "status.report" in allowlist
    # A stop that a privilege check can refuse is not a stop.
    tiers = [ln for ln in text.splitlines() if ln.startswith("ROBOT_MD_TOOL_MIN_TIER")][0]
    assert "duck.stop:read|" in tiers


def test_the_manifest_says_the_duck_is_real_and_the_link_is_not_yet():
    text = render("ROBOT.md.tmpl", plan())
    assert "hardware_present: true" in text, "a Microduck out of its box already walks"
    assert "huey-duckbridge.service" in text
    # The three deadmen, named with their numbers.
    assert "lease_timeout_ms: 1500" in text
    assert "relay_timeout_ms: 700" in text
    assert "firmware_lease_ms: 500" in text
    # And the one thing OpenCastor does not control.
    assert "mediad" in text and "0.0.0.0:8443" in text


def test_the_duck_runtime_refuses_the_three_states_that_look_healthy():
    text = render("runtime.py.tmpl", plan())
    assert "OPENCASTOR_API_TOKEN" in text
    assert "OPENCASTOR_DUCK_REQUIRE_HARDWARE" in text
    assert "_mode" in text, "mock mode is the failure this file exists to report"
    # And it wraps the lifespan rather than registering an on_event handler that
    # a lifespan-configured app would silently never run.
    assert "lifespan_context" in text
    assert '@app.on_event("startup")' not in [ln.strip() for ln in text.splitlines()]


def test_no_duck_actuator_means_noop_and_a_sentence():
    name, note = resolve_actuator(MICRODUCK)
    assert name == "noop"
    assert "duckbridge" in note and "robotd" in note


# ---------------------------------------------------------------------------
# The whole command, on a stubbed host
# ---------------------------------------------------------------------------


def _stub_the_host(monkeypatch, tmp_path) -> None:
    """Let `up` run for real without touching this machine.

    Twin of the helper in test_up.py: HOME is redirected so systemd units land
    in the scratch tree, the bus scan and port probes are stubbed, and gaps
    collection is skipped. Nothing here reaches the operator's real systemd
    session, real I2C bus, or any duck.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(up, "detect_brain", lambda: ("ollama", ""))
    monkeypatch.setattr(up, "_port_answers", lambda port: False)
    monkeypatch.setattr(up, "_tcp_answers", lambda host, port, timeout=0.4: False)
    monkeypatch.setattr(up, "_path_exists", lambda path: False)
    monkeypatch.setattr("castor.peripherals.scan_i2c", lambda: [])
    monkeypatch.setattr("castor.gaps.collect", lambda **kwargs: [])


def test_up_on_a_duck_writes_six_units_and_never_starts_one(tmp_path, monkeypatch):
    _stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "huey"
    result = up.run_up(
        home=home, base_port=8300, python=sys.executable, start_services=False,
        archetype=MICRODUCK, host="192.168.1.42", user="radxa",
    )
    assert result.archetype == MICRODUCK
    units = sorted(p.name for p in (tmp_path / ".config" / "systemd" / "user").glob("huey-*"))
    assert units == [
        "huey-castor.service",
        "huey-console.service",
        "huey-discovery.service",
        "huey-duckbridge.service",
        "huey-gateway.service",
        "huey-rrf-stub.service",
    ]
    assert (home / DUCKBRIDGE_ENV).is_file()
    assert (home / "ROBOT.md").is_file()
    assert (home / "robot.rcan.yaml").is_file()


def test_THEMODE_up_mints_the_token_0600_and_a_rerun_reuses_it(tmp_path, monkeypatch, capsys):
    """It was typed into a phone. A rerun that rotated it would unpair the app
    in silence, and the person holding it would blame the robot."""
    _stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "huey"
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False,
              archetype=MICRODUCK, host="192.168.1.42")
    token_file = Path(BRIDGE_TOKEN_FILE.replace("~", str(tmp_path)))
    assert token_file.is_file()
    assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o600
    first = token_file.read_text().strip()
    assert first in capsys.readouterr().out, "the owner has to be able to type it into the app"

    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False,
              archetype=MICRODUCK, host="192.168.1.42")
    assert token_file.read_text().strip() == first
    assert "reused" in capsys.readouterr().out


def test_up_on_a_duck_never_writes_a_pwm_block(tmp_path, monkeypatch):
    """--real-wheels on a duck must not name a chip that belongs to no one here.

    A Microduck's fifteen servos are on `/dev/ttyS2` and belong to robotd on the
    duck's own computer. There is no PCA9685 anywhere in this robot.
    """
    _stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "huey"
    result = up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False,
                       archetype=MICRODUCK, host="192.168.1.42", real_wheels=True)
    assert result.real_wheels is False
    text = (home / "gateway-policy.env").read_text()
    assert "OPENCASTOR_DRIVE=pca9685" not in text
    assert "PCA9685" not in text


def test_up_records_the_duck_so_a_rerun_does_not_lose_it(tmp_path, monkeypatch):
    _stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "huey"
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False,
              archetype=MICRODUCK, host="192.168.1.42", user="radxa")
    state = json.loads((home / ".castor-up.json").read_text())
    assert state["archetype"] == MICRODUCK
    assert state["duck_host"] == "192.168.1.42"
    assert state["duck_user"] == "radxa"


def test_a_bare_host_with_no_duck_is_still_sim(tmp_path, monkeypatch):
    """The new probe must not turn every empty-bus host into a duck."""
    _stub_the_host(monkeypatch, tmp_path)
    result = up.run_up(home=tmp_path / "plain", base_port=8300, python=sys.executable,
                       start_services=False)
    assert result.archetype == "sim"
    assert not (tmp_path / ".config" / "systemd" / "user" / "plain-duckbridge.service").exists()


# ---------------------------------------------------------------------------
# The driver's one new line, end to end through the real bridge
# ---------------------------------------------------------------------------


class _MockRobotd:
    def __init__(self, path):
        self.path = path
        self.lines: list[str] = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(2)
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._talk, args=(conn,), daemon=True).start()

    def _talk(self, conn):
        buf = b""
        while self.running:
            try:
                chunk = conn.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.lines.append(line.decode())
                try:
                    call = json.loads(line)
                except ValueError:
                    continue
                if call.get("id") is None:
                    continue
                conn.sendall(
                    json.dumps({"jsonrpc": "2.0", "id": call["id"],
                                "result": {"accepted": True}}).encode() + b"\n"
                )

    def close(self):
        self.running = False
        self.server.close()


@pytest.fixture()
def bridged_duck(tmp_path):
    """A real bridge in front of a robotd-shaped socket, and its token file."""
    from castor import microduck_bridge as mb

    socket_path = str(tmp_path / "robotd.sock")
    robotd = _MockRobotd(socket_path)
    token_file = tmp_path / "token"
    token, _ = mb.mint_token(str(token_file))
    ready = threading.Event()
    port = {}

    def note(p):
        port["n"] = p
        ready.set()

    threading.Thread(
        target=mb.serve,
        args=("127.0.0.1", 0, socket_path, token, 5000),
        kwargs={"log": lambda *a: None, "ready": note},
        daemon=True,
    ).start()
    assert ready.wait(5)
    try:
        yield robotd, port["n"], str(token_file), token
    finally:
        robotd.close()


def test_THEHELLO_the_driver_reaches_hardware_mode_through_the_bridge(bridged_duck):
    """The whole of fix 8, in one assertion.

    `transport: tcp` already pointed at 7788 — the driver's `local_port`
    default, the bridge's bind port and StudioKit's `BridgeHandshake.defaultPort`
    agreed before anybody connected them. The only thing missing was the first
    line.
    """
    from castor.drivers.microduck_driver import MicroduckDriver

    robotd, port, token_file, _token = bridged_duck
    driver = MicroduckDriver(
        {"transport": "tcp", "host": "127.0.0.1", "port": port, "bridge_token_file": token_file}
    )
    try:
        assert driver._mode == "hardware", "mock mode is a fail, never a pass"
        # And the vocabulary crossed the relay untouched: the driver's own
        # robot.subscribe is on the wire the mock recorded.
        assert any("robot.subscribe" in line for line in robotd.lines)
    finally:
        driver.close()


def test_a_wrong_token_leaves_the_driver_in_mock_mode_rather_than_half_open(bridged_duck, tmp_path):
    from castor.drivers.microduck_driver import MicroduckDriver

    _robotd, port, _token_file, _token = bridged_duck
    wrong = tmp_path / "wrong-token"
    wrong.write_text("ffffffffffffffffffffffffffffffff")
    wrong.chmod(0o600)
    driver = MicroduckDriver(
        {"transport": "tcp", "host": "127.0.0.1", "port": port, "bridge_token_file": str(wrong)}
    )
    try:
        assert driver._mode == "mock"
    finally:
        driver.close()


def test_THECOMPAT_a_plain_forward_with_no_token_still_works(tmp_path, monkeypatch):
    """`transport: tcp` at a raw `ssh -L` or `socat` has worked for as long as
    this driver has existed. A hello line would be the first thing that broke
    it, so no token configured and none on disk means no hello."""
    from castor.drivers.microduck_driver import MicroduckDriver

    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.microduck-bridge-token here
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    seen: list[bytes] = []
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def accept():
        conn, _ = listener.accept()
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        seen.append(buf)
        conn.sendall(b'{"jsonrpc":"2.0","id":1,"result":{"accepted":true}}\n')

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    driver = MicroduckDriver({"transport": "tcp", "host": "127.0.0.1", "port": port})
    try:
        thread.join(timeout=3)
        assert seen, "the driver connected and sent nothing at all"
        assert b'"microduck"' not in seen[0], "no token means no hello"
        assert b"robot.subscribe" in seen[0], "the first line is still the driver's own"
    finally:
        driver.close()
        listener.close()


def test_a_named_token_file_that_is_unusable_is_a_configuration_error(bridged_duck, tmp_path):
    """Naming a file explicitly and having it be unreadable is not a
    plain-relay target; it is a typo, and it must not degrade quietly."""
    from castor.drivers.microduck_driver import MicroduckDriver

    _robotd, port, _token_file, _token = bridged_duck
    driver = MicroduckDriver(
        {"transport": "tcp", "host": "127.0.0.1", "port": port,
         "bridge_token_file": str(tmp_path / "nope")}
    )
    try:
        assert driver._mode == "mock"
    finally:
        driver.close()
