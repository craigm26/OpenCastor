"""robotd's replies, transcribed from Pollen's wire contract — not invented.

Every value below has a citation to ``pollen-robotics/microduck`` (rev ``5620aa2``,
``API_VERSION = 25`` at ``duck-ipc-proto/src/lib.rs:304``).  Field *names* and their
*presence* are the load-bearing part and come straight from the Rust structs; the
numbers are plausible readings for a healthy duck and are labelled as such.

This file exists because four reads in ``castor/drivers/microduck_driver.py`` were
wrong for weeks against a test double that invented the same wrong keys.  A fixture
that agrees with the code proves nothing.  A fixture transcribed from the contract,
with the line it came from written next to it, is the only kind worth having.

It is also runnable, so the same replies can drive a live command::

    python3 tests/microduck_wire_fixtures.py --socket /tmp/duck.sock

which serves NDJSON JSON-RPC 2.0 on a Unix socket, exactly as ``robotd`` does
(``robotd/src/main.rs:60``, ``:3232``).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time

# ---------------------------------------------------------------------------
# hello — HelloResult, duck-ipc-proto/src/lib.rs:2799-2806
#   api_version: u32
#   daemon_version: Option<semver::Version>
#   revision: Option<String>
# ---------------------------------------------------------------------------
HELLO = {
    "api_version": 25,  # pub const API_VERSION: u32 = 25  (:304)
    "daemon_version": "0.30.0",
    "revision": "5620aa2",
}

# ---------------------------------------------------------------------------
# robot.subscribe — SubscribeResult, duck-ipc-proto/src/lib.rs:2519-2546
#   accepted: bool
#   walk, stand, unavailable, sitstand, ground_pick: Option<String>   (file names)
#   skills: Vec<String>
#
# There is NO `networks` key and no `status` key on this struct. Handler:
# robotd/src/main.rs:4282-4300. The policy file names are the nine seeded by
# scripts/seed-policies.sh:47-49.
# ---------------------------------------------------------------------------
SUBSCRIBE = {
    "accepted": True,
    "walk": "alpha_walking.onnx",
    "stand": "alpha_stand.onnx",
    "sitstand": "alpha_sitstand.onnx",
    "ground_pick": "alpha_ground_pick.onnx",
    "skills": ["ground_pick", "kick_left", "kick_right", "sit_toggle", "roulade"],
}

#: The same reply from a board that could not reach Hugging Face at install time
#: (``scripts/seed-policies.sh:25-31`` — no gait, non-fatally). ``unavailable`` is
#: the field that says why nothing is driving (:2529-2534).
SUBSCRIBE_NO_GAIT = {
    "accepted": True,
    "unavailable": "walking policy disabled in params",
    "skills": [],
}

# ---------------------------------------------------------------------------
# robot.health — HealthResult, duck-ipc-proto/src/lib.rs:3089-3147
#   healthy: bool                                   (:3090)
#   degraded: bool                                  (:3103)
#   reason: Option<String>                          (:3105)
#   battery: Option<Battery>{volts, percent}        (:3118, struct at :3263-3266)
#   motors: Option<MotorThermal>                    (:3122)
#   cpu_temp_c: Option<f64>                         (:3132)
#   control_loop: Option<LoopHealth>                (:3140)   <-- NOT `loop`
#   bus: BusHealth{consecutive_errors, startup_failures}  (:3144)
#   imu: Option<ImuHealth>                          (:3147)
#
# LoopHealth, :3152-3166:
#   target_hz: f64
#   achieved_hz: Option<f64>   ("None until the first window closes")
#   ticks: u64
#   missed: u64
#   last_tick_age_ms: u64
# ---------------------------------------------------------------------------
HEALTH = {
    "healthy": True,
    "degraded": False,
    "control_loop": {
        "target_hz": 50.0,
        "achieved_hz": 49.8,
        "ticks": 124_318,
        "missed": 0,
        "last_tick_age_ms": 18,
    },
    "battery": {"volts": 7.9, "percent": 64.0},
    "cpu_temp_c": 47.2,
    "motors": {"hottest": "left_knee", "max_c": 44.0, "mean_c": 38.5},
    "bus": {"consecutive_errors": 0, "startup_failures": 0},
    "imu": {"ready": True, "stale_blocks": 3, "consecutive_stale_blocks": 0},
}

#: A duck in its first second of uptime. ``achieved_hz`` is ``None`` — *unknown*,
#: which the proto is explicit about (:3157-3160): "a rate of 0 Hz describes a
#: stopped loop, and printing that for the first second of every robot's uptime
#: would be a lie."
HEALTH_NO_WINDOW_YET = {
    "healthy": True,
    "degraded": False,
    "control_loop": {
        "target_hz": 50.0,
        "achieved_hz": None,
        "ticks": 7,
        "missed": 0,
        "last_tick_age_ms": 19,
    },
    "battery": {"volts": 8.1, "percent": 78.0},
    "bus": {"consecutive_errors": 0, "startup_failures": 0},
}

#: A duck below the choreographer's 12% floor.
HEALTH_FLAT_BATTERY = {
    **HEALTH,
    "degraded": True,
    "reason": "battery low",
    "battery": {"volts": 6.9, "percent": 9.0},
}

# ---------------------------------------------------------------------------
# robot.state notification — RobotState, duck-ipc-proto/src/lib.rs:3317-3365
#
# There is NO battery on this struct. docs/robot/cheatsheet.md:51-53 says so
# outright, "because none of it is on the state stream".
#   movement: MoveState{requested, applied, limited_by}   (:3480-3485)
#   policy: String  ("walk" | "stand" | "held")
#   safety: SafetyState{fallen, limp, gravity, gain}      (:3491-3508)
#   control_loop: LoopState{hz, missed}                   (:3512-3517)  <-- has `hz`
#   odom: OdomState{position: [f64; 3], yaw: f64}         (:3472-3476)
# ---------------------------------------------------------------------------
STATE = {
    "t": 61.42,
    "movement": {
        "requested": [0.06, 0.0, 0.0],
        "applied": [0.06, 0.0, 0.0],
        "limited_by": [],
    },
    "head": [0.0, 0.0, 0.0, 0.0],
    "policy": "walk",
    "safety": {"fallen": False, "limp": False, "gravity": [0.0, 0.0, -9.81]},
    # The struct that genuinely has `hz` and `missed`. Reading these names off this
    # stream and applying them to robot.health is exactly the bug being fixed.
    "control_loop": {"hz": 49.8, "missed": 0},
    "joints": [0.0] * 15,
    "targets": [0.0] * 15,
    "odom": {"position": [1.0, 2.0, 0.31], "yaw": 0.5},
    "t_ns": 61_420_000_000,
}

STATE_FALLEN = {
    **STATE,
    "policy": "held",
    "safety": {"fallen": True, "limp": False, "gravity": [0.0, -9.7, -1.2]},
}

#: robot.stop / robot.enable — IntentResult, :3283-3290: {accepted, reason?}
INTENT_ACCEPTED = {"accepted": True}

#: What every other wrapped method answers with, absent a richer struct.
_DEFAULT_RESULTS = {
    "hello": HELLO,
    "robot.subscribe": SUBSCRIBE,
    "robot.health": HEALTH,
    "robot.stop": INTENT_ACCEPTED,
    "robot.enable": INTENT_ACCEPTED,
    "robot.init": INTENT_ACCEPTED,
    "robot.relax": INTENT_ACCEPTED,
    "robot.safeToRestart": True,
}


def reply_for(method: str, overrides: dict | None = None):
    """The result robotd would return for *method*, or ``{}``."""
    table = dict(_DEFAULT_RESULTS)
    table.update(overrides or {})
    return table.get(method, {})


class WireRobotd:
    """A robotd-shaped Unix socket that answers with the fixtures above.

    NDJSON JSON-RPC 2.0, one object per line, notifications answered with silence —
    the framing ``robotd`` uses (``robotd/src/main.rs:3232``).
    """

    def __init__(self, path: str, overrides: dict | None = None) -> None:
        self.path = path
        self.overrides = dict(overrides or {})
        self.notifications: list[dict] = []
        self.requests: list[dict] = []
        self._conn: socket.socket | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

        if os.path.exists(path):
            os.unlink(path)
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(path)
        self._srv.listen(4)
        self._srv.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    # -- server ------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self._conn = conn
            conn.settimeout(0.2)
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if raw.strip():
                        try:
                            self._handle(json.loads(raw))
                        except ValueError:
                            continue
            try:
                conn.close()
            except OSError:
                pass
            self._conn = None

    def _handle(self, msg: dict) -> None:
        method = msg.get("method")
        if "id" not in msg:
            with self._lock:
                self.notifications.append(msg)
            return
        with self._lock:
            self.requests.append(msg)
        self.send(
            {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": reply_for(str(method), self.overrides),
            }
        )

    # -- helpers -----------------------------------------------------

    def send(self, obj: dict) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            conn.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            pass

    def push_state(self, params: dict | None = None) -> None:
        """Push a ``robot.state`` notification, as robotd does after subscribe."""
        self.send(
            {
                "jsonrpc": "2.0",
                "method": "robot.state",
                "params": STATE if params is None else params,
            }
        )

    def notifications_for(self, method: str) -> list[dict]:
        with self._lock:
            return [n for n in self.notifications if n.get("method") == method]

    def request_methods(self) -> list[str]:
        with self._lock:
            return [r.get("method") for r in self.requests]

    def close(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--socket", default="/tmp/duck-wire.sock")
    ap.add_argument(
        "--unhealthy",
        action="store_true",
        help="answer robot.health with healthy: false",
    )
    ap.add_argument(
        "--no-gait",
        action="store_true",
        help="answer robot.subscribe with no walking policy loaded",
    )
    args = ap.parse_args()

    overrides: dict = {}
    if args.unhealthy:
        overrides["robot.health"] = {**HEALTH, "healthy": False, "reason": "motor bus down"}
    if args.no_gait:
        overrides["robot.subscribe"] = SUBSCRIBE_NO_GAIT

    server = WireRobotd(args.socket, overrides)
    print(f"wire-shaped robotd on {args.socket} — replies transcribed from duck-ipc-proto")
    try:
        while True:
            time.sleep(0.5)
            server.push_state()
    except KeyboardInterrupt:
        server.close()


if __name__ == "__main__":
    _main()
