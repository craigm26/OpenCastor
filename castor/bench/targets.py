"""The three things the ten-minute benchmark can be run against.

**The mock, for CI.** ``castor.bench.mock_robotd`` on a Unix socket in a
temporary directory. It answers the four identity methods with replies
transcribed from ``duck-ipc-proto``, so C3 is exercised, and it has no physics,
so C7 is null with a reason. A run against it is ``ci-pass`` at best.

**The simulator, for honesty without hardware.** Pollen's ``scripts/duck-sim``
runs the *real* ``robotd`` binary with ``duck_control::sim::RemoteIo`` in place
of the servo bus, against a MuJoCo body from ``microduck_rl``. Everything above
the servo seam is the code a robot runs: the control loop, the policy, safety,
fall detection, kinematics, odometry and the whole IPC surface. It answers C3
with real numbers and is the only target short of hardware that can honestly
answer C7. It tells you nothing about a driver, and Pollen's own doc says so.

**A real duck, for the number that counts.** Everything above, plus a floor.

Each target hands the runner a ``MicroduckDriver`` config and the facts the
record needs about how it was reached. No target ever fabricates a reading.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from castor.bench.record import Record

#: The port duck-studio's bridge binds and ``MicroduckDriver``'s ``local_port``
#: defaults to (bridge/microduck-bridge.py:51, microduck_driver.py:127-129).
BRIDGE_PORT = 7788


class TargetUnavailable(RuntimeError):
    """This target cannot run here, and the message says exactly why.

    Never a skip. A benchmark that reports success because the thing it measures
    was absent is the failure mode this whole document exists to refuse.
    """


@dataclass
class Target:
    """A place to point the driver at.

    Args:
        kind: ``"mock"``, ``"sim"`` or ``"real"`` — what the record calls it.
        driver_config: The dict handed to ``MicroduckDriver``.
        transport: What the record's ``environment.transport`` should say.
        counts_as_real: Whether a pass here may be a plain ``pass``. False only
            for the mock.
        why_not_real: One sentence, carried into ``verdict_reason``.
        supports_odometry: Whether C7 can be attempted at all.
    """

    kind: str
    driver_config: dict[str, Any]
    transport: dict[str, Any]
    counts_as_real: bool = True
    why_not_real: str = ""
    supports_odometry: bool = True
    notes: list[str] = field(default_factory=list)

    def setup(self, record: Record) -> None:  # pragma: no cover - overridden
        """Bring the target up, recording every command typed."""

    def teardown(self) -> None:  # pragma: no cover - overridden
        """Put it away. Must be safe to call twice."""


# ---------------------------------------------------------------------------
# mock
# ---------------------------------------------------------------------------


class MockTarget(Target):
    """duck-studio's mock robotd, or ours, on a Unix socket.

    Args:
        in_process: Serve in a daemon thread rather than a subprocess. The
            tests use this; the CLI does not, because a benchmark that shares a
            process with the thing it measures cannot report a wheel or a venv.
        mock_cmd: Override the command. The CLI's ``--mock-cmd``.
        replies: Override the shaped replies, for a test that needs a key gone.
    """

    def __init__(
        self,
        *,
        in_process: bool = False,
        mock_cmd: Optional[str] = None,
        replies: Optional[dict] = None,
    ) -> None:
        self._dir = tempfile.mkdtemp(prefix="castor-bench-")
        sock = str(Path(self._dir) / "robotd.sock")
        self._in_process = in_process
        self._mock_cmd = mock_cmd
        self._replies = replies
        self._proc: Optional[subprocess.Popen] = None
        self._mock = None
        super().__init__(
            kind="mock",
            driver_config={"transport": "unix", "socket": sock},
            transport={"kind": "unix", "target": sock, "mock": True},
            counts_as_real=False,
            why_not_real=(
                "the target was a mock robotd, which has no motors and no physics, "
                "so this run measures wiring and never the ten-minute goal"
            ),
            supports_odometry=False,
            notes=[
                "The mock has no physics, so C7 cannot be attempted here. "
                "A run that skipped C7 has proved commands were accepted, not that a robot moved."
            ],
        )

    @property
    def socket_path(self) -> str:
        return self.driver_config["socket"]

    def setup(self, record: Record) -> None:
        from castor.bench.mock_robotd import MockRobotd

        if self._in_process:
            record.typed(f"# in-process mock robotd on {self.socket_path}")
            self._mock = MockRobotd(self.socket_path, replies=self._replies).start()
        else:
            if self._mock_cmd:
                cmd = self._mock_cmd.replace("<tmp>", self.socket_path).split()
            else:
                cmd = [
                    sys.executable,
                    "-u",
                    "-m",
                    "castor.bench.mock_robotd",
                    "--socket",
                    self.socket_path,
                ]
            record.typed(" ".join(cmd))
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if os.path.exists(self.socket_path):
                return
            if self._proc is not None and self._proc.poll() is not None:
                out = self._proc.stdout.read() if self._proc.stdout else ""
                raise TargetUnavailable(f"the mock exited before it bound its socket: {out}")
            time.sleep(0.02)
        raise TargetUnavailable(f"the mock never bound {self.socket_path}")

    def teardown(self) -> None:
        if self._mock is not None:
            self._mock.stop()
            self._mock = None
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(self._dir, ignore_errors=True)

    def calls(self, method: Optional[str] = None) -> list[dict]:
        """What the mock was asked, when it is in-process. Empty otherwise."""
        return self._mock.calls(method) if self._mock is not None else []


# ---------------------------------------------------------------------------
# duck-sim
# ---------------------------------------------------------------------------

#: What ``scripts/duck-sim`` needs before it can start, each with the sentence
#: the script itself prints when it is missing. Checked before anything is run,
#: so a machine that cannot host the simulator says so in one line rather than
#: failing halfway through a benchmark.
SIM_REQUIREMENTS = (
    (
        "microduck checkout",
        "the Pollen `microduck` repository; set DUCK_SIM_REPO or pass --sim-repo",
    ),
    (
        "target/debug/robotd",
        "a cargo build of the workspace: `cargo build -p robotd -p robotctl` in that checkout",
    ),
    (
        "target/debug/robotctl",
        "the same build; duck-sim drives the daemon through robotctl",
    ),
    (
        "microduck_rl venv",
        "the `microduck_rl` repository with a `.venv` (`uv sync` there); "
        "duck-sim reads libonnxruntime out of it, and MuJoCo is the body "
        "(scripts/duck-sim:193-200)",
    ),
)


class SimTarget(Target):
    """Pollen's ``scripts/duck-sim``: the real ``robotd``, on a MuJoCo body.

    Args:
        repo: The ``microduck`` checkout. ``$DUCK_SIM_REPO`` when omitted.
        rl: The ``microduck_rl`` checkout. ``$DUCK_SIM_RL`` when omitted, which
            is what ``scripts/duck-sim`` itself reads.
        state: ``$DUCK_SIM_STATE``; the socket lives under it, and a Unix path
            is capped at ~108 bytes, so it has to be short.
    """

    def __init__(
        self,
        *,
        repo: Optional[str] = None,
        rl: Optional[str] = None,
        state: Optional[str] = None,
    ) -> None:
        self.repo = Path(repo or os.environ.get("DUCK_SIM_REPO", "")).expanduser()
        self.rl = Path(
            rl or os.environ.get("DUCK_SIM_RL", str(Path.home() / "Pollen" / "microduck_rl"))
        ).expanduser()
        self.state = Path(
            state or os.environ.get("DUCK_SIM_STATE", str(Path.home() / ".cache" / "duck-sim"))
        ).expanduser()
        # `duck_name 0` is "duck-a" (scripts/duck-sim:583), and robotd binds
        # "$STATE/<name>.sock". The state directory has to be short: a Unix
        # socket path is capped at about 108 bytes.
        sock = str(self.state / "duck-a.sock")
        self._proc: Optional[subprocess.Popen] = None
        super().__init__(
            kind="sim",
            driver_config={"transport": "unix", "socket": sock},
            transport={"kind": "unix", "target": sock, "sim": "pollen scripts/duck-sim"},
            counts_as_real=True,
            why_not_real="",
            supports_odometry=True,
            notes=[
                "duck-sim runs the real robotd binary with duck_control::sim::RemoteIo in "
                "place of the servo bus. Everything above that seam is the code a robot runs. "
                "It says nothing about a driver's transport, and a sim pass is not a duck on "
                "a floor: read `target`."
            ],
        )

    def missing(self) -> list[str]:
        """Every requirement this machine does not meet, as sentences.

        Empty means duck-sim can at least be attempted here.
        """
        gaps: list[str] = []
        if not self.repo or not (self.repo / "scripts" / "duck-sim").is_file():
            gaps.append(
                f"no scripts/duck-sim under {self.repo or '<unset>'}: "
                f"{SIM_REQUIREMENTS[0][1]}"
            )
            return gaps  # nothing below can be checked without the checkout
        for name in ("robotd", "robotctl"):
            if not (self.repo / "target" / "debug" / name).is_file():
                gaps.append(f"no target/debug/{name}: {SIM_REQUIREMENTS[1][1]}")
        if not (self.rl / ".venv" / "bin" / "python").is_file():
            gaps.append(f"no .venv in {self.rl}: {SIM_REQUIREMENTS[3][1]}")
        return gaps

    def setup(self, record: Record) -> None:
        gaps = self.missing()
        if gaps:
            raise TargetUnavailable(
                "duck-sim cannot run here:\n  " + "\n  ".join(gaps)
            )
        env = {
            **os.environ,
            "DUCK_SIM_RL": str(self.rl),
            "DUCK_SIM_STATE": str(self.state),
            # No MuJoCo window. The variable is DUCK_SIM_VIEWER, read at
            # scripts/duck-sim:455 as `[ "${DUCK_SIM_VIEWER:-1}" = 0 ]`; there
            # is no DUCK_SIM_HEADLESS, and setting one would silently open a
            # viewer on a machine with no display and hang the run.
            "DUCK_SIM_VIEWER": "0",
        }
        cmd = [str(self.repo / "scripts" / "duck-sim"), "up"]
        record.typed(
            f"DUCK_SIM_RL={self.rl} DUCK_SIM_STATE={self.state} "
            f"DUCK_SIM_VIEWER=0 {' '.join(cmd)}"
        )
        self._proc = subprocess.Popen(
            cmd, env=env, cwd=str(self.repo), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True
        )
        sock = self.driver_config["socket"]
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            if os.path.exists(sock):
                return
            if self._proc.poll() is not None:
                out = self._proc.stdout.read() if self._proc.stdout else ""
                raise TargetUnavailable(f"duck-sim exited before robotd bound {sock}:\n{out}")
            time.sleep(0.25)
        raise TargetUnavailable(f"duck-sim did not bind {sock} within 120 s")

    def teardown(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self.repo and (self.repo / "scripts" / "duck-sim").is_file():
            subprocess.run(
                [str(self.repo / "scripts" / "duck-sim"), "down"],
                cwd=str(self.repo),
                env={**os.environ, "DUCK_SIM_STATE": str(self.state)},
                capture_output=True,
                timeout=30,
                check=False,
            )


# ---------------------------------------------------------------------------
# a real duck
# ---------------------------------------------------------------------------


class RealTarget(Target):
    """A duck, over whichever transport the operator has.

    Args:
        transport_kind: ``unix``, ``ssh``, ``tcp`` or ``webrtc``.
        host: The duck's address. Discovery is deliberately not attempted here:
            the review found three of ``castor duck``'s four discovery methods
            cannot find a stock duck, so the benchmark asks for ``--host``
            rather than spending the clock on a search that cannot succeed.
        user: SSH login, for ``transport: ssh``.
        port: TCP port, for ``transport: tcp``. Defaults to the bridge's 7788.
        socket_path: robotd socket, for ``transport: unix`` and the far end of
            an SSH forward.
        extra: Anything else to pass through to the driver config.
    """

    def __init__(
        self,
        *,
        transport_kind: str = "ssh",
        host: Optional[str] = None,
        user: Optional[str] = None,
        port: Optional[int] = None,
        socket_path: str = "/run/robotd.sock",
        extra: Optional[dict] = None,
    ) -> None:
        if transport_kind == "webrtc":
            raise TargetUnavailable(
                "MicroduckDriver has no webrtc transport yet (microduck_driver.py:186-213). "
                "mediad's signalling server on 8443 is the contract to write it against; "
                "until then use ssh, tcp or unix."
            )
        if transport_kind not in ("unix", "ssh", "tcp"):
            raise TargetUnavailable(f"unknown transport {transport_kind!r}")
        if transport_kind in ("ssh", "tcp") and not host:
            raise TargetUnavailable(f"transport {transport_kind!r} needs --host")

        config: dict[str, Any] = {"transport": transport_kind, "socket": socket_path}
        if transport_kind == "ssh":
            config["ssh_host"] = host
            if user:
                config["ssh_user"] = user
        elif transport_kind == "tcp":
            config["host"] = host
            config["port"] = int(port or BRIDGE_PORT)
        config.update(extra or {})

        target = {
            "unix": socket_path,
            "ssh": f"ssh://{user + '@' if user else ''}{host}{socket_path}",
            "tcp": f"{host}:{int(port or BRIDGE_PORT)}",
        }[transport_kind]

        notes = []
        if transport_kind == "tcp" and int(port or BRIDGE_PORT) == BRIDGE_PORT:
            notes.append(
                "Port 7788 is duck-studio's bridge, whose own deadman is 700 ms "
                "(bridge/microduck-bridge.py:52). If C6 names bridge_deadman rather than "
                "driver_ttl, this client stopped feeding the robot sooner than it meant to."
            )
        super().__init__(
            kind="real",
            driver_config=config,
            transport={"kind": transport_kind, "target": target},
            counts_as_real=True,
            supports_odometry=True,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# a driver that never connects
# ---------------------------------------------------------------------------


class MockModeTarget(Target):
    """``--transport mock``: the driver with nothing behind it.

    ``MicroduckDriver`` degrades to mock mode on any connect failure and keeps
    answering — ``health_check()`` returns ``{"ok": True, "mode": "mock"}``
    (microduck_driver.py:208-213, :463-465, :535-536). That is trap 7, and this
    target exists so the benchmark can prove it fails C2 rather than passing
    quietly. **Mock mode is a fail, never a pass.**
    """

    def __init__(self) -> None:
        super().__init__(
            kind="mock",
            driver_config={"transport": "mock"},
            transport={"kind": "mock", "target": None},
            counts_as_real=False,
            why_not_real="the driver was never connected to anything",
            supports_odometry=False,
            notes=[
                "MicroduckDriver answers every command in mock mode and reports ok. "
                "C2 exists to catch exactly that."
            ],
        )
