"""A refused motor command stops the motors, not only /dev/motor.

/dev/motor is a node in the virtual filesystem. When the safety layer refuses a command (a velocity
that is not a finite number, or a move whose stopping path leaves the workspace) it writes a stop
or a no-translation command there, but the driver keeps running the last command it was handed
until someone tells it otherwise. Review of the EV-03 fix found this on the API's direct-action
path: POST /api/action answered 422 to a NaN velocity while the previous accepted move kept the
wheels turning. Drivers now register their stop() with the safety layer (add_motor_halt), which
calls it on every refusal.
"""

from __future__ import annotations

import ast
import contextlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
ADMIN = "test-motor-halt-admin"


@pytest.fixture
def api(monkeypatch):
    import castor.api as api_mod
    from castor.fs import CastorFS

    monkeypatch.setattr(api_mod, "API_TOKEN", None)
    monkeypatch.setattr(api_mod, "ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(api_mod, "ADMIN_TOKEN_SHA256", None)
    monkeypatch.setattr(api_mod.state, "fs", CastorFS())
    driver = MagicMock()
    monkeypatch.setattr(api_mod.state, "driver", driver)
    # What the gateway's startup does right after it creates the driver:
    api_mod.state.fs.safety.add_motor_halt(driver.stop)
    api_mod.state.fs.safety.limits["motor_rate_hz"] = 1000.0
    api_mod._command_history.clear()

    app = api_mod.app
    original = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan
    try:
        with TestClient(
            app, raise_server_exceptions=False, headers={"Authorization": f"Bearer {ADMIN}"}
        ) as client:
            yield client, driver
    finally:
        app.router.lifespan_context = original


def test_a_refused_direct_action_stops_the_running_move(api):
    client, driver = api
    ok = client.post("/api/action", json={"type": "move", "linear": 0.3, "angular": 0.0})
    assert ok.status_code == 200, ok.text
    driver.move.assert_called_once()
    driver.stop.assert_not_called()

    refused = client.post(  # httpx will not encode NaN, so the body is sent as text
        "/api/action",
        content='{"type": "move", "linear": NaN}',
        headers={"Content-Type": "application/json"},
    )
    assert refused.status_code == 422
    driver.stop.assert_called_once()  # the move it was running is stopped, not left going


def _source(path: Path) -> str:
    return ast.unparse(ast.parse(path.read_text()))


def test_the_gateway_registers_its_driver_and_removes_it_on_shutdown():
    source = _source(ROOT / "castor" / "api.py")
    created = source.index("state.driver = get_driver(state.config)")
    registered = source.index("state.fs.safety.add_motor_halt(state.driver.stop)")
    assert 0 < registered - created < 200  # straight after the driver is created
    removed = source.index("state.fs.safety.remove_motor_halt(state.driver.stop)")
    assert removed < source.index("state.driver.close()")


def test_the_runtime_registers_its_driver():
    assert "fs.safety.add_motor_halt(driver.stop)" in _source(ROOT / "castor" / "main.py")
