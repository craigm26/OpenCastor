#!/usr/bin/env python3
"""Relay robotd's Unix socket to TCP so a phone and OpenCastor can reach it.

WHY THIS EXISTS. `robotd` listens on a Unix domain socket, `/run/robotd.sock`
by default. A phone cannot open a Unix socket on another machine, so either the
phone speaks SSH (a real implementation, host-key trust, key management) or a
small program moves the bytes. This is the small program.

WHY IT IS IN OPENCASTOR NOW, and not only in duck-studio. Before this file
there were two relays for one duck: duck-studio's `bridge/microduck-bridge.py`
for the phone, and `MicroduckDriver`'s own `ssh -L 7788:/run/robotd.sock` for
`castor run`. Two relays means two deadmen with different numbers, two tokens,
two things to install, and a duck that can be driven by two processes neither
of which knows about the other. One unit, one token, one deadman, both clients.
The port is unchanged (7788) because the driver's `local_port` default was
already that number, so `transport: tcp` reaches this the moment the driver
sends the hello line.

WHAT IT IS NOT. It is not a protocol. It does not parse robotd's vocabulary,
rewrite its parameters, or answer on its behalf: every byte a client sends
reaches robotd unchanged and every byte robotd sends reaches the client
unchanged. Two things are added and both are safety rather than semantics,
a first line that authorises the relay, and a deadman that sends a stop when a
client goes quiet. (`policy.install` is the one documented exception, below,
and it is a verb robotd does not have rather than one this rewrites.)

THE DEADMAN IS THE REASON TO PREFER THIS OVER A RAW `socat`. A simulator gives
you a duck that stops when nobody is asking it to move; hardware does not. If
the phone goes down a lift, the Wi-Fi drops, or the app is killed mid-hold, the
robot is still walking. This sends `robot.stop` when no client line has arrived
for `--deadman` milliseconds, then keeps sending nothing. It is a floor, not a
guarantee: it cannot act if this process itself dies, which is why the systemd
unit restarts it and why robotd's own twist deadman (500 ms) stays the real
backstop underneath.

THE TOKEN IS NOT A SECURITY BOUNDARY, and saying so is part of shipping it. It
stops a television or a housemate's laptop stumbling into a robot. It does not
stop anyone who can read your Wi-Fi. Bind to the LAN, never to the world, and
do not port-forward it.

RUNS ANYWHERE PYTHON 3.9 DOES: standard library only, no pip, nothing to build.
Imported by `castor up --archetype microduck` and by `castor duck --bridge`;
runnable on its own as `python3 -m castor.microduck_bridge`.

Provenance: lifted from `duck-studio/bridge/microduck-bridge.py` (the relay,
the token check, the deadman and `policy.install` are byte-for-byte the proven
code, tests included). Added here: `mint_token`, the client half of the
handshake (`hello_line` / `read_greeting`, pinned against StudioKit's
`BridgeHandshake.swift`), an `Upstream` so one bridge can serve a duck that is
on the network rather than on this machine, and environment-variable defaults
so a systemd unit is an EnvironmentFile rather than a command line.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import selectors
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time

VERSION = "microduck-bridge/1"
DEFAULT_SOCKET = "/run/robotd.sock"

#: The port. UNCHANGED from duck-studio's bridge, and it is also
#: `MicroduckDriver`'s `local_port` default (`drivers/microduck_driver.py`), and
#: also `BridgeHandshake.defaultPort` in StudioKit. Four places agree by
#: accident of good luck; this comment is here so the next change to any of
#: them finds the other three.
DEFAULT_PORT = 7788

#: Milliseconds of client silence before `robot.stop`. duck-studio's number,
#: and the one the app prints on screen through `BridgeHandshake.deadmanSaid`.
#: It sits ABOVE robotd's own 500 ms twist deadman on purpose: this is the
#: layer that acts when the CLIENT vanished, robotd's is the layer that acts
#: when this process did.
DEFAULT_DEADMAN_MS = 700

#: The local end of the `ssh -L` forward when the duck is on the network. NOT
#: 7788: that is the port this process is listening on, and forwarding onto
#: your own listener is a loop that looks like a hang.
DEFAULT_FORWARD_PORT = 7789

#: Where the token lives, and it is duck-studio's path unchanged
#: (`bridge/install.sh`). Same file, same 0600, so a duck whose owner already
#: ran `install.sh` and typed that token into the app keeps working: `castor`
#: reuses the token it finds and never mints a second one.
DEFAULT_TOKEN_FILE = "~/.microduck-bridge-token"

#: The one line the deadman sends. `id` names the sender so a robotd log shows
#: WHO stopped the robot, which is the difference between "the link dropped"
#: and "the pilot let go".
STOP_LINE = b'{"jsonrpc":"2.0","method":"robot.stop","params":{},"id":"bridge-deadman"}\n'

#: The handshake version. Both ends refuse anything else BY NAME rather than
#: guessing — `BridgeHandshake.Refusal.wrongVersion` on the app side.
HELLO_VERSION = "v1"

#: The minimum token length this bridge accepts, and the number `mint_token`
#: comfortably clears (32 hex characters).
MIN_TOKEN_LEN = 16


class Refusal(Exception):
    """Something the bridge will not do, in words a person can act on."""


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


def read_token(path: str) -> str:
    """The shared token, and a refusal if the file is readable by anybody else.

    A token in a world-readable file is not a token. This checks the mode
    rather than trusting the installer, because the installer is a shell script
    somebody may have edited — or, now, a `castor up` somebody ran before this
    check existed.
    """
    try:
        mode = os.stat(path).st_mode
    except OSError as error:
        raise Refusal(f"no token file at {path}: {error.strerror}. "
                      "Run `castor duck --bridge`, or pass --token-file.") from error
    if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
        raise Refusal(f"{path} is readable or writable by other users "
                      f"(mode {oct(stat.S_IMODE(mode))}). chmod 600 it.")
    with open(path, "r", encoding="utf-8") as handle:
        token = handle.read().strip()
    if len(token) < MIN_TOKEN_LEN:
        raise Refusal(f"the token in {path} is {len(token)} characters; "
                      f"{MIN_TOKEN_LEN} is the minimum this bridge accepts.")
    return token


def mint_token(path: str) -> tuple[str, bool]:
    """Return (token, reused). Generated once, 0600, and NEVER rotated.

    Reuse-don't-refuse, the same contract `castor up`'s console token and
    gateway bearers follow, for the same reason with one extra edge: this token
    was TYPED INTO A PHONE by hand. Rotating it on a rerun silently unpairs the
    app, and the person holding it has no way to know why the robot stopped
    answering — they would reasonably blame the robot.

    THE MODE IS RE-ASSERTED ON REUSE, not only on minting. The file `castor`
    finds may not be the file `castor` wrote: `install.sh` wrote it, or a `cp`
    took the umask instead of the source mode, or a backup was restored. The
    bridge itself refuses a group-or-other-readable token file at startup
    (:func:`read_token`), so a mode nobody fixed is a unit that will not start
    — this fixes it at the one moment somebody is watching the output.
    """
    target = os.path.expanduser(path)
    if os.path.exists(target):
        with open(target, "r", encoding="utf-8") as handle:
            existing = handle.read().strip()
        if len(existing) >= MIN_TOKEN_LEN:
            os.chmod(target, 0o600)
            return existing, True
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    token = secrets.token_hex(16)
    # Written 0600 from the first byte, not chmodded after: a token that exists
    # world-readable for one scheduler tick has been readable, and "briefly" is
    # not a property of a secret anybody can measure.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    os.chmod(target, 0o600)
    return token, False


# ---------------------------------------------------------------------------
# The handshake, both halves
# ---------------------------------------------------------------------------


def _escaped(text: str) -> str:
    """Exactly StudioKit's `BridgeHandshake.escaped` — backslash, then quote."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def hello_line(token: str) -> bytes:
    """The first line a client sends, byte-identical to the app's.

    PINNED, NOT DERIVED. `BridgeHandshake.hello(token:)` builds this string by
    hand precisely so the key order is fixed, and its own comment says why:
    "the bridge reads JSON and a person reads the log line; neither is helped
    by two spellings of one greeting". `json.dumps` would produce the same
    bytes today and a different order the day somebody sorts keys, so this
    formats the string the same way Swift does and a test compares the two.
    """
    return f'{{"microduck":"{HELLO_VERSION}","token":"{_escaped(token)}"}}\n'.encode("utf-8")


def hello_is_valid(line: bytes, token: str) -> bool:
    """The first line a client sends: a version and the token, nothing else.

    Compared in constant time, which costs nothing here and means the failure
    mode is "wrong token" rather than "wrong token, and how wrong".
    """
    try:
        hello = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(hello, dict):
        return False
    if hello.get("microduck") != HELLO_VERSION:
        return False
    given = hello.get("token")
    if not isinstance(given, str):
        return False
    import hmac

    return hmac.compare_digest(given, token)


def greeting_line(deadman_ms: int, policy_install: bool) -> bytes:
    """What the bridge says back when it has accepted the relay."""
    return json.dumps({"microduck": HELLO_VERSION, "bridge": VERSION,
                       "deadman_ms": deadman_ms,
                       "policy_install": bool(policy_install)}).encode() + b"\n"


def read_greeting(line: bytes) -> dict:
    """Read the bridge's answer to a hello. Raises :class:`Refusal`.

    THE ERROR SHAPE IS THE BRIDGE'S OWN, and this reads for it FIRST — the same
    order `BridgeHandshake.read` uses, for the reason its comment gives: a
    client that checked for its own success key first would report "not a
    bridge" for a bridge that answered perfectly well and said no.
    """
    try:
        top = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as why:
        raise Refusal("that answered, but not with anything a Microduck bridge says. "
                      "Check the address and the port.") from why
    if not isinstance(top, dict):
        raise Refusal("that answered with JSON, but not an object; not a Microduck bridge.")
    if isinstance(top.get("error"), str):
        raise Refusal(f"the bridge refused: {top['error']}")
    version = top.get("microduck")
    if not isinstance(version, str):
        raise Refusal("that answered, but not with anything a Microduck bridge says. "
                      "Check the address and the port.")
    if version != HELLO_VERSION:
        raise Refusal(f"that bridge speaks {version} and this speaks {HELLO_VERSION}; "
                      "update whichever is older.")
    return top


# ---------------------------------------------------------------------------
# policy.install — the one verb this bridge answers itself
# ---------------------------------------------------------------------------


INSTALL_METHOD = "policy.install"
INSTALL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
INSTALL_CAP = 8 * 1024 * 1024


class Installer:
    """THE ONE VERB THIS BRIDGE ANSWERS ITSELF, and why that is not a contradiction.

    Everything else passes through to robotd unchanged. `policy.install` cannot:
    robotd has no method that takes a file, and a network somebody searched on a
    phone reaches the robot's disk or it reaches nothing. So the bridge, which is
    the one process here that HAS a disk, takes exactly this request, writes the
    bytes where robotd loads policies from, and answers in JSON-RPC's own shape.
    It never restarts robotd and never edits a config it was not pointed at.

    WHAT IT REFUSES, BY NAME. No `--policy-dir` means the verb is off, not
    silently pointed somewhere. A name that could be a path is refused before
    anything is decoded. A sha256 is REQUIRED and checked against the bytes
    that arrived: a policy that landed one byte short would load and drive the
    servos with whatever those bytes mean. A slot is applied only when the
    bridge was given `--robotd-toml`, only to a key that table already has, and
    with a backup written first — and the answer says the change takes effect
    when robotd restarts, because it does.
    """

    def __init__(self, policy_dir: str | None, robotd_toml: str | None = None,
                 log=print) -> None:
        self.policy_dir = os.path.abspath(policy_dir) if policy_dir else None
        self.robotd_toml = os.path.abspath(robotd_toml) if robotd_toml else None
        self.log = log

    @staticmethod
    def wants(line: bytes) -> bool:
        return b'"' + INSTALL_METHOD.encode() + b'"' in line

    def handle(self, line: bytes) -> bytes:
        rpc_id = None
        try:
            message = json.loads(line.decode("utf-8"))
            rpc_id = message.get("id") if isinstance(message, dict) else None
            if not isinstance(message, dict) or message.get("method") != INSTALL_METHOD:
                raise Refusal("that line is not a policy.install request")
            result = self.install(message.get("params") or {})
            reply = {"jsonrpc": "2.0", "id": rpc_id, "result": result}
        except Refusal as why:
            reply = {"jsonrpc": "2.0", "id": rpc_id,
                     "error": {"code": -32602, "message": str(why)}}
        except (ValueError, UnicodeDecodeError) as why:
            reply = {"jsonrpc": "2.0", "id": rpc_id,
                     "error": {"code": -32700, "message": f"that line is not JSON: {why}"}}
        return json.dumps(reply, separators=(",", ":")).encode() + b"\n"

    def install(self, params: dict) -> dict:
        if not self.policy_dir:
            raise Refusal("this bridge was started without --policy-dir, so it cannot "
                          "install a policy; start it with the directory robotd loads "
                          "policies from")
        name = str(params.get("name") or "")
        if name.lower().endswith(".onnx"):
            name = name[:-5]
        if not INSTALL_NAME.match(name) or ".." in name:
            raise Refusal(f'"{name}" cannot name a policy on the robot: letters, digits, dots, '
                          "dashes and underscores, up to 64, starting with a letter or digit")
        encoded = params.get("bytes")
        if not isinstance(encoded, str) or not encoded:
            raise Refusal("policy.install needs `bytes`: the .onnx file, base64")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise Refusal("the `bytes` field is not base64")
        if not data:
            raise Refusal("the policy is empty")
        if len(data) > INSTALL_CAP:
            raise Refusal(f"the policy is {len(data)} bytes; the shipped ones are under 1 MB "
                          f"and this bridge stops at {INSTALL_CAP}")
        claimed = str(params.get("sha256") or "").lower()
        actual = hashlib.sha256(data).hexdigest()
        if not claimed:
            raise Refusal("policy.install needs `sha256`: the digest of the bytes as sent, "
                          "so a network that arrived short is refused rather than driven")
        if claimed != actual:
            raise Refusal(f"the bytes that arrived digest to {actual[:12]}…, not the "
                          f"{claimed[:12]}… that was claimed; nothing was written")
        os.makedirs(self.policy_dir, exist_ok=True)
        path = os.path.join(self.policy_dir, f"{name}.onnx")
        tmp = f"{path}.part-{os.getpid()}"
        with open(tmp, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        self.log(f"policy.install: {len(data)} bytes -> {path} ({actual[:12]})")
        result = {"installed": path, "sha256": actual, "bytes": len(data),
                  "takes_effect": "when robotd next starts; this bridge does not restart it"}
        slot = params.get("slot")
        if slot is not None:
            result["slot"] = self.assign(str(slot), path)
        return result

    def assign(self, slot: str, path: str) -> dict:
        """Point one `[policy]` key at the installed file, or say why not."""
        if not INSTALL_NAME.match(slot):
            raise Refusal(f'"{slot}" is not the shape of a robotd.toml policy key')
        if not self.robotd_toml:
            return {"asked": slot, "applied": False,
                    "why": "this bridge was started without --robotd-toml; edit the "
                           f"[policy] table yourself: {slot} = \"{path}\""}
        try:
            with open(self.robotd_toml, "r", encoding="utf-8") as f:
                lines = f.read().split("\n")
        except OSError as why:
            return {"asked": slot, "applied": False,
                    "why": f"robotd.toml could not be read: {why}"}
        in_policy = False
        found = None
        keys = []
        for index, raw in enumerate(lines):
            stripped = raw.strip()
            if stripped.startswith("["):
                in_policy = stripped == "[policy]"
                continue
            if not in_policy or not stripped or stripped.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z0-9_.-]+)\s*=", stripped)
            if not match:
                continue
            keys.append(match.group(1))
            if match.group(1) == slot:
                found = index
        if found is None:
            return {"asked": slot, "applied": False,
                    "why": f"robotd.toml has no `{slot}` key under [policy]; the keys it has "
                           f"are {', '.join(keys) or 'none'}. A key robotd does not expect is "
                           "not added for it."}
        indent = lines[found][: len(lines[found]) - len(lines[found].lstrip())]
        lines[found] = f'{indent}{slot} = "{path}"'
        backup = f"{self.robotd_toml}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        with open(backup, "w", encoding="utf-8") as f:
            with open(self.robotd_toml, "r", encoding="utf-8") as original:
                f.write(original.read())
        tmp = f"{self.robotd_toml}.part-{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp, self.robotd_toml)
        self.log(f"policy.install: [policy] {slot} -> {path} (backup {os.path.basename(backup)})")
        return {"asked": slot, "applied": True, "backup": backup}


# ---------------------------------------------------------------------------
# Where robotd is
# ---------------------------------------------------------------------------


def forward_command(dest: str, socket_path: str, local_port: int, ssh_port: int = 22) -> list[str]:
    """The `ssh -L` argv, as data, so a test can read it without spawning ssh.

    `ExitOnForwardFailure` because a forward that silently did not bind gives
    you a bridge that accepts clients and connects to nothing; `BatchMode`
    because a unit has nowhere to type a password and a prompt would hang the
    service rather than fail it.
    """
    return [
        "ssh", "-N", "-T",
        "-p", str(ssh_port),
        "-o", "ExitOnForwardFailure=yes",
        "-o", "BatchMode=yes",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=2",
        "-L", f"127.0.0.1:{local_port}:{socket_path}",
        dest,
    ]


class Upstream:
    """Where this bridge opens robotd, and the only thing that knows how.

    TWO SHAPES, ONE OF WHICH IS THE POINT OF PUTTING THIS IN OPENCASTOR.

    * ``unix``  — robotd's socket is on this machine. duck-studio's shape: the
      bridge runs on the duck, installed over ssh.
    * ``ssh``   — robotd is on a duck across the network and this bridge runs
      where `castor up` ran. One `ssh -L` is held open for the life of the
      process and every client connection dials the local end of it.

    The second shape is what makes ONE relay serve BOTH clients: the phone
    reaches this bridge over the LAN and `MicroduckDriver`'s `transport: tcp`
    reaches the same bridge on 127.0.0.1, so there is one deadman, one token
    and one connection to the duck rather than two of each. It is also the
    shape `castor up --archetype microduck --host <ip>` writes, because that
    command runs on the laptop or Pi, not on the duck.

    It is still byte-transparent either way: an Upstream returns a socket and
    has no opinion about what travels over it.
    """

    def __init__(self, socket_path: str = DEFAULT_SOCKET, *, ssh: str | None = None,
                 ssh_port: int = 22, forward_port: int = DEFAULT_FORWARD_PORT,
                 log=print) -> None:
        self.socket_path = socket_path
        self.ssh = ssh or None
        self.ssh_port = ssh_port
        self.forward_port = forward_port
        self.log = log
        self._proc: subprocess.Popen | None = None

    @property
    def kind(self) -> str:
        return "ssh" if self.ssh else "unix"

    def describe(self) -> str:
        if self.ssh:
            return f"ssh://{self.ssh}{self.socket_path} (via 127.0.0.1:{self.forward_port})"
        return self.socket_path

    def start(self) -> None:
        """Open the ssh forward, if there is one. A unix upstream does nothing."""
        if not self.ssh:
            return
        if shutil.which("ssh") is None:
            raise Refusal("this bridge was asked to reach a duck over ssh and there is no "
                          "`ssh` on PATH.")
        cmd = forward_command(self.ssh, self.socket_path, self.forward_port, self.ssh_port)
        self.log(f"forward: {' '.join(cmd)}")
        self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                err = b""
                if self._proc.stderr is not None:
                    err = self._proc.stderr.read() or b""
                raise Refusal(f"the ssh forward exited: "
                              f"{err.decode(errors='replace').strip() or 'no reason given'}")
            try:
                probe = socket.create_connection(("127.0.0.1", self.forward_port), timeout=0.2)
                probe.close()
                return
            except OSError:
                time.sleep(0.05)
        raise Refusal("the ssh forward did not start listening within 10 s")

    def connect(self) -> socket.socket:
        """One robotd connection for one client."""
        if self.ssh:
            return socket.create_connection(("127.0.0.1", self.forward_port), timeout=5)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        return sock

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:  # noqa: BLE001 - a forward that will not die is not fatal here
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# The relay
# ---------------------------------------------------------------------------


class Relay:
    """One client, one robotd connection, bytes both ways and a deadman."""

    def __init__(self, client: socket.socket, robotd: socket.socket,
                 deadman_ms: int, log=print, installer: Installer | None = None) -> None:
        self.client = client
        self.robotd = robotd
        self.deadman = deadman_ms / 1000 if deadman_ms > 0 else 0
        self.log = log
        self.installer = installer
        self.last_from_client = time.monotonic()
        self.stopped_for_silence = False
        self.running = True
        # WHAT THE CLIENT HAS SENT SINCE ITS LAST NEWLINE. The one verb this
        # relay answers itself has to be seen whole to be recognised, so the
        # client's bytes are forwarded a line at a time rather than a chunk at
        # a time. Every byte still reaches robotd unchanged and in order; what
        # changed is that a line waits for its own newline, which a JSON-RPC
        # message needs anyway before robotd would act on it.
        self.pending = b""

    def run(self) -> None:
        watchdog = None
        if self.deadman > 0:
            watchdog = threading.Thread(target=self._watch, daemon=True)
            watchdog.start()
        selector = selectors.DefaultSelector()
        selector.register(self.client, selectors.EVENT_READ, "client")
        selector.register(self.robotd, selectors.EVENT_READ, "robotd")
        try:
            while self.running:
                for key, _ in selector.select(timeout=0.2):
                    who = key.data
                    source = self.client if who == "client" else self.robotd
                    target = self.robotd if who == "client" else self.client
                    chunk = source.recv(65536)
                    if not chunk:
                        self.running = False
                        break
                    if who == "client":
                        # ANY BYTE FROM THE CLIENT FEEDS THE DEADMAN, not only a
                        # move: a client that is asking for state is a client
                        # that is still there, and a robot that stops because
                        # nobody drove it for a moment is a robot nobody trusts.
                        self.last_from_client = time.monotonic()
                        self.stopped_for_silence = False
                        self._from_client(chunk)
                        continue
                    target.sendall(chunk)
        except OSError:
            pass
        finally:
            self.running = False
            selector.close()
            if watchdog:
                watchdog.join(timeout=1)

    def _from_client(self, chunk: bytes) -> None:
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            if self.installer is not None and Installer.wants(line):
                self.client.sendall(self.installer.handle(line))
                continue
            self.robotd.sendall(line + b"\n")

    def _watch(self) -> None:
        while self.running:
            time.sleep(0.05)
            if self.deadman <= 0 or self.stopped_for_silence:
                continue
            quiet = time.monotonic() - self.last_from_client
            if quiet < self.deadman:
                continue
            try:
                self.robotd.sendall(STOP_LINE)
                self.stopped_for_silence = True
                self.log(f"deadman: {quiet * 1000:.0f} ms of silence, sent robot.stop")
            except OSError:
                self.running = False


def serve(host: str, port: int, socket_path: str, token: str,
          deadman_ms: int, log=print, ready=None,
          policy_dir: str | None = None, robotd_toml: str | None = None,
          upstream: Upstream | None = None) -> None:
    """Listen, greet, relay. ``socket_path`` is ignored when ``upstream`` is given.

    The signature keeps duck-studio's positional order so its own 17 tests port
    across unchanged; ``upstream`` is the one addition and it defaults to the
    local-socket behaviour those tests exercise.
    """
    up = upstream or Upstream(socket_path, log=log)
    up.start()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(4)
    # ALWAYS BUILT, so the verb is answered by the bridge whether or not it is
    # on: without a --policy-dir the answer is the refusal that names the flag,
    # not a line forwarded to a robotd that has no such method and a phone
    # waiting on a reply that never comes.
    installer = Installer(policy_dir, robotd_toml, log=log)
    log(f"{VERSION} on {host}:{listener.getsockname()[1]} -> {up.describe()}, "
        f"deadman {deadman_ms} ms, policy.install "
        + (f"-> {installer.policy_dir}" if installer.policy_dir else "off (no --policy-dir)"))
    if ready:
        ready(listener.getsockname()[1])
    try:
        while True:
            client, where = listener.accept()
            threading.Thread(target=_greet,
                             args=(client, where, up, token, deadman_ms, log, installer),
                             daemon=True).start()
    finally:
        listener.close()
        up.close()


def _greet(client: socket.socket, where, upstream: Upstream, token: str,
           deadman_ms: int, log, installer: Installer | None = None) -> None:
    client.settimeout(5)
    try:
        hello = b""
        while not hello.endswith(b"\n") and len(hello) < 4096:
            chunk = client.recv(1)
            if not chunk:
                return
            hello += chunk
        if not hello_is_valid(hello, token):
            # NAMED, AND THEN CLOSED. A client with the wrong token gets one
            # line saying which door it is at; anything more would be a probe
            # answering questions for whoever is asking them.
            client.sendall(b'{"error":"microduck-bridge: wrong or missing token"}\n')
            log(f"refused {where[0]}: wrong or missing token")
            return
        client.settimeout(None)
        robotd = upstream.connect()
        # THE GREETING SAYS WHETHER INSTALL IS ON, so a phone can offer the
        # button or the sentence without asking and being refused.
        client.sendall(greeting_line(deadman_ms,
                                     bool(installer and installer.policy_dir)))
        log(f"relaying {where[0]}")
        Relay(client, robotd, deadman_ms, log=log, installer=installer).run()
        robotd.close()
        log(f"closed {where[0]}")
    except socket.timeout:
        log(f"refused {where[0]}: no hello within 5 s")
    except (FileNotFoundError, ConnectionRefusedError):
        client.sendall(b'{"error":"microduck-bridge: no robotd socket here"}\n')
        log(f"nothing at {upstream.describe()} — is robotd running?")
    except OSError as error:
        log(f"{where[0]}: {error}")
    finally:
        try:
            client.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Command line — every flag also readable from the environment
# ---------------------------------------------------------------------------

#: The unit written by `castor up --archetype microduck` is an EnvironmentFile
#: and nothing else, so an operator changes the deadman or the socket by
#: editing one readable file rather than by hand-editing an ExecStart line that
#: the next `castor up` would rewrite.
ENV_PREFIX = "MICRODUCK_BRIDGE_"


def _env(name: str, fallback):
    value = os.environ.get(ENV_PREFIX + name)
    return value if value not in (None, "") else fallback


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="castor.microduck_bridge",
        description=(__doc__ or "").splitlines()[0],
    )
    parser.add_argument("--socket", default=_env("SOCKET", DEFAULT_SOCKET))
    parser.add_argument("--port", type=int, default=int(_env("PORT", DEFAULT_PORT)))
    parser.add_argument("--host", default=_env("HOST", "0.0.0.0"),
                        help="the interface to bind. The default is every "
                             "interface on the robot's own LAN; do not "
                             "port-forward it.")
    parser.add_argument("--token-file", default=_env("TOKEN_FILE", DEFAULT_TOKEN_FILE))
    parser.add_argument("--ssh", default=_env("SSH", None),
                        help="user@host of the machine robotd runs on, when that is not "
                             "this machine. One `ssh -L` is held open for the life of the "
                             "process and every client dials its local end.")
    parser.add_argument("--ssh-port", type=int, default=int(_env("SSH_PORT", 22)))
    parser.add_argument("--forward-port", type=int,
                        default=int(_env("FORWARD_PORT", DEFAULT_FORWARD_PORT)),
                        help="local end of the ssh forward; must not be --port")
    parser.add_argument("--policy-dir", default=_env("POLICY_DIR", None),
                        help="the directory robotd loads policies from; enables policy.install")
    parser.add_argument("--robotd-toml", default=_env("ROBOTD_TOML", None),
                        help="robotd's config, so an install can point a [policy] key at the file")
    parser.add_argument("--deadman", type=int, default=int(_env("DEADMAN_MS", DEFAULT_DEADMAN_MS)),
                        help="milliseconds of client silence before robot.stop. "
                             "0 disables it, which you should not do on hardware.")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.ssh and args.forward_port == args.port:
        print(f"microduck-bridge: --forward-port {args.forward_port} is also --port; the "
              "forward would loop back onto this listener.", file=sys.stderr)
        return 2
    try:
        token = read_token(os.path.expanduser(args.token_file))
    except Refusal as refusal:
        print(f"microduck-bridge: {refusal}", file=sys.stderr)
        return 2
    upstream = Upstream(args.socket, ssh=args.ssh, ssh_port=args.ssh_port,
                        forward_port=args.forward_port)
    try:
        serve(args.host, args.port, args.socket, token, args.deadman,
              policy_dir=args.policy_dir, robotd_toml=args.robotd_toml,
              upstream=upstream)
    except Refusal as refusal:
        print(f"microduck-bridge: {refusal}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    finally:
        upstream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
