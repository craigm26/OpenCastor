"""The robotd relay, proved against a mock robotd.

THE MOCK IS THE POINT. Nobody here has a duck on the desk, so every claim this
bridge makes is either proved against a socket that records what it was sent or
it is not proved at all. The mock speaks nothing: it accepts a connection,
records every line, and replies with whatever it was told to reply with. That
is enough, because the bridge's whole contract is that it does not interpret
the protocol.

Ported from duck-studio's `bridge/test_bridge.py` (17 tests, the ones that
proved the relay, the token and the deadman before this file existed) and
extended with what OpenCastor adds: minting, the client half of the handshake,
and the ssh upstream's argv.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import stat
import threading
import time

import pytest

from castor import microduck_bridge as bridge

TOKEN = "0123456789abcdef0123456789abcdef"


class MockRobotd:
    """A Unix socket that records lines and can push its own."""

    def __init__(self, path):
        self.path = path
        self.lines: list[str] = []
        self.conns: list[socket.socket] = []
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
            self.conns.append(conn)
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _read(self, conn):
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

    def push(self, text):
        for conn in self.conns:
            conn.sendall(text.encode() + b"\n")

    def close(self):
        self.running = False
        self.server.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _line(sock) -> str:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            return buf.decode()
        buf += chunk
    return buf.decode().strip()


def _start(socket_path, deadman_ms=300, **kwargs) -> int:
    ready = threading.Event()
    port = {}

    def note(p):
        port["n"] = p
        ready.set()

    threading.Thread(
        target=bridge.serve,
        args=("127.0.0.1", 0, socket_path, TOKEN, deadman_ms),
        kwargs={"log": lambda *a: None, "ready": note, **kwargs},
        daemon=True,
    ).start()
    assert ready.wait(5), "the bridge never started listening"
    return port["n"]


@pytest.fixture()
def relay(tmp_path):
    """A running bridge in front of a recording robotd."""
    socket_path = str(tmp_path / "robotd.sock")
    robotd = MockRobotd(socket_path)
    port = _start(socket_path)

    def connect(token=TOKEN):
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(json.dumps({"microduck": "v1", "token": token}).encode() + b"\n")
        return client

    try:
        yield robotd, connect
    finally:
        robotd.close()


# ---------------------------------------------------------------------------
# What it carries
# ---------------------------------------------------------------------------


def test_every_byte_reaches_robotd_unchanged(relay):
    robotd, connect = relay
    client = connect()
    _line(client)
    sent = '{"jsonrpc":"2.0","method":"robot.move","params":{"vx":0.3},"id":1}'
    client.sendall(sent.encode() + b"\n")
    time.sleep(0.2)
    assert sent in robotd.lines, "the relay must not rewrite a single byte of the protocol"
    client.close()


def test_robotds_answer_reaches_the_client_unchanged(relay):
    robotd, connect = relay
    client = connect()
    _line(client)
    answer = '{"jsonrpc":"2.0","result":{"upright":true},"id":1}'
    time.sleep(0.1)
    robotd.push(answer)
    assert _line(client) == answer
    client.close()


def test_the_bridge_never_learns_the_vocabulary(relay):
    """A method robotd has never heard of travels through untouched.

    The one property that makes this a relay and not a protocol: the bridge has
    no list of verbs, so a robotd that grows one tomorrow needs no change here.
    """
    robotd, connect = relay
    client = connect()
    _line(client)
    invented = '{"jsonrpc":"2.0","method":"robot.doTheThingInventedNextYear","id":4}'
    client.sendall(invented.encode() + b"\n")
    time.sleep(0.2)
    assert invented in robotd.lines
    client.close()


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


def test_a_wrong_token_is_refused_by_name_and_never_reaches_robotd(relay):
    robotd, connect = relay
    client = connect(token="not-the-token-not-the-token")
    assert "wrong or missing token" in _line(client)
    time.sleep(0.2)
    assert robotd.lines == [], "a refused client must not reach robotd at all"
    client.close()


def test_a_hello_that_is_not_json_at_all_is_refused(tmp_path):
    socket_path = str(tmp_path / "robotd.sock")
    robotd = MockRobotd(socket_path)
    try:
        port = _start(socket_path)
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(b"hello there\n")
        assert "wrong or missing token" in _line(client)
        client.close()
    finally:
        robotd.close()


def test_a_token_file_other_people_can_read_is_refused(tmp_path):
    path = tmp_path / "token"
    path.write_text(TOKEN)
    path.chmod(0o644)
    with pytest.raises(bridge.Refusal) as caught:
        bridge.read_token(str(path))
    assert "readable or writable by other users" in str(caught.value)
    path.chmod(0o600)
    assert bridge.read_token(str(path)) == TOKEN


def test_a_short_token_is_refused_with_its_length(tmp_path):
    path = tmp_path / "short"
    path.write_text("tooshort")
    path.chmod(0o600)
    with pytest.raises(bridge.Refusal) as caught:
        bridge.read_token(str(path))
    assert "8 characters" in str(caught.value)


# ---------------------------------------------------------------------------
# The deadman, which is the reason to prefer this to a raw relay
# ---------------------------------------------------------------------------


def test_silence_sends_a_stop_and_only_one(relay):
    robotd, connect = relay
    client = connect()
    _line(client)
    client.sendall(b'{"jsonrpc":"2.0","method":"robot.move","params":{"vx":0.3},"id":1}\n')
    time.sleep(0.9)
    stops = [line for line in robotd.lines if '"robot.stop"' in line]
    assert len(stops) == 1, f"one stop for one silence, got {len(stops)}: {robotd.lines}"
    assert "bridge-deadman" in stops[0], "a robotd log must show WHO stopped the robot"
    client.close()


def test_talking_again_rearms_the_deadman(relay):
    robotd, connect = relay
    client = connect()
    _line(client)
    client.sendall(b'{"jsonrpc":"2.0","method":"robot.move","params":{"vx":0.3},"id":1}\n')
    time.sleep(0.5)
    client.sendall(b'{"jsonrpc":"2.0","method":"robot.move","params":{"vx":0.3},"id":2}\n')
    time.sleep(0.5)
    stops = [line for line in robotd.lines if '"robot.stop"' in line]
    assert len(stops) == 2, "the deadman re-arms when the client speaks again"
    client.close()


def test_a_client_that_keeps_talking_is_never_stopped(relay):
    robotd, connect = relay
    client = connect()
    _line(client)
    for _ in range(10):
        client.sendall(b'{"jsonrpc":"2.0","method":"robot.move","params":{"vx":0.3},"id":9}\n')
        time.sleep(0.1)
    stops = [line for line in robotd.lines if '"robot.stop"' in line]
    assert stops == [], "a driven robot is not stopped by its own driver"
    client.close()


def test_any_byte_feeds_the_deadman_not_only_a_move(relay):
    """A client asking for state is a client that is still there.

    Named because the opposite is a tempting optimisation: a robot that stops
    because nobody drove it for a moment is a robot nobody trusts.
    """
    robotd, connect = relay
    client = connect()
    _line(client)
    for _ in range(8):
        client.sendall(b'{"jsonrpc":"2.0","method":"robot.state","id":3}\n')
        time.sleep(0.1)
    assert [line for line in robotd.lines if '"robot.stop"' in line] == []
    client.close()


# ---------------------------------------------------------------------------
# The token, minted
# ---------------------------------------------------------------------------


def test_a_minted_token_is_0600_and_long_enough(tmp_path):
    path = tmp_path / "nested" / "token"
    token, reused = bridge.mint_token(str(path))
    assert not reused
    assert len(token) >= bridge.MIN_TOKEN_LEN
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"a token file at {oct(mode)} is not a token"
    # And the bridge itself accepts what it minted, which is the whole loop.
    assert bridge.read_token(str(path)) == token


def test_minting_twice_reuses_the_token_it_already_has(tmp_path):
    """It was typed into a phone. Rotating it unpairs the app in silence."""
    path = tmp_path / "token"
    first, reused_first = bridge.mint_token(str(path))
    second, reused_second = bridge.mint_token(str(path))
    assert (reused_first, reused_second) == (False, True)
    assert first == second


def test_reuse_reasserts_the_mode_on_a_file_somebody_else_wrote(tmp_path):
    """`cp` takes the umask, not the source mode; a restore takes the backup's.

    The bridge refuses a group-readable token file at startup, so a mode nobody
    fixed is a unit that will not start. This fixes it while somebody is
    watching the output.
    """
    path = tmp_path / "token"
    path.write_text(TOKEN)
    path.chmod(0o644)
    token, reused = bridge.mint_token(str(path))
    assert (token, reused) == (TOKEN, True)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_a_file_holding_something_too_short_is_replaced_not_reused(tmp_path):
    path = tmp_path / "token"
    path.write_text("nope")
    path.chmod(0o600)
    token, reused = bridge.mint_token(str(path))
    assert not reused and len(token) >= bridge.MIN_TOKEN_LEN


# ---------------------------------------------------------------------------
# The handshake, pinned to the app that already speaks it
# ---------------------------------------------------------------------------


def test_THEPIN_the_hello_line_is_byte_identical_to_the_apps(tmp_path):
    """Pinned against StudioKit/Sources/StudioKit/BridgeHandshake.swift.

    `BridgeHandshake.hello(token:)` builds this string BY HAND rather than with
    an encoder, and its own comment says why: "KEY ORDER IS FIXED because the
    bridge reads JSON and a person reads the log line; neither is helped by two
    spellings of one greeting."

        let body = "{\\"microduck\\":\\"\\(version)\\",\\"token\\":\\"\\(escaped(token))\\"}\\n"

    with `version = "v1"`. `json.dumps` happens to agree today and would stop
    agreeing the day somebody sorts keys or adds a space after the colon, and
    the failure would be a phone that pairs and an OpenCastor that does not.
    So the bytes are written out here and compared literally.
    """
    assert bridge.hello_line("abc123") == b'{"microduck":"v1","token":"abc123"}\n'
    assert bridge.HELLO_VERSION == "v1"
    # `escaped` is backslash first, then quote — the same order, so a token
    # containing either survives the round trip through the bridge's own reader.
    tricky = 'a"b\\c'
    line = bridge.hello_line(tricky)
    assert line == b'{"microduck":"v1","token":"a\\"b\\\\c"}\n'
    assert json.loads(line)["token"] == tricky
    assert bridge.hello_is_valid(line, tricky)


def test_the_default_token_file_is_the_path_the_app_was_installed_with():
    """duck-studio's `bridge/install.sh` writes `$HOME/.microduck-bridge-token`.

    Unchanged on purpose. A duck whose owner already ran install.sh and typed
    that token into Microduck Studio keeps working, and `castor` reuses it
    rather than minting a second token nobody has.
    """
    assert bridge.DEFAULT_TOKEN_FILE == "~/.microduck-bridge-token"
    assert bridge.DEFAULT_PORT == 7788, "StudioKit's BridgeHandshake.defaultPort"


def test_a_greeting_round_trips_and_a_refusal_is_read_first():
    greeting = bridge.read_greeting(bridge.greeting_line(700, True))
    assert greeting["deadman_ms"] == 700
    assert greeting["policy_install"] is True
    assert greeting["bridge"] == bridge.VERSION
    # THE ERROR SHAPE IS THE BRIDGE'S OWN and is read before the success key:
    # a client that checked its own key first would report "not a bridge" for a
    # bridge that answered perfectly well and said no.
    with pytest.raises(bridge.Refusal) as caught:
        bridge.read_greeting(b'{"error":"microduck-bridge: wrong or missing token"}\n')
    assert "wrong or missing token" in str(caught.value)
    with pytest.raises(bridge.Refusal):
        bridge.read_greeting(b"not json at all\n")
    with pytest.raises(bridge.Refusal) as newer:
        bridge.read_greeting(b'{"microduck":"v2"}\n')
    assert "v2" in str(newer.value)


def test_the_greeting_says_the_deadman_the_bridge_is_actually_running(relay):
    """The app draws no promise it was not made — BridgeHandshake.deadmanSaid."""
    _robotd, connect = relay
    client = connect()
    greeting = bridge.read_greeting(_line(client).encode() + b"\n")
    assert greeting["deadman_ms"] == 300
    client.close()


# ---------------------------------------------------------------------------
# The upstream
# ---------------------------------------------------------------------------


def test_a_unix_upstream_starts_nothing_and_describes_itself():
    up = bridge.Upstream("/run/robotd.sock")
    assert up.kind == "unix"
    up.start()  # a no-op, and must not require ssh to exist
    assert up.describe() == "/run/robotd.sock"


def test_the_ssh_forward_argv_is_pinned():
    """Read as data so the shape is testable without spawning ssh.

    ExitOnForwardFailure because a forward that silently did not bind gives you
    a bridge that accepts clients and connects to nothing; BatchMode because a
    systemd unit has nowhere to type a password and a prompt would hang the
    service rather than fail it.
    """
    argv = bridge.forward_command("radxa@192.168.1.42", "/run/robotd.sock", 7789)
    assert argv[0] == "ssh"
    assert "-N" in argv and "-T" in argv
    assert "ExitOnForwardFailure=yes" in argv
    assert "BatchMode=yes" in argv
    assert "127.0.0.1:7789:/run/robotd.sock" in argv
    assert argv[-1] == "radxa@192.168.1.42"
    assert bridge.DEFAULT_FORWARD_PORT != bridge.DEFAULT_PORT, (
        "forwarding onto our own listener is a loop that looks like a hang"
    )


def test_main_refuses_a_forward_port_that_is_the_listen_port(tmp_path, capsys):
    token = tmp_path / "token"
    token.write_text(TOKEN)
    token.chmod(0o600)
    code = bridge.main(
        ["--ssh", "radxa@duck", "--port", "7788", "--forward-port", "7788",
         "--token-file", str(token)]
    )
    assert code == 2
    assert "loop back onto this listener" in capsys.readouterr().err


def test_every_flag_is_also_an_environment_variable(monkeypatch):
    """The unit is an EnvironmentFile, so every flag has to be readable there."""
    monkeypatch.setenv("MICRODUCK_BRIDGE_PORT", "9001")
    monkeypatch.setenv("MICRODUCK_BRIDGE_DEADMAN_MS", "450")
    monkeypatch.setenv("MICRODUCK_BRIDGE_SSH", "radxa@duck")
    monkeypatch.setenv("MICRODUCK_BRIDGE_SOCKET", "/tmp/elsewhere.sock")
    args = bridge.build_parser().parse_args([])
    assert (args.port, args.deadman, args.ssh, args.socket) == (
        9001, 450, "radxa@duck", "/tmp/elsewhere.sock"
    )


# ---------------------------------------------------------------------------
# policy.install — the one verb the bridge answers itself
# ---------------------------------------------------------------------------


@pytest.fixture()
def installer(tmp_path):
    socket_path = str(tmp_path / "robotd.sock")
    robotd = MockRobotd(socket_path)
    policy_dir = str(tmp_path / "policies")
    toml = tmp_path / "robotd.toml"
    toml.write_text(
        '[robot]\nname = "huey"\n\n[policy]\nwalk = "/opt/old/alpha_walking.onnx"\n'
        'stand = "/opt/old/alpha_stand.onnx"\n# roulade = "off"\n'
    )

    def start(policy=policy_dir, config=str(toml)):
        return _start(socket_path, policy_dir=policy, robotd_toml=config)

    def connect(port):
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(json.dumps({"microduck": "v1", "token": TOKEN}).encode() + b"\n")
        return client

    try:
        yield robotd, policy_dir, str(toml), start, connect
    finally:
        robotd.close()


def _payload(data=b"ONNX" * 1000, name="walk_two", **extra):
    p = {"name": name, "bytes": base64.b64encode(data).decode(),
         "sha256": hashlib.sha256(data).hexdigest()}
    p.update(extra)
    return p


def _request(sock, params, rpc_id=7):
    sock.sendall(
        json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": "policy.install",
                    "params": params}).encode() + b"\n"
    )
    return json.loads(_line(sock))


def test_the_greeting_says_install_is_on(installer):
    _robotd, _dir, _toml, start, connect = installer
    client = connect(start())
    assert json.loads(_line(client))["policy_install"] is True
    client.close()


def test_an_install_lands_on_disk_and_never_reaches_robotd(installer):
    robotd, policy_dir, _toml, start, connect = installer
    client = connect(start())
    _line(client)
    data = b"\x08\x07ONNX-ish" * 500
    answer = _request(client, _payload(data, name="walk_two.onnx"))
    assert answer["id"] == 7
    result = answer["result"]
    assert result["installed"] == os.path.join(policy_dir, "walk_two.onnx")
    with open(result["installed"], "rb") as f:
        assert f.read() == data
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert "robotd next starts" in result["takes_effect"]
    time.sleep(0.1)
    assert robotd.lines == [], "the one verb the bridge answers itself"
    client.close()


def test_a_wrong_digest_writes_nothing_and_says_so(installer):
    _robotd, policy_dir, _toml, start, connect = installer
    client = connect(start())
    _line(client)
    bad = _payload()
    bad["sha256"] = "0" * 64
    answer = _request(client, bad)
    assert "nothing was written" in answer["error"]["message"]
    assert not os.path.exists(os.path.join(policy_dir, "walk_two.onnx"))
    client.close()


def test_a_name_that_could_be_a_path_is_refused_before_anything_is_decoded(installer):
    _robotd, policy_dir, _toml, start, connect = installer
    client = connect(start())
    _line(client)
    for bad in ["../escape", "/abs", "a/b", "", ".hidden", "x" * 65, "walk two"]:
        assert "error" in _request(client, _payload(name=bad)), bad
    assert not (os.path.exists(policy_dir) and os.listdir(policy_dir))
    client.close()


def test_a_slot_is_pointed_at_the_file_with_a_backup(installer):
    _robotd, _dir, toml, start, connect = installer
    client = connect(start())
    _line(client)
    answer = _request(client, _payload(slot="walk"))
    slot = answer["result"]["slot"]
    assert slot["applied"], slot
    text = open(toml).read()
    assert 'walk = "%s"' % answer["result"]["installed"] in text
    assert 'stand = "/opt/old/alpha_stand.onnx"' in text, "the other key is untouched"
    assert os.path.exists(slot["backup"])
    refused = _request(client, _payload(name="another", slot="roulade"))["result"]["slot"]
    assert not refused["applied"]
    assert "no `roulade` key" in refused["why"]
    client.close()


def test_without_a_policy_dir_the_verb_is_off_by_name(installer):
    _robotd, _dir, _toml, start, connect = installer
    client = connect(start(policy=None, config=None))
    assert json.loads(_line(client))["policy_install"] is False
    assert "--policy-dir" in _request(client, _payload())["error"]["message"]
    client.close()


def test_every_other_line_still_reaches_robotd_whole_and_in_order(installer):
    robotd, _dir, _toml, start, connect = installer
    client = connect(start())
    _line(client)
    first = b'{"jsonrpc":"2.0","method":"robot.move","params":{"vx":0.1}}\n'
    client.sendall(first[:20])  # a line split across two sends
    time.sleep(0.05)
    client.sendall(first[20:])
    _request(client, _payload(name="between"))
    client.sendall(b'{"jsonrpc":"2.0","id":9,"method":"robot.stop"}\n')
    time.sleep(0.2)
    assert robotd.lines == [
        first.decode().strip(),
        '{"jsonrpc":"2.0","id":9,"method":"robot.stop"}',
    ]
    robotd.push('{"jsonrpc":"2.0","id":9,"result":true}')
    assert _line(client) == '{"jsonrpc":"2.0","id":9,"result":true}'
    client.close()
