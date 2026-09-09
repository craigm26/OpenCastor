"""`castor doctor`, for a duck.

Everything here runs against a FAKE robotd — a real AF_UNIX socket speaking
real NDJSON JSON-RPC, so the framing is exercised rather than mocked — plus
fakes for the two things a socket cannot answer: group membership and mediad's
ports. No hardware, no duck, no network.

The fixtures answer the wire keys `duck-ipc-proto` actually defines:
`control_loop` (not `loop`), `achieved_hz` (not `hz`), and `battery` on
`robot.health` (there is no battery on `robot.state` at all). A test written
against the keys OpenCastor used to read would pass on a robot that does not
exist.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest
import yaml

from castor import doctor

# ── A robotd-shaped socket ───────────────────────────────────────────────────

HEALTHY = {
    "healthy": True,
    "battery": {"volts": 7.6, "percent": 64.0},
    "control_loop": {
        "target_hz": 50.0,
        "achieved_hz": 49.8,
        "ticks": 2_000_000,
        "missed": 0,
        "last_tick_age_ms": 12,
    },
    "bus": {"consecutive_errors": 0},
}

POLICIES = {
    "mode": "walk",
    "enabled": True,
    "slots": [
        {"slot": "walk", "path": "alpha_walking.onnx", "origin": "official", "overridden": False},
        {"slot": "stand", "path": "alpha_stand.onnx", "origin": "official", "overridden": False},
        {"slot": "ground_pick", "path": None, "origin": None, "overridden": False},
    ],
    "skills": ["ball_kick_left", "ball_kick_right", "roulade"],
}


class FakeRobotd:
    """Answers robot.health and robot.policies on a unix socket, and nothing else."""

    def __init__(self, path, health=None, policies=None, drop=False):
        self.path = str(path)
        self.health = HEALTHY if health is None else health
        self.policies = POLICIES if policies is None else policies
        self.drop = drop
        self.seen: list[str] = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(4)
        self._srv.settimeout(5.0)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        if self.drop:
            conn.close()
            return
        buf = b""
        with conn:
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    call = json.loads(line)
                    method = call.get("method")
                    self.seen.append(method)
                    if call.get("id") is None:
                        continue
                    result = {"robot.health": self.health, "robot.policies": self.policies}.get(
                        method, {}
                    )
                    conn.sendall(
                        json.dumps({"jsonrpc": "2.0", "id": call["id"], "result": result}).encode()
                        + b"\n"
                    )

    def close(self):
        try:
            self._srv.close()
        except OSError:
            pass


def write_duck_config(directory, socket_path, harness=True, name="duck-01"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.rcan.yaml"
    path.write_text(
        yaml.dump(
            {
                "rcan_version": "3.0",
                "metadata": {"robot_name": name},
                "agent": {"provider": "anthropic", "harness": {"enabled": harness}},
                "drivers": [
                    {
                        "id": "duck",
                        "protocol": "microduck",
                        "transport": "unix",
                        "socket": str(socket_path),
                    }
                ],
                "connection": {"type": "wifi", "host": "192.168.1.42"},
            }
        )
    )
    return path


@pytest.fixture
def duck_home(tmp_path):
    return tmp_path / "config"


@pytest.fixture
def robotd(tmp_path):
    server = FakeRobotd(tmp_path / "robotd.sock")
    yield server
    server.close()


def no_ports(port, host="127.0.0.1", timeout=2.0):
    return False


def all_ports(port, host="127.0.0.1", timeout=2.0):
    return True


# ── Finding the duck at all ──────────────────────────────────────────────────


def test_no_duck_config_means_no_duck_section(tmp_path):
    """A car owner must never see a word about ducks."""
    report = doctor.run_duck_checks(config_dir=tmp_path / "empty")
    assert report.checks == []
    assert report.can_move is True


def test_a_car_config_is_not_a_duck(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    (d / "car.rcan.yaml").write_text(
        yaml.dump({"drivers": [{"id": "wheels", "protocol": "pca9685"}]})
    )
    assert doctor.find_duck_config(config_dir=d) is None
    assert doctor.run_duck_checks(config_dir=d).checks == []


def test_finds_the_duck_config_and_reads_its_transport(duck_home, robotd):
    write_duck_config(duck_home, robotd.path)
    target = doctor.find_duck_config(config_dir=duck_home)
    assert target is not None
    assert target.transport == "unix"
    assert target.socket_path == robotd.path
    assert target.harness_enabled is True
    assert target.probe_host == "192.168.1.42"


# ── The wire ─────────────────────────────────────────────────────────────────


def test_probe_reads_the_real_wire_keys(duck_home, robotd):
    write_duck_config(duck_home, robotd.path)
    target = doctor.find_duck_config(config_dir=duck_home)
    probe = doctor.probe_duck(target)
    assert probe["ok"] is True
    assert probe["health"]["control_loop"]["achieved_hz"] == 49.8
    assert probe["policies"]["slots"][0]["slot"] == "walk"
    # And it never opens the 50 Hz state stream to answer a health question.
    assert "robot.subscribe" not in robotd.seen
    assert set(robotd.seen) == {"robot.health", "robot.policies"}


def test_a_healthy_duck_can_walk(duck_home, robotd, monkeypatch):
    write_duck_config(duck_home, robotd.path)
    report = doctor.run_duck_checks(
        config_dir=duck_home, groups=["radxa", "robot"], probe_port=all_ports
    )
    names = {c.name: c for c in report.checks}
    assert names["robotd"].status == "ok"
    assert names["Duck drive mode"].status == "ok"
    assert names["Control loop"].status == "ok"
    assert "49.8 Hz of 50.0 Hz" in names["Control loop"].detail
    assert names["Policies"].status == "ok"
    assert "walk=alpha_walking.onnx" in names["Policies"].detail
    assert names["Battery"].status == "ok"
    assert report.can_move is True
    assert report.exit_code == 0


def test_mediad_row_says_it_does_not_authenticate(duck_home, robotd):
    write_duck_config(duck_home, robotd.path)
    report = doctor.run_duck_checks(config_dir=duck_home, groups=["robot"], probe_port=all_ports)
    mediad = next(c for c in report.checks if c.name == "mediad")
    assert mediad.status == "ok"
    assert "NEITHER AUTHENTICATES" in mediad.detail


def test_mediad_down_is_a_warning_not_a_block(duck_home, robotd):
    write_duck_config(duck_home, robotd.path)
    report = doctor.run_duck_checks(config_dir=duck_home, groups=["robot"], probe_port=no_ports)
    mediad = next(c for c in report.checks if c.name == "mediad")
    assert mediad.status == "warn"
    assert mediad.blocking is False
    assert report.can_move is True


# ── The failures that mean the duck cannot walk ──────────────────────────────


def test_unreachable_robotd_is_a_mock_and_a_mock_is_a_failure(duck_home, tmp_path):
    """The RC car's simulated-wheels trap, in a beak."""
    write_duck_config(duck_home, tmp_path / "nothing-here.sock")
    report = doctor.run_duck_checks(
        config_dir=duck_home, groups=["robot"], probe_port=no_ports
    )
    names = {c.name: c for c in report.checks}
    assert names["robotd"].status == "fail" and names["robotd"].blocking
    assert names["Duck drive mode"].status == "fail"
    assert names["Duck drive mode"].blocking
    assert "MOCK" in names["Duck drive mode"].detail
    assert "ok:True" in names["Duck drive mode"].detail
    # Everything downstream skips rather than inventing a reading.
    assert names["Control loop"].status == "skip"
    assert names["Policies"].status == "skip"
    assert names["Battery"].status == "skip"
    assert report.can_move is False
    assert report.exit_code == 1


def test_login_not_in_the_robot_group_blocks_and_names_the_reboot(duck_home, robotd):
    write_duck_config(duck_home, robotd.path)
    report = doctor.run_duck_checks(
        config_dir=duck_home, groups=["radxa", "sudo"], probe_port=all_ports
    )
    group = next(c for c in report.checks if c.name == "robot group")
    assert group.status == "fail" and group.blocking
    assert "0660" in group.detail
    assert "REBOOT" in group.detail
    assert "usermod -aG robot" in group.fix
    assert report.can_move is False


def test_unknown_groups_is_a_skip_never_a_pass(duck_home, robotd):
    write_duck_config(duck_home, robotd.path)
    report = doctor.run_duck_checks(
        config_dir=duck_home, group_run=lambda target: None, probe_port=all_ports
    )
    group = next(c for c in report.checks if c.name == "robot group")
    assert group.status == "skip"
    assert group.blocking is False


def test_a_wedged_loop_blocks(tmp_path, duck_home):
    health = dict(HEALTHY)
    health["control_loop"] = dict(HEALTHY["control_loop"], last_tick_age_ms=4200)
    server = FakeRobotd(tmp_path / "wedged.sock", health=health)
    try:
        write_duck_config(duck_home, server.path)
        report = doctor.run_duck_checks(
            config_dir=duck_home, groups=["robot"], probe_port=no_ports
        )
    finally:
        server.close()
    loop = next(c for c in report.checks if c.name == "Control loop")
    assert loop.status == "fail" and loop.blocking
    assert "WEDGED" in loop.detail
    assert "500 ms" in loop.detail
    assert report.can_move is False


def test_a_slow_loop_blocks(tmp_path, duck_home):
    health = dict(HEALTHY)
    health["control_loop"] = dict(HEALTHY["control_loop"], achieved_hz=31.0)
    server = FakeRobotd(tmp_path / "slow.sock", health=health)
    try:
        write_duck_config(duck_home, server.path)
        report = doctor.run_duck_checks(
            config_dir=duck_home, groups=["robot"], probe_port=no_ports
        )
    finally:
        server.close()
    loop = next(c for c in report.checks if c.name == "Control loop")
    assert loop.status == "fail" and loop.blocking
    assert "31.0 Hz of 50.0 Hz" in loop.detail


def test_achieved_hz_absent_is_not_zero(tmp_path, duck_home):
    """`achieved_hz: None` means the first window has not closed. Not 0 Hz."""
    health = dict(HEALTHY)
    health["control_loop"] = dict(HEALTHY["control_loop"], achieved_hz=None)
    server = FakeRobotd(tmp_path / "young.sock", health=health)
    try:
        write_duck_config(duck_home, server.path)
        report = doctor.run_duck_checks(
            config_dir=duck_home, groups=["robot"], probe_port=no_ports
        )
    finally:
        server.close()
    loop = next(c for c in report.checks if c.name == "Control loop")
    assert loop.status == "ok"
    assert "not reported yet" in loop.detail


def test_an_empty_walk_slot_blocks_with_the_hub_in_the_fix(tmp_path, duck_home):
    """A board that could not reach Hugging Face at install has no gait."""
    policies = dict(POLICIES)
    policies["slots"] = [{"slot": "walk", "path": None}, {"slot": "stand", "path": None}]
    server = FakeRobotd(tmp_path / "nogait.sock", policies=policies)
    try:
        write_duck_config(duck_home, server.path)
        report = doctor.run_duck_checks(
            config_dir=duck_home, groups=["robot"], probe_port=no_ports
        )
    finally:
        server.close()
    pol = next(c for c in report.checks if c.name == "Policies")
    assert pol.status == "fail" and pol.blocking
    assert "walk slot is EMPTY" in pol.detail
    assert "seed-policies" in pol.fix
    assert report.can_move is False


def test_policies_disabled_blocks(tmp_path, duck_home):
    policies = dict(POLICIES, enabled=False)
    server = FakeRobotd(tmp_path / "disabled.sock", policies=policies)
    try:
        write_duck_config(duck_home, server.path)
        report = doctor.run_duck_checks(
            config_dir=duck_home, groups=["robot"], probe_port=no_ports
        )
    finally:
        server.close()
    pol = next(c for c in report.checks if c.name == "Policies")
    assert pol.status == "fail" and pol.blocking
    assert "DISABLED" in pol.detail


def test_battery_under_twelve_percent_blocks(tmp_path, duck_home):
    """The abort the guide promises, from the place the number actually is."""
    health = dict(HEALTHY, battery={"volts": 6.7, "percent": 8.0})
    server = FakeRobotd(tmp_path / "flat.sock", health=health)
    try:
        write_duck_config(duck_home, server.path)
        report = doctor.run_duck_checks(
            config_dir=duck_home, groups=["robot"], probe_port=no_ports
        )
    finally:
        server.close()
    bat = next(c for c in report.checks if c.name == "Battery")
    assert bat.status == "fail" and bat.blocking
    assert "8%" in bat.detail
    assert report.can_move is False


def test_absent_battery_is_unknown_not_empty(tmp_path, duck_home):
    health = {k: v for k, v in HEALTHY.items() if k != "battery"}
    server = FakeRobotd(tmp_path / "nobat.sock", health=health)
    try:
        write_duck_config(duck_home, server.path)
        report = doctor.run_duck_checks(
            config_dir=duck_home, groups=["robot"], probe_port=no_ports
        )
    finally:
        server.close()
    bat = next(c for c in report.checks if c.name == "Battery")
    assert bat.status == "warn"
    assert bat.blocking is False
    assert "not zero volts" in bat.detail
    assert report.can_move is True


def test_unhealthy_but_degraded_is_a_warning(tmp_path, duck_home):
    """`degraded` is a property of the board, not of the release."""
    health = dict(HEALTHY, healthy=False, degraded=True, reason="no servo power")
    server = FakeRobotd(tmp_path / "degraded.sock", health=health)
    try:
        write_duck_config(duck_home, server.path)
        report = doctor.run_duck_checks(
            config_dir=duck_home, groups=["robot"], probe_port=no_ports
        )
    finally:
        server.close()
    loop = next(c for c in report.checks if c.name == "Control loop")
    assert loop.status == "warn"
    assert loop.blocking is False
    assert "no servo power" in loop.detail


# ── The harness gate, seen from doctor and from gaps ─────────────────────────


def test_harness_off_is_a_warning_and_a_gap(duck_home, robotd):
    write_duck_config(duck_home, robotd.path, harness=False)
    report = doctor.run_duck_checks(config_dir=duck_home, groups=["robot"], probe_port=no_ports)
    cfg = next(c for c in report.checks if c.name == "Duck config")
    assert cfg.status == "warn"
    assert "harness.enabled" in cfg.detail
    gap = next(c for c in report.checks if c.name.startswith("gap:"))
    assert gap.name == "gap:duck.tools.gated"
    assert "duck_perform" in gap.detail
    # Not blocking: a duck with no choreography still walks.
    assert report.can_move is True


def test_harness_on_reports_no_duck_gap(duck_home, robotd):
    write_duck_config(duck_home, robotd.path, harness=True)
    report = doctor.run_duck_checks(config_dir=duck_home, groups=["robot"], probe_port=no_ports)
    gaps = next(c for c in report.checks if c.name == "Duck gaps")
    assert gaps.status == "ok"


def test_duck_gaps_ignores_a_car():
    from castor.gaps import duck_gaps

    assert duck_gaps({"drivers": [{"protocol": "pca9685"}]}) == []


# ── Transports doctor understands ────────────────────────────────────────────


def test_tcp_without_a_bridge_token_says_so(tmp_path, duck_home):
    """duck-studio's bridge drops any client whose first line is not its hello."""
    duck_home.mkdir(parents=True, exist_ok=True)
    (duck_home / "duck.rcan.yaml").write_text(
        yaml.dump(
            {
                "metadata": {"robot_name": "duck"},
                "drivers": [
                    {
                        "protocol": "microduck",
                        "transport": "tcp",
                        "host": "127.0.0.1",
                        "port": 1,  # nothing listens
                    }
                ],
            }
        )
    )
    report = doctor.run_duck_checks(config_dir=duck_home, probe_port=no_ports)
    names = {c.name: c for c in report.checks}
    assert names["robotd"].status == "fail"
    assert "hello" in names["robotd"].detail
    # A bridge opens the socket for you, so this login's groups are irrelevant.
    assert names["robot group"].status == "skip"


def test_tcp_with_a_token_file_sends_the_hello(tmp_path, duck_home):
    token_file = tmp_path / "token"
    token_file.write_text("s3cret\n")
    duck_home.mkdir(parents=True, exist_ok=True)
    (duck_home / "duck.rcan.yaml").write_text(
        yaml.dump(
            {
                "drivers": [
                    {
                        "protocol": "microduck",
                        "transport": "tcp",
                        "host": "127.0.0.1",
                        "port": 7788,
                        "token_file": str(token_file),
                    }
                ]
            }
        )
    )
    target = doctor.find_duck_config(config_dir=duck_home)
    assert target.bridge_token == "s3cret"

    sent: list[bytes] = []

    class _Sock:
        def settimeout(self, _):
            pass

        def sendall(self, data):
            sent.append(data)

        def recv(self, _n):
            return (
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": HEALTHY}).encode()
                + b"\n"
                + json.dumps({"jsonrpc": "2.0", "id": 2, "result": POLICIES}).encode()
                + b"\n"
            )

    probe = doctor.probe_duck(target, connect=lambda t, timeout: (_Sock(), lambda: None))
    assert probe["ok"] is True
    assert probe["health"]["battery"]["percent"] == 64.0


def test_an_unknown_transport_fails_loudly(duck_home):
    duck_home.mkdir(parents=True, exist_ok=True)
    (duck_home / "duck.rcan.yaml").write_text(
        yaml.dump({"drivers": [{"protocol": "microduck", "transport": "carrier-pigeon"}]})
    )
    target = doctor.find_duck_config(config_dir=duck_home)
    probe = doctor.probe_duck(target)
    assert probe["ok"] is False
    assert "carrier-pigeon" in probe["error"]


def test_rpc_survives_a_dropped_connection(tmp_path, duck_home):
    server = FakeRobotd(tmp_path / "rude.sock", drop=True)
    try:
        write_duck_config(duck_home, server.path)
        target = doctor.find_duck_config(config_dir=duck_home)
        probe = doctor.probe_duck(target, timeout=1.0)
    finally:
        server.close()
    assert probe["ok"] is False
    # Whichever way the drop lands (a reset on the read, a broken pipe on the
    # write), doctor reports the OS error verbatim rather than crashing: an
    # owner debugging a duck needs "Connection reset by peer", not "failed".
    assert probe["error"]
    assert "Error" in probe["error"]
    assert probe["health"] is None
