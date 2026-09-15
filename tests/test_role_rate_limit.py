"""The per-role pacing cap: fails closed, names its reason, and sees the arm.

WHAT WAS WRONG (OC-M-05). Three things, in the one pacing cap that actually
runs on a shipped robot.

1. It failed OPEN. ``SafetyLayer.check_role_rate_limit`` ended in
   ``except Exception: return True  # Graceful fallback``, so any failure
   inside the handler -- an import error in the RBAC module, a renamed role, a
   table that no longer matches -- admitted the request. A cap that admits
   everything the moment it breaks is not a cap.
2. CREATOR was UNLIMITED. ``ROLE_RATE_LIMITS[CREATOR]`` was 0, which the
   handler read as "return True before counting anything". ``root`` maps to
   CREATOR, so the role a runaway loop runs as was the one role the cap could
   not see.
3. The ARM was invisible to it. A wheel command goes through
   ``state.fs.write("/dev/motor", ...)`` and is paced. ``grip`` and
   ``arm_pose`` reach the servos through ``castor.api._execute_action``
   without touching the filesystem layer, so no arm command was ever counted.

These tests pin the flip and, just as importantly, pin what the flip must NOT
do: a stop must always go through, including when the cap itself is broken.

Nothing here is a hardware guarantee. The cap is a software pacing limit on a
best-effort software hold, and no test in this file makes it safety rated.
"""

from __future__ import annotations

import contextlib
import time

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from castor.fs.namespace import Namespace
from castor.fs.permissions import PermissionTable
from castor.fs.safety import SafetyLayer
from castor.rcan import rbac
from castor.rcan.rbac import (
    API_ROLE_RATE_LIMITS,
    RATE_LIMIT_WINDOW_S,
    ROLE_RATE_LIMITS,
    RCANPrincipal,
    RCANRole,
    resolve_role_name,
)


def _safety() -> SafetyLayer:
    return SafetyLayer(Namespace(), PermissionTable(), limits={"motor_rate_hz": 1000.0})


def _safety_log(sl: SafetyLayer) -> list[dict]:
    rows = sl.ns.read("/var/log/safety")
    return rows if isinstance(rows, list) else []


def _reasons(sl: SafetyLayer) -> list[str]:
    return [row.get("event") for row in _safety_log(sl)]


# =====================================================================
# 1. The handler fails closed, and says why
# =====================================================================
def test_handler_exception_refuses(monkeypatch):
    """A broken RBAC module REFUSES, and leaves a named row behind.

    This is the whole item in one test. Before OC-M-05 this returned True.
    """
    sl = _safety()

    def _boom(cls, legacy_name):
        raise RuntimeError("rbac is misconfigured")

    monkeypatch.setattr(RCANPrincipal, "from_legacy", classmethod(_boom))

    assert sl.check_role_rate_limit("api") is False
    assert sl.last_rate_limit_reason == "rate_limit_unavailable"

    rows = [r for r in _safety_log(sl) if r.get("event") == "rate_limit_unavailable"]
    assert rows, f"no rate_limit_unavailable row in {_reasons(sl)}"
    assert rows[-1]["who"] == "api"
    assert "rbac is misconfigured" in rows[-1]["detail"]


def test_a_real_overage_is_named_differently_from_a_broken_cap():
    """The two refusals must not look alike, or the flip is undiagnosable."""
    sl = _safety()
    limit = ROLE_RATE_LIMITS[RCANRole.GUEST]
    for _ in range(limit):
        assert sl.check_role_rate_limit("driver") is True
    assert sl.check_role_rate_limit("driver") is False
    assert sl.last_rate_limit_reason == "role_rate_limited"
    assert "role_rate_limited" in _reasons(sl)
    assert "rate_limit_unavailable" not in _reasons(sl)


def test_session_check_also_fails_closed(monkeypatch):
    """The other graceful fallback in the same file, same rule."""
    sl = _safety()

    def _boom(cls, legacy_name):
        raise RuntimeError("rbac is misconfigured")

    monkeypatch.setattr(RCANPrincipal, "from_legacy", classmethod(_boom))

    assert sl.check_session_timeout("api") is False
    assert "session_check_unavailable" in _reasons(sl)


def test_no_graceful_fallback_string_remains():
    """done_when: the exact string the item greps for is gone from the file."""
    from pathlib import Path

    import castor.fs.safety as safety_mod

    source = Path(safety_mod.__file__).read_text()
    assert "return True  # Graceful fallback" not in source


# =====================================================================
# 2. The table, in the vocabulary rbac.py publishes today
# =====================================================================
def test_creator_has_a_finite_limit():
    """CREATOR was 0, which the handler read as 'do not even count'."""
    limit = ROLE_RATE_LIMITS[RCANRole.CREATOR]
    assert limit > 0, "CREATOR must be finite: root maps to it"
    assert limit != 0
    # Finite, and still far above anything a real loop on this runtime does:
    # the motor ceiling in castor/fs/safety.py DEFAULT_LIMITS is 20 Hz.
    assert limit >= 20 * RATE_LIMIT_WINDOW_S
    assert RCANPrincipal(name="root", role=RCANRole.CREATOR).rate_limit == limit
    assert RCANPrincipal.from_legacy("root").rate_limit == limit


def test_no_role_is_unlimited():
    assert all(v > 0 for v in ROLE_RATE_LIMITS.values()), ROLE_RATE_LIMITS


def test_role_names_match_rbac_module():
    """Every key of the table resolves in castor/rcan/rbac.py.

    A key that no longer names a role is how a cap silently stops applying to
    somebody, so the table is only allowed to speak the vocabulary the module
    publishes.
    """
    for key in ROLE_RATE_LIMITS:
        assert isinstance(key, RCANRole), f"{key!r} is not an RCANRole"
        assert key.name in RCANRole.__members__
        assert resolve_role_name(key.name) == key.name
        assert rbac.rate_limit_for_role_name(key.name) == ROLE_RATE_LIMITS[key]

    # Every tier the module publishes has a limit; none may be missing.
    assert set(ROLE_RATE_LIMITS) == set(RCANRole)


def test_the_api_vocabulary_resolves_too():
    """admin / operator / viewer is the vocabulary the HTTP layer publishes."""
    from castor.auth_jwt import ROLES

    assert set(API_ROLE_RATE_LIMITS) == set(ROLES)
    for name, limit in API_ROLE_RATE_LIMITS.items():
        assert limit > 0
        assert rbac.rate_limit_for_role_name(name) == limit
    # Derived, not restated: admin is the floor of CREATOR and OWNER.
    assert API_ROLE_RATE_LIMITS["admin"] == min(
        ROLE_RATE_LIMITS[RCANRole.CREATOR], ROLE_RATE_LIMITS[RCANRole.OWNER]
    )
    assert API_ROLE_RATE_LIMITS["operator"] == min(
        ROLE_RATE_LIMITS[RCANRole.LEASEE], ROLE_RATE_LIMITS[RCANRole.USER]
    )
    assert API_ROLE_RATE_LIMITS["viewer"] == ROLE_RATE_LIMITS[RCANRole.GUEST]


def test_from_legacy_is_a_shim_that_says_so(caplog):
    """The compatibility shim logs its translation, so drift is visible."""
    rbac._LEGACY_TRANSLATIONS_LOGGED.clear()
    with caplog.at_level("INFO", logger="castor.rcan.rbac"):
        p = RCANPrincipal.from_legacy("api")
    assert p.role is RCANRole.LEASEE
    assert any("from_legacy" in r.message for r in caplog.records), caplog.text
    # Once per name per process, not once per request.
    caplog.clear()
    with caplog.at_level("INFO", logger="castor.rcan.rbac"):
        RCANPrincipal.from_legacy("api")
    assert not [r for r in caplog.records if "from_legacy" in r.message]


def test_from_legacy_warns_on_a_name_it_does_not_know():
    rbac._LEGACY_TRANSLATIONS_LOGGED.clear()
    p = RCANPrincipal.from_legacy("some_new_principal")
    assert p.role is RCANRole.GUEST


# =====================================================================
# 3. The arm crosses the same cap
# =====================================================================
class _ArmDriver:
    """Minimal arm driver: records what reached the servos."""

    def __init__(self):
        self.joint_calls: list[dict] = []
        self.stops = 0

    def set_joint_positions(self, joints):
        self.joint_calls.append(dict(joints))

    def stop(self):
        self.stops += 1

    def move(self, linear, angular):
        pass


@pytest.fixture()
def arm_client(monkeypatch):
    """A TestClient with a real SafetyLayer and an arm driver attached."""
    import castor.api as api_mod

    sl = _safety()
    monkeypatch.setattr(api_mod.state, "fs", sl)
    driver = _ArmDriver()
    monkeypatch.setattr(api_mod.state, "driver", driver)

    app = api_mod.app
    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    app.router.lifespan_context = _noop_lifespan
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, sl, driver
    finally:
        app.router.on_startup[:] = original_startup
        app.router.on_shutdown[:] = original_shutdown
        app.router.lifespan_context = original_lifespan


def test_arm_endpoint_is_rate_limited(arm_client, monkeypatch):
    """limit+1 arm commands as one principal: the last is refused with 429.

    POST /cap/teleop with ``type: grip`` is an arm command -- it reaches
    ``driver.set_joint_positions`` -- and it does not write /dev/motor, so
    before OC-M-05 nothing counted it. The HTTP principal is ``api``, which
    from_legacy maps to LEASEE; the limit is lowered here so the test is a
    burst and not a thousand round trips.
    """
    limit = 5
    monkeypatch.setitem(ROLE_RATE_LIMITS, RCANRole.LEASEE, limit)
    _client, sl, driver = arm_client

    for i in range(limit):
        resp = _client.post("/cap/teleop", json={"type": "grip", "state": "open"})
        assert resp.status_code == 200, f"call {i} refused: {resp.text}"
    assert len(driver.joint_calls) == limit

    resp = _client.post("/cap/teleop", json={"type": "grip", "state": "close"})
    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert "role_rate_limited" in body["error"]
    assert "api" in body["error"]
    assert body["status"] == 429
    # The refusal is a record, not only a status code.
    assert "role_rate_limited" in _reasons(sl)
    # And it never reached the servos.
    assert len(driver.joint_calls) == limit


def test_arm_command_refused_when_the_cap_itself_is_broken(arm_client, monkeypatch):
    """A broken cap refuses the arm, and the 429 names why."""
    _client, sl, driver = arm_client

    def _boom(cls, legacy_name):
        raise RuntimeError("rbac is misconfigured")

    monkeypatch.setattr(RCANPrincipal, "from_legacy", classmethod(_boom))

    resp = _client.post("/cap/teleop", json={"type": "grip", "state": "open"})
    assert resp.status_code == 429, resp.text
    assert "rate_limit_unavailable" in resp.json()["error"]
    assert driver.joint_calls == []
    assert "rate_limit_unavailable" in _reasons(sl)


def test_every_arm_entry_point_is_named():
    """The sweep is auditable: the endpoints are listed, not inferred."""
    from castor.api import _ARM_ACTION_TYPES, _ARM_DISPATCH_ENTRY_POINTS

    assert _ARM_ACTION_TYPES == frozenset({"arm_pose", "grip"})
    for endpoint in (
        "POST /api/command",
        "POST /api/command/stream",
        "POST /api/action",
        "POST /api/arm/pick_place",
        "POST /cap/teleop",
        "POST /api/memory/replay/{episode_id}",
        "POST /api/memory/trajectory",
        "POST /webhooks/slack",
        "rcan_router:teleop",
        "rcan_router:nav",
        "_handle_channel_message",
    ):
        assert endpoint in _ARM_DISPATCH_ENTRY_POINTS


def test_arm_pose_is_gated_too(arm_client, monkeypatch):
    """grip is not the only arm action; arm_pose goes through the same gate."""
    import castor.api as api_mod

    limit = 2
    monkeypatch.setitem(ROLE_RATE_LIMITS, RCANRole.LEASEE, limit)
    _client, sl, driver = arm_client

    action = {"type": "arm_pose", "joints": {"shoulder": 0.2}}
    for _ in range(limit):
        api_mod._execute_action(dict(action))
    assert len(driver.joint_calls) == limit

    with pytest.raises(Exception) as excinfo:
        api_mod._execute_action(dict(action))
    assert getattr(excinfo.value, "status_code", None) == 429
    assert len(driver.joint_calls) == limit


# =====================================================================
# 4. A stop always goes through
# =====================================================================
def test_stop_is_never_rate_limited(arm_client, monkeypatch):
    """Exhausted budget, broken cap: the stop still goes through.

    This is the counterweight to failing closed. The cap now refuses when it
    cannot run, which is right for every command except the one a person uses
    to halt a robot that is moving.
    """
    _client, sl, driver = arm_client
    monkeypatch.setitem(ROLE_RATE_LIMITS, RCANRole.LEASEE, 1)

    # Spend the whole budget.
    assert sl.check_role_rate_limit("api") is True
    assert sl.check_role_rate_limit("api") is False

    # POST /api/stop still stops.
    resp = _client.post("/api/stop")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "stopped"
    assert driver.stops >= 1
    assert sl.is_estopped is True

    # And the hold state can still be read while over the cap.
    status = _client.get("/api/fs/estop")
    assert status.status_code == 200
    assert status.json()["estopped"] is True
    assert status.json()["proc_status"] == "estop"


def test_stop_goes_through_when_the_cap_cannot_run(arm_client, monkeypatch):
    """A broken RBAC module must not be able to prevent a stop."""
    _client, sl, driver = arm_client

    def _boom(cls, legacy_name):
        raise RuntimeError("rbac is misconfigured")

    monkeypatch.setattr(RCANPrincipal, "from_legacy", classmethod(_boom))

    resp = _client.post("/api/stop")
    assert resp.status_code == 200, resp.text
    assert driver.stops >= 1
    assert sl.is_estopped is True


def test_a_stop_write_skips_the_cap():
    """SafetyLayer.write() does not pace a stop command."""
    sl = _safety()
    limit = ROLE_RATE_LIMITS[RCANRole.LEASEE]
    for _ in range(limit):
        assert sl.check_role_rate_limit("api") is True
    assert sl.check_role_rate_limit("api") is False

    assert sl.write("/dev/motor", {"type": "stop"}, principal="api") is True
    # An ordinary motion write from the same principal is still refused.
    assert sl.write("/dev/motor", {"type": "move", "linear": 0.2}, principal="api") is False
    assert "rate" in sl.last_write_denial.lower()


def test_stop_action_never_reaches_the_arm_gate(arm_client, monkeypatch):
    """_execute_action('stop') is not in _ARM_ACTION_TYPES and is not counted."""
    import castor.api as api_mod

    _client, sl, driver = arm_client

    def _boom(cls, legacy_name):
        raise RuntimeError("rbac is misconfigured")

    monkeypatch.setattr(RCANPrincipal, "from_legacy", classmethod(_boom))

    api_mod._execute_action({"type": "stop"})
    assert driver.stops == 1


def test_a_released_joystick_is_not_refused():
    """The halt the console actually sends is a ZERO MOVE, not type=stop.

    ``stopMove()`` in castor/api.py's gamepad page posts
    ``{type:"move", linear:0, angular:0}``. If the cap paced that, an operator
    who held the stick past the budget would get a refusal on the one tick that
    brings the wheels to zero, and the last accepted velocity would keep
    standing: this gateway has no teleop deadman to catch it.
    """
    sl = _safety()
    limit = ROLE_RATE_LIMITS[RCANRole.LEASEE]
    for _ in range(limit):
        assert sl.check_role_rate_limit("api") is True
    assert sl.check_role_rate_limit("api") is False

    # The release tick, in every spelling the console and the phone send.
    assert sl.write("/dev/motor", {"type": "move", "linear": 0, "angular": 0}, principal="api")
    assert sl.write("/dev/motor", {"type": "move", "linear": 0.0, "angular": -0.0}, principal="api")
    assert sl.write("/dev/motor", {"type": "move"}, principal="api")
    # And motion is still paced.
    moving = {"type": "move", "linear": 0.2, "angular": 0}
    turning = {"type": "move", "linear": 0, "angular": 0.2}
    assert sl.write("/dev/motor", moving, principal="api") is False
    assert sl.write("/dev/motor", turning, principal="api") is False


def test_a_released_joystick_is_not_refused_when_the_cap_cannot_run(monkeypatch):
    """A broken RBAC module must not be able to leave a wheel spinning."""
    sl = _safety()

    def _boom(cls, legacy_name):
        raise RuntimeError("rbac is misconfigured")

    monkeypatch.setattr(RCANPrincipal, "from_legacy", classmethod(_boom))
    assert sl.write("/dev/motor", {"type": "move", "linear": 0, "angular": 0}, principal="api")
    assert sl.write("/dev/motor", {"type": "move", "linear": 0.2}, principal="api") is False


def test_clearing_a_stop_is_not_paced(arm_client, monkeypatch):
    """Clearing a stop answers while the caller is over its budget.

    A clear is privileged (admin role plus the e-stop code) and rare, so it is
    not metered: pacing it could only ever strand a stopped robot that nothing
    on the network could lift. Stated as a test rather than left as an accident
    of where the call sites happen to be. Over budget the endpoint answers on
    its own merits -- 401/403 on a runtime with no auth configured, 200 with
    the admin bearer and the code -- and never 429.
    """
    client, sl, _driver = arm_client
    assert client.post("/api/stop").status_code == 200
    assert sl.is_estopped is True

    monkeypatch.setitem(ROLE_RATE_LIMITS, RCANRole.LEASEE, 1)
    assert sl.check_role_rate_limit("api") is True
    assert sl.check_role_rate_limit("api") is False

    assert client.post("/api/estop/clear").status_code != 429
    # And the layer underneath it does not consult the cap at all.
    assert sl.clear_estop(principal="root", source="local") is True
    assert sl.is_estopped is False


def test_a_broken_session_check_is_not_filed_as_an_expired_session(monkeypatch):
    """A session store that throws must not be audited as a timed-out session.

    write() named every session refusal ``session_expired``, whatever caused
    it, so a broken check told the operator to re-authenticate and then refused
    again with nothing in /var/log/safety to say the check itself was the
    problem.
    """
    sl = _safety()

    class _BrokenStore(dict):
        def get(self, *_a, **_kw):
            raise RuntimeError("session store is unreachable")

    # Broken INSIDE check_session_timeout, so its own fail-closed branch runs.
    sl._session_starts = _BrokenStore()
    assert sl.write("/dev/motor", {"type": "move", "linear": 0.2}, principal="api") is False
    assert sl.last_rate_limit_reason == "session_check_unavailable"
    assert "session_check_unavailable" in sl.last_write_denial

    rows = sl.ns.read("/var/log/safety") or []
    reasons = [r.get("reason") or r.get("action") or str(r) for r in rows]
    assert any("session_check_unavailable" in str(r) for r in reasons), rows
    assert not any(str(r) == "session_expired" for r in reasons), rows


def test_a_broken_cap_is_named_on_a_read_too(monkeypatch):
    """read/ls/stat/append/mkdir file the named reason, not a generic one."""
    sl = _safety()

    def _boom(cls, legacy_name):
        raise RuntimeError("rbac is misconfigured")

    monkeypatch.setattr(RCANPrincipal, "from_legacy", classmethod(_boom))
    assert sl.read("/proc/status", principal="api") is None
    rows = sl.ns.read("/var/log/safety") or []
    assert any("rate_limit_unavailable" in str(r) for r in rows), rows


def test_operator_means_two_different_limits_and_case_says_which():
    """``operator`` (HTTP) and ``OPERATOR`` (deprecated RCAN) are not the same.

    The HTTP role ``operator`` is paced at 100, the floor of LEASEE and USER.
    ``OPERATOR`` is the pre-f414b90 spelling of LEASEE and is paced at 500. A
    case-insensitive lookup answered 100 for both and never logged the
    deprecation, so a caller asking about the old RCAN role was told somebody
    else's number.
    """
    assert rbac.rate_limit_for_role_name("operator") == API_ROLE_RATE_LIMITS["operator"]
    assert rbac.rate_limit_for_role_name("OPERATOR") == ROLE_RATE_LIMITS[RCANRole.LEASEE]
    assert rbac.rate_limit_for_role_name("ADMIN") == ROLE_RATE_LIMITS[RCANRole.OWNER]
    assert rbac.rate_limit_for_role_name("admin") == API_ROLE_RATE_LIMITS["admin"]
    with pytest.raises(KeyError):
        rbac.rate_limit_for_role_name("nobody")


def test_owner_and_leasee_are_the_current_names_not_the_old_ones():
    """Direction check: f414b90 renamed ADMIN to OWNER and OPERATOR to LEASEE.

    So OWNER and LEASEE are current and ADMIN and OPERATOR are deprecated, not
    the other way round. The rate-limit table is keyed on the current names.
    """
    assert set(ROLE_RATE_LIMITS) == set(RCANRole)
    assert rbac._DEPRECATED_ROLE_NAMES == {"ADMIN": "OWNER", "OPERATOR": "LEASEE"}
    assert not hasattr(RCANRole, "ADMIN")
    assert not hasattr(RCANRole, "OPERATOR")


def test_the_pacing_ladder_is_monotonic():
    """A senior role is never paced below a junior one.

    LEASEE was raised to the runtime's own 20 Hz motor ceiling so the
    operator's joystick is not refused mid-drive; without moving OWNER with it
    the brain, which supervises that operator, would have been capped below
    them.
    """
    ordered = sorted(RCANRole, key=int)
    limits = [ROLE_RATE_LIMITS[r] for r in ordered]
    assert limits == sorted(limits), dict(zip([r.name for r in ordered], limits))
    assert all(v > 0 for v in limits)


def test_leasee_clears_the_console_joystick_cadence():
    """The operator's own tools must fit inside the operator's own budget.

    The built-in console and the gamepad page post /api/action every 80 ms
    while a stick is held. That is 12.5 Hz, 750 a minute, and each request now
    costs exactly one slot because the clamp read-back is read raw.
    """
    held_joystick_per_min = 60 / 0.080
    assert ROLE_RATE_LIMITS[RCANRole.LEASEE] > held_joystick_per_min
    # And the cap does not bind before the runtime's own motor gate does.
    from castor.fs.safety import DEFAULT_LIMITS

    assert ROLE_RATE_LIMITS[RCANRole.LEASEE] >= DEFAULT_LIMITS["motor_rate_hz"] * 60


def test_the_429_body_carries_a_machine_readable_code(arm_client, monkeypatch):
    """A client can branch on the refusal without parsing a Python repr.

    The global handler stringified a dict detail into `error`, so the named
    reason, the whole point of the fail-closed flip, was readable by a person
    and not by a client.
    """
    client, sl, _driver = arm_client
    monkeypatch.setitem(ROLE_RATE_LIMITS, RCANRole.LEASEE, 1)
    assert sl.check_role_rate_limit("api") is True
    assert sl.check_role_rate_limit("api") is False

    resp = client.post("/cap/teleop", json={"type": "grip", "state": "open"})
    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["code"] == "rate_limited"
    assert body["reason"] == "role_rate_limited"
    assert body["status"] == 429
    assert body["detail"]["principal"] == "api"
    assert body["detail"]["window_s"] == rbac.RATE_LIMIT_WINDOW_S
    # The stringified form other callers already read is unchanged.
    assert "rate_limited" in body["error"]


def test_a_string_detail_is_unchanged(arm_client):
    """Only a dict detail gains the new fields; every other error is as it was."""
    client, _sl, _driver = arm_client
    resp = client.post("/api/memory/replay/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "HTTP_404"
    assert "detail" not in body


def test_an_over_budget_read_says_429_not_404(arm_client, monkeypatch):
    """A paced read that the cap refused must not read as a missing path.

    SafetyLayer.read and .ls answer None both when the cap refuses and when the
    path is absent, so an over-budget caller was told "Path not found" and sent
    to check its own request. Same mistake POST /api/action made with 422.
    """
    client, sl, _driver = arm_client
    monkeypatch.setitem(ROLE_RATE_LIMITS, RCANRole.LEASEE, 1)
    assert sl.check_role_rate_limit("api") is True
    assert sl.check_role_rate_limit("api") is False

    resp = client.get("/api/fs/ls", params={"path": "/"})
    assert resp.status_code == 429, resp.text
    assert resp.json()["code"] == "rate_limited"

    resp = client.post("/api/fs/read", json={"path": "/proc/status"})
    assert resp.status_code == 429, resp.text
    assert resp.json()["reason"] == "role_rate_limited"


def test_a_genuinely_missing_path_still_404s(arm_client):
    """The 429 only fires when the cap actually refused."""
    client, _sl, _driver = arm_client
    resp = client.post("/api/fs/read", json={"path": "/proc/no-such-node"})
    assert resp.status_code == 404, resp.text


def test_the_real_ls_route_answers_429_past_the_cap(arm_client):
    """Drive GET /api/fs/ls past the real cap on the real route.

    Bob answered 1200 x 200 then 50 x 404 "Not a directory: /proc" on exactly
    this request, so the route is exercised end to end here rather than through
    a monkeypatched limit.
    """
    client, sl, _driver = arm_client
    limit = ROLE_RATE_LIMITS[RCANRole.LEASEE]
    codes: dict[int, int] = {}
    for _ in range(limit + 20):
        code = client.get("/api/fs/ls", params={"path": "/proc"}).status_code
        codes[code] = codes.get(code, 0) + 1
    assert codes.get(200) == limit, codes
    assert codes.get(429) == 20, codes
    assert 404 not in codes, codes

    resp = client.get("/api/fs/ls", params={"path": "/proc"})
    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["code"] == "rate_limited"
    assert body["reason"] == "role_rate_limited"
    assert sl.rate_limit_refusal("api") == "role_rate_limited"
    assert client.get("/api/fs/estop").json()["rate_limit_reason"] is not None


def test_a_facade_that_cannot_answer_is_loud_instead_of_silently_404ing(arm_client, caplog):
    """THE LIVE BUG. A state.fs that cannot be asked must not read as "allowed".

    state.fs is a CastorFS facade that delegates to SafetyLayer by hand. The
    three names OC-M-05 added were never added to it, so the gateway's getattr
    returned None, _raise_if_paced_out quietly did nothing, and an over-budget
    read answered 404 with rate_limit_reason null while the cap was refusing at
    exactly its limit. It is still a 404 here, because a gateway that cannot
    ask the cap genuinely cannot know. What changed is that it says so, once,
    naming the object and the file to fix.
    """
    import castor.api as api_mod

    client, sl, _driver = arm_client
    limit = ROLE_RATE_LIMITS[RCANRole.LEASEE]
    for _ in range(limit + 1):
        sl.check_role_rate_limit("api")

    class _StaleFacade:
        """A facade that delegates the old names and not the new ones."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name in api_mod._SAFETY_SURFACE_REQUIRED:
                raise AttributeError(name)
            return getattr(self._inner, name)

    api_mod.state.fs = _StaleFacade(sl)
    api_mod._REPORTED_SAFETY_SKEW.clear()
    with caplog.at_level("ERROR"):
        resp = client.get("/api/fs/ls", params={"path": "/proc"})
    assert resp.status_code == 404, resp.text
    assert any("SAFETY SURFACE GAP" in r.message for r in caplog.records), caplog.text
    assert any("castor/fs/__init__.py" in r.getMessage() for r in caplog.records)

    # One line per name per process, not one per request.
    caplog.clear()
    with caplog.at_level("ERROR"):
        client.get("/api/fs/ls", params={"path": "/proc"})
    assert not [r for r in caplog.records if "SAFETY SURFACE GAP" in r.message]


def test_the_startup_check_reads_the_live_object_not_the_class():
    """Checking SafetyLayer would have called the broken runtime healthy.

    That is precisely why the gap survived two deploys: every name was on
    SafetyLayer, so a class-level check reported nothing wrong, while the
    object the gateway actually holds exposed none of them.
    """
    import castor.api as api_mod
    from castor.fs import CastorFS
    from castor.fs.safety import SafetyLayer

    api_mod._REPORTED_SAFETY_SKEW.clear()
    assert api_mod._check_safety_layer_coherence(CastorFS()) == []

    class _OnlyTheOldSurface:
        read = SafetyLayer.read
        write = SafetyLayer.write
        ls = SafetyLayer.ls

    api_mod._REPORTED_SAFETY_SKEW.clear()
    missing = api_mod._check_safety_layer_coherence(_OnlyTheOldSurface())
    assert missing == list(api_mod._SAFETY_SURFACE_REQUIRED), missing


def test_the_refusal_rows_are_readable_once_the_window_rolls(arm_client):
    """The exact read that shows the role_rate_limited row.

    The rows are written by _audit_safety through the RAW namespace append, so
    they are never themselves paced. Reading them back IS paced, which is why
    POST /api/fs/read answered {"data": null} while the caller was still over
    budget: the read was refused and the node exists, so it was neither a 404
    nor an error.
    """
    client, sl, _driver = arm_client
    limit = ROLE_RATE_LIMITS[RCANRole.LEASEE]
    for _ in range(limit + 2):
        sl.ls("/proc", principal="api")

    rows = sl.ns.read("/var/log/safety") or []
    assert any(r.get("event") == "role_rate_limited" for r in rows), rows[-5:]

    # Over budget the read is refused, and says so instead of answering null.
    assert client.post("/api/fs/read", json={"path": "/var/log/safety"}).status_code == 429

    # Window rolled: the same read now returns the rows.
    sl._role_request_timestamps["api"] = []
    resp = client.post("/api/fs/read", json={"path": "/var/log/safety"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert isinstance(data, list) and data, resp.text
    assert any(r.get("event") == "role_rate_limited" for r in data)


# =====================================================================
# 7. The real object graph, through the real bearer path
#
# Every fixture above assigns a bare SafetyLayer to state.fs. THE GATEWAY
# NEVER HOLDS ONE. castor/api.py builds a CastorFS, which delegates to
# SafetyLayer by hand, one name at a time. That gap is what broke OC-M-05
# twice on Bob: the cap enforced correctly at exactly 1200, because it is
# reached through the delegated ls(), while every question the gateway asked
# ABOUT the cap missed the facade and answered empty. These tests hold the
# object the gateway actually holds and present the header the phone actually
# sends.
# =====================================================================


@pytest.fixture()
def real_client(monkeypatch):
    """A TestClient over a real CastorFS, authenticated by a real bearer."""
    import castor.api as api_mod
    from castor.fs import CastorFS

    token = "test-runtime-bearer-0123456789"
    monkeypatch.setenv("OPENCASTOR_API_TOKEN", token)
    monkeypatch.delenv("OPENCASTOR_USERS", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)
    monkeypatch.setattr(api_mod, "API_TOKEN", token, raising=False)

    fs = CastorFS()
    monkeypatch.setattr(api_mod.state, "fs", fs)

    app = api_mod.app
    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    app.router.lifespan_context = _noop_lifespan
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            client.headers.update({"Authorization": f"Bearer {token}"})
            yield client, fs
    finally:
        app.router.on_startup[:] = original_startup
        app.router.on_shutdown[:] = original_shutdown
        app.router.lifespan_context = original_lifespan


def test_the_facade_exposes_everything_the_gateway_asks_it_for():
    """CastorFS must answer every name castor/api.py asks state.fs for.

    This is the regression guard for the actual bug. All four names existed on
    SafetyLayer and none on CastorFS, so a check against the class said the
    runtime was healthy while the gateway could reach none of them.
    """
    import castor.api as api_mod
    from castor.fs import CastorFS

    fs = CastorFS()
    assert api_mod._check_safety_layer_coherence(fs) == []
    for name in api_mod._SAFETY_SURFACE_REQUIRED:
        assert hasattr(fs, name), name
    # And the delegation is live, not just present.
    assert fs.rate_limit_refusal("api") is None
    assert fs.check_role_rate_limit("api") is True
    assert fs.last_rate_limit_reason is None


def test_a_bearer_request_is_paced_and_refused_with_429(real_client):
    """THE LIVE CASE. Real CastorFS, real bearer header, past the real cap.

    Bob answered 1200 x 200 then 404 "Not a directory: /proc" to exactly this
    request, with rate_limit_reason null, because state.fs is a CastorFS and
    the gateway's questions missed it.
    """
    client, fs = real_client
    limit = ROLE_RATE_LIMITS[RCANRole.LEASEE]

    seen: list[int] = []
    for _ in range(limit + 15):
        seen.append(client.get("/api/fs/ls", params={"path": "/proc"}).status_code)
    assert seen.count(200) == limit, (seen.count(200), seen.count(404), seen.count(429))
    assert seen.count(429) == 15
    assert 404 not in seen

    resp = client.get("/api/fs/ls", params={"path": "/proc"})
    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["code"] == "rate_limited"
    assert body["reason"] == "role_rate_limited"

    # The field the operator reads on the robot is populated, not null.
    est = client.get("/api/fs/estop")
    assert est.status_code == 200
    assert est.json()["rate_limit_reason"] == "role_rate_limited"


def test_the_same_bearer_reads_the_audit_row_once_the_window_rolls(real_client):
    """The exact live check: the refusal row, read back by the same bearer."""
    client, fs = real_client
    limit = ROLE_RATE_LIMITS[RCANRole.LEASEE]
    for _ in range(limit + 3):
        client.get("/api/fs/ls", params={"path": "/proc"})

    # Still over budget: the log read is itself paced and says so.
    assert client.post("/api/fs/read", json={"path": "/var/log/safety"}).status_code == 429

    fs.safety._role_request_timestamps["api"] = []
    resp = client.post("/api/fs/read", json={"path": "/var/log/safety"})
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert isinstance(rows, list) and rows
    assert any(r.get("event") == "role_rate_limited" and r.get("who") == "api" for r in rows)


def test_the_arm_gate_does_not_explode_on_the_real_facade(real_client):
    """_check_arm_rate_limit calls fs.check_role_rate_limit directly.

    CastorFS had no such method, so on a real robot every arm command raised
    AttributeError, which the gateway turns into a 500. Bob's arm is unplugged,
    so nobody had seen it.
    """
    import castor.api as api_mod

    _client, fs = real_client
    assert api_mod._check_arm_rate_limit("grip", "api") is None

    fs.safety._role_request_timestamps["api"] = [time.time()] * ROLE_RATE_LIMITS[RCANRole.LEASEE]
    with pytest.raises(HTTPException) as exc:
        api_mod._check_arm_rate_limit("grip", "api")
    assert exc.value.status_code == 429
    assert exc.value.detail["reason"] == "role_rate_limited"
