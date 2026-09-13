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
        # them is the signed invoke.
        assert '_invoke("drive.stop", "HALT", {})' in text
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


def test_stop_retries_then_reports_stop_not_confirmed(tmp_path):
    """A stop over a hop that can fail must not be reported as one that landed.

    Exercised against the rendered rc-car source: ``_stop_at_actuator`` is
    lifted out of the template and run with a stub ``_invoke`` that never
    confirms, which is exactly what an unreachable gateway looks like.
    """
    text = render("runtime.py.tmpl", _plan(tmp_path, "rc-car"))
    tree = ast.parse(text)
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_stop_at_actuator"
    )
    calls = {"n": 0}

    def _invoke(tool, scope, args=None, timeout=3.0):
        calls["n"] += 1
        return 0, {"error": "ConnectionRefusedError"}

    ns = {
        "_invoke": _invoke,
        "STOP_ATTEMPTS": 3,
        "logger": _QuietLogger(),
        "time": time,
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<tmpl>", "exec"), ns)
    ok, detail = ns["_stop_at_actuator"]("test")
    assert ok is False
    assert calls["n"] == 3, "one attempt is not a retry"
    assert "ConnectionRefusedError" in detail["reason"]


class _QuietLogger:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def test_safety_layer_alone_still_works_with_no_robot_home(monkeypatch):
    """The library path (no `castor up`, no ROBOT_HOME) is unchanged."""
    monkeypatch.delenv("ROBOT_HOME", raising=False)
    from castor.fs.namespace import Namespace as _Ns
    from castor.fs.permissions import PermissionTable

    sl = SafetyLayer(_Ns(), PermissionTable())
    assert sl.is_estopped is False
    assert sl.estop_source == ""
    assert sl.resync_from_latch() is False
