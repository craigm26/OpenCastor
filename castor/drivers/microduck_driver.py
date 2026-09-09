"""
Pollen Robotics Microduck driver for OpenCastor.

Speaks ``robotd``'s JSON-RPC 2.0 contract directly — one object per line
(NDJSON) over a Unix domain socket at ``/run/robotd.sock``.  This is the same
wire protocol used by ``robotctl``, ``padd`` and the phone app, so OpenCastor
is a first-class client rather than a bolted-on layer.

Transports
----------
``unix``
    Direct connection to ``/run/robotd.sock``.  Use when OpenCastor runs on the
    duck itself.  Requires membership in the ``robot`` group::

        sudo usermod -aG robot "$USER"

``ssh``
    Off-board.  Opens an ``ssh -L <local_port>:/run/robotd.sock`` forward to the
    duck and talks to the local end.  Nothing extra is installed on the robot;
    OpenSSH forwards to Unix sockets natively.  This is the recommended layout —
    the duck has 1 GB of RAM and a 50 Hz control loop to protect, so the brain
    belongs on another machine.

``tcp``
    Connect to an already-established forward or bridge at ``host:port``.

``webrtc``
    Off-board over the network the duck already ships: ``mediad``'s signalling
    server on ``<host>:8443`` and the ``control`` datachannel, which carries
    the same JSON-RPC as the socket.  No SSH key, no ``robot`` group edit, no
    reboot, nothing installed on the duck.  Needs the optional extra::

        pip install 'opencastor[microduck-webrtc]'

    **``mediad`` does not authenticate and binds all interfaces.**  Choosing
    this transport is choosing that; ``castor/drivers/microduck_webrtc.py``
    and ``docs/hardware/microduck.md`` carry the reasoning verbatim from
    ``mediad/src/main.rs:9-13``.

RCAN config::

    drivers:
      - id: duck
        protocol: microduck
        transport: ssh          # unix | ssh | tcp
        ssh_host: 192.168.1.42  # `duckctl ip` over Bluetooth, or your DHCP table
        ssh_user: pierre
        max_vx: 0.2             # m/s at |linear| == 1.0
        max_vyaw: 1.0           # rad/s at |angular| == 1.0

Deadman
-------
``robotd`` zeroes the twist if intents stop arriving (~0.5 s).  OpenCastor's
``move()`` is a one-shot call, so this driver runs a background intent loop that
re-sends the last twist at ``intent_hz`` until ``command_ttl_s`` elapses, then
sends a single zero and goes quiet.  That gives two independent deadmen: ours,
so a wedged brain cannot leave the duck walking, and robotd's, so a wedged
driver cannot either.

Safety
------
Velocity commands go through ``_move()``, so they are routed through
OpenCastor's SafetyLayer when one is attached.  ``robotd`` applies its own
limits on top and reports them in ``robot.state`` as ``limited_by`` — the
authoritative envelope lives on the robot, not here.  ``init()``, ``relax()``
and ``enable()`` reach the duck over every transport this driver has, WebRTC
included: ``mediad/src/route.rs:86-91`` permits them precisely because "a peer
holding this session has the camera: it is looking at the robot".  What WebRTC
refuses is ``robot.setMode``, the pairing PIN and the ``update.*`` mutations
(``route.rs:100-104``, ``:22-27``).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from typing import Any, Optional

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.MicroduckDriver")

__all__ = ["MicroduckDriver"]

#: Default ``robotd`` control socket on the robot.
DEFAULT_SOCKET = "/run/robotd.sock"

#: Rate at which the intent loop re-sends the last twist (Hz).
DEFAULT_INTENT_HZ = 20.0

#: How long a single ``move()`` stays alive before this driver zeroes it (s).
DEFAULT_COMMAND_TTL_S = 1.5

#: robotd's own deadman — velocity zeroes if intents stop arriving.  Informational;
#: ``intent_hz`` must stay comfortably above ``1 / _ROBOTD_DEADMAN_S``.
_ROBOTD_DEADMAN_S = 0.5

#: Default velocity envelope, in robotd units, at full stick deflection.
DEFAULT_MAX_VX = 0.2  # m/s
DEFAULT_MAX_VY = 0.1  # m/s
DEFAULT_MAX_VYAW = 1.0  # rad/s

#: What Pollen's own gamepad daemon allows, for comparison when we print our
#: envelope.  ``padd/src/main.rs:139-163`` — ``--max-linear`` 0.3 m/s,
#: ``--max-linear-backward`` 0.3, ``--max-angular`` 1.5 rad/s.
PADD_MAX_LINEAR_MS = 0.3
PADD_MAX_ANGULAR_RADS = 1.5

#: The named policy slots on ``SubscribeResult`` (``duck-ipc-proto/src/lib.rs:2519-2546``).
#: Each is an ``Option<String>`` holding a **file name**, absent when that slot is
#: not loaded.  There is no ``networks`` key on this reply and never was.
POLICY_SLOTS = ("walk", "stand", "sitstand", "ground_pick")


def loop_hz(control_loop: Optional[dict]) -> Optional[float]:
    """Return the loop rate to show a human, or ``None`` when it is not known yet.

    ``LoopHealth`` (``duck-ipc-proto/src/lib.rs:3152-3166``) carries ``target_hz``
    (always) and ``achieved_hz`` (``Option<f64>`` — ``None`` until the first window
    closes, which is *unknown*, not zero).  Prefer the achieved figure and fall back
    to the configured one; never invent a number when neither is present.
    """
    if not isinstance(control_loop, dict):
        return None
    achieved = control_loop.get("achieved_hz")
    if isinstance(achieved, (int, float)):
        return float(achieved)
    target = control_loop.get("target_hz")
    if isinstance(target, (int, float)):
        return float(target)
    return None


def _policy_slots(subscribe_result: dict) -> dict:
    """Normalise a ``robot.subscribe`` reply into ``{slot: name}`` plus ``skills``.

    Transcribed from ``SubscribeResult`` (``duck-ipc-proto/src/lib.rs:2519-2546``,
    handler ``robotd/src/main.rs:4282-4300``)::

        accepted: bool
        walk, stand, unavailable, sitstand, ground_pick: Option<String>
        skills: Vec<String>

    ``unavailable`` is *why nothing is driving* when nothing is — a disabled policy
    or one that failed to load — and is deliberately kept, because a duck that will
    not walk is the case an owner needs named.
    """
    out: dict = {}
    for slot in POLICY_SLOTS:
        name = subscribe_result.get(slot)
        if isinstance(name, str) and name:
            out[slot] = name
    skills = subscribe_result.get("skills")
    out["skills"] = [s for s in skills if isinstance(s, str)] if isinstance(skills, list) else []
    unavailable = subscribe_result.get("unavailable")
    if isinstance(unavailable, str) and unavailable:
        out["unavailable"] = unavailable
    out["accepted"] = bool(subscribe_result.get("accepted"))
    return out


def _policy_names(subscribe_result: dict) -> list[str]:
    """Flatten a ``robot.subscribe`` reply to the policy/skill names it reported."""
    slots = _policy_slots(subscribe_result)
    names = [slots[slot] for slot in POLICY_SLOTS if slot in slots]
    names.extend(s for s in slots.get("skills", []) if s not in names)
    return names


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


class MicroduckDriver(DriverBase):
    """Driver for the Pollen Robotics Microduck biped.

    Args:
        config: RCAN driver config dict. Relevant keys:

            - ``transport`` (str): ``"unix"``, ``"ssh"``, ``"tcp"`` or
              ``"webrtc"``. Default: ``"unix"``.
            - ``socket`` (str): robotd socket path. Default ``/run/robotd.sock``.
            - ``ssh_host`` / ``ssh_user`` / ``ssh_port`` (str/int): SSH forward target.
            - ``local_port`` (int): local end of the SSH forward. Default ``7788``.
            - ``host`` / ``port``: target for ``transport: tcp``, and for
              ``transport: webrtc`` (where ``port`` defaults to ``8443``).
            - ``robot`` / ``webrtc_video`` / ``webrtc_timeout_s``: see
              :mod:`castor.drivers.microduck_webrtc`.
            - ``max_vx`` / ``max_vy`` / ``max_vyaw`` (float): velocity envelope at
              full deflection.
            - ``intent_hz`` (float): intent re-send rate. Default ``20``.
            - ``command_ttl_s`` (float): driver-side deadman. Default ``1.5``.
            - ``rpc_timeout_s`` (float): request/response timeout. Default ``2.0``.
            - ``subscribe_hz`` (int): ask robotd for a slower ``robot.state`` stream.
              Absent means every tick, which is what ``SubscribeParams`` documents.
            - ``auto_init`` (bool): call ``robot.init`` on connect. Default ``False``
              — the duck deliberately does not move on process start.
    """

    def __init__(self, config: dict) -> None:
        self._config = dict(config or {})

        self._transport = str(self._config.get("transport", "unix")).lower()
        self._socket_path = str(self._config.get("socket", DEFAULT_SOCKET))
        self._ssh_host: Optional[str] = self._config.get("ssh_host")
        self._ssh_user: Optional[str] = self._config.get("ssh_user")
        self._ssh_port = int(self._config.get("ssh_port", 22))
        self._local_port = int(self._config.get("local_port", 7788))
        self._host: Optional[str] = self._config.get("host", "127.0.0.1")
        self._port = int(self._config.get("port", self._local_port))
        # The bridge handshake, and the only two config keys it needs. Read
        # here, used in exactly one place (`_bridge_hello`), touched by nothing
        # else in this driver.
        self._bridge_token: Optional[str] = self._config.get("bridge_token")
        self._bridge_token_file: Optional[str] = self._config.get("bridge_token_file")

        self._max_vx = float(self._config.get("max_vx", DEFAULT_MAX_VX))
        self._max_vy = float(self._config.get("max_vy", DEFAULT_MAX_VY))
        self._max_vyaw = float(self._config.get("max_vyaw", DEFAULT_MAX_VYAW))

        self._intent_hz = max(
            1.0 / _ROBOTD_DEADMAN_S, float(self._config.get("intent_hz", DEFAULT_INTENT_HZ))
        )
        self._command_ttl_s = float(self._config.get("command_ttl_s", DEFAULT_COMMAND_TTL_S))
        self._rpc_timeout_s = float(self._config.get("rpc_timeout_s", 2.0))

        self._mode = "mock"
        self._target = "<disconnected>"
        self._sock: Optional[socket.socket] = None
        self._ssh_proc: Optional[subprocess.Popen] = None
        self._rx = b""

        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, dict] = {}

        self._alive = False
        self._reader: Optional[threading.Thread] = None
        self._intent_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

        # Intent slots — robotd treats twist and head as separate last-writer-wins
        # slots, so they expire independently.
        self._twist = (0.0, 0.0, 0.0)
        self._twist_ts = 0.0
        self._twist_idle = True
        self._head: Optional[dict[str, float]] = None
        self._head_ts = 0.0
        # Mouth and body pose are continuous intents too — same last-writer-wins
        # slot discipline, same expiry, so a released button stops being held.
        self._mouth: Optional[dict[str, float]] = None
        self._mouth_ts = 0.0
        self._pose: Optional[dict[str, Any]] = None
        self._pose_ts = 0.0

        self._last_state: dict[str, Any] = {}
        self._policies: list[str] = []
        self._policy_slots: dict[str, Any] = {}
        self._last_health: dict[str, Any] = {}
        self._last_health_ts = 0.0

        self._connect()

        if self._mode == "hardware" and bool(self._config.get("auto_init", False)):
            try:
                self.init()
            except Exception as exc:
                logger.warning("MicroduckDriver auto_init failed: %s", exc)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open the control connection, degrading to mock mode on failure."""
        try:
            if self._transport == "unix":
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(self._rpc_timeout_s)
                sock.connect(self._socket_path)
                target = self._socket_path
            elif self._transport == "ssh":
                self._start_ssh_forward()
                sock = socket.create_connection(
                    ("127.0.0.1", self._local_port), timeout=self._rpc_timeout_s
                )
                target = f"ssh://{self._ssh_host}{self._socket_path}"
            elif self._transport == "tcp":
                sock = socket.create_connection(
                    (self._host, self._port), timeout=self._rpc_timeout_s
                )
                self._bridge_hello(sock)
                target = f"{self._host}:{self._port}"
            elif self._transport == "webrtc":
                # mediad's `control` datachannel, shaped like a socket so the
                # NDJSON reader, writer, id correlation and intent loop below
                # are used unchanged. See castor/drivers/microduck_webrtc.py.
                from castor.drivers.microduck_webrtc import (
                    DEFAULT_SIGNALLING_PORT,
                    connect_webrtc,
                )

                sock = connect_webrtc(self._config)
                target = (
                    f"webrtc://{self._host}:"
                    f"{self._config.get('port') or DEFAULT_SIGNALLING_PORT}"
                )
            else:
                logger.warning("MicroduckDriver: unknown transport %r — mock mode", self._transport)
                return
        except Exception as exc:
            # A missing optional extra is a config error, not a duck that is
            # off: degrading to mock mode would hide the one line that fixes it.
            if type(exc).__name__ == "WebRTCUnavailable":
                self._kill_ssh()
                raise
            logger.warning(
                "MicroduckDriver connect failed (%s): %s — mock mode", self._transport, exc
            )
            self._kill_ssh()
            return

        sock.settimeout(0.2)
        self._sock = sock
        self._mode = "hardware"
        self._alive = True
        self._target = target

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._intent_thread = threading.Thread(target=self._intent_loop, daemon=True)
        self._intent_thread.start()

        logger.info("MicroduckDriver connected to %s", target)

        # Subscribe so robot.state notifications start flowing; health, battery and
        # odometry then come from the cached last value rather than a synchronous RPC.
        try:
            # `params` must be an OBJECT, not absent. `SubscribeParams`
            # (duck-ipc-proto/src/lib.rs:2500-2505) is a struct, and omitting the key
            # sends `null`, which robotd refuses outright:
            #   -32602 "invalid type: null, expected struct SubscribeParams"
            # measured against a real robotd 0.11.0 under Pollen's scripts/duck-sim,
            # 2026-09-08. So `robot.subscribe` has never once succeeded against a
            # real duck, on top of the reply then being read for a key it never had.
            # `{}` means every tick; `subscribe_hz` asks for a slower stream.
            params: dict[str, Any] = {}
            hz = self._config.get("subscribe_hz")
            if hz:
                params["hz"] = int(hz)
            result = self._request("robot.subscribe", params)
            if isinstance(result, dict):
                self._policy_slots = _policy_slots(result)
                self._policies = _policy_names(result)
                logger.info(
                    "Microduck ready: accepted=%s policies=%s unavailable=%s",
                    result.get("accepted"),
                    self._policies,
                    result.get("unavailable"),
                )
        except Exception as exc:
            logger.warning("MicroduckDriver robot.subscribe failed: %s", exc)

    def _bridge_hello(self, sock: socket.socket) -> None:
        """Say hello to a `microduck-bridge`, if this `tcp` target is one.

        WHY THIS IS ONE SMALL METHOD AND NOT A TRANSPORT. `transport: tcp`
        already reached the bridge's port — 7788 is this driver's `local_port`
        default and the bridge's bind port and StudioKit's
        `BridgeHandshake.defaultPort`, three files that agreed before anybody
        connected them. The ONLY thing missing was the first line. So the
        change is a first line, sent at connect time, and nothing about framing,
        parsing, intents or the request/response split moves.

        THE HANDSHAKE IS SKIPPED WHEN THERE IS NO TOKEN, deliberately. A
        `transport: tcp` pointed at a plain forward (`socat`, an `ssh -L` an
        operator opened by hand, `robotd` behind anything else) has worked for
        as long as this driver has existed, and a hello line would be the first
        thing that broke it. No token configured and no token file on disk means
        the previous behaviour, unchanged.

        Raises on a refusal rather than continuing: `_connect` catches it and
        degrades to mock mode with the bridge's own sentence in the log, which
        is the difference between "wrong token" and a driver that reports
        healthy while every command falls into a closed socket.
        """
        from castor.microduck_bridge import (
            DEFAULT_TOKEN_FILE,
            Refusal,
            hello_line,
            read_greeting,
            read_token,
        )

        token = self._bridge_token
        if not token:
            path = self._bridge_token_file or DEFAULT_TOKEN_FILE
            try:
                token = read_token(os.path.expanduser(str(path)))
            except Refusal as why:
                if self._bridge_token_file:
                    # Named a file explicitly and it is not usable: that is a
                    # configuration error, not a plain-relay target.
                    raise RuntimeError(f"bridge token: {why}") from why
                logger.debug("MicroduckDriver: no bridge token (%s) — sending no hello", why)
                return

        sock.sendall(hello_line(token))
        line = b""
        while not line.endswith(b"\n") and len(line) < 4096:
            chunk = sock.recv(1)
            if not chunk:
                raise RuntimeError("the bridge closed the connection without answering the hello")
            line += chunk
        greeting = read_greeting(line)
        logger.info(
            "MicroduckDriver: bridge %s, deadman %s ms",
            greeting.get("bridge", "?"),
            greeting.get("deadman_ms", "unstated"),
        )

    def _start_ssh_forward(self) -> None:
        """Spawn ``ssh -N -L <local_port>:<robotd.sock>`` and wait for it to listen."""
        if not self._ssh_host:
            raise RuntimeError("transport 'ssh' requires ssh_host")
        if shutil.which("ssh") is None:
            raise RuntimeError("ssh not found on PATH")

        dest = f"{self._ssh_user}@{self._ssh_host}" if self._ssh_user else self._ssh_host
        cmd = [
            "ssh",
            "-N",
            "-T",
            "-p",
            str(self._ssh_port),
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            "-L",
            f"127.0.0.1:{self._local_port}:{self._socket_path}",
            dest,
        ]
        logger.debug("MicroduckDriver ssh forward: %s", " ".join(cmd))
        self._ssh_proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )

        deadline = time.monotonic() + max(2.0, self._rpc_timeout_s * 3)
        while time.monotonic() < deadline:
            if self._ssh_proc.poll() is not None:
                err = b""
                if self._ssh_proc.stderr is not None:
                    err = self._ssh_proc.stderr.read() or b""
                raise RuntimeError(f"ssh forward exited: {err.decode(errors='replace').strip()}")
            try:
                probe = socket.create_connection(("127.0.0.1", self._local_port), timeout=0.2)
                probe.close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("ssh forward did not come up in time")

    def _kill_ssh(self) -> None:
        proc, self._ssh_proc = self._ssh_proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # NDJSON transport
    # ------------------------------------------------------------------

    def _write(self, obj: dict) -> None:
        sock = self._sock
        if sock is None:
            raise RuntimeError("not connected")
        line = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        with self._io_lock:
            sock.sendall(line)

    def _notify(self, method: str, params: Optional[dict] = None) -> None:
        """Send a JSON-RPC notification (continuous intent, no reply expected)."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def _request(
        self, method: str, params: Optional[dict] = None, *, timeout: Optional[float] = None
    ) -> Any:
        """Send a JSON-RPC request and block for its correlated response."""
        timeout = self._rpc_timeout_s if timeout is None else timeout
        with self._io_lock:
            req_id = self._next_id
            self._next_id += 1
        slot = {"event": threading.Event(), "result": None, "error": None}
        self._pending[req_id] = slot

        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._write(msg)
            if not slot["event"].wait(timeout):
                raise TimeoutError(f"microduck RPC timeout: {method}")
        finally:
            self._pending.pop(req_id, None)

        if slot["error"] is not None:
            raise RuntimeError(f"microduck RPC error on {method}: {slot['error']}")
        return slot["result"]

    def _read_loop(self) -> None:
        """Consume NDJSON frames: correlate responses, cache state notifications."""
        while not self._stop_evt.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                break
            if not chunk:
                break

            self._rx += chunk
            while b"\n" in self._rx:
                raw, self._rx = self._rx.split(b"\n", 1)
                if not raw.strip():
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("microduck: dropping unparseable frame %r", raw[:120])
                    continue
                self._dispatch(msg)

        if not self._stop_evt.is_set():
            logger.warning("MicroduckDriver: robotd connection closed — falling back to mock")
            self._alive = False
            self._mode = "mock"

    def _dispatch(self, msg: dict) -> None:
        msg_id = msg.get("id")
        if msg_id is not None:
            slot = self._pending.get(msg_id)
            if slot is not None:
                slot["error"] = msg.get("error")
                slot["result"] = msg.get("result")
                slot["event"].set()
            return

        if msg.get("method") == "robot.state":
            with self._state_lock:
                self._last_state = msg.get("params") or {}

    # ------------------------------------------------------------------
    # Intent loop (deadman)
    # ------------------------------------------------------------------

    def _intent_loop(self) -> None:
        """Re-send live intents so robotd's deadman stays fed, and expire them."""
        period = 1.0 / self._intent_hz
        while not self._stop_evt.wait(period):
            if not self._alive:
                return
            now = time.monotonic()

            expired = now - self._twist_ts > self._command_ttl_s
            if not self._twist_idle:
                if expired:
                    # Our own deadman: one explicit zero, then go quiet and let
                    # robotd hold the duck standing (stop is not limp).
                    self._twist = (0.0, 0.0, 0.0)
                    self._twist_idle = True
                self._send_twist()

            head = self._head
            if head is not None:
                if now - self._head_ts > self._command_ttl_s:
                    self._head = None
                else:
                    self._safe_notify("robot.head", head)

            mouth = self._mouth
            if mouth is not None:
                if now - self._mouth_ts > self._command_ttl_s:
                    self._mouth = None
                else:
                    self._safe_notify("robot.mouth", mouth)

            pose = self._pose
            if pose is not None:
                if now - self._pose_ts > self._command_ttl_s:
                    # Expiry snaps the body back to nominal rather than leaving
                    # it leaning — `active: false` is robotd's own instant exit.
                    self._safe_notify("robot.pose", {**pose, "active": False})
                    self._pose = None
                else:
                    self._safe_notify("robot.pose", pose)

    def _send_twist(self) -> None:
        vx, vy, vyaw = self._twist
        self._safe_notify("robot.move", {"vx": vx, "vy": vy, "vyaw": vyaw})

    def _safe_notify(self, method: str, params: dict) -> None:
        try:
            self._notify(method, params)
        except Exception as exc:
            logger.debug("microduck notify %s failed: %s", method, exc)

    # ------------------------------------------------------------------
    # DriverBase interface
    # ------------------------------------------------------------------

    def _move(self, linear: float = 0.0, angular: float = 0.0) -> None:
        """Send a velocity intent, scaled into robotd's trunk-frame twist.

        Args:
            linear: Forward speed in ``[-1.0, 1.0]``, scaled by ``max_vx`` (m/s).
            angular: Turn rate in ``[-1.0, 1.0]``, scaled by ``max_vyaw`` (rad/s).
        """
        vx = _clamp(linear) * self._max_vx
        vyaw = _clamp(angular) * self._max_vyaw
        self._twist = (vx, 0.0, vyaw)
        self._twist_ts = time.monotonic()
        self._twist_idle = False

        if self._mode == "mock":
            logger.debug("MOCK microduck move: vx=%.3f vyaw=%.3f", vx, vyaw)
            return
        self._send_twist()

    def strafe(self, lateral: float) -> None:
        """Send a sideways velocity intent — the duck's twist has a real ``vy``.

        Args:
            lateral: Lateral speed in ``[-1.0, 1.0]``, scaled by ``max_vy``.
                     Positive is left (right-handed trunk frame).
        """
        vx, _, vyaw = self._twist
        self._twist = (vx, _clamp(lateral) * self._max_vy, vyaw)
        self._twist_ts = time.monotonic()
        self._twist_idle = False
        if self._mode != "mock":
            self._send_twist()

    def stop(self) -> None:
        """Halt motion. Standing still, not limp — use :meth:`relax` to cut torque."""
        self._twist = (0.0, 0.0, 0.0)
        self._twist_idle = True
        self._head = None
        self._mouth = None
        self._pose = None
        if self._mode == "mock":
            logger.debug("MOCK microduck stop")
            return
        try:
            self._safe_notify("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0})
            self._request("robot.stop", timeout=min(1.0, self._rpc_timeout_s))
        except Exception as exc:
            logger.warning("MicroduckDriver.stop failed: %s", exc)

    def close(self) -> None:
        """Stop the duck, tear down threads, drop the connection and SSH forward."""
        if self._mode == "hardware":
            try:
                self.stop()
            except Exception:
                pass

        self._alive = False
        self._stop_evt.set()

        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

        for thread in (self._reader, self._intent_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)
        self._reader = self._intent_thread = None

        self._kill_ssh()
        self._mode = "mock"

    def health_check(self) -> dict:
        """Query ``robot.health``.

        The wire contract is ``HealthResult`` (``duck-ipc-proto/src/lib.rs:3089-3147``):
        ``healthy``, ``degraded``, ``reason``, ``battery`` (``Battery`` — ``volts`` and
        ``percent``, ``:3263-3266``), ``motors``, ``cpu_temp_c``, ``control_loop``
        (``LoopHealth`` — ``target_hz``, ``achieved_hz``, ``ticks``, ``missed``,
        ``last_tick_age_ms``, ``:3152-3166``), ``bus`` and ``imu``.

        The key is ``control_loop``, not ``loop``, and there is no serde rename.
        ``loop`` with an ``hz`` field is a **different** struct — ``LoopState`` on the
        ``robot.state`` stream (``:3512-3517``) — and reading one for the other is why
        a healthy duck used to print ``loop ? Hz``.  ``loop`` is still returned here as
        an alias so older callers keep working, but it now holds the health struct.

        Returns:
            Dict with ``ok``, ``mode``, ``error``, ``degraded``, ``reason``, plus
            ``control_loop``, ``loop_hz``, ``battery``, ``imu`` and ``bus`` when
            connected to real hardware.
        """
        if self._mode == "mock":
            return {"ok": True, "mode": "mock", "error": None, "transport": self._transport}

        try:
            res = self._request("robot.health", timeout=min(1.0, self._rpc_timeout_s))
        except Exception as exc:
            return {
                "ok": False,
                "mode": "hardware",
                "error": str(exc),
                "transport": self._transport,
            }

        if not isinstance(res, dict):
            return {"ok": False, "mode": "hardware", "error": f"bad health payload: {res!r}"}

        healthy = bool(res.get("healthy"))
        control_loop = res.get("control_loop")
        battery = res.get("battery")
        reason = res.get("reason")
        with self._state_lock:
            self._last_health = dict(res)
            self._last_health_ts = time.monotonic()
        return {
            "ok": healthy,
            "mode": "hardware",
            "error": None if healthy else (reason or "robotd reports unhealthy"),
            "transport": self._transport,
            "degraded": bool(res.get("degraded")),
            "reason": reason,
            "control_loop": control_loop,
            # Back-compat alias. Same object, correct source.
            "loop": control_loop,
            "loop_hz": loop_hz(control_loop),
            "battery": battery,
            "imu": res.get("imu"),
            "bus": res.get("bus"),
            "cpu_temp_c": res.get("cpu_temp_c"),
            "motors": res.get("motors"),
        }

    # ------------------------------------------------------------------
    # Extended API — bring-up, head, telemetry
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Torque servos on and ramp to the standing pose (~2 s). Maintenance call."""
        if self._mode == "mock":
            logger.debug("MOCK microduck init")
            return
        self._request("robot.init", timeout=max(5.0, self._rpc_timeout_s))

    def relax(self) -> None:
        """Cut power to every servo. The duck goes limp — make sure it is seated."""
        if self._mode == "mock":
            logger.debug("MOCK microduck relax")
            return
        self._twist_idle = True
        self._request("robot.relax")

    def enable(self, on: bool = True) -> None:
        """Enable or disable the RL policy that actually drives the joints."""
        if self._mode == "mock":
            logger.debug("MOCK microduck enable: %s", on)
            return
        self._request("robot.enable", {"on": bool(on)})

    def head(
        self,
        neck_pitch: float = 0.0,
        head_pitch: float = 0.0,
        head_yaw: float = 0.0,
        head_roll: float = 0.0,
    ) -> None:
        """Point the head. Angles in radians, trunk frame.

        Head is a separate last-writer-wins intent slot from the twist, so this
        does not disturb walking.
        """
        params = {
            "neck_pitch": float(neck_pitch),
            "head_pitch": float(head_pitch),
            "head_yaw": float(head_yaw),
            "head_roll": float(head_roll),
        }
        self._head = params
        self._head_ts = time.monotonic()
        if self._mode == "mock":
            logger.debug("MOCK microduck head: %s", params)
            return
        self._safe_notify("robot.head", params)

    def look_at(self, x: float, y: float, z: float, neck_pitch: float = 0.0) -> None:
        """Aim the head at a point, using robotd's own inverse kinematics.

        ``robot.look`` is not ``robot.head`` with the trigonometry moved: the
        daemon holds ``neck_pitch`` as posture and aims the remaining three
        joints around it, which is a better answer than any yaw/pitch this end
        can compute. Mirrors ``ReachyDriver.look_at``.

        Args:
            x: Forward of the trunk origin, metres.
            y: Left of it, metres.
            z: Above it, metres — trunk frame, so the floor is ~0.12 m below 0.
            neck_pitch: Posture the aim is computed around, radians.
        """
        params = {"x": float(x), "y": float(y), "z": float(z), "neck_pitch": float(neck_pitch)}
        if self._mode == "mock":
            logger.debug("MOCK microduck look: %s", params)
            return
        self._safe_notify("robot.look", params)

    # ------------------------------------------------------------------
    # Skills — the one-shot scripted moves robotd schedules
    # ------------------------------------------------------------------

    #: Skills the daemon will run, named exactly as the wire spells them.
    SKILLS = ("ground_pick", "kick_left", "kick_right", "sit_toggle", "roulade")

    def do_skill(self, skill: str) -> Any:
        """Run a one-shot skill. Answered — a refusal names what is holding the duck.

        Args:
            skill: One of :data:`SKILLS`.
        """
        if skill not in self.SKILLS:
            raise ValueError(f"unknown skill {skill!r}; expected one of {self.SKILLS}")
        if self._mode == "mock":
            logger.debug("MOCK microduck skill: %s", skill)
            return None
        return self._request("robot.do", {"skill": skill}, timeout=max(5.0, self._rpc_timeout_s))

    def kick(self, left: bool = False) -> Any:
        """Kick with one leg. Half a second, and blind — the duck does not look
        for a ball, so aiming is the caller's job."""
        return self.do_skill("kick_left" if left else "kick_right")

    def ground_pick(self) -> Any:
        """Run the scripted ground pick — the beak goes down and comes up with
        whatever was there. One shot, about three seconds."""
        return self.do_skill("ground_pick")

    def sit_toggle(self) -> Any:
        """Sit if standing, stand if sitting. The daemon knows which."""
        return self.do_skill("sit_toggle")

    def roulade(self) -> Any:
        """One forward roll, about a second. Requests made during a roll chain
        another when it finishes, which is how a held button maps onto it."""
        return self.do_skill("roulade")

    # ------------------------------------------------------------------
    # Voice, mouth, body pose, theremin
    # ------------------------------------------------------------------

    #: The voice bank, as the wire spells it.
    SOUNDS = ("alarm", "greet", "inquire", "peck", "chirp", "coo", "wheee")

    def sound(self, tag: str = "chirp", hold: Optional[bool] = None) -> Any:
        """Play a voice-bank sound.

        ``wheee`` is the held ride: pass ``hold=True`` repeatedly to keep it
        going, ``hold=False`` to cut it. A hold that simply stops arriving
        plays out through its end segment instead — the two endings differ on
        purpose.

        Args:
            tag: One of :data:`SOUNDS`.
            hold: Only meaningful for ``wheee``.
        """
        if tag not in self.SOUNDS:
            raise ValueError(f"unknown sound {tag!r}; expected one of {self.SOUNDS}")
        params: dict[str, Any] = {"tag": tag}
        if hold is not None:
            params["hold"] = bool(hold)
        if self._mode == "mock":
            logger.debug("MOCK microduck sound: %s", params)
            return None
        return self._request("robot.sound", params)

    def quack(self) -> Any:
        """The mouth-trigger quack — what ``robotctl quack`` plays."""
        return self.sound("chirp")

    def mouth(self, open: float = 0.0) -> None:
        """Open the beak, 0 closed to 1 open.

        The mouth is in no policy — this is the only thing that moves it — and
        it is a continuous intent, so the driver keeps re-sending it until the
        command TTL expires. That is what makes "hold the beak open" work
        without the caller running its own loop.
        """
        params = {"open": _clamp(float(open), 0.0, 1.0)}
        self._mouth = params
        self._mouth_ts = time.monotonic()
        if self._mode == "mock":
            logger.debug("MOCK microduck mouth: %s", params)
            return
        self._safe_notify("robot.mouth", params)

    #: Body-pose offsets the policies were trained across. The robot clamps
    #: nothing here — out-of-distribution values just produce a policy leaning
    #: on inputs it never saw — so the envelope is enforced on this side.
    POSE_LIMITS = {"z": (-0.025, 0.010), "roll": (-0.26, 0.26), "pitch": (-0.26, 0.26)}

    def pose(
        self, z: float = 0.0, roll: float = 0.0, pitch: float = 0.0, active: bool = True
    ) -> None:
        """Lean the standing body. Continuous, and held only while re-sent.

        Args:
            z: Height offset, metres. Negative crouches.
            roll: Radians.
            pitch: Radians.
            active: ``False`` snaps the body back to nominal at once.
        """
        params = {
            "z": _clamp(float(z), *self.POSE_LIMITS["z"]),
            "roll": _clamp(float(roll), *self.POSE_LIMITS["roll"]),
            "pitch": _clamp(float(pitch), *self.POSE_LIMITS["pitch"]),
            "active": bool(active),
        }
        if not active:
            self._pose = None
        else:
            self._pose = params
            self._pose_ts = time.monotonic()
        if self._mode == "mock":
            logger.debug("MOCK microduck pose: %s", params)
            return
        self._safe_notify("robot.pose", params)

    def theremin(self, active: bool = True) -> Any:
        """Pick up the ToF theremin, or put it down.

        The head's depth sensor becomes an instrument: the distance of a hand
        in front of the beak is the pitch, and the mouth opens with it, so the
        note is visible as well as audible. Idempotent both ways.
        """
        if self._mode == "mock":
            logger.debug("MOCK microduck theremin: %s", active)
            return None
        return self._request("robot.theremin", {"active": bool(active)})

    def shutdown(self) -> Any:
        """Ask for the sit-then-power-off sequence — the duck sits down first."""
        if self._mode == "mock":
            logger.debug("MOCK microduck shutdown")
            return None
        return self._request("robot.shutdown", timeout=max(10.0, self._rpc_timeout_s))

    def get_state(self) -> dict:
        """Return the most recent ``robot.state`` notification (last-value-wins)."""
        with self._state_lock:
            return dict(self._last_state)

    def get_battery(self, max_age_s: float = 2.0) -> dict:
        """Return ``{"volts": …, "percent": …}`` from ``robot.health``, or ``{}``.

        The battery is **not** on the state stream.  ``RobotState``
        (``duck-ipc-proto/src/lib.rs:3317-3365``) carries no battery at all, and
        Pollen's own cheatsheet says so in as many words
        (``docs/robot/cheatsheet.md:51-53``, "because none of it is on the state
        stream").  It lives on ``HealthResult.battery`` (``:3117-3118``, ``Battery``
        at ``:3263-3266``), which is a request/response call — so the answer is
        cached for *max_age_s* rather than fetched on every read.

        Args:
            max_age_s: Reuse a cached ``robot.health`` answer younger than this.
                       ``0`` forces a fresh call.
        """
        if self._mode == "mock":
            return {}
        with self._state_lock:
            fresh = self._last_health_ts and (time.monotonic() - self._last_health_ts) <= max_age_s
            cached = dict(self._last_health) if fresh else None
        if cached is None:
            try:
                self.health_check()
            except Exception as exc:  # noqa: BLE001 — a missing battery is not fatal
                logger.debug("microduck get_battery: health call failed: %s", exc)
            with self._state_lock:
                cached = dict(self._last_health)
        battery = cached.get("battery") if isinstance(cached, dict) else None
        return dict(battery) if isinstance(battery, dict) else {}

    def get_odometry(self) -> dict:
        """Return ``{"position": [x, y, z], "yaw": θ}`` from the cached state, or ``{}``.

        ``OdomState.position`` is ``[f64; 3]`` (``duck-ipc-proto/src/lib.rs:3472-3476``)
        — three components, not two.
        """
        return dict(self.get_state().get("odom") or {})

    def get_policies(self) -> list[str]:
        """Return the policy and skill file names robotd reported at subscribe time."""
        return list(self._policies)

    def get_policy_slots(self) -> dict:
        """Return ``robot.subscribe``'s named slots: which network is in which role.

        ``{"walk": "alpha_walking.onnx", "stand": …, "sitstand": …, "ground_pick": …,
        "skills": [...], "unavailable": …, "accepted": bool}`` — absent keys mean that
        slot is not loaded on this robot.
        """
        return dict(self._policy_slots)

    @property
    def envelope(self) -> dict:
        """The velocity envelope full stick maps onto, and what Pollen's pad allows.

        ``castor duck test`` prints this: the duck is not struggling at 0.06 m/s, it
        is being asked for a fifth of what the gamepad asks for, and the owner is
        entitled to see both numbers.
        """
        return {
            "max_vx": self._max_vx,
            "max_vy": self._max_vy,
            "max_vyaw": self._max_vyaw,
            "padd_max_linear": PADD_MAX_LINEAR_MS,
            "padd_max_angular": PADD_MAX_ANGULAR_RADS,
        }

    def safe_to_restart(self) -> bool:
        """Whether robotd considers it safe to restart (false while walking)."""
        if self._mode == "mock":
            return True
        return bool(self._request("robot.safeToRestart"))

    def call(self, method: str, params: Optional[dict] = None) -> Any:
        """Escape hatch for robotd methods this driver does not wrap yet.

        Args:
            method: A ``robot.*`` method name.
            params: Params object, or None.
        """
        if self._mode == "mock":
            logger.debug("MOCK microduck call: %s %s", method, params)
            return None
        return self._request(method, params)
