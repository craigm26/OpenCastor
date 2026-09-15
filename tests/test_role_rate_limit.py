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

import pytest
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
