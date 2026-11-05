"""The bridge's stop gaps from the brains-layer safety review (duck-studio
docs/plans/brains-layer.md, A1 and A3), proved against the recording robotd.

(a) The deadman used to reset on ANY client line, so a phone polling state
    kept a duck walking on a twist it sent long ago. Only motion feeds it now.
(b) A `robot.stop` from any client zeroed robotd once, and a run the bridge
    itself carried sent its next move a tick later. Every stop now cancels
    every bridge-owned run before the stop line goes out.
"""

from __future__ import annotations

import json
import socket
import time

import pytest
from test_microduck_bridge import TOKEN, MockRobotd, _line, _start

from castor import microduck_bridge as bridge

MOVE = b'{"jsonrpc":"2.0","method":"robot.move","params":{"vx":0.3},"id":1}\n'
STATE = b'{"jsonrpc":"2.0","method":"robot.state","id":3}\n'
STOP = b'{"jsonrpc":"2.0","method":"robot.stop","params":{},"id":"studio-stop"}\n'


def _stops(robotd) -> list[str]:
    return [line for line in robotd.lines if '"robot.stop"' in line]


def _walk_moves(robotd) -> list[str]:
    return [line for line in robotd.lines if '"bridge-walk-' in line]


def _wait(predicate, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture()
def duck(tmp_path, monkeypatch):
    """A bridge (300 ms deadman) whose server-side relays the test can reach."""
    relays: list[bridge.Relay] = []

    class Recording(bridge.Relay):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            relays.append(self)

    monkeypatch.setattr(bridge, "Relay", Recording)
    socket_path = str(tmp_path / "robotd.sock")
    robotd = MockRobotd(socket_path)
    port = _start(socket_path, deadman_ms=300)

    def connect():
        before = len(relays)
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(json.dumps({"microduck": "v1", "token": TOKEN}).encode() + b"\n")
        _line(client)
        assert _wait(lambda: len(relays) > before), "the bridge never built a relay"
        return client, relays[-1]

    try:
        yield robotd, connect
    finally:
        robotd.close()


# ---------------------------------------------------------------------------
# (a) only motion feeds the deadman
# ---------------------------------------------------------------------------


def test_a_non_motion_line_does_not_refresh_the_deadman(duck):
    """One move, then state polls every 100 ms: the move goes stale and expires into a stop."""
    robotd, connect = duck
    client, _ = connect()
    client.sendall(MOVE)
    started = time.monotonic()
    fired_at = None
    for _ in range(10):
        client.sendall(STATE)
        time.sleep(0.1)
        if fired_at is None and _stops(robotd):
            fired_at = time.monotonic() - started
    stops = _stops(robotd)
    assert len(stops) == 1, f"one stale move, one stop: {robotd.lines}"
    assert "bridge-deadman" in stops[0]
    assert fired_at is not None and fired_at < 0.6, f"stop came at {fired_at}s, deadman is 0.3s"
    client.close()


def test_a_line_that_only_mentions_robot_move_is_not_motion():
    assert bridge.line_method(MOVE) == "robot.move"
    assert bridge.line_method(STOP) == "robot.stop"
    assert bridge.line_method(STATE) is None
    sound = b'{"jsonrpc":"2.0","method":"robot.sound","params":{"tag":"robot.move"},"id":2}'
    assert bridge.line_method(sound) == "robot.sound"
    assert bridge.line_method(b'{"robot.move" not json') is None


def test_a_bridge_run_feeds_its_own_relays_deadman(duck):
    """The bridge's own walk is motion: it is not stopped by the deadman it is feeding."""
    robotd, connect = duck
    client, relay = connect()
    walk = bridge.MoveStream(relay, {"vx": 0.2}, hz=20, duration_s=0.8).start()
    assert walk.finished.wait(3)
    time.sleep(0.1)
    stops = _stops(robotd)
    assert len(stops) == 1 and "bridge-run-end" in stops[0], robotd.lines
    assert len(_walk_moves(robotd)) == walk.sent >= 10
    time.sleep(0.5)
    assert len(_stops(robotd)) == 1, "a finished run disarms nothing it did not arm"
    client.close()


# ---------------------------------------------------------------------------
# (b) a stop from anyone cancels every bridge-owned run
# ---------------------------------------------------------------------------


def test_a_stop_from_another_client_cancels_a_bridge_run_within_one_tick(duck):
    """The A3 golden case: stop during an active run, then no new moves."""
    robotd, connect = duck
    owner, relay = connect()
    walk = bridge.MoveStream(relay, {"vx": 0.3}, hz=20, duration_s=10).start()
    assert _wait(lambda: len(_walk_moves(robotd)) >= 3)

    siri, _ = connect()
    siri.sendall(STOP)
    assert _wait(lambda: any("studio-stop" in line for line in robotd.lines))
    # THE FENCE: by the time robotd has the stop, the run has sent its last move.
    sent_at_stop = walk.sent
    assert walk.cancelled.is_set()
    assert walk.finished.wait(0.05), "the run did not end within one 50 ms tick"
    time.sleep(0.6)
    assert walk.sent == sent_at_stop, "the run moved the duck again after a stop"
    assert len(_walk_moves(robotd)) == sent_at_stop
    assert not any("bridge-run-end" in line for line in robotd.lines)
    assert relay.runs.active() == []
    owner.close()
    siri.close()


def test_a_stop_from_the_runs_own_client_cancels_it_too(duck):
    robotd, connect = duck
    client, relay = connect()
    walk = bridge.MoveStream(relay, {"vx": 0.3}, hz=20, duration_s=10).start()
    assert _wait(lambda: len(_walk_moves(robotd)) >= 2)
    client.sendall(STOP)
    assert _wait(lambda: any("studio-stop" in line for line in robotd.lines))
    sent_at_stop = walk.sent
    assert walk.finished.wait(0.05)
    time.sleep(0.3)
    assert walk.sent == sent_at_stop
    client.close()


def test_another_clients_deadman_cancels_a_bridge_run(duck):
    """Every stop the bridge sends is a stop: a run does not resume past a deadman either."""
    robotd, connect = duck
    owner, relay = connect()
    walk = bridge.MoveStream(relay, {"vx": 0.3}, hz=20, duration_s=10).start()
    other, _ = connect()
    other.sendall(MOVE)  # then silence: its deadman fires 300 ms later
    assert _wait(lambda: any("bridge-deadman" in line for line in robotd.lines), timeout=2)
    assert walk.finished.wait(0.1)
    sent = walk.sent
    time.sleep(0.3)
    assert walk.sent == sent
    owner.close()
    other.close()


def test_a_client_that_leaves_cancels_the_runs_on_its_relay(duck):
    robotd, connect = duck
    client, relay = connect()
    walk = bridge.MoveStream(relay, {"vx": 0.3}, hz=20, duration_s=10).start()
    assert _wait(lambda: len(_walk_moves(robotd)) >= 2)
    client.close()
    assert walk.finished.wait(1)


# ---------------------------------------------------------------------------
# a zero twist is standing, so a slow dream.act is not cut off
# ---------------------------------------------------------------------------


ZERO = b'{"jsonrpc":"2.0","method":"robot.move","params":{"vx":0,"vy":0,"vyaw":0},"id":2}\n'


def test_a_zero_twist_disarms_the_deadman(duck):
    """DreamPilot's pattern: walk, then a zero twist, then a long wait. No deadman stop."""
    robotd, connect = duck
    client, relay = connect()
    client.sendall(MOVE)
    assert _wait(lambda: relay.armed)
    client.sendall(ZERO)
    assert _wait(lambda: not relay.armed)
    time.sleep(0.6)  # twice the 300 ms deadman
    assert not any('"bridge-deadman"' in line for line in _stops(robotd))
    client.close()


def test_zero_twist_parsing():
    assert bridge.is_zero_twist(ZERO)
    assert bridge.is_zero_twist(b'{"jsonrpc":"2.0","method":"robot.move","params":{},"id":2}')
    assert not bridge.is_zero_twist(MOVE)
    assert not bridge.is_zero_twist(b'{"method":"robot.move","params":{"vx":"fast"}}')
