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
and ``enable()`` are maintenance calls that Pollen deliberately keeps off remote
transports; they work over ``unix`` and over an SSH forward (both are trusted
paths), not through the WebRTC/rendezvous bridge.
"""

from __future__ import annotations

import json
import logging
import math
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


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


class MicroduckDriver(DriverBase):
    """Driver for the Pollen Robotics Microduck biped.

    Args:
        config: RCAN driver config dict. Relevant keys:

            - ``transport`` (str): ``"unix"``, ``"ssh"`` or ``"tcp"``.
              Default: ``"unix"``.
            - ``socket`` (str): robotd socket path. Default ``/run/robotd.sock``.
            - ``ssh_host`` / ``ssh_user`` / ``ssh_port`` (str/int): SSH forward target.
            - ``local_port`` (int): local end of the SSH forward. Default ``7788``.
            - ``host`` / ``port``: target for ``transport: tcp``.
            - ``max_vx`` / ``max_vy`` / ``max_vyaw`` (float): velocity envelope at
              full deflection.
            - ``intent_hz`` (float): intent re-send rate. Default ``20``.
            - ``command_ttl_s`` (float): driver-side deadman. Default ``1.5``.
            - ``rpc_timeout_s`` (float): request/response timeout. Default ``2.0``.
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

        self._last_state: dict[str, Any] = {}
        self._policies: list[str] = []

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
                target = f"{self._host}:{self._port}"
            else:
                logger.warning(
                    "MicroduckDriver: unknown transport %r — mock mode", self._transport
                )
                return
        except Exception as exc:
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
            result = self._request("robot.subscribe")
            if isinstance(result, dict):
                self._policies = list(result.get("networks") or [])
                logger.info(
                    "Microduck ready: status=%s policies=%s",
                    result.get("status"),
                    self._policies,
                )
        except Exception as exc:
            logger.warning("MicroduckDriver robot.subscribe failed: %s", exc)

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
            "-p", str(self._ssh_port),
            "-o", "ExitOnForwardFailure=yes",
            "-o", "BatchMode=yes",
            "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2",
            "-L", f"127.0.0.1:{self._local_port}:{self._socket_path}",
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
            except socket.timeout:
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

        Returns:
            Dict with ``ok``, ``mode``, ``error``, plus ``loop``, ``battery``,
            ``imu`` and ``bus`` when connected to real hardware.
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
        return {
            "ok": healthy,
            "mode": "hardware",
            "error": None if healthy else "robotd reports unhealthy",
            "transport": self._transport,
            "loop": res.get("loop"),
            "battery": res.get("battery"),
            "imu": res.get("imu"),
            "bus": res.get("bus"),
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

    def look_at(self, x: float, y: float, z: float) -> None:
        """Point the head at a Cartesian target, mirroring ``ReachyDriver.look_at``.

        Args:
            x: Forward distance in metres.
            y: Lateral offset in metres (positive = left).
            z: Vertical offset in metres.
        """
        yaw = math.atan2(y, x)
        pitch = -math.atan2(z, math.hypot(x, y))
        self.head(neck_pitch=pitch / 2.0, head_pitch=pitch / 2.0, head_yaw=yaw)

    def get_state(self) -> dict:
        """Return the most recent ``robot.state`` notification (last-value-wins)."""
        with self._state_lock:
            return dict(self._last_state)

    def get_battery(self) -> dict:
        """Return ``{"volts": …, "percent": …}`` from the cached state, or ``{}``."""
        return dict(self.get_state().get("battery") or {})

    def get_odometry(self) -> dict:
        """Return ``{"position": [x, y], "yaw": θ}`` from the cached state, or ``{}``."""
        return dict(self.get_state().get("odom") or {})

    def get_policies(self) -> list[str]:
        """Return the ONNX policies robotd reported at subscribe time."""
        return list(self._policies)

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
