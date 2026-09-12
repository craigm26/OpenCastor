"""The agent's bearer must not carry the role that gates the agent.

Three properties are pinned here, and each of them failed before this file
existed:

1. **The role split.** ``OPENCASTOR_API_TOKEN`` is the credential the agent, the
   app and the robot's own services carry, and `castor up` writes it into
   tokens.env under the runtime uid — so anything running as the robot can read
   it. It used to map to ``jwt_role = "admin"``, which made
   ``POST /api/hitl/authorize`` — the one production human-in-the-loop gate —
   resolvable by the very agent it gates. It now maps to `operator`: it drives,
   it does not authorize.

2. **No silent open access.** ``verify_token``'s layer 4 was a comment and then
   the end of the function. With ``OPENCASTOR_USERS``, the JWT secret and
   ``OPENCASTOR_API_TOKEN`` all unset it returned ``None`` having set no role at
   all, and ``_check_min_role`` returned early on a role of ``None`` — so an
   unconfigured runtime served ``/api/system/reboot``, ``/api/system/upgrade``
   and ``/api/harness/apply-champion`` to anyone who could reach the port.

3. **The champion fence.** ``POST /api/harness/apply-champion`` merged keys from
   a REMOTE document against its own ``TUNABLE_KEYS``, a set that included
   ``p66_consent_threshold`` — the number deciding when the runtime stops to ask
   a human. A fenced key now rejects the whole document with 400
   ``forbidden_key`` and nothing is written; the file is asserted byte-identical
   by SHA-256 afterwards.

The reason codes are read out of the gateway's standard error envelope
(``{"error": ..., "code": "HTTP_401", ...}``): ``register_error_handlers``
flattens an HTTPException's dict detail into that ``error`` string, so that
string is what a caller actually sees.
"""

import collections
import contextlib
import hashlib
import time

import pytest
import yaml
from starlette.testclient import TestClient

RUNTIME_TOKEN = "oc_api_runtime_bearer_the_agent_can_read"
ADMIN_TOKEN = "oc_admin_the_humans_bearer"

_RUNTIME = {"Authorization": f"Bearer {RUNTIME_TOKEN}"}
_ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _RecordingAudit:
    """Stands in for the AuditLog singleton; keeps entries in memory."""

    def __init__(self):
        self.entries = []

    def log(self, event, source="system", **kwargs):
        entry = {"event": event, "source": source, "ts": "2026-09-12T00:00:00"}
        entry.update(kwargs)
        self.entries.append(entry)

    def read(self, since=None, event=None, limit=50):
        out = [e for e in self.entries if event is None or e.get("event") == event]
        return out[-limit:]

    def of(self, event):
        return [e for e in self.entries if e["event"] == event]


class _FakeDriver:
    """Enough of a hardware driver for POST /api/action to succeed."""

    def __init__(self):
        self.moves = []

    def move(self, linear, angular):
        self.moves.append((linear, angular))

    def stop(self):
        self.moves.append("stop")


class _FakeHiTLGateManager:
    def __init__(self):
        self.resolved = []

    def authorize(self, pending_id, decision):
        self.resolved.append((pending_id, decision))
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    """Both bearers configured, and every ambient auth source cleared.

    ``ROBOT_HOME`` is pointed at a tmp dir so ``_admin_digest``'s file fallback
    cannot pick up this developer's own robot.
    """
    for var in (
        "OPENCASTOR_USERS",
        "OPENCASTOR_JWT_SECRET",
        "JWT_SECRET",
        "OPENCASTOR_CONFIG",
        "CASTOR_RRN",
        "OPENCASTOR_ADMIN_TOKEN_SHA256",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path / "no-robot-here"))
    monkeypatch.setenv("OPENCASTOR_API_TOKEN", RUNTIME_TOKEN)

    import castor.api as api_mod

    api_mod.API_TOKEN = RUNTIME_TOKEN
    api_mod.ADMIN_TOKEN = ADMIN_TOKEN
    api_mod.ADMIN_TOKEN_SHA256 = None
    api_mod.state.config = None
    api_mod.state.brain = None
    api_mod.state.driver = None
    api_mod.state.fs = None
    api_mod.state.hitl_gate_manager = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.state.boot_time = time.time()
    yield
    api_mod.API_TOKEN = None
    api_mod.ADMIN_TOKEN = None
    api_mod.ADMIN_TOKEN_SHA256 = None
    api_mod.state.config = None
    api_mod.state.driver = None
    api_mod.state.hitl_gate_manager = None


@pytest.fixture()
def unconfigured(monkeypatch):
    """No credential of any kind: OPENCASTOR_USERS, JWT secret and both bearers."""
    import castor.api as api_mod

    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_ADMIN_TOKEN_SHA256", raising=False)
    monkeypatch.delenv("ROBOT_HOME", raising=False)
    api_mod.API_TOKEN = None
    api_mod.ADMIN_TOKEN = None
    api_mod.ADMIN_TOKEN_SHA256 = None


@pytest.fixture()
def audit(monkeypatch):
    """Swap the audit singleton so nothing writes a log file during tests."""
    import castor.api as api_mod

    recorder = _RecordingAudit()
    monkeypatch.setattr(api_mod, "get_audit", lambda: recorder)
    return recorder


@pytest.fixture()
def client():
    from castor.api import app

    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.router.on_startup[:] = original_startup
        app.router.on_shutdown[:] = original_shutdown
        app.router.lifespan_context = original_lifespan


@pytest.fixture()
def champion(monkeypatch, tmp_path):
    """An ops-checkout champion.yaml plus a real robot.rcan.yaml on disk.

    Returns a callable taking the champion's ``config`` dict and giving back the
    path of the config file the endpoint would rewrite, so a test can hash it
    before and after.
    """
    import castor.api as api_mod

    config_path = tmp_path / "robot.rcan.yaml"
    config_path.write_text(
        yaml.dump(
            {"agent": {"harness": {"max_iterations": 3, "p66_consent_threshold": 0.4}}},
            default_flow_style=False,
        )
    )
    monkeypatch.setenv("OPENCASTOR_CONFIG", str(config_path))
    monkeypatch.setenv("OPENCASTOR_OPS_DIR", str(tmp_path / "ops"))
    api_mod.state.config = yaml.safe_load(config_path.read_text())
    api_mod.state.rrn = None

    def _write(config: dict) -> "object":
        research = tmp_path / "ops" / "harness-research"
        research.mkdir(parents=True, exist_ok=True)
        (research / "champion.yaml").write_text(
            yaml.dump({"candidate_id": "cand-7", "score": 0.91, "config": config})
        )
        return config_path

    return _write


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1. The runtime bearer cannot authorize the gate it is gated by
# ---------------------------------------------------------------------------
def test_runtime_bearer_cannot_authorize_hitl(client, audit):
    import castor.api as api_mod

    api_mod.state.hitl_gate_manager = _FakeHiTLGateManager()

    resp = client.post(
        "/api/hitl/authorize",
        json={"pending_id": "pending-1", "decision": "approve"},
        headers=_RUNTIME,
    )

    assert resp.status_code == 403
    assert "insufficient_role" in resp.json()["error"]
    # And the gate really was not resolved — the refusal is not cosmetic.
    assert api_mod.state.hitl_gate_manager.resolved == []


def test_admin_bearer_can_authorize_hitl(client, audit):
    import castor.api as api_mod

    api_mod.state.hitl_gate_manager = _FakeHiTLGateManager()

    resp = client.post(
        "/api/hitl/authorize",
        json={"pending_id": "pending-1", "decision": "approve"},
        headers=_ADMIN,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["authorized_by"] == "admin"
    assert api_mod.state.hitl_gate_manager.resolved == [("pending-1", "approve")]
    # The record names who, not merely that.
    assert audit.of("hitl_authorize")[0]["actor"] == "admin"


# ---------------------------------------------------------------------------
# 2. The demotion narrows and nothing else: the runtime bearer still drives
# ---------------------------------------------------------------------------
def test_runtime_bearer_can_post_action(client):
    import castor.api as api_mod

    api_mod.state.driver = _FakeDriver()

    resp = client.post(
        "/api/action",
        json={"type": "move", "linear": 0.2, "angular": 0.0},
        headers=_RUNTIME,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "executed"
    assert api_mod.state.driver.moves == [(0.2, 0.0)]


def test_runtime_bearer_reports_operator_not_admin(client):
    """`GET /auth/me` is what a console asks; it must not still say admin."""
    resp = client.get("/auth/me", headers=_RUNTIME)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"username": "api", "role": "operator", "auth_type": "static"}

    admin = client.get("/auth/me", headers=_ADMIN)
    assert admin.json() == {
        "username": "admin",
        "role": "admin",
        "auth_type": "static_admin",
    }


def test_runtime_bearer_cannot_clear_estop(client):
    """Setting a stop is anybody's; clearing one the human set is not."""
    resp = client.post("/api/estop/clear", headers=_RUNTIME)
    assert resp.status_code == 403
    assert "insufficient_role" in resp.json()["error"]


def test_wrong_token_is_still_401_not_anonymous(client):
    resp = client.post(
        "/api/hitl/authorize",
        json={"pending_id": "x", "decision": "approve"},
        headers={"Authorization": "Bearer not-either-of-them"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. An unconfigured runtime refuses the privileged routes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    ["/api/harness/apply-champion", "/api/system/reboot", "/api/system/upgrade"],
)
def test_no_auth_configured_refuses_privileged(client, unconfigured, path):
    resp = client.post(path, json={})

    assert resp.status_code == 401, f"{path} -> {resp.status_code} {resp.text}"
    assert "no_auth_configured" in resp.json()["error"]


def test_no_auth_configured_still_serves_status_and_health(client, unconfigured):
    """The refusal is scoped: read-only routes keep answering, so a half-set-up
    robot can still be diagnosed from the phone."""
    assert client.get("/health").status_code == 200
    assert client.get("/api/status").status_code == 200


def test_no_auth_configured_does_not_reboot_the_host(client, unconfigured, monkeypatch):
    """The 401 has to come BEFORE `sudo reboot`, not after it."""
    import subprocess

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))

    assert client.post("/api/system/reboot", json={}).status_code == 401
    assert calls == []


# ---------------------------------------------------------------------------
# 4. The champion fence
# ---------------------------------------------------------------------------
def test_champion_document_with_consent_threshold_rejected(client, audit, champion):
    config_path = champion(
        {"max_iterations": 9, "p66_consent_threshold": 0.99, "cost_gate_usd": 2.0}
    )
    before = _sha256(config_path)

    resp = client.post("/api/harness/apply-champion", json={}, headers=_ADMIN)

    assert resp.status_code == 400, resp.text
    assert "forbidden_key" in resp.json()["error"]
    assert "p66_consent_threshold" in resp.json()["error"]
    # Nothing was written: not the fenced key, not the two innocent ones.
    assert _sha256(config_path) == before
    refused = audit.of("champion_apply")
    assert refused and refused[-1]["outcome"] == "refused"
    assert refused[-1]["forbidden_key"] == "p66_consent_threshold"


def test_clean_champion_document_applies_and_is_recorded(client, audit, champion):
    config_path = champion({"max_iterations": 9, "cost_gate_usd": 2.0})
    before = _sha256(config_path)

    resp = client.post("/api/harness/apply-champion", json={}, headers=_ADMIN)

    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] is True
    assert _sha256(config_path) != before

    written = yaml.safe_load(config_path.read_text())
    harness = written["agent"]["harness"]
    assert harness["max_iterations"] == 9
    assert harness["cost_gate_usd"] == 2.0
    # The pre-existing consent threshold is untouched by an applied document.
    assert harness["p66_consent_threshold"] == 0.4

    entry = audit.of("champion_apply")[-1]
    assert entry["outcome"] == "applied"
    assert entry["actor"] == "admin"
    assert entry["champion_source"].startswith("file:")
    assert entry["applied_keys"]["max_iterations"] == {"from": 3, "to": 9}


def test_runtime_bearer_cannot_apply_a_champion(client, audit, champion):
    config_path = champion({"max_iterations": 9})
    before = _sha256(config_path)

    resp = client.post("/api/harness/apply-champion", json={}, headers=_RUNTIME)

    assert resp.status_code == 403
    assert _sha256(config_path) == before


def test_runtime_bearer_cannot_enable_auto_apply(client):
    resp = client.post("/api/harness/auto-apply", json={"enabled": True}, headers=_RUNTIME)
    assert resp.status_code == 403


def test_forbidden_key_matching_is_anchored_not_substring():
    """`pin` is in the fence; `mapping` must not be refused for containing it."""
    from castor.api import _champion_forbidden_key

    assert _champion_forbidden_key({"mapping": 1, "spinner": 2, "max_iterations": 3}) is None
    assert _champion_forbidden_key({"cost_gate_usd": 1.0}) is None
    assert _champion_forbidden_key({"p66_consent_threshold": 0.9}) == "p66_consent_threshold"
    assert _champion_forbidden_key({"api_key": "x"}) == "api_key"
    assert _champion_forbidden_key({"pin": 17}) == "pin"
    # Nested documents are screened too, and the path is reported.
    assert _champion_forbidden_key({"agent": {"auth": {}}}) == "agent.auth"


# ---------------------------------------------------------------------------
# 5. The admin bearer is minted by `castor up` and its secret is not on disk
# ---------------------------------------------------------------------------
def test_up_mints_admin_token_without_writing_it_to_tokens_env(tmp_path):
    """`grep -c ADMIN_TOKEN tokens.env` must return 0 on a fresh robot."""
    from castor.up import ADMIN_TOKEN_DIGEST_FILE, mint_admin_token

    home = tmp_path / "robot"
    home.mkdir()
    tokens_env = home / "tokens.env"
    tokens_env.write_text("ACTUATE_TOKEN=a\nREAD_TOKEN=b\nOPENCASTOR_API_TOKEN=c\n")

    token = mint_admin_token(home)

    assert token.startswith("oc_admin_")
    assert "ADMIN_TOKEN" not in tokens_env.read_text()
    # Not the secret anywhere under the robot home — only its digest.
    digest_file = home / ADMIN_TOKEN_DIGEST_FILE
    assert digest_file.read_text().strip() == hashlib.sha256(token.encode()).hexdigest()
    assert digest_file.stat().st_mode & 0o777 == 0o600
    for path in home.rglob("*"):
        if path.is_file():
            assert token not in path.read_text(), f"admin secret leaked into {path}"


def test_runtime_accepts_the_admin_token_via_its_digest_file(client, tmp_path, monkeypatch):
    """The runtime only ever holds the digest; the bearer still verifies."""
    import castor.api as api_mod

    home = tmp_path / "robot"
    home.mkdir()
    from castor.up import mint_admin_token

    token = mint_admin_token(home)
    monkeypatch.setenv("ROBOT_HOME", str(home))
    api_mod.ADMIN_TOKEN = None
    api_mod.ADMIN_TOKEN_SHA256 = None
    api_mod.state.hitl_gate_manager = _FakeHiTLGateManager()

    resp = client.post(
        "/api/hitl/authorize",
        json={"pending_id": "p", "decision": "deny"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["authorized_by"] == "admin"


# ---------------------------------------------------------------------------
# 6. `castor doctor` prints the last champion apply with its actor
# ---------------------------------------------------------------------------
def test_doctor_reports_the_last_champion_apply_with_its_actor():
    from castor.doctor import _check_last_champion_apply

    recorder = _RecordingAudit()
    recorder.log(
        "champion_apply",
        source="api",
        outcome="applied",
        reason="ok",
        actor="admin",
        champion_source="file:/home/pi/opencastor-ops/harness-research/champion.yaml",
        candidate_id="cand-7",
        score=0.91,
        applied_keys={"max_iterations": {"from": 3, "to": 9}},
    )

    result = _check_last_champion_apply(audit=recorder)

    assert result.status == "ok"
    assert "cand-7" in result.detail
    assert "by=admin" in result.detail
    assert "champion.yaml" in result.detail


def test_doctor_says_never_when_no_champion_was_ever_applied():
    from castor.doctor import _check_last_champion_apply

    result = _check_last_champion_apply(audit=_RecordingAudit())
    assert result.status == "ok"
    assert "never" in result.detail


# ---------------------------------------------------------------------------
# 7. The approval gate records WHO
# ---------------------------------------------------------------------------
def test_approval_gate_records_the_principal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from castor.approvals import ApprovalGate

    gate = ApprovalGate({"agent": {"require_approval": True}, "physics": {"max_speed_ms": 0.1}})
    queued = gate.check({"type": "move", "linear": 5.0, "angular": 0.0})
    assert queued["status"] == "pending"

    action = gate.approve(queued["approval_id"], principal="cli:owner")

    assert action["linear"] == 5.0
    entry = [e for e in gate._queue if e["id"] == queued["approval_id"]][0]
    assert entry["approved_by"] == "cli:owner"
