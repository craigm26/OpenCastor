"""``castor bench ten-minutes`` — the ten-minute goal as a thing you can run.

The goal has been a sentence in a memory note since 2026-08-14. This is the
command that turns it into a JSON file, specified in
``docs/reviews/microduck-ten-minutes-2026-09-08.md``.

**The clock starts when the runner writes its first command** and **ends at the
first ``robot.move`` OpenCastor originated from a model's answer, accepted by
robotd with the deadman armed** — not at "config written", not at "the brain
replied", and not at ``castor duck test``, which needs no brain and is
therefore not the thing being measured.

Seven checkpoints, six of them mandatory, each a single timestamp with a
defined evidence source and a defined failure. The pass rule is all six, in
order, with ``C6.t - T0 < 600 s``: wall clock from the first command, including
every retry, prompt and wait. Three rules keep it honest:

* **A skipped checkpoint is a fail, not an omission.**
* **Mock targets never pass.** They report ``ci-pass`` and are never counted as
  a pass of the ten-minute goal.
* **Wi-Fi onboarding is excluded and reported**, with a value or ``null`` and a
  one-line reason, never folded in silently.

C3 is written the way it is because of the review's central finding: four wire
keys were wrong for weeks in shipped code because nothing in this project ever
ran OpenCastor against a ``robotd``. C3 reads identity **from the keys the wire
actually uses** (``castor/bench/wire.py``) and **fails when a key is absent**
rather than defaulting to ``"?"``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from castor.bench import wire
from castor.bench.record import DEFAULT_BUDGET_S, Record, host_environment
from castor.bench.targets import (
    MockModeTarget,
    MockTarget,
    RealTarget,
    SimTarget,
    Target,
    TargetUnavailable,
)

logger = logging.getLogger("OpenCastor.Bench")

#: The checkpoints that must all pass, in order, inside the budget.
MANDATORY = ("C1", "C2", "C3", "C4", "C5", "C6")

#: C7 is optional: it needs a floor and a charged duck, or the simulator. It is
#: the only checkpoint that proves motion rather than acceptance, and a run
#: without it must say ``"stepped": null``, never ``"stepped": true``.
OPTIONAL = ("C7",)

#: How far the duck must move for C7 to count, in metres.
C7_MIN_DISPLACEMENT_M = 0.030

#: What the runner asks the brain for, when the operator does not say.
DEFAULT_REQUEST = "walk forward a little, then stop"

#: The plan a scripted brain answers with. It is a plan, not a model: a run that
#: used it can never be a plain ``pass``.
SCRIPTED_PLAN = '[{"move": "walk", "speed": 0.6, "seconds": 1.0}, {"move": "stop"}]'


CHECKPOINTS: dict[str, str] = {
    "T0": "Clock start — the moment the runner writes its first command to its transcript",
    "C1": "Package installed — importlib.metadata.version('opencastor') returns",
    "C2": "Duck reachable — the transport is open and one JSON-RPC round trip has completed",
    "C3": "Identity read — hello, robot.health and robot.policies have all answered",
    "C4": "First LLM turn accepted — a plan that parsed and that expand() accepted",
    "C5": "First robot.move accepted, deadman armed — and the intent loop observed re-sending",
    "C6": "robot.stop observed after silence — and the record names which deadman fired",
    "C7": "(optional) First step measured by the duck — odom moved more than 30 mm",
}


# ---------------------------------------------------------------------------
# the wire tap
# ---------------------------------------------------------------------------


@dataclass
class Frame:
    """One JSON-RPC line, as it went over the socket."""

    t: float
    direction: str
    obj: dict


class WireTap:
    """Every frame this process wrote or read, with the benchmark's own clock.

    This is what makes C5 and C6 measurements rather than log lines. It wraps
    the driver's own ``_write`` and ``_dispatch`` on the instance, so nothing in
    the driver changes and nothing else in the process is affected.
    """

    def __init__(self, driver: Any, record: Record) -> None:
        self._record = record
        self._lock = threading.Lock()
        self.frames: list[Frame] = []

        original_write = driver._write
        original_dispatch = driver._dispatch

        def write(obj: dict) -> None:
            self._add("out", obj)
            return original_write(obj)

        def dispatch(msg: dict) -> None:
            self._add("in", msg)
            return original_dispatch(msg)

        driver._write = write
        driver._dispatch = dispatch

    def _add(self, direction: str, obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        with self._lock:
            self.frames.append(Frame(self._record.now(), direction, obj))

    def outgoing(self, method: str, since: float = 0.0) -> list[Frame]:
        with self._lock:
            return [
                f
                for f in self.frames
                if f.direction == "out" and f.obj.get("method") == method and f.t >= since
            ]

    def wait_for(
        self,
        predicate: "Callable[[Frame], bool]",
        timeout: float,
        *,
        since: float = 0.0,
    ) -> Optional[Frame]:
        """Block until a frame matches, or the timeout expires. Returns it or None."""
        deadline = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < deadline:
            with self._lock:
                frames = self.frames[seen:]
                seen = len(self.frames)
            for frame in frames:
                if frame.t >= since and predicate(frame):
                    return frame
            time.sleep(0.005)
        return None

    def carry(self, frames: "list[Frame]") -> None:
        """Copy frames into the record's ``wire`` list, in order."""
        for frame in frames:
            self._record.wire_line(frame.direction, frame.obj, t=frame.t)


def _twist_of(frame: Frame) -> Optional[tuple[float, float, float]]:
    """The ``{vx, vy, vyaw}`` a ``robot.move`` frame carries, or None."""
    if frame.obj.get("method") != wire.M_MOVE:
        return None
    params = frame.obj.get("params")
    if not isinstance(params, dict):
        return None
    try:
        values = [float(params.get(key, 0.0)) for key in wire.MOVE_KEYS]
        return (values[0], values[1], values[2])
    except (TypeError, ValueError):
        return None


def _is_moving(frame: Frame) -> bool:
    twist = _twist_of(frame)
    return twist is not None and any(abs(v) > 1e-9 for v in twist)


def _is_zeroing(frame: Frame) -> bool:
    twist = _twist_of(frame)
    return twist is not None and all(abs(v) <= 1e-9 for v in twist)


# ---------------------------------------------------------------------------
# brains
# ---------------------------------------------------------------------------


@dataclass
class BrainAnswer:
    """One turn, with everything the record needs to describe it."""

    text: str
    provider: str
    model: Optional[str]
    latency_s: float
    prompt_bytes: int
    scripted: bool


class ScriptedBrain:
    """A canned plan, so CI can exercise C4 with no key and no network.

    It is not a model, and the record says so: a run that used it can only
    reach ``ci-pass``.
    """

    name = "scripted"
    scripted = True

    def answer(self, prompt: str) -> BrainAnswer:
        start = time.monotonic()
        return BrainAnswer(
            text=SCRIPTED_PLAN,
            provider="scripted",
            model=None,
            latency_s=time.monotonic() - start,
            prompt_bytes=len(prompt.encode()),
            scripted=True,
        )


class ProviderBrain:
    """Whatever ``castor.providers.get_provider`` resolves to.

    Args:
        spec: ``provider`` or ``provider:model``. None asks the registry for its
            default, which is ``google`` (``castor/registry.py:176``) unless a
            config says otherwise — the record names what it actually got, not
            what was asked for.
    """

    scripted = False

    def __init__(self, spec: Optional[str] = None) -> None:
        self.spec = spec
        self.provider_name: Optional[str] = None
        self.model_name: Optional[str] = None
        self._provider: Any = None
        if spec:
            name, _, model = spec.partition(":")
            self.provider_name = name or None
            self.model_name = model or None

    @property
    def name(self) -> str:
        return self.provider_name or "default"

    def _resolve(self) -> Any:
        if self._provider is not None:
            return self._provider
        from castor.providers import get_provider

        config: dict[str, Any] = {}
        if self.provider_name:
            config["provider"] = self.provider_name
        if self.model_name:
            config["model"] = self.model_name
        self._provider = get_provider(config)
        # Name what we got rather than what was asked for.
        self.provider_name = (
            getattr(self._provider, "name", None)
            or self.provider_name
            or type(self._provider).__name__
        )
        self.model_name = getattr(self._provider, "model", None) or self.model_name
        return self._provider

    def answer(self, prompt: str) -> BrainAnswer:
        provider = self._resolve()
        start = time.monotonic()
        thought = provider.think(prompt)
        latency = time.monotonic() - start
        text = getattr(thought, "text", None) or str(thought)
        return BrainAnswer(
            text=text,
            provider=str(self.provider_name),
            model=self.model_name,
            latency_s=latency,
            prompt_bytes=len(prompt.encode()),
            scripted=False,
        )


# ---------------------------------------------------------------------------
# robots
# ---------------------------------------------------------------------------


@dataclass
class RobotSpec:
    """What a robot brings to this benchmark.

    Args:
        name: ``--robot`` value.
        implemented: False for a robot whose runner does not exist yet. The CLI
            still prints the checkpoint list it would use, because a benchmark
            that cannot be described is a benchmark nobody will write.
        checkpoints: id → what it means, for this robot.
        note: Why it is not implemented, when it is not.
    """

    name: str
    implemented: bool
    checkpoints: dict[str, str] = field(default_factory=lambda: dict(CHECKPOINTS))
    note: str = ""


RC_CAR_CHECKPOINTS = {
    "T0": CHECKPOINTS["T0"],
    "C1": CHECKPOINTS["C1"],
    "C2": "Car reachable — the PCA9685 answered on I2C and the driver is not simulating wheels",
    "C3": "Identity read — chip address, PWM frequency, channel map and the ESC arming state",
    "C4": CHECKPOINTS["C4"],
    "C5": "First throttle command accepted, deadman armed — and the loop observed re-sending",
    "C6": "Zero throttle observed after silence — and the record names which layer sent it",
    "C7": "(optional) First movement measured by the car — wheel odometry or a marker",
}

ROBOTS: dict[str, RobotSpec] = {
    "microduck": RobotSpec("microduck", True),
    "rc-car": RobotSpec(
        "rc-car",
        False,
        RC_CAR_CHECKPOINTS,
        note=(
            "not implemented. Every checkpoint above C2 would be answered by our own logs "
            "rather than by the robot: the car has no health reply, no policy slots and no "
            "odometry on the wire, so C3 and C7 have no honest source yet. The duck was "
            "written first because it can answer all seven itself."
        ),
    ),
}


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------


class BenchError(RuntimeError):
    """The run could not be set up. Never a verdict — a verdict needs a run."""


def build_target(
    *,
    transport: str = "auto",
    host: Optional[str] = None,
    user: Optional[str] = None,
    port: Optional[int] = None,
    socket_path: str = "/run/robotd.sock",
    ci: bool = False,
    sim: bool = False,
    mock_cmd: Optional[str] = None,
    sim_repo: Optional[str] = None,
    sim_rl: Optional[str] = None,
    in_process_mock: bool = False,
    mock_replies: Optional[dict] = None,
) -> Target:
    """Pick the target the flags describe, refusing the mock unless ``--ci``."""
    if sim:
        return SimTarget(repo=sim_repo, rl=sim_rl)
    if transport == "mock":
        if not ci:
            raise BenchError(
                "--transport mock is refused without --ci: mock mode is a fail, never a pass, "
                "and a run that cannot pass should say so before it starts"
            )
        return MockModeTarget()
    if ci:
        return MockTarget(
            in_process=in_process_mock, mock_cmd=mock_cmd, replies=mock_replies
        )
    if transport == "auto":
        transport = "unix" if host is None else "ssh"
    return RealTarget(
        transport_kind=transport,
        host=host,
        user=user,
        port=port,
        socket_path=socket_path,
    )


def run(
    *,
    robot: str = "microduck",
    target: Optional[Target] = None,
    brain: Any = None,
    request: str = DEFAULT_REQUEST,
    budget_s: float = DEFAULT_BUDGET_S,
    floor: bool = False,
    wifi_onboarding_s: Optional[float] = None,
    wifi_reason: Optional[str] = None,
    fresh_venv: Optional[str] = None,
    repo_shas: Optional[dict[str, Optional[str]]] = None,
    say: "Callable[[str], None]" = lambda _line: None,
) -> Record:
    """Run the benchmark and return the record. Never raises on a failed checkpoint.

    Args:
        robot: ``microduck`` today; ``rc-car`` is a stub that refuses.
        target: Where to point the driver. Required.
        brain: Anything with ``answer(prompt) -> BrainAnswer``.
        request: What the brain is asked for at C4.
        budget_s: The ten minutes, in seconds.
        floor: Attempt C7. Asks nothing here — the CLI does the asking.
        wifi_onboarding_s: The excluded interval, or None.
        wifi_reason: One line saying why it is what it is.
        fresh_venv: A venv built after T0, for C1. None records the running one.
        repo_shas: Extra shas for ``environment.repo_shas``.
        say: Where progress lines go.

    Returns:
        The record, with a verdict already decided.
    """
    spec = ROBOTS.get(robot)
    if spec is None:
        raise BenchError(f"unknown robot {robot!r}; known: {', '.join(sorted(ROBOTS))}")
    if not spec.implemented:
        raise BenchError(
            f"`castor bench ten-minutes --robot {robot}` is {spec.note}\n"
            + "\n".join(f"  {cid}  {text}" for cid, text in spec.checkpoints.items())
        )
    if target is None:
        raise BenchError("a target is required")

    brain = brain or ScriptedBrain()
    record = Record(robot=robot, target=target.kind, budget_s=float(budget_s))
    record.environment = host_environment()
    record.environment["repo_shas"].update(
        {k: v for k, v in (repo_shas or {}).items() if v is not None}
    )
    record.environment["transport"] = dict(target.transport)
    record.excluded = {
        "wifi_onboarding_s": wifi_onboarding_s,
        "reason": wifi_reason
        or (
            "not measured by this run; Wi-Fi onboarding belongs to Pollen and to whatever "
            "ships in the retail box, and is excluded from the clock by design"
        ),
    }
    for note in target.notes:
        record.note(note)

    driver = None
    tap: Optional[WireTap] = None
    try:
        record.reset_clock()
        # ── C1 ──────────────────────────────────────────────────────────
        _c1_package(record, fresh_venv, say)

        # ── target up ───────────────────────────────────────────────────
        target.setup(record)

        # ── C2 ──────────────────────────────────────────────────────────
        from castor.drivers.microduck_driver import MicroduckDriver

        record.typed(
            "python -c 'MicroduckDriver("
            + json.dumps(target.driver_config, separators=(",", ":"))
            + ")'"
        )
        driver = MicroduckDriver(dict(target.driver_config))
        tap = WireTap(driver, record)
        hello = _c2_reachable(record, driver, tap, target, say)

        # ── C3 ──────────────────────────────────────────────────────────
        _c3_identity(record, driver, hello, say)

        # ── C4 ──────────────────────────────────────────────────────────
        plan = _c4_brain(record, driver, brain, request, say)

        # ── C5, C6, C7 ─────────────────────────────────────────────────
        _c5_c6_c7(record, driver, tap, plan, target, floor, say)
    except TargetUnavailable as exc:
        record.note(f"the target could not be brought up: {exc}")
        record.mark("C2", False, {"error": str(exc)})
    except BenchError:
        raise
    except Exception as exc:  # noqa: BLE001 — an exploded run is a failed run, recorded
        logger.exception("bench ten-minutes crashed")
        record.note(f"the run raised {type(exc).__name__}: {exc}")
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            target.teardown()
        except Exception:  # noqa: BLE001
            pass

    # Anything never stamped is a fail, not an omission.
    for cid in MANDATORY:
        if record.checkpoint(cid) is None:
            record.mark(cid, False, {"reason": "the run never reached this checkpoint"})
    if record.checkpoint("C7") is None:
        record.mark("C7", None, {"reason": "not attempted"})

    real = target.counts_as_real and not getattr(brain, "scripted", False)
    why = target.why_not_real or (
        "the brain was a scripted plan, not a model" if getattr(brain, "scripted", False) else ""
    )
    record.decide(MANDATORY, real=real, real_reason=why)
    return record


# ---------------------------------------------------------------------------
# C1
# ---------------------------------------------------------------------------


def _c1_package(record: Record, fresh_venv: Optional[str], say) -> None:
    """Is OpenCastor installed, and does the installed thing have a duck in it?

    Fails when the resolved distribution has no ``castor/microduck.py``, which
    is the one file every route in the review's Route A depends on.
    """
    import importlib.metadata as md

    import castor

    if fresh_venv:
        python = str(Path(fresh_venv) / "bin" / "python")
        record.typed(f"{python} -c 'import importlib.metadata as m; m.version(\"opencastor\")'")
    else:
        import sys as _sys

        python = _sys.executable
        record.typed(f"{python} -c 'import importlib.metadata as m; m.version(\"opencastor\")'")

    try:
        version = md.version("opencastor")
    except Exception:  # noqa: BLE001
        version = None

    castor_dir = Path(castor.__file__).resolve().parent
    has_duck = (castor_dir / "microduck.py").is_file()
    has_driver = (castor_dir / "drivers" / "microduck_driver.py").is_file()

    ok = bool(has_duck and has_driver)
    record.mark(
        "C1",
        ok,
        {
            "version": version,
            "sys_executable": python,
            "venv": fresh_venv,
            "fresh_venv": bool(fresh_venv),
            "castor_path": str(castor_dir),
            "has_microduck_py": has_duck,
            "has_microduck_driver_py": has_driver,
            "wheel": None,
        },
    )
    if not fresh_venv:
        record.note(
            "C1 timed an import in the interpreter already running, not a venv built after T0. "
            "A published number must use --fresh-venv, which times the pip install too."
        )
    say(f"  C1 package: {'ok' if ok else 'FAILED'} ({version})")


# ---------------------------------------------------------------------------
# C2
# ---------------------------------------------------------------------------


def _c2_reachable(record: Record, driver: Any, tap: WireTap, target: Target, say) -> Any:
    """Transport open, one round trip done. Mock mode is a fail, never a pass."""
    mode = getattr(driver, "_mode", "mock")
    evidence: dict[str, Any] = {
        "mode": mode,
        "transport": target.transport.get("kind"),
        "target": target.transport.get("target"),
        "hello": None,
        "mock_target": not target.counts_as_real,
    }
    if mode != "hardware":
        record.mark("C2", False, {**evidence, "error": "MicroduckDriver degraded to mock mode"})
        record.note(
            "MicroduckDriver answers every command in mock mode and health_check() returns "
            "ok (microduck_driver.py:208-213, :463-465, :535-536). C2 is what catches it."
        )
        say("  C2 reachable: FAILED (mock mode)")
        return None

    hello: Any = None
    try:
        hello = driver.call(wire.M_HELLO, {wire.HELLO_API_VERSION: wire.API_VERSION})
        evidence["hello"] = hello
    except Exception as exc:  # noqa: BLE001
        record.mark("C2", False, {**evidence, "error": f"hello did not answer: {exc}"})
        say(f"  C2 reachable: FAILED ({exc})")
        return None

    record.mark("C2", True, evidence)
    say(f"  C2 reachable: ok ({target.transport.get('kind')} → {target.transport.get('target')})")
    return hello


# ---------------------------------------------------------------------------
# C3
# ---------------------------------------------------------------------------


def _c3_identity(record: Record, driver: Any, hello: Any, say) -> None:
    """Read identity off the wire, and fail on an absent key rather than print '?'.

    This is the checkpoint the review's traps 1, 3 and 4 would have failed on
    the first push. Every key comes from ``castor/bench/wire.py``, which carries
    its ``duck-ipc-proto`` line.
    """
    if record.checkpoint("C2") is None or record.checkpoint("C2").ok is not True:
        record.mark("C3", False, {"reason": "C2 did not pass, so nothing could be read"})
        say("  C3 identity: FAILED (C2 did not pass, so nothing could be read)")
        return

    problems: list[str] = []
    evidence: dict[str, Any] = {"sources": dict(wire.SOURCES)}

    # -- hello --------------------------------------------------------
    gone = wire.missing_keys(hello, wire.HELLO_REQUIRED)
    if gone:
        problems.append(f"hello is missing {gone} ({wire.SOURCES['HelloResult']})")
    hello_d = hello if isinstance(hello, dict) else {}
    evidence["hello"] = {
        wire.HELLO_API_VERSION: hello_d.get(wire.HELLO_API_VERSION),
        wire.HELLO_DAEMON_VERSION: hello_d.get(wire.HELLO_DAEMON_VERSION),
        wire.HELLO_REVISION: hello_d.get(wire.HELLO_REVISION),
    }

    # -- robot.health -------------------------------------------------
    try:
        health = driver.call(wire.M_HEALTH)
    except Exception as exc:  # noqa: BLE001
        health = None
        problems.append(f"{wire.M_HEALTH} did not answer: {exc}")

    gone = wire.missing_keys(health, wire.HEALTH_REQUIRED)
    if gone:
        problems.append(f"{wire.M_HEALTH} is missing {gone} ({wire.SOURCES['HealthResult']})")
    health_d = health if isinstance(health, dict) else {}

    loop = health_d.get(wire.HEALTH_CONTROL_LOOP)
    gone = wire.missing_keys(loop, wire.LOOP_REQUIRED)
    if loop is not None and gone:
        problems.append(
            f"{wire.HEALTH_CONTROL_LOOP} is missing {gone} ({wire.SOURCES['LoopHealth']})"
        )
    battery = health_d.get(wire.HEALTH_BATTERY)
    gone = wire.missing_keys(battery, wire.BATTERY_REQUIRED)
    if battery is not None and gone:
        problems.append(f"{wire.HEALTH_BATTERY} is missing {gone} ({wire.SOURCES['Battery']})")

    if health_d and health_d.get(wire.HEALTH_HEALTHY) is not True:
        problems.append(
            f"{wire.HEALTH_HEALTHY} is {health_d.get(wire.HEALTH_HEALTHY)!r}"
            + (f": {health_d.get(wire.HEALTH_REASON)}" if health_d.get(wire.HEALTH_REASON) else "")
        )

    percent = (battery or {}).get(wire.BATTERY_PERCENT) if isinstance(battery, dict) else None
    if isinstance(percent, (int, float)) and percent < wire.MIN_BATTERY_PERCENT:
        problems.append(
            f"battery {percent:g}% is below the choreographer's "
            f"{wire.MIN_BATTERY_PERCENT:g}% floor (microduck_choreography.py:373)"
        )

    evidence["health"] = {
        wire.HEALTH_HEALTHY: health_d.get(wire.HEALTH_HEALTHY),
        wire.HEALTH_DEGRADED: health_d.get(wire.HEALTH_DEGRADED),
        wire.HEALTH_CONTROL_LOOP: loop,
        wire.HEALTH_BATTERY: battery,
        wire.HEALTH_BUS: health_d.get(wire.HEALTH_BUS),
        wire.HEALTH_IMU: health_d.get(wire.HEALTH_IMU),
    }

    # -- robot.policies -----------------------------------------------
    try:
        policies = driver.call(wire.M_POLICIES)
    except Exception as exc:  # noqa: BLE001
        policies = None
        problems.append(f"{wire.M_POLICIES} did not answer: {exc}")

    gone = wire.missing_keys(policies, wire.POLICIES_REQUIRED)
    if gone:
        problems.append(
            f"{wire.M_POLICIES} is missing {gone} ({wire.SOURCES['PoliciesResult']})"
        )
    policies_d = policies if isinstance(policies, dict) else {}
    slots = policies_d.get(wire.POL_SLOTS) or []
    walk_slot = next(
        (
            s
            for s in slots
            if isinstance(s, dict) and s.get(wire.SLOT_SLOT) == wire.WALK_SLOT
            and s.get(wire.SLOT_PATH)
        ),
        None,
    )
    if walk_slot is None:
        problems.append(
            f"no {wire.WALK_SLOT!r} slot with a {wire.SLOT_PATH!r} in {wire.M_POLICIES}; "
            "a board that could not reach the Hub has no gait "
            "(scripts/seed-policies.sh:25-31)"
        )

    evidence["policies"] = {
        wire.POL_MODE: policies_d.get(wire.POL_MODE),
        wire.POL_ENABLED: policies_d.get(wire.POL_ENABLED),
        wire.POL_SLOTS: slots,
        wire.POL_SKILLS: policies_d.get(wire.POL_SKILLS),
        wire.POL_CHANGE_ERROR: policies_d.get(wire.POL_CHANGE_ERROR),
    }
    evidence["obs_len"] = wire.POLICY_OBS_LEN
    evidence["action_len"] = wire.POLICY_ACTION_LEN

    # -- what the driver says it read, beside what the wire said ------
    try:
        driver_policies = list(driver.get_policies())
    except Exception:  # noqa: BLE001
        driver_policies = []
    evidence["driver_get_policies"] = driver_policies
    slot_names = [s.get(wire.SLOT_PATH) for s in slots if isinstance(s, dict)]
    if slot_names and not driver_policies:
        record.note(
            f"the wire reported {len([n for n in slot_names if n])} filled policy slot(s) and "
            "MicroduckDriver.get_policies() returned []. That is the review's trap 3: "
            f"microduck_driver.py:233 reads result['networks'], and SubscribeResult "
            f"({wire.SOURCES['SubscribeResult']}) has no such key."
        )

    if problems:
        record.mark("C3", False, {**evidence, "problems": problems})
        say("  C3 identity: FAILED")
        for problem in problems:
            say(f"      {problem}")
        return

    record.duck = {
        "hello": evidence["hello"],
        "health": evidence["health"],
        "policies": evidence["policies"],
        "obs_len": wire.POLICY_OBS_LEN,
        "action_len": wire.POLICY_ACTION_LEN,
        "name": None,
        "serial": None,
        "name_source": (
            "not read; the robot's name and serial are on configd's own socket "
            "(system.info, duck-ipc-proto/src/lib.rs:3650-3663), and claiming one "
            "without reading it is not allowed"
        ),
    }
    record.mark("C3", True, evidence)
    achieved = (loop or {}).get(wire.LOOP_ACHIEVED_HZ)
    say(
        f"  C3 identity: ok (healthy, "
        f"{achieved if achieved is not None else (loop or {}).get(wire.LOOP_TARGET_HZ)} Hz, "
        f"battery {percent}%)"
    )


# ---------------------------------------------------------------------------
# C4
# ---------------------------------------------------------------------------


def _c4_brain(record: Record, driver: Any, brain: Any, request: str, say) -> Optional[list[dict]]:
    """One turn from the brain, expanded by the real choreographer.

    A plan refused whole is a **correct** refusal and a failed checkpoint: the
    duck is not halfway through anything, and the benchmark did not measure a
    ten-minute clock to a robot that never moved.
    """
    from castor.microduck_choreography import ChoreographyError, DuckChoreographer

    choreographer = DuckChoreographer(driver)
    prompt = (
        choreographer.vocabulary()
        + "\n\nRequest: "
        + request
        + "\n\nAnswer with ONLY a JSON array of steps. No prose, no code fence."
    )
    record.typed(f"castor duck do {request!r}")

    try:
        answer = brain.answer(prompt)
    except Exception as exc:  # noqa: BLE001
        record.mark("C4", False, {"error": f"the brain could not answer: {exc}"})
        say(f"  C4 brain: FAILED ({exc})")
        return None

    record.brain = {
        "provider": answer.provider,
        "model": answer.model,
        "scripted": answer.scripted,
        "tools": ["duck_vocabulary", "duck_perform"],
        "turns": 1,
        "latency_s": round(answer.latency_s, 3),
        "prompt_bytes": answer.prompt_bytes,
        "response_bytes": len(answer.text.encode()),
    }

    start, end = answer.text.find("["), answer.text.rfind("]")
    if start < 0 or end < start:
        record.mark(
            "C4",
            False,
            {"error": "no JSON array in the answer", "response_head": answer.text[:200]},
        )
        say("  C4 brain: FAILED (no plan in the answer)")
        return None
    try:
        plan = json.loads(answer.text[start : end + 1])
    except ValueError as exc:
        record.mark("C4", False, {"error": f"the plan did not parse: {exc}"})
        say(f"  C4 brain: FAILED ({exc})")
        return None

    try:
        expanded = choreographer.expand(plan)
    except ChoreographyError as exc:
        record.mark(
            "C4",
            False,
            {"error": f"expand() refused the plan whole: {exc}", "plan": plan, "refusal": True},
        )
        say(f"  C4 brain: FAILED (refused: {exc})")
        return None

    record.mark(
        "C4",
        True,
        {
            "provider": answer.provider,
            "model": answer.model,
            "scripted": answer.scripted,
            "latency_s": round(answer.latency_s, 3),
            "prompt_bytes": answer.prompt_bytes,
            "response_bytes": len(answer.text.encode()),
            "plan": plan,
            "steps": [step.get("move") for step in expanded],
        },
    )
    say(f"  C4 brain: ok ({answer.provider}, {len(expanded)} steps)")
    return expanded


# ---------------------------------------------------------------------------
# C5, C6, C7
# ---------------------------------------------------------------------------


def _first_motion(plan: "list[dict]") -> Optional[dict]:
    """The first step of an expanded plan that produces a twist."""
    for step in plan:
        if step.get("move") in ("walk", "turn", "strafe"):
            return step
    return None


def _c5_c6_c7(
    record: Record,
    driver: Any,
    tap: WireTap,
    plan: Optional[list[dict]],
    target: Target,
    floor: bool,
    say,
) -> None:
    """The move, its re-send, the stop after silence, and optionally a step."""
    if plan is None:
        record.mark("C5", False, {"reason": "there was no accepted plan to move on"})
        record.mark("C6", False, {"reason": "nothing was moving, so nothing had to stop"})
        return

    step = _first_motion(plan)
    if step is None:
        record.mark(
            "C5",
            False,
            {
                "reason": "the accepted plan never moves the duck",
                "steps": [s.get("move") for s in plan],
            },
        )
        record.mark("C6", False, {"reason": "nothing was moving, so nothing had to stop"})
        say("  C5 move: FAILED (the plan never moves)")
        return

    intent_hz = float(getattr(driver, "_intent_hz", 20.0))
    ttl = float(getattr(driver, "_command_ttl_s", 1.5))

    odom_before = _odom(driver)
    since = record.now()

    if step["move"] == "walk":
        record.typed(f"driver.move(linear={step['speed']}, angular=0.0)")
        driver.move(float(step["speed"]), 0.0)
    elif step["move"] == "turn":
        record.typed(f"driver.move(linear=0.0, angular={step['rate']})")
        driver.move(0.0, float(step["rate"]))
    else:
        record.typed(f"driver.strafe({step['speed']})")
        driver.strafe(float(step["speed"]))

    first = tap.wait_for(_is_moving, timeout=2.0, since=since)
    if first is None:
        record.mark(
            "C5",
            False,
            {
                "reason": f"no non-zero {wire.M_MOVE} reached the wire within 2 s",
                "intent_hz": intent_hz,
            },
        )
        record.mark("C6", False, {"reason": "C5 never put a move on the wire"})
        say("  C5 move: FAILED (nothing on the wire)")
        return

    resend = tap.wait_for(
        lambda f: _is_moving(f) and f.t > first.t,
        timeout=max(0.5, 2.0 / intent_hz),
        since=first.t,
    )
    twist = _twist_of(first) or (0.0, 0.0, 0.0)
    c5_evidence: dict[str, Any] = {
        "params": dict(zip(wire.MOVE_KEYS, twist)),
        "intent_hz": intent_hz,
        "command_ttl_s": ttl,
        "resend_seen_at": round(resend.t, 4) if resend else None,
        "envelope": {
            "max_vx": getattr(driver, "_max_vx", None),
            "max_vy": getattr(driver, "_max_vy", None),
            "max_vyaw": getattr(driver, "_max_vyaw", None),
            "note": (
                "padd drives the same duck at up to 0.3 m/s and 1.5 rad/s "
                "(padd/src/main.rs:139-163). A slower number here is an envelope, "
                "not a robot that is struggling."
            ),
        },
    }
    if resend is None:
        c5_evidence["reason"] = (
            f"the intent loop did not re-send within 2/{intent_hz:g} Hz. "
            "A deadman not observed to re-arm was never armed."
        )
        record.mark("C5", False, c5_evidence, t=first.t)
        record.mark("C6", False, {"reason": "the deadman was never observed armed"})
        tap.carry([first])
        say("  C5 move: FAILED (no re-send; the deadman was never armed)")
        return

    record.mark("C5", True, c5_evidence, t=first.t)
    tap.carry([first, resend])
    say(
        f"  C5 move: ok ({c5_evidence['params']}, re-sent at "
        f"+{(resend.t - first.t) * 1000:.0f} ms)"
    )

    # ── C6: stop sending, and watch for the stop ───────────────────────
    silence_start = record.now()
    record.typed("# the runner stops sending and waits for a deadman")
    window = ttl + 1.0 / intent_hz + 0.25
    stopper = tap.wait_for(
        lambda f: _is_zeroing(f) or f.obj.get("method") == wire.M_STOP,
        timeout=window + 0.5,
        since=silence_start,
    )
    if stopper is None:
        record.mark(
            "C6",
            False,
            {
                "reason": (
                    f"no stop within command_ttl_s + 1/intent_hz + 250 ms ({window:.2f} s). "
                    f"Only robotd's own {wire.ROBOTD_DEADMAN_MS} ms deadman could have fired, "
                    "which means this client stopped feeding the robot and did not notice."
                ),
                "window_s": round(window, 3),
                "silence_started_at": round(silence_start, 4),
            },
        )
        say("  C6 stop: FAILED (nothing stopped it from this end)")
    else:
        fired_by = "explicit_stop" if stopper.obj.get("method") == wire.M_STOP else "driver_ttl"
        record.mark(
            "C6",
            True,
            {
                "fired_by": fired_by,
                "silence_to_stop_ms": round((stopper.t - silence_start) * 1000, 1),
                "window_ms": round(window * 1000, 1),
                "line": json.dumps(stopper.obj, separators=(",", ":")),
                "deadmen": {
                    "driver_command_ttl_s": ttl,
                    "bridge_ms": wire.BRIDGE_DEADMAN_MS,
                    "robotd_ms": wire.ROBOTD_DEADMAN_MS,
                },
            },
            t=stopper.t,
        )
        tap.carry([stopper])
        say(
            f"  C6 stop: ok ({fired_by}, "
            f"{(stopper.t - silence_start) * 1000:.0f} ms after silence)"
        )

    # ── C7 ─────────────────────────────────────────────────────────────
    if not floor:
        record.mark("C7", None, {"reason": "not asked for; --floor enables C7", "stepped": None})
        return
    if not target.supports_odometry:
        record.mark(
            "C7",
            None,
            {
                "reason": f"the {target.kind} target has no physics and no odometry",
                "stepped": None,
            },
        )
        return

    odom_after = _odom(driver)
    if not odom_before or not odom_after:
        record.mark(
            "C7",
            None,
            {
                "reason": (
                    f"no {wire.STATE_ODOM} on the state stream "
                    f"({wire.SOURCES['OdomState']}); nothing measured the duck"
                ),
                "stepped": None,
            },
        )
        return

    p0 = list(odom_before.get(wire.ODOM_POSITION) or [])
    p1 = list(odom_after.get(wire.ODOM_POSITION) or [])
    moved = (
        sum((a - b) ** 2 for a, b in zip(p1[:2], p0[:2])) ** 0.5 if len(p0) >= 2 and len(p1) >= 2
        else None
    )
    fallen = bool((driver.get_state().get(wire.STATE_SAFETY) or {}).get(wire.SAFETY_FALLEN))
    ok = moved is not None and moved > C7_MIN_DISPLACEMENT_M and not fallen
    record.mark(
        "C7",
        ok,
        {
            "stepped": bool(ok),
            "displacement_m": None if moved is None else round(moved, 4),
            "threshold_m": C7_MIN_DISPLACEMENT_M,
            "start": odom_before,
            "end": odom_after,
            "fallen": fallen,
        },
    )
    say(f"  C7 step: {'ok' if ok else 'FAILED'} ({moved} m)")


def _odom(driver: Any) -> dict:
    try:
        return dict(driver.get_odometry() or {})
    except Exception:  # noqa: BLE001
        return {}
