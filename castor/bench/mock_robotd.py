"""A robotd-shaped socket with real-shaped replies, for CI.

WHAT THIS IS AND IS NOT. It answers the *shape* of ``robotd``'s JSON-RPC and
claims nothing else. Nothing here has physics; a state it returns is a constant.
It exists so the whole ten-minute benchmark can be exercised end to end on a
machine with no duck, which is what makes the benchmark a thing CI can run on
every push. **A run against this target can never be a pass of the ten-minute
goal** — see ``castor/bench/ten_minutes.py``, which forces ``ci-pass``.

WHY THE REPLIES ARE SHAPED. duck-studio's ``bridge/mock-robotd.py`` answered
``{"ok": true}`` to every method, and the review that specified this benchmark
measured what that costs: ``MicroduckDriver.health_check()`` correctly read it
as unhealthy, and nothing in the project could ever exercise the identity
checkpoint. **The four replies below are the fixture that would have caught
traps 1, 3 and 4** — the four wire keys OpenCastor reads that ``robotd`` does
not send. Every field is transcribed from ``duck-ipc-proto/src/lib.rs`` at
rev ``5620aa2``, never invented, with the source line beside it in
``castor/bench/wire.py``.

Run it standalone the way the CLI does::

    python3 -u -m castor.bench.mock_robotd --socket /tmp/mock.sock
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from typing import Any, Optional

from castor.bench import wire

#: What a healthy duck's ``hello`` looks like. HelloResult, lib.rs:2800-2807.
#: ``daemon_version`` is a semver string and ``revision`` is ``None`` for a
#: build that did not come from CI, which is what a mock is.
HELLO_RESULT: dict[str, Any] = {
    wire.HELLO_API_VERSION: wire.API_VERSION,  # :2801
    wire.HELLO_DAEMON_VERSION: "0.0.0-mock",  # :2802
    wire.HELLO_REVISION: None,  # :2806 — always serialised, including as null
}

#: HealthResult, lib.rs:3089-3147, with LoopHealth :3152-3167, Battery
#: :3263-3265 and BusHealth :3174-3184 nested inside it. The numbers are the
#: ones the review's own example prints, so a reader can compare them.
HEALTH_RESULT: dict[str, Any] = {
    wire.HEALTH_HEALTHY: True,  # :3090
    wire.HEALTH_DEGRADED: False,  # :3103
    wire.HEALTH_CONTROL_LOOP: {  # :3140 — `control_loop`, NOT `loop`
        wire.LOOP_TARGET_HZ: 50.0,  # :3155
        wire.LOOP_ACHIEVED_HZ: 49.8,  # :3159 — Option; a real one is null at first
        wire.LOOP_TICKS: 124_000,  # :3160
        wire.LOOP_MISSED: 0,  # :3164 — `missed`, and there is no `hz` here
        wire.LOOP_LAST_TICK_AGE_MS: 12,  # :3166
    },
    wire.HEALTH_BATTERY: {  # :3118 — battery is on HEALTH, never on STATE
        wire.BATTERY_VOLTS: 7.9,  # :3264
        wire.BATTERY_PERCENT: 64.0,  # :3265
    },
    wire.HEALTH_BUS: {  # :3144 — present on every answer; zeros are meaningful
        wire.BUS_CONSECUTIVE_ERRORS: 0,  # :3179
        wire.BUS_STARTUP_FAILURES: 0,  # :3183
    },
}

#: SubscribeResult, lib.rs:2519-2546. **There is no ``networks`` key**, which is
#: the whole point of this fixture: ``microduck_driver.py:233`` reads one.
SUBSCRIBE_RESULT: dict[str, Any] = {
    wire.SUB_ACCEPTED: True,  # :2520 — `accepted`, not `status`
    wire.SUB_WALK: "alpha_walking.onnx",  # :2525
    wire.SUB_STAND: "alpha_stand.onnx",  # :2529
    wire.SUB_SITSTAND: "alpha_sitstand.onnx",  # :2539
    wire.SUB_GROUND_PICK: "alpha_ground_pick.onnx",  # :2541
    wire.SUB_SKILLS: [  # :2546
        "ground_pick",
        "kick_left",
        "kick_right",
        "sit_toggle",
        "roulade",
    ],
}

#: PoliciesResult, lib.rs:2218-2248, with PolicySlot :2253-2269.
POLICIES_RESULT: dict[str, Any] = {
    wire.POL_MODE: "walk",  # :2220
    wire.POL_ENABLED: True,  # :2224
    wire.POL_SLOTS: [  # :2226
        {
            wire.SLOT_SLOT: "walk",  # :2255
            wire.SLOT_PATH: "alpha_walking.onnx",  # :2258
            wire.SLOT_ORIGIN: "official",  # :2263 — "official"|"community"|"local"
            wire.SLOT_OVERRIDDEN: False,  # :2265
            wire.SLOT_ERROR: None,  # :2268
        },
        {
            wire.SLOT_SLOT: "stand",
            wire.SLOT_PATH: "alpha_stand.onnx",
            wire.SLOT_ORIGIN: "official",
            wire.SLOT_OVERRIDDEN: False,
            wire.SLOT_ERROR: None,
        },
    ],
    wire.POL_SKILLS: list(SUBSCRIBE_RESULT[wire.SUB_SKILLS]),  # :2235
}

#: Every shaped reply, by method. Anything not here still gets an answer, the
#: way the original mock did, because a mock that refuses an unknown method
#: tests our list of methods rather than the client.
REPLIES: dict[str, dict] = {
    wire.M_HELLO: HELLO_RESULT,
    wire.M_HEALTH: HEALTH_RESULT,
    wire.M_SUBSCRIBE: SUBSCRIBE_RESULT,
    wire.M_POLICIES: POLICIES_RESULT,
}


def reply_for(method: str) -> dict:
    """The result object this mock answers ``method`` with.

    A shaped reply is returned by value, so a caller that mutates one does not
    edit the fixture for the next request.
    """
    shaped = REPLIES.get(method)
    if shaped is not None:
        return json.loads(json.dumps(shaped))
    return {"ok": True, "method": method, "t": round(time.time(), 3)}


class MockRobotd:
    """A ``robotd``-shaped Unix socket, in this process or its own.

    Args:
        path: Socket path to bind. Capped at ~108 bytes by the kernel, so keep
            the directory short.
        replies: Override the shaped replies. Used by tests that need a reply
            with a key deliberately missing.
    """

    def __init__(self, path: str, replies: Optional[dict[str, dict]] = None) -> None:
        self.path = path
        self.replies = dict(REPLIES if replies is None else replies)
        self.seen: list[dict] = []
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------

    def start(self) -> "MockRobotd":
        """Bind, listen and serve in a daemon thread. Returns self."""
        if os.path.exists(self.path):
            os.unlink(self.path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.path)
        server.listen(4)
        server.settimeout(0.2)
        self._server = server
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop serving and unlink the socket. Idempotent."""
        self._stop.set()
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def __enter__(self) -> "MockRobotd":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    # -- what it was asked --------------------------------------------

    def calls(self, method: Optional[str] = None) -> list[dict]:
        """Every frame received, or only those naming ``method``."""
        with self._lock:
            seen = list(self.seen)
        return seen if method is None else [c for c in seen if c.get("method") == method]

    # -- serving -------------------------------------------------------

    def _accept(self) -> None:
        while not self._stop.is_set():
            server = self._server
            if server is None:
                return
            try:
                conn, _ = server.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._talk, args=(conn,), daemon=True).start()

    def _talk(self, conn: socket.socket) -> None:
        conn.settimeout(0.2)
        buf = b""
        with conn:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if not raw.strip():
                        continue
                    try:
                        call = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    with self._lock:
                        self.seen.append({**call, "_t": time.monotonic()})
                    if call.get("id") is None:
                        continue  # a notification is not answered
                    shaped = self.replies.get(call.get("method", ""))
                    result = (
                        json.loads(json.dumps(shaped))
                        if shaped is not None
                        else reply_for(call.get("method", "?"))
                    )
                    frame = {"jsonrpc": "2.0", "id": call["id"], "result": result}
                    try:
                        conn.sendall(json.dumps(frame).encode() + b"\n")
                    except OSError:
                        return


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--socket", default="/tmp/mock-robotd.sock")
    args = parser.parse_args(argv)
    mock = MockRobotd(args.socket).start()
    print(
        f"mock robotd on {args.socket} — hello, robot.health, robot.subscribe and "
        f"robot.policies are shaped from duck-ipc-proto {wire.PROTO_REV}; "
        "everything else is accepted and nothing is simulated",
        flush=True,
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        mock.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
