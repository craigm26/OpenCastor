"""The stop the SHIPPED robot runs, not the one castor/main.py runs.

WHAT WAS WRONG. `castor up` renders a systemd unit whose ExecStart is
``{python} {home}/runtime.py``, and that file serves ``castor.api:app``. So
everything in castor/main.py — SensorMonitor's automatic e-stop after three
consecutive critical readings, ``wire_safety_layer``, BrainWatchdog — sat on a
code path the shipped robot never started. The safety monitor existed and was
never constructed at either place the server is built. The only periodic work in
the generated rc-car runtime lived inside the /ws/telemetry handler, so it ran
only while a phone was connected. And the e-stop itself was a bool on an object:
a robot that stopped and was restarted by systemd twelve seconds later came back
free, because nothing on disk remembered.

These tests pin the four things that had to become true, and they pin them
against the GENERATED TEMPLATE rather than a hand-edited robot file, because a
robot-side fix that lives in a hand-edited file is not a fix for the next robot.

NOT TESTED HERE, and deliberately: that a critical sensor reading on the rover
produces a signed drive.stop receipt in the gateway audit. That needs the rover
and a gateway. The simulated-drive equivalent is
``test_hold_refuses_every_scope_but_halt_and_observe``, which proves the runtime
refuses to send motion while latched, and
``test_stop_retries_then_reports_stop_not_confirmed``, which proves an
unreachable actuator is reported rather than assumed.

Everything here describes a best-effort SOFTWARE hold. Nothing in it cuts power
and nothing in it is safety rated.
"""

from __future__ import annotations

import ast
import collections
import contextlib
import json
import time
from argparse import Namespace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from castor.fs import CastorFS
from castor.fs.safety import SafetyLayer
from castor.up import UpPlan, ensure_estop_auth, render

RUNTIME_TOKEN = "oc_api_runtime_bearer"
ADMIN_TOKEN = "oc_admin_the_humans_bearer"
_ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _plan(tmp_path: Path, archetype: str) -> UpPlan:
    return UpPlan(
        name="rover",
        home=tmp_path / "robot",
        archetype=archetype,
        rrn="RRN-LOCAL-0123456789",
        robot_uuid="4a1f0000-0000-0000-0000-000000000000",
        base_port=8200,
    )


# ---------------------------------------------------------------------------
# 1. The generated runtime constructs what the shipped robot never constructed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("archetype", ["rc-car", "microduck"])
def test_generated_runtime_constructs_the_sensor_monitor(tmp_path, archetype):
    """Both archetypes, rendered through the generator the CLI actually uses.

    Deliberately NOT `castor up --dry-run`: the up subparser defines --home,
    --name, --archetype, --host, --user, --base-port, --python, --no-start,
    --real-wheels, --simulated-wheels and --no-link, and no --dry-run.
    ``castor.up.render`` is what the generator exposes.
    """
    text = render("runtime.py.tmpl", _plan(tmp_path, archetype))

    # It has to be valid Python before it has to be anything else: this file is
    # written to disk and handed straight to a systemd ExecStart.
    ast.parse(text)

    for name in ("SensorMonitor", "wire_safety_layer", "BrainWatchdog"):
        assert name in text, f"{archetype} runtime never names {name}"

    # A FastAPI startup background task, wrapping the lifespan rather than
    # registering an @app.on_event("startup") that Starlette would ignore.
    assert "lifespan_context" in text
    assert "_lifespan_with_safety_guard" in text
    assert "asyncio.create_task(_hold_loop(watchdog))" in text

    # And the hold is honest about what it is.
    assert "not a hardware cut" in text
    assert "stop_not_confirmed" in text


@pytest.mark.parametrize("archetype", ["rc-car", "microduck"])
def test_generated_runtime_latches_and_reasserts_at_the_actuator(tmp_path, archetype):
    """A latch that never reaches the actuator is a flag, not a stop."""
    text = render("runtime.py.tmpl", _plan(tmp_path, archetype))
    assert "_stop_at_actuator" in text
    assert "STOP_ATTEMPTS" in text, "a stop over a hop that can fail must retry"
    assert "_hold()" in text
    if archetype == "rc-car":
        # The wheels are in the gateway process; the only thing that can stop
        # them is the signed invoke. WHICH TOOL it invokes is the robot's own
        # declared stop capability rather than a constant, so the same runtime
        # in front of an arm asks for arm.estop.
        assert '_invoke(tool, "HALT", {})' in text
        assert "_declared_stop_tools" in text
        assert 'FALLBACK_STOP_TOOLS = ("drive.stop", "arm.estop")' in text
    else:
        # The duck's driver is real.
        assert "driver.stop()" in text


@pytest.mark.parametrize("archetype", ["rc-car", "microduck"])
def test_the_watchdog_is_constructed_but_not_armed_by_default(tmp_path, archetype):
    """The ten-minute regression this item could most easily have caused.

    BrainWatchdog stops the robot when no brain response has arrived inside
    ``timeout_s`` (default 10), and its heartbeat is called from
    castor/main.py's perception loop, which the generated runtime deliberately
    does not run. Armed by default it would stop every freshly generated robot
    ten seconds after boot and latch a sensor e-stop: a ten-minute failure
    manufactured by the safety feature. `castor up` writes no top-level
    ``watchdog:`` key, so the generated robot comes up unarmed.
    """
    text = render("runtime.py.tmpl", _plan(tmp_path, archetype))
    assert 'get("watchdog", {}).get("enabled") is not True' in text
    assert "watchdog.enabled = False" in text
    assert "NOT armed" in text

    generated_config = render("robot.rcan.yaml.tmpl", _plan(tmp_path, archetype))
    import yaml

    parsed = yaml.safe_load(generated_config) or {}
    assert parsed.get("watchdog", {}).get("enabled") is not True, (
        "castor up must not generate a robot whose watchdog arms with nothing "
        "feeding it heartbeats"
    )


def test_hold_refuses_every_scope_but_halt_and_observe(tmp_path):
    """The rendered rc-car runtime checks the hold BEFORE it actuates.

    Run against the rendered source rather than an import, because importing it
    needs a gateway, a config and two tokens. The gate is a guard clause at the
    top of ``_invoke``, which is the single outbound path in that file.
    """
    text = render("runtime.py.tmpl", _plan(tmp_path, "rc-car"))
    tree = ast.parse(text)
    invoke = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_invoke"
    )
    # First statement after the docstring must be the hold check, not the
    # envelope: a check further down is a check something can be added above.
    body = [n for n in invoke.body if not isinstance(n, ast.Expr)]
    first = body[0]
    assert isinstance(first, ast.Assign)
    assert ast.unparse(first) == "held = _hold()"
    assert "HELD_SCOPES_ALLOWED" in ast.unparse(body[1])


# ---------------------------------------------------------------------------
# 2. An in-process clear with no principal was a capability-free clear
# ---------------------------------------------------------------------------
def test_in_process_clear_estop_without_principal_raises():
    """``fs.clear_estop()`` used to default to principal='root'.

    root bypasses the capability gate, so any code inside the runtime could
    resume motion a person halted by calling a method with no arguments.
    """
    fs = CastorFS()
    fs.boot({})
    with pytest.raises(ValueError, match="explicit principal"):
        fs.clear_estop(None)
    with pytest.raises(ValueError, match="explicit principal"):
        fs.clear_estop("")
    with pytest.raises(ValueError, match="explicit principal"):
        fs.estop("")
    # And the argument is positional-or-keyword, so the old call sites that
    # named it keep working.
    assert fs.estop(principal="api", source="api") is True
    assert fs.clear_estop(principal="root") is True


# ---------------------------------------------------------------------------
# 3. A stop a sensor set is not clearable from somewhere that cannot see it
# ---------------------------------------------------------------------------
def test_clear_refused_when_source_is_sensor(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCASTOR_ESTOP_AUTH", raising=False)

    fs = CastorFS()
    fs.boot({})
    assert fs.estop(principal="root", source="sensor", reason="cpu_temp=95C") is True
    assert fs.estop_source == "sensor"

    # A remote resume is refused, and the denial names the latch's source.
    assert fs.clear_estop(principal="root", source="rcan") is False
    assert fs.is_estopped is True
    rows = fs.safety.ns.read("/var/log/safety") or []
    denials = [r for r in rows if r.get("event") == "deny_clear_estop"]
    assert denials, "a refused clear must leave an audit row"
    assert "sensor" in denials[-1].get("detail", "")

    # An API clear is refused for the same reason: it cannot see the robot.
    assert fs.clear_estop(principal="api", source="api") is False

    # Somebody standing at the robot can lift it.
    assert fs.clear_estop(principal="root", source="local") is True
    assert fs.is_estopped is False


def test_clear_still_succeeds_when_the_latch_source_is_api(tmp_path, monkeypatch):
    """The sensor rule must not turn into "nothing can ever be cleared"."""
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCASTOR_ESTOP_AUTH", raising=False)

    fs = CastorFS()
    fs.boot({})
    assert fs.estop(principal="api", source="api", reason="operator pressed STOP") is True
    assert fs.clear_estop(principal="api", source="api") is True
    assert fs.is_estopped is False
    assert fs.estop_source == ""


# ---------------------------------------------------------------------------
# 4. The latch survives a restart
# ---------------------------------------------------------------------------
def test_estop_state_survives_restart(tmp_path, monkeypatch):
    """A restart used to be a way to clear a stop nobody had cleared."""
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))

    first = CastorFS()
    first.boot({})
    assert first.estop(principal="root", source="sensor", reason="disk=99%") is True

    latch_file = tmp_path / "safety-latch.json"
    assert latch_file.exists(), "the stop must be on disk, not only in this process"
    assert json.loads(latch_file.read_text())["estop"]["engaged"] is True
    assert latch_file.stat().st_mode & 0o777 == 0o600

    # A whole new process' worth of objects, reading only what is on disk.
    second = CastorFS()
    second.boot({})
    assert second.is_estopped is True, "the robot came back free"
    assert second.estop_source == "sensor"
    assert second.safety.ns.read("/proc/status") == "estop"
    # ...and it still refuses motor writes, which is the point of remembering.
    assert second.write("/dev/motor", {"type": "move", "linear": 0.5}, principal="api") is False

    # Cleared at the robot, the next process comes back free.
    assert second.clear_estop(principal="root", source="local") is True
    third = CastorFS()
    third.boot({})
    assert third.is_estopped is False


def test_a_latch_cleared_out_of_process_is_adopted_by_a_running_one(tmp_path, monkeypatch):
    """`castor resume` runs in a DIFFERENT process from the runtime."""
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    from castor.safety import latch as latch_mod

    fs = CastorFS()
    fs.boot({})
    fs.estop(principal="api", source="api", reason="stop button")
    assert fs.is_estopped is True

    latch_mod.record_clear(tmp_path)  # stands in for `castor resume --clear-estop`
    assert fs.safety.resync_from_latch() is True
    assert fs.is_estopped is False

    latch_mod.record_estop(principal="operator", source="local", reason="again", home=tmp_path)
    assert fs.safety.resync_from_latch() is True
    assert fs.is_estopped is True
    assert fs.estop_source == "local"


def test_deleting_the_latch_file_does_not_lift_a_hold(tmp_path, monkeypatch):
    """A stale or absent file must never release a stop silently.

    ``latch.load()`` reads a missing file as "nothing held", which is the right
    answer at boot and the wrong one during a resync: allowed to reconcile, a
    plain ``rm`` of the latch would clear a sensor e-stop inside one guard
    cycle, with no auth code and without the sensor rule ever running. A real
    clear always leaves a file behind saying engaged=false.
    """
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    fs = CastorFS()
    fs.boot({})
    fs.estop(principal="root", source="sensor", reason="thermal")
    assert fs.is_estopped is True

    (tmp_path / "safety-latch.json").unlink()
    assert fs.safety.resync_from_latch() is False
    assert fs.is_estopped is True, "rm is not a clear"
    assert fs.estop_source == "sensor"


def test_no_robot_home_means_no_state_file_anywhere(tmp_path, monkeypatch):
    """Importing castor.fs in a test must not start writing state files."""
    monkeypatch.delenv("ROBOT_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    fs = CastorFS()
    fs.boot({})
    fs.estop(principal="api", source="api")
    assert list(tmp_path.iterdir()) == []
    assert fs.is_estopped is True


# ---------------------------------------------------------------------------
# 5. castor pause / castor resume
# ---------------------------------------------------------------------------
def test_pause_resume_cli_reports_principal_and_reason(tmp_path, capsys, monkeypatch):
    """A sticky pause nobody can explain looks exactly like broken hardware."""
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    from castor.cli import cmd_pause, cmd_resume

    cmd_pause(Namespace(reason="battery swap", principal="craig", home=""))
    out = capsys.readouterr().out
    assert "craig" in out and "battery swap" in out
    assert "not a hardware cut" in out

    latch = json.loads((tmp_path / "safety-latch.json").read_text())
    assert latch["pause"]["engaged"] is True
    assert latch["pause"]["principal"] == "craig"
    assert latch["pause"]["reason"] == "battery swap"

    cmd_resume(Namespace(clear_estop=False, auth_code="", home=""))
    out = capsys.readouterr().out
    assert "craig" in out, "resume must say WHO paused"
    assert "battery swap" in out, "resume must say WHY"
    assert time.strftime("%Y") in out, "resume must say WHEN"
    assert json.loads((tmp_path / "safety-latch.json").read_text())["pause"]["engaged"] is False


def test_a_pause_actually_blocks_motion(tmp_path, monkeypatch):
    """A pause that let the next /api/action through would be a note in a file.

    It is NOT an e-stop: `castor resume` lifts it with no auth code, and the
    e-stop flag stays down. But it refuses motor writes the same way.
    """
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    from castor.cli import cmd_pause, cmd_resume

    fs = CastorFS()
    fs.boot({})
    assert fs.write("/dev/motor", {"type": "move", "linear": 0.3}, principal="api") is True

    cmd_pause(Namespace(reason="battery swap", principal="craig", home=""))
    assert fs.safety.resync_from_latch() is True
    assert fs.is_estopped is False, "a pause is not an e-stop"
    assert fs.write("/dev/motor", {"type": "move", "linear": 0.3}, principal="api") is False
    assert "paused" in fs.last_write_denial
    assert "battery swap" in fs.last_write_denial

    # A restart comes back paused, with the reason intact.
    restarted = CastorFS()
    restarted.boot({})
    assert restarted.write("/dev/motor", {"type": "move", "linear": 0.3}, principal="api") is False

    cmd_resume(Namespace(clear_estop=False, auth_code="", home=""))
    assert fs.safety.resync_from_latch() is True
    assert fs.write("/dev/motor", {"type": "move", "linear": 0.3}, principal="api") is True


def test_pause_without_a_reason_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    from castor.cli import cmd_pause

    with pytest.raises(SystemExit):
        cmd_pause(Namespace(reason="  ", principal="", home=""))
    assert not (tmp_path / "safety-latch.json").exists()


def test_resume_clear_estop_needs_the_auth_code(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCASTOR_ESTOP_AUTH", raising=False)
    from castor.cli import cmd_resume
    from castor.safety import latch as latch_mod

    code = ensure_estop_auth(tmp_path)
    assert code.startswith("oc_estop_")
    latch_mod.record_estop(
        principal="monitor", source="sensor", reason="cpu_temp=95C", home=tmp_path
    )

    with pytest.raises(SystemExit):
        cmd_resume(Namespace(clear_estop=True, auth_code="wrong", home=""))
    assert latch_mod.load(tmp_path).estop_engaged is True

    # Without --clear-estop, resume explains the stop rather than lifting it.
    cmd_resume(Namespace(clear_estop=False, auth_code="", home=""))
    out = capsys.readouterr().out
    assert "sensor" in out and "cpu_temp=95C" in out
    assert latch_mod.load(tmp_path).estop_engaged is True

    cmd_resume(Namespace(clear_estop=True, auth_code=code, home=""))
    assert latch_mod.load(tmp_path).estop_engaged is False


def test_pause_and_resume_are_in_the_cli_help(capsys, monkeypatch):
    """`check_from_outside`: anyone can read these off the released CLI.

    The whole external check for this item is that ``castor --help`` names the
    two subcommands, so the check is run against the parser the CLI builds
    rather than against the dispatch dict.
    """
    import sys

    import castor.cli as cli_mod

    monkeypatch.setattr(sys, "argv", ["castor", "--help"])
    with pytest.raises(SystemExit):
        cli_mod.main()
    text = capsys.readouterr().out
    assert "pause" in text
    assert "resume" in text

    monkeypatch.setattr(sys, "argv", ["castor", "pause", "--help"])
    with pytest.raises(SystemExit):
        cli_mod.main()
    pause_help = capsys.readouterr().out
    assert "--reason" in pause_help, "the reason is what makes a pause explainable"

    monkeypatch.setattr(sys, "argv", ["castor", "resume", "--help"])
    with pytest.raises(SystemExit):
        cli_mod.main()
    resume_help = capsys.readouterr().out
    assert "--clear-estop" in resume_help


# ---------------------------------------------------------------------------
# 6. `castor up` writes the auth code the clear path has always read
# ---------------------------------------------------------------------------
def test_up_writes_the_estop_auth_code_into_tokens_env(tmp_path):
    """fs/safety.py has read OPENCASTOR_ESTOP_AUTH since it was written.

    Nothing ever set it, so the second factor was a branch that could not fire.
    """
    home = tmp_path / "robot"
    home.mkdir()
    (home / "tokens.env").write_text("ACTUATE_TOKEN=a\nREAD_TOKEN=r\n")

    code = ensure_estop_auth(home)
    body = (home / "tokens.env").read_text()
    assert f"OPENCASTOR_ESTOP_AUTH={code}\n" in body
    assert "ACTUATE_TOKEN=a" in body, "the file's existing lines must survive"
    assert (home / "tokens.env").stat().st_mode & 0o777 == 0o600

    # Reused, never rotated: an operator who wrote it down keeps it.
    assert ensure_estop_auth(home) == code
    assert body.count("OPENCASTOR_ESTOP_AUTH") == 1


def test_safety_layer_requires_the_code_once_up_has_written_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    code = ensure_estop_auth(tmp_path)
    monkeypatch.setenv("OPENCASTOR_ESTOP_AUTH", code)

    fs = CastorFS()
    fs.boot({})
    fs.estop(principal="api", source="api")
    assert fs.clear_estop(principal="root", source="local") is False
    assert fs.clear_estop(principal="root", auth_code="nope", source="local") is False
    assert fs.clear_estop(principal="root", auth_code=code, source="local") is True


# ---------------------------------------------------------------------------
# 7. The endpoint, on a robot generated after this change
# ---------------------------------------------------------------------------
@pytest.fixture()
def _api_client(monkeypatch, tmp_path):
    import castor.api as api_mod

    for var in ("OPENCASTOR_USERS", "OPENCASTOR_JWT_SECRET", "JWT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.setenv("OPENCASTOR_API_TOKEN", RUNTIME_TOKEN)
    api_mod.API_TOKEN = RUNTIME_TOKEN
    api_mod.ADMIN_TOKEN = ADMIN_TOKEN
    api_mod.ADMIN_TOKEN_SHA256 = None
    api_mod.state.config = None
    api_mod.state.driver = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.state.boot_time = time.time()

    app = api_mod.app
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    app.router.lifespan_context = _noop_lifespan
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, api_mod
    finally:
        app.router.lifespan_context = original_lifespan
        api_mod.API_TOKEN = None
        api_mod.ADMIN_TOKEN = None
        api_mod.state.fs = None


def test_estop_clear_without_the_auth_code_is_403(_api_client, tmp_path, monkeypatch):
    """On a robot generated after this change, the admin bearer is not enough."""
    client, api_mod = _api_client
    code = ensure_estop_auth(tmp_path)
    monkeypatch.setenv("OPENCASTOR_ESTOP_AUTH", code)

    fs = CastorFS()
    fs.boot({})
    api_mod.state.fs = fs
    fs.estop(principal="api", source="api", reason="stop button")

    denied = client.post("/api/estop/clear", headers=_ADMIN)
    assert denied.status_code == 403
    assert "auth code" in denied.json()["error"]
    assert fs.is_estopped is True

    allowed = client.post(
        "/api/estop/clear", headers={**_ADMIN, "X-Estop-Auth": code}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "cleared"
    assert fs.is_estopped is False


def test_estop_clear_of_a_sensor_latch_is_403_over_the_network(_api_client, tmp_path, monkeypatch):
    client, api_mod = _api_client
    code = ensure_estop_auth(tmp_path)
    monkeypatch.setenv("OPENCASTOR_ESTOP_AUTH", code)

    fs = CastorFS()
    fs.boot({})
    api_mod.state.fs = fs
    fs.estop(principal="root", source="sensor", reason="cpu_temp=95C")

    resp = client.post("/api/estop/clear", headers={**_ADMIN, "X-Estop-Auth": code})
    assert resp.status_code == 403
    assert "castor resume --clear-estop" in resp.json()["error"]
    assert fs.is_estopped is True


def test_fs_estop_status_says_what_kind_of_hold_this_is(_api_client, tmp_path):
    """Software never renders this "verified" or "safe": it says what it is."""
    client, api_mod = _api_client
    fs = CastorFS()
    fs.boot({})
    api_mod.state.fs = fs
    fs.estop(principal="api", source="api", reason="stop button")

    body = client.get("/api/fs/estop", headers=_ADMIN).json()
    assert body["estopped"] is True
    assert body["source"] == "api"
    assert body["hold_kind"] == "best_effort_software_hold"
    assert body["latch"]["estop_reason"] == "stop button"


# ---------------------------------------------------------------------------
# 8. The monitor's auto-stop latches as 'sensor', against the simulated drive
# ---------------------------------------------------------------------------
def test_sensor_auto_estop_latches_with_source_sensor(tmp_path, monkeypatch):
    """Three consecutive critical readings, simulated.

    The rover equivalent (a signed drive.stop receipt in the gateway audit that
    validates under scripts/verify_receipt.py) needs the rover and is not run
    here. This is the same callback, wired the same way, against the in-process
    safety layer.
    """
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    from castor.safety.monitor import MonitorSnapshot, SensorMonitor, wire_safety_layer

    fs = CastorFS()
    fs.boot({})
    monitor = SensorMonitor(consecutive_critical=3)
    wire_safety_layer(monitor, fs.safety)

    snap = MonitorSnapshot(timestamp=1.0, overall_status="critical")
    for cb in monitor._critical_callbacks:
        cb(snap)
    monitor._estop_callback()

    assert fs.is_estopped is True
    assert fs.estop_source == "sensor", "the source is what makes it unclearable remotely"
    assert json.loads((tmp_path / "safety-latch.json").read_text())["estop"]["source"] == "sensor"
    # And the runtime now refuses its own motor writes.
    assert fs.write("/dev/motor", {"type": "move", "linear": 0.4}, principal="api") is False


def _stop_namespace(tmp_path, _invoke, capabilities=None):
    """Lift the rc-car template's stop path out of the rendered source and run it.

    The template is not importable (it needs ROBOT_HOME, a gateway URL and a
    read token in the environment before its first import), so the two
    functions that decide WHICH tool is asked and WHETHER it answered are
    compiled on their own against a stub ``_invoke`` and a stub ``state``. That
    is the honest way to test generated code: the thing under test is the text
    `castor up` writes to disk.
    """
    text = render("runtime.py.tmpl", _plan(tmp_path, "rc-car"))
    tree = ast.parse(text)
    wanted = {"_stop_at_actuator", "_declared_stop_tools"}
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {f.name for f in fns} == wanted, "the template lost a stop helper"
    config = {}
    if capabilities is not None:
        config = {"rcan_protocol": {"capabilities": list(capabilities)}}
    ns = {
        "_invoke": _invoke,
        "STOP_ATTEMPTS": 3,
        "FALLBACK_STOP_TOOLS": ("drive.stop", "arm.estop"),
        "_stop_tool_that_answered": None,
        "state": Namespace(config=config),
        "logger": _QuietLogger(),
        "time": time,
    }
    exec(compile(ast.Module(body=fns, type_ignores=[]), "<tmpl>", "exec"), ns)
    return ns


def test_stop_retries_then_reports_stop_not_confirmed(tmp_path):
    """A stop over a hop that can fail must not be reported as one that landed.

    Exercised against the rendered rc-car source with a stub ``_invoke`` that
    never confirms, which is exactly what an unreachable gateway looks like.
    """
    calls = {"n": 0}

    def _invoke(tool, scope, args=None, timeout=3.0):
        calls["n"] += 1
        return 0, {"error": "ConnectionRefusedError"}

    ns = _stop_namespace(tmp_path, _invoke, capabilities=["drive.set", "drive.stop"])
    ok, detail = ns["_stop_at_actuator"]("test")
    assert ok is False
    assert calls["n"] == 3, "one attempt is not a retry"
    assert "ConnectionRefusedError" in detail["reason"]


def test_the_stop_asks_for_the_declared_stop_capability(tmp_path):
    """An arm is stopped with arm.estop, a drive with drive.stop.

    THE HALF THIS FIXES. `_stop_at_actuator` used to be hard-wired to
    drive.stop, so the same generated runtime in front of an SO-ARM101 asked
    for a tool that robot does not have, got a refusal, and reported
    stop_not_confirmed forever while the arm held torque. The tool comes from
    the robot's own declared capabilities now.
    """
    asked = []

    def _invoke(tool, scope, args=None, timeout=3.0):
        asked.append(tool)
        return 200, {"receipt": "signed"}

    arm = _stop_namespace(tmp_path, _invoke, capabilities=["arm.move", "arm.estop"])
    ok, detail = arm["_stop_at_actuator"]("arm")
    assert ok is True
    assert asked == ["arm.estop"], "an arm must not be asked for drive.stop"
    assert detail["stop_tool"] == "arm.estop", "the tool that answered is recorded"

    asked.clear()
    drive = _stop_namespace(tmp_path, _invoke, capabilities=["drive.set", "drive.stop"])
    ok, detail = drive["_stop_at_actuator"]("drive")
    assert ok is True
    assert asked == ["drive.stop"]
    assert detail["stop_tool"] == "drive.stop"


def test_an_undeclared_stop_tries_both_and_records_which_answered(tmp_path):
    """No declared stop capability is the only case where this process guesses."""
    asked = []

    def _invoke(tool, scope, args=None, timeout=3.0):
        asked.append(tool)
        # This robot is an arm whose config forgot to declare its stop.
        if tool == "arm.estop":
            return 200, {"receipt": "signed"}
        return 404, {"detail": "unknown tool"}

    ns = _stop_namespace(tmp_path, _invoke, capabilities=[])
    ok, detail = ns["_stop_at_actuator"]("guess")
    assert ok is True
    assert asked == ["drive.stop", "arm.estop"]
    assert detail["stop_tool"] == "arm.estop"
    assert ns["_stop_tool_that_answered"] == "arm.estop", "ask the one that works first"


def test_neither_tool_answering_is_still_stop_not_confirmed(tmp_path):
    """Trying two tools instead of one must not turn a failure into a success."""

    def _invoke(tool, scope, args=None, timeout=3.0):
        return 403, {"detail": "not allowlisted"}

    ns = _stop_namespace(tmp_path, _invoke, capabilities=[])
    ok, detail = ns["_stop_at_actuator"]("nothing answers")
    assert ok is False
    assert "not allowlisted" in detail["reason"]
    assert detail["stop_tool"] in ("drive.stop", "arm.estop")


class _QuietLogger:
    def __getattr__(self, _name):
        return lambda *a, **k: None


# ---------------------------------------------------------------------------
# The sensor thresholds are generated, not the library's
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("archetype", ["rc-car", "microduck"])
def test_the_generated_runtime_reads_the_monitor_block(tmp_path, archetype):
    """SensorMonitor is configured from the config, the way castor/main.py does.

    Constructed with the LIBRARY defaults it was the biggest regression risk in
    this item: three consecutive criticals latch a source='sensor' e-stop that
    nothing on the network can clear, and the library calls 80 C critical,
    which a fanless Pi under a benchmark reaches while working perfectly.
    """
    text = render("runtime.py.tmpl", _plan(tmp_path, archetype))
    assert "MonitorThresholds" in text
    assert '(state.config or {}).get("monitor", {})' in text
    assert "consecutive_critical=consecutive" in text
    assert "SensorMonitor()" not in text, "the library defaults must not be the shipped ones"


@pytest.mark.parametrize("archetype", ["rc-car", "microduck"])
def test_castor_up_writes_conservative_monitor_defaults(tmp_path, archetype):
    """A benchmark must not be able to latch a stop nobody can clear remotely."""
    import yaml

    cfg = yaml.safe_load(render("robot.rcan.yaml.tmpl", _plan(tmp_path, archetype)))
    thresholds = cfg["monitor"]["thresholds"]

    # CPU LOAD: critical is load_warn_multiplier x 2 x CPU count. At the
    # library's 2.0 that is 4x the core count, which a parallel benchmark on a
    # four-core Pi can reach. The generated value puts it out of reach.
    assert thresholds["load_warn_multiplier"] >= 8.0, "a benchmark's load must not stop the robot"

    # CPU TEMP is an honest trigger, above the Pi's own hard throttle (85 C) so
    # that a merely hot board is not a stopped one.
    assert thresholds["cpu_temp_critical"] > 85.0

    # DISK is the other honest one: a full disk breaks the audit log first.
    assert 95.0 <= thresholds["disk_critical"] <= 98.0

    # MEMORY at the library's 95 is reachable by a benchmark; the OOM killer is
    # already acting by the generated value.
    assert thresholds["memory_critical"] >= 99.0

    # And the block the runtime actually reads is complete enough to build
    # MonitorThresholds from, with no key the dataclass does not have.
    from castor.safety.monitor import MonitorThresholds

    MonitorThresholds(**{k: float(v) for k, v in thresholds.items()})

    # The watchdog is still NOT armed by the generated config: nothing in the
    # runtime feeds it heartbeats, so a top-level `watchdog:` block would stop
    # every fresh robot ten seconds after boot.
    assert cfg.get("watchdog") is None


def test_safety_layer_alone_still_works_with_no_robot_home(monkeypatch):
    """The library path (no `castor up`, no ROBOT_HOME) is unchanged."""
    monkeypatch.delenv("ROBOT_HOME", raising=False)
    from castor.fs.namespace import Namespace as _Ns
    from castor.fs.permissions import PermissionTable

    sl = SafetyLayer(_Ns(), PermissionTable())
    assert sl.is_estopped is False
    assert sl.estop_source == ""
    assert sl.resync_from_latch() is False


# ---------------------------------------------------------------------------
# 9. The STOCK gateway reconciles the latch too, not just the generated runtime
# ---------------------------------------------------------------------------
# WHAT WAS STILL WRONG AFTER THE FIRST PASS. `SafetyLayer.resync_from_latch`
# existed and only the GENERATED runtime templates called it. A robot whose
# actuator lives behind the gateway runs the stock `castor gateway` app, and
# that app never called it, so `castor pause --reason ...` typed at a shell
# wrote the latch file and then waited for a restart. From outside, a pause
# that takes effect at the next restart and a pause that does nothing look
# exactly the same.
@pytest.fixture()
def _live_gateway(monkeypatch, tmp_path):
    """The stock app under its REAL lifespan, with the heavy startup skipped.

    The point of this fixture is the lifespan wiring itself, so it must not be
    replaced with a no-op the way `_api_client` replaces it. `on_startup` opens
    cameras, channels and mDNS, none of which this is about, so those two
    coroutines are stubbed and everything the lifespan itself does is real.
    """
    import castor.api as api_mod

    for var in ("OPENCASTOR_USERS", "OPENCASTOR_JWT_SECRET", "JWT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.setenv("OPENCASTOR_API_TOKEN", RUNTIME_TOKEN)
    monkeypatch.setattr(api_mod, "API_TOKEN", RUNTIME_TOKEN)
    monkeypatch.setattr(api_mod, "ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setattr(api_mod, "ADMIN_TOKEN_SHA256", None)
    # A cadence a test can wait out. The shipped value is SAFETY_LATCH_RESYNC_S,
    # which is the generated runtime's HOLD_REASSERT_S, which is 5 seconds.
    monkeypatch.setattr(api_mod, "SAFETY_LATCH_RESYNC_S", 0.05)

    fs = CastorFS()
    fs.boot({})

    async def _no_startup():
        api_mod.state.thought_history = collections.deque(maxlen=50)
        api_mod.state.boot_time = time.time()
        api_mod.state.fs = fs

    async def _no_shutdown():
        api_mod.state.fs = None

    monkeypatch.setattr(api_mod, "on_startup", _no_startup)
    monkeypatch.setattr(api_mod, "on_shutdown", _no_shutdown)

    with TestClient(api_mod.app, raise_server_exceptions=False) as client:
        yield client, api_mod, fs


def _hold_within_a_cycle(client, want: bool, timeout: float = 5.0) -> dict:
    """Poll /api/fs/estop until `held` is what we want, or give up loudly."""
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        body = client.get("/api/fs/estop", headers=_ADMIN).json()
        if body.get("held") is want:
            return body
        time.sleep(0.02)
    raise AssertionError(f"held never became {want}; last body was {body}")


def test_the_stock_gateway_notices_a_pause_written_out_of_process(_live_gateway, tmp_path):
    """`castor pause` from a shell reaches a RUNNING stock server, not the next one."""
    client, _api_mod, fs = _live_gateway
    from castor.safety import latch as _latch

    assert client.get("/api/fs/estop", headers=_ADMIN).json()["held"] is False

    _latch.record_pause(principal="craig", reason="battery swap", home=tmp_path)
    body = _hold_within_a_cycle(client, True)

    assert body["paused"] is True
    assert body["estopped"] is False, "a pause is not an e-stop"
    assert "battery swap" in body["hold_detail"]
    assert body["hold_kind"] == "best_effort_software_hold"
    # And the hold is real: the runtime refuses its own motor writes.
    assert fs.write("/dev/motor", {"type": "move", "linear": 0.3}, principal="api") is False

    _latch.record_resume(home=tmp_path)
    lifted = _hold_within_a_cycle(client, False)
    assert lifted["paused"] is False
    assert fs.write("/dev/motor", {"type": "move", "linear": 0.3}, principal="api") is not False


def test_the_stock_gateway_adopts_an_estop_written_out_of_process(_live_gateway, tmp_path):
    """The same reconcile, for the latch that matters most."""
    client, _api_mod, _fs = _live_gateway
    from castor.safety import latch as _latch

    _latch.record_estop(principal="craig", source="local", reason="smoke", home=tmp_path)
    body = _hold_within_a_cycle(client, True)
    assert body["estopped"] is True
    assert body["source"] == "local"

    _latch.record_clear(home=tmp_path)
    lifted = _hold_within_a_cycle(client, False)
    assert lifted["estopped"] is False


def test_a_deleted_latch_file_does_not_lift_the_stock_gateway_hold(_live_gateway, tmp_path):
    """`rm safety-latch.json` is not a clear, and the reconcile loop is not a way in.

    A clear always LEAVES a file behind (`record_clear` writes engaged=false),
    so the absence of one is never evidence that anybody cleared anything. The
    rule lives in SafetyLayer.resync_from_latch; this pins that running the
    reconcile on a timer did not create a way around it.
    """
    client, _api_mod, fs = _live_gateway
    from castor.safety import latch as _latch

    _latch.record_estop(principal="craig", source="sensor", reason="cpu_temp=95C", home=tmp_path)
    _hold_within_a_cycle(client, True)

    (tmp_path / "safety-latch.json").unlink()
    time.sleep(0.3)  # several reconcile cycles at the fixture's cadence

    body = client.get("/api/fs/estop", headers=_ADMIN).json()
    assert body["held"] is True, "deleting the latch file must not clear a stop"
    assert body["estopped"] is True
    assert body["source"] == "sensor"
    assert fs.is_estopped is True


def test_the_reconcile_reads_the_latch_on_the_event_loop_and_not_in_a_thread(
    _live_gateway, tmp_path
):
    """A worker thread here loses stops, so pin that there is not one.

    Every other mutator of the in-memory hold in this process is an ``async
    def`` handler, so nothing can interleave with a reconcile that does not
    await. Hand the reconcile to ``asyncio.to_thread`` and it can be
    descheduled between reading the latch file and comparing the result with
    ``self._estop``: a ``POST /api/stop`` that lands in that window looks, to
    the resumed thread, exactly like an out-of-process CLEAR, so the stop is
    reverted, /proc/status goes back to 'active' and a false clear_estop row is
    audited. The file re-adopts it a cadence later, which means the robot is
    unheld for up to one cadence immediately after somebody hit the stop.

    The check: every latch read the SERVER does must find a running event loop
    under it. A read from a worker thread would not. Reads from this test's own
    thread are not the server and are excluded by ident.
    """
    import asyncio as _asyncio
    import threading as _threading

    _client, _api_mod, _fs = _live_gateway
    from castor.safety import latch as _latch

    mine = _threading.get_ident()
    server_reads: list[bool] = []
    real_load = _latch.load

    def _watching_load(*args, **kwargs):
        if _threading.get_ident() != mine:
            try:
                _asyncio.get_running_loop()
                server_reads.append(True)
            except RuntimeError:
                server_reads.append(False)
        return real_load(*args, **kwargs)

    _latch.load = _watching_load
    try:
        _latch.record_pause(principal="craig", reason="thread check", home=tmp_path)
        _hold_within_a_cycle(_client, True)
    finally:
        _latch.load = real_load

    assert server_reads, "the server never read the latch file"
    assert all(server_reads), (
        "the reconcile read the latch file off the event loop, which is where a "
        "stop arriving mid-reconcile gets reverted"
    )


def test_the_resync_helper_is_inert_without_a_filesystem(monkeypatch):
    """No fs, no latch, no crash: the loop must survive a gateway with no robot."""
    import castor.api as api_mod

    monkeypatch.setattr(api_mod.state, "fs", None, raising=False)
    assert api_mod._resync_safety_latch() is False


# ---------------------------------------------------------------------------
# 10. `castor resume --clear-estop` insists on the code, or says it did not
# ---------------------------------------------------------------------------
# WHAT WAS STILL WRONG. The clear path read `if required and supplied !=
# required`, so a robot with NO code cleared with NO code, silently. That is
# the ordinary state of a gateway-only robot: the actuator lives behind the
# gateway, `castor up` may never have run on this host, and nothing ever
# provisioned OPENCASTOR_ESTOP_AUTH. The robots with no second factor were the
# ones that asked for nothing, which is exactly backwards. A missing secret is
# a missing secret; it is not consent.
def _latched(tmp_path):
    from castor.safety import latch as latch_mod

    latch_mod.record_estop(
        principal="monitor", source="local", reason="bumped the table", home=tmp_path
    )
    return latch_mod


def test_clear_estop_path_1_code_provisioned_and_supplied(tmp_path, capsys, monkeypatch):
    """The happy path: the code exists, it is passed, the stop lifts."""
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCASTOR_ESTOP_AUTH", raising=False)
    from castor.cli import cmd_resume

    latch_mod = _latched(tmp_path)
    code = ensure_estop_auth(tmp_path)

    cmd_resume(
        Namespace(clear_estop=True, auth_code=code, no_auth_code=False, home="")
    )
    out = capsys.readouterr().out
    assert latch_mod.load(tmp_path).estop_engaged is False
    assert "UNAUTHENTICATED" not in out, "a checked clear must not claim it was unchecked"


def test_clear_estop_path_2_code_provisioned_and_not_supplied(tmp_path, capsys, monkeypatch):
    """The code exists and none was passed: refuse, and name the variable."""
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCASTOR_ESTOP_AUTH", raising=False)
    from castor.cli import cmd_resume

    latch_mod = _latched(tmp_path)
    ensure_estop_auth(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cmd_resume(Namespace(clear_estop=True, auth_code="", no_auth_code=False, home=""))
    assert exc.value.code == 3
    out = capsys.readouterr().out
    assert "OPENCASTOR_ESTOP_AUTH" in out
    assert "tokens.env" in out
    assert latch_mod.load(tmp_path).estop_engaged is True

    # And --no-auth-code is not a way past a code this robot actually has.
    with pytest.raises(SystemExit) as exc2:
        cmd_resume(Namespace(clear_estop=True, auth_code="", no_auth_code=True, home=""))
    assert exc2.value.code == 3
    assert "ignored" in capsys.readouterr().out
    assert latch_mod.load(tmp_path).estop_engaged is True


def test_clear_estop_path_3_no_code_anywhere_is_refused(tmp_path, capsys, monkeypatch):
    """A gateway-only robot with nothing to check against refuses, and says how.

    This is the path that used to clear silently. The message has to name the
    variable and the one command that provisions it, because an operator who is
    told only "refused" will reach for the latch file with rm.
    """
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCASTOR_ESTOP_AUTH", raising=False)
    from castor.cli import cmd_resume

    latch_mod = _latched(tmp_path)
    assert not (tmp_path / "tokens.env").exists()

    with pytest.raises(SystemExit) as exc:
        cmd_resume(Namespace(clear_estop=True, auth_code="", no_auth_code=False, home=""))
    assert exc.value.code == 3
    out = capsys.readouterr().out
    assert "OPENCASTOR_ESTOP_AUTH" in out, "name the variable"
    assert "castor up" in out, "say how to provision it"
    assert "ensure_estop_auth" in out
    assert "--no-auth-code" in out, "say what the escape hatch is"
    assert latch_mod.load(tmp_path).estop_engaged is True


def test_clear_estop_path_4_no_code_with_the_explicit_flag_warns(tmp_path, capsys, monkeypatch):
    """--no-auth-code clears, and says on the record that nothing checked it."""
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCASTOR_ESTOP_AUTH", raising=False)
    from castor.cli import cmd_resume

    latch_mod = _latched(tmp_path)

    cmd_resume(Namespace(clear_estop=True, auth_code="", no_auth_code=True, home=""))
    out = capsys.readouterr().out
    assert "UNAUTHENTICATED" in out
    assert "OPENCASTOR_ESTOP_AUTH" in out
    assert "castor up" in out
    assert latch_mod.load(tmp_path).estop_engaged is False


def test_the_code_may_come_from_the_environment_instead_of_tokens_env(tmp_path, monkeypatch):
    """"Supplied" means the flag OR the environment, the way the API reads it.

    `castor/fs/safety.py` reads OPENCASTOR_ESTOP_AUTH from the environment at
    clear time, so a robot whose code lives only in the environment is a robot
    WITH a code, and the CLI has to agree or the two surfaces enforce different
    secrets.
    """
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.setenv("OPENCASTOR_ESTOP_AUTH", "oc_estop_from_the_env")
    from castor.cli import _estop_auth_sources, cmd_resume

    required, where = _estop_auth_sources(tmp_path)
    assert required == "oc_estop_from_the_env"
    assert "environment" in where

    latch_mod = _latched(tmp_path)
    cmd_resume(
        Namespace(clear_estop=True, auth_code="", no_auth_code=False, home="")
    )
    assert latch_mod.load(tmp_path).estop_engaged is False


def test_no_auth_code_is_in_the_resume_help(capsys, monkeypatch):
    """Readable from the released CLI, which is this item's outside check."""
    import sys

    import castor.cli as cli_mod

    monkeypatch.setattr(sys, "argv", ["castor", "resume", "--help"])
    with pytest.raises(SystemExit):
        cli_mod.main()
    text = capsys.readouterr().out
    assert "--no-auth-code" in text
    assert "unauthenticated" in text.lower()


# ---------------------------------------------------------------------------
# 11. The bundled /gamepad page can send the e-stop code
# ---------------------------------------------------------------------------
# WHAT WAS STILL WRONG. Once POST /api/estop/clear started demanding
# X-Estop-Auth, the page's one-argument fetch helper had no way to set a
# header, so the gamepad's clear could not succeed on any robot `castor up`
# had provisioned. It answered 403 every time and rendered `d.detail`, which
# the gateway does not send, so the person holding the phone saw "error".
def test_the_gamepad_page_can_send_the_estop_auth_header(_api_client):
    """The page is tested at the level it is served: the HTML it returns."""
    client, _api_mod = _api_client
    html = client.get("/gamepad").text

    assert 'id="estop-code"' in html, "there must be a field to type the code into"
    assert 'id="clear-btn"' in html, "and a clear button beside it"
    assert "X-Estop-Auth" in html, "the header the server reads has to be sent"
    assert "/api/estop/clear" in html

    # The field is never persisted: a phone on a bench must not still be able
    # to lift a stop tomorrow.
    assert "localStorage." not in html, "the code must not outlive the page"
    assert "sessionStorage." not in html

    # The server's own refusal is what gets shown, not the word "error".
    assert "d.error || d.detail" in html

    # Honest copy, and no claim that any of this is a hardware cut.
    assert "best-effort software hold" in html
    assert "not a hardware cut" in html
    for word in ("safety rated", "fail-safe", "verified"):
        assert word not in html.lower(), f"the page must not claim {word!r}"


# ---------------------------------------------------------------------------
# 12. The docs page, and the two words it must not use
# ---------------------------------------------------------------------------
def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_the_hold_doc_exists_and_says_what_the_hold_is():
    """One page an operator can read before the robot is already stopped."""
    doc = _repo_root() / "docs" / "safety" / "hold.md"
    assert doc.exists(), "docs/safety/hold.md is the page every stop surface points at"
    text = doc.read_text(encoding="utf-8")

    # What it is, and what it is not.
    assert "best-effort software hold" in text
    assert "not a hardware cut" in text
    assert "safety rated" in text

    # The mechanics an operator actually needs.
    assert "safety-latch.json" in text
    assert "castor pause" in text and "castor resume" in text
    assert "OPENCASTOR_ESTOP_AUTH" in text
    assert "ensure_estop_auth" in text and "castor up" in text
    assert "source=sensor" in text or "`sensor`" in text

    # The disambiguation. These two are confused constantly and they are not
    # the same lever: one pauses the perception loop, one is the latch.
    assert "/api/runtime/resume" in text
    assert "perception-action loop" in text

    # House style.
    assert "—" not in text, "no em-dashes"
    assert "–" not in text, "no en-dashes either"
    assert "verified" not in text.lower(), "software never renders anything verified"


def test_the_hold_doc_is_linked_from_where_safety_docs_are_indexed():
    root = _repo_root()
    arch = (root / "docs" / "safety-architecture.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "safety/hold.md" in arch, "the safety module map has to point at it"
    assert "docs/safety/hold.md" in readme, "and so does the README's P66 section"


# ---------------------------------------------------------------------------
# 13. The arm branch, against a gateway that actually answers
# ---------------------------------------------------------------------------
# WHY THIS IS NOT ANOTHER STUB. `test_the_stop_asks_for_the_declared_stop_
# capability` proves the tool selection with a stub `_invoke`, which is
# honest as far as it goes and goes no further than this process: it never
# builds an envelope, never opens a socket and never reads a status line. The
# arm is the archetype where the whole hop matters, because the joints are in
# the gateway and `arm.estop` is the only thing that reaches them. So this one
# runs the template's REAL `_invoke` against a real HTTP server standing in for
# the gateway, and asserts what that server saw.
#
# What it still is not: a receipt from Bob's gateway. The signed-receipt check
# needs the arm and stays a manual step. This closes the distance between a
# stub and that.
class _FakeGateway:
    """A gateway that allowlists exactly one stop tool, and records every ask."""

    def __init__(self, answers: str = "arm.estop"):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.seen: list[dict] = []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):  # keep pytest output readable
                pass

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
                length = int(self.headers.get("Content-Length") or 0)
                envelope = json.loads(self.rfile.read(length) or b"{}")
                envelope["_path"] = self.path
                envelope["_authorization"] = self.headers.get("Authorization")
                outer.seen.append(envelope)
                if envelope.get("tool_name") == answers:
                    body = json.dumps(
                        {"ok": True, "receipt": {"signed": True, "tool": answers}}
                    ).encode()
                    self.send_response(200)
                else:
                    # What a real gateway says about a tool this robot has not
                    # declared: a 4xx, which no amount of retrying changes.
                    body = json.dumps({"detail": "tool not allowlisted"}).encode()
                    self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _live_stop_namespace(tmp_path, gateway_url, capabilities):
    """The template's real stop path, wired to a real URL.

    Four functions come out of the rendered source this time, `_invoke` and
    `_hold` included, so the envelope, the bearer, the transport and the status
    handling are the template's own and not a test's idea of them.
    """
    import urllib.error
    import urllib.request
    import uuid

    text = render("runtime.py.tmpl", _plan(tmp_path, "rc-car"))
    tree = ast.parse(text)
    wanted = {"_invoke", "_hold", "_stop_at_actuator", "_declared_stop_tools"}
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {f.name for f in fns} == wanted, "the template lost a stop helper"
    ns = {
        "GATEWAY_URL": gateway_url,
        "READ_TOKEN": "oc_read_bearer",
        "MANIFEST_PATH": str(tmp_path / "ROBOT.md"),
        "HELD_SCOPES_ALLOWED": ("HALT", "OBSERVE"),
        "STOP_ATTEMPTS": 3,
        "FALLBACK_STOP_TOOLS": ("drive.stop", "arm.estop"),
        "_stop_tool_that_answered": None,
        "state": Namespace(config={"rcan_protocol": {"capabilities": list(capabilities)}},
                           fs=None),
        "load_latch": lambda: __import__(
            "castor.safety.latch", fromlist=["LatchState"]
        ).LatchState(),
        "logger": _QuietLogger(),
        "json": json,
        "time": time,
        "uuid": uuid,
        "urllib": urllib,
    }
    exec(compile(ast.Module(body=fns, type_ignores=[]), "<tmpl>", "exec"), ns)
    return ns


def test_an_arm_is_stopped_through_a_gateway_that_advertises_arm_estop(tmp_path):
    """The arm branch, end to end inside this process: envelope, hop, 2xx, name."""
    with _FakeGateway(answers="arm.estop") as gw:
        ns = _live_stop_namespace(
            tmp_path, gw.url, capabilities=["arm.move", "arm.grip", "arm.estop"]
        )

        # Resolution first: an arm's stop is arm.estop, ahead of everything.
        assert ns["_declared_stop_tools"]() == ("arm.estop",), (
            "an arm that declares arm.estop must not be asked for drive.stop"
        )

        ok, detail = ns["_stop_at_actuator"]("critical thermal reading")

    assert ok is True, "a 2xx from the gateway is a confirmed stop"
    assert detail["stop_tool"] == "arm.estop", "the 2xx yields the tool name"
    assert detail["receipt"]["signed"] is True, "the gateway's own body comes back"

    # And what the gateway actually received, which a stub could not show.
    assert len(gw.seen) == 1, "the first ask answered; there is nothing to retry"
    asked = gw.seen[0]
    assert asked["tool_name"] == "arm.estop"
    assert asked["scope"] == "HALT", "a stop is a HALT, which is allowed while held"
    assert asked["_path"] == "/v1/invoke"
    assert asked["_authorization"] == "Bearer oc_read_bearer"
    assert asked["type"] == "rcan/v1/invoke"
    assert asked["nonce"] and asked["msg_id"]


def test_an_arm_whose_config_forgot_arm_estop_still_finds_it_over_the_wire(tmp_path):
    """The fallback, against a gateway that refuses drive.stop for real.

    A 404 is what an undeclared tool looks like from a gateway. The candidate
    is dropped, the next one is asked, and the tool that answered is the one
    reported: `stop_not_confirmed` would be wrong here, and so would claiming
    drive.stop stopped anything.
    """
    with _FakeGateway(answers="arm.estop") as gw:
        ns = _live_stop_namespace(tmp_path, gw.url, capabilities=[])
        ok, detail = ns["_stop_at_actuator"]("no declared stop")

    assert ok is True
    assert detail["stop_tool"] == "arm.estop"
    assert [e["tool_name"] for e in gw.seen] == ["drive.stop", "arm.estop"]
    assert ns["_stop_tool_that_answered"] == "arm.estop", "ask the one that works first"


def test_a_gateway_that_refuses_everything_is_stop_not_confirmed(tmp_path):
    """An arm holding torque behind a gateway that says no is not a stopped arm."""
    with _FakeGateway(answers="nothing.at.all") as gw:
        ns = _live_stop_namespace(tmp_path, gw.url, capabilities=["arm.move", "arm.estop"])
        ok, detail = ns["_stop_at_actuator"]("everything refused")

    assert ok is False, "a refusal must never be reported as a stop that landed"
    assert detail["gateway_status"] == 404
    assert "not allowlisted" in detail["reason"]
    assert len(gw.seen) == 3, "STOP_ATTEMPTS asks, then it says it did not land"
