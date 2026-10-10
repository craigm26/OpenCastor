"""Per-cycle workspace enforcement (castor.safety.workspace_enforcer).

The workspace policy re-checked the command standing on /dev/motor only when something wrote, and
castor/main.py writes once per brain step. With a slow brain, a full-speed command that was safe
when it was accepted kept running after its stopping path had left the workspace. The EV-03
hostile-model test left this gap open; these tests pin the fix.
"""

from __future__ import annotations

import ast
import math
import threading
import time
from pathlib import Path

import pytest

from castor.fs.namespace import Namespace
from castor.fs.permissions import PermissionTable
from castor.fs.safety import SafetyLayer
from castor.safety.workspace import (
    parse_workspace_config,
    register_pose_source,
    unregister_pose_source,
)
from castor.safety.workspace_enforcer import WorkspaceEnforcer, start_workspace_enforcer

POSE_SOURCE = "test-workspace-enforcer"
BAY = [[0, 0], [6, 0], [6, 4], [0, 4]]
CONTROL_DT = 0.02  # one 50 Hz control cycle
PHYSICS_DT = 0.01
DECEL = 1.0
FULL_SPEED_AT_THE_WALL = {"type": "move", "linear": 1.0, "angular": 0.0}
MAIN_PY = Path(__file__).resolve().parents[1] / "castor" / "main.py"


class Rover:
    """A unicycle base with bounded acceleration, driven like a DriverBase (move/stop).

    Its true pose is what the registered pose source reports: it stands in for independent
    localization, never for anything computed from the commands sent.
    """

    def __init__(self, x: float = 3.3, y: float = 2.0, top_speed: float = 1.5):
        self.x, self.y, self.heading, self.v = x, y, 0.0, 0.0
        self.cmd_v = self.cmd_w = 0.0
        self.top = top_speed
        self.calls: list[tuple] = []

    def move(self, linear: float, angular: float) -> None:
        self.calls.append(("move", linear, angular))
        self.cmd_v, self.cmd_w = linear * self.top, angular * 1.5

    def stop(self) -> None:
        self.calls.append(("stop",))
        self.cmd_v = self.cmd_w = 0.0

    def pose(self):
        return (self.x, self.y, self.heading, self.v)

    def tick(self, dt: float) -> None:
        self.v += max(-DECEL * dt, min(DECEL * dt, self.cmd_v - self.v))
        self.heading += self.cmd_w * dt
        self.x += self.v * math.cos(self.heading) * dt
        self.y += self.v * math.sin(self.heading) * dt


@pytest.fixture
def rover():
    r = Rover()
    register_pose_source(POSE_SOURCE, r.pose)
    yield r
    unregister_pose_source(POSE_SOURCE)


def _config(**overrides):
    block = {
        "keep_in": BAY,
        "top_speed_mps": 1.5,
        "max_decel_mps2": DECEL,
        "reaction_s": 0.05,
        "margin_m": 0.05,
        "enforce_hz": 50,
        "pose_source": POSE_SOURCE,
    }
    block.update(overrides)
    return parse_workspace_config(block)


def _safety(cfg=None) -> SafetyLayer:
    cfg = cfg or _config()
    return SafetyLayer(
        Namespace(),
        PermissionTable(),
        limits={"motor_rate_hz": 1000.0},
        workspace_policy=cfg.build_policy(),
    )


def _brain_step(sl: SafetyLayer, driver: Rover, action: dict) -> None:
    """What castor/main.py does once per brain step, under the motor lock: write /dev/motor
    through the safety layer, read it back, and hand the driver what is standing there."""
    with sl.motor_lock:
        sl.write("/dev/motor", action, principal="brain")
        standing = sl.read("/dev/motor", principal="brain")
        if isinstance(standing, dict) and standing.get("type") == "move":
            driver.move(standing.get("linear", 0.0), standing.get("angular", 0.0))
        else:
            driver.stop()


def _drive(sl, rover, enforcer, brain_every=None, seconds=6.0):
    """The 50 Hz control loop. The brain writes on cycle 0 and then every *brain_every* cycles
    (never again when None); the enforcer, if any, runs once every cycle."""
    trace, replaced_at = [], None
    for cycle in range(round(seconds / CONTROL_DT)):
        if cycle == 0 or (brain_every and cycle % brain_every == 0):
            _brain_step(sl, rover, dict(FULL_SPEED_AT_THE_WALL))
        if enforcer is not None and not enforcer.step() and replaced_at is None:
            replaced_at = (rover.x, rover.v)
        for _ in range(round(CONTROL_DT / PHYSICS_DT)):
            rover.tick(PHYSICS_DT)
            trace.append((rover.x, rover.y))
    return trace, replaced_at


def _inside(x: float, y: float) -> bool:
    return 0.0 <= x <= 6.0 and 0.0 <= y <= 4.0


def _events(sl: SafetyLayer) -> list:
    return [row.get("event") for row in sl.ns.read("/var/log/safety") or []]


def _enforcer_threads() -> list:
    return [t for t in threading.enumerate() if t.name == "workspace-enforcer"]


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


# ----- (a) a slow brain -------------------------------------------------------------------------
@pytest.mark.parametrize("brain_every", [None, 25], ids=["brain-silent", "brain-2Hz"])
def test_standing_full_speed_command_is_replaced_before_its_stopping_path_leaves(
    rover, brain_every
):
    """A full-speed command toward the wall, accepted with room to stop, then no new write for
    many cycles: the per-cycle re-check replaces it with zero translation while the base can
    still stop inside, and the base never leaves the bay."""
    sl = _safety()
    enforcer = WorkspaceEnforcer(sl, hz=50, halt=rover.stop)

    trace, replaced_at = _drive(sl, rover, enforcer, brain_every=brain_every)

    assert replaced_at is not None, "the standing command was never replaced"
    x, v = replaced_at
    assert v > 1.0, "the scenario has to catch the base at speed"
    # From where it was replaced: one more control cycle at speed, then guaranteed braking.
    assert x + v * CONTROL_DT + v * v / (2 * DECEL) < 6.0
    assert all(_inside(px, py) for px, py in trace), max(px for px, _ in trace)
    assert rover.v == pytest.approx(0.0)
    assert sl.ns.read("/dev/motor")["linear"] == 0.0
    assert "workspace_enforced" in _events(sl)


@pytest.mark.parametrize("brain_every", [None, 25], ids=["brain-silent", "brain-2Hz"])
def test_without_per_cycle_enforcement_the_same_command_leaves_the_bay(rover, brain_every):
    """The control case: the same policy, re-checked only when the brain writes."""
    trace, _ = _drive(_safety(), rover, enforcer=None, brain_every=brain_every)
    assert not all(_inside(px, py) for px, py in trace)


def test_the_enforcer_halts_and_never_starts_the_turn(rover):
    """The refusal command on /dev/motor keeps the turn rate, but the enforcer only stops the
    motors: the watchdog or an e-stop may already have stopped them, and a re-check must not be
    what starts a turn after that."""
    sl = _safety()
    rover.x, rover.v = 5.5, 1.0
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.6, "angular": 0.4})
    enforcer = WorkspaceEnforcer(sl, halt=rover.stop)

    assert enforcer.step() is False
    assert rover.calls == [("stop",)]
    assert sl.ns.read("/dev/motor") == {"type": "move", "linear": 0.0, "angular": 0.4}
    assert enforcer.step() is True  # zero translation may stand
    assert rover.calls == [("stop",)]
    assert (enforcer.cycles, enforcer.enforced) == (2, 1)


def test_a_safe_standing_command_is_left_alone(rover):
    sl = _safety()
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.2, "angular": 0.1})
    enforcer = WorkspaceEnforcer(sl, halt=rover.stop)
    assert all(enforcer.step() for _ in range(10))
    assert rover.calls == []
    assert sl.ns.read("/dev/motor")["linear"] == 0.2


def test_losing_the_pose_stops_a_standing_move(rover):
    sl = _safety()
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.2, "angular": 0.0})
    unregister_pose_source(POSE_SOURCE)  # the localizer went away
    assert WorkspaceEnforcer(sl, halt=rover.stop).step() is False
    assert rover.calls == [("stop",)]


def test_a_policy_check_that_raises_writes_a_stop():
    class BrokenPolicy:
        def check(self, data):
            raise RuntimeError("localizer library crashed")

        @staticmethod
        def refusal_command(data):
            return {"type": "move", "linear": 0.0, "angular": 0.0}

    sl = SafetyLayer(Namespace(), PermissionTable(), workspace_policy=BrokenPolicy())
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.2, "angular": 0.0})
    halts = []
    assert WorkspaceEnforcer(sl, halt=lambda: halts.append(1)).step() is False
    assert sl.ns.read("/dev/motor") == {"type": "stop"}
    assert halts == [1]
    assert "workspace_check_failed" in _events(sl)


def test_a_recheck_that_cannot_run_stops_the_motors():
    class Exploding:
        ns = Namespace()

        def enforce_workspace(self, path):
            raise RuntimeError("boom")

    safety = Exploding()
    halts = []
    assert WorkspaceEnforcer(safety, halt=lambda: halts.append(1)).step() is False
    assert safety.ns.read("/dev/motor") == {"type": "stop"}
    assert halts == [1]


def test_a_halt_that_fails_does_not_end_enforcement(rover):
    sl = _safety()
    rover.x, rover.v = 5.6, 1.0
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.5, "angular": 0.0})

    def broken_stop():
        raise OSError("serial port gone")

    enforcer = WorkspaceEnforcer(sl, halt=broken_stop)
    assert enforcer.step() is False  # logged, not raised
    assert enforcer.step() is True


def test_motor_writes_and_rechecks_share_the_motor_lock(rover):
    sl = _safety()
    assert WorkspaceEnforcer(sl).lock is sl.motor_lock
    written = threading.Event()

    def write():
        sl.write("/dev/motor", {"type": "move", "linear": 0.1, "angular": 0.0}, principal="brain")
        written.set()

    with sl.motor_lock:
        writer = threading.Thread(target=write)
        writer.start()
        assert not written.wait(0.2), "a motor write ran while the motor lock was held"
    writer.join(5)
    assert written.is_set()


@pytest.mark.parametrize("hz", [0, -5, math.nan, math.inf, True, "50"])
def test_a_bad_rate_is_rejected(hz):
    with pytest.raises(ValueError):
        WorkspaceEnforcer(_safety(), hz=hz)


# ----- (b) started only when configured, stopped on shutdown -------------------------------------
def test_no_workspace_policy_means_no_enforcer():
    before = len(_enforcer_threads())
    assert start_workspace_enforcer(SafetyLayer(Namespace(), PermissionTable())) is None
    assert len(_enforcer_threads()) == before


def test_a_configured_policy_starts_the_enforcer_and_stop_ends_it(rover):
    cfg = _config(enforce_hz=200)
    sl = _safety(cfg)
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.3, "angular": 0.0})
    before = len(_enforcer_threads())
    enforcer = start_workspace_enforcer(sl, hz=cfg.enforce_hz, halt=rover.stop)
    try:
        assert enforcer is not None and enforcer.running
        assert len(_enforcer_threads()) == before + 1
        enforcer.start()  # already running: no second thread
        assert len(_enforcer_threads()) == before + 1
        _wait_for(lambda: enforcer.cycles >= 5)
        assert rover.calls == []

        rover.x, rover.v = 5.6, 1.0  # the base has driven up to the wall; nobody writes
        _wait_for(lambda: rover.calls)
        assert rover.calls[0] == ("stop",)
        assert sl.ns.read("/dev/motor")["linear"] == 0.0
    finally:
        enforcer.stop()

    assert not enforcer.running
    assert len(_enforcer_threads()) == before
    cycles = enforcer.cycles
    time.sleep(0.05)
    assert enforcer.cycles == cycles
    enforcer.stop()  # stopping twice is harmless


def test_absent_config_block_means_no_policy_and_no_enforcer():
    """The chain castor/main.py runs, with no safety.workspace block."""
    from castor.fs import CastorFS

    cfg = parse_workspace_config({"motor_rate_hz": 20}.get("workspace"))
    assert cfg is None
    fs = CastorFS(workspace_policy=None)
    assert fs.safety.workspace_policy is None
    assert start_workspace_enforcer(fs.safety) is None


def test_a_rate_slower_than_the_reaction_time_is_refused(rover):
    before = len(_enforcer_threads())
    with pytest.raises(ValueError, match="reaction_s"):
        start_workspace_enforcer(_safety(), hz=10)  # 0.1 s period, 0.05 s reaction
    assert len(_enforcer_threads()) == before


# ----- castor/main.py wiring ----------------------------------------------------------------------
def _main_function() -> ast.FunctionDef:
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")


def _control_loop_try(main: ast.FunctionDef) -> ast.Try:
    return next(
        n
        for n in ast.walk(main)
        if isinstance(n, ast.Try)
        and isinstance(n.body[0], ast.While)
        and ast.unparse(n.body[0].test) == "not _shutdown_requested"
    )


def test_main_starts_the_enforcer_only_through_the_configured_helper():
    source = ast.unparse(_main_function())
    assert "workspace_enforcer = start_workspace_enforcer(fs.safety" in source
    assert "WorkspaceEnforcer(" not in source


def test_main_stops_the_enforcer_before_the_shutdown_motor_stop():
    final = [ast.unparse(s) for s in _control_loop_try(_main_function()).finalbody]
    enforcer_stop = next(i for i, s in enumerate(final) if "workspace_enforcer.stop()" in s)
    motor_stop = next(i for i, s in enumerate(final) if "driver.stop()" in s)
    assert enforcer_stop < motor_stop


def test_main_commands_the_motors_under_the_motor_lock():
    loop = _control_loop_try(_main_function())
    locked = [
        n
        for n in ast.walk(loop)
        if isinstance(n, ast.With)
        and ast.unparse(n.items[0].context_expr) == "fs.safety.motor_lock"
    ]
    assert len(locked) == 1
    body = "\n".join(ast.unparse(s) for s in locked[0].body)
    assert "fs.write('/dev/motor', action_to_execute" in body
    assert "driver.move(linear, angular)" in body
    # The action as the brain sent it is only ever written, never handed to the driver.
    assert body.count("action_to_execute") == 1


def test_main_refuses_to_boot_on_an_invalid_workspace_block():
    handlers = [
        h
        for n in ast.walk(_main_function())
        if isinstance(n, ast.Try)
        for h in n.handlers
        if h.type is not None and ast.unparse(h.type) == "WorkspaceConfigError"
    ]
    assert len(handlers) == 1
    assert any(
        isinstance(s, ast.Raise) and "SystemExit" in ast.unparse(s) for s in handlers[0].body
    )
