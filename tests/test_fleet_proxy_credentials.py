"""The fleet proxies must never lend this robot's own credential to a peer.

``/api/fleet/{ruri}/command`` and ``/api/fleet/{ruri}/status`` relay to an
address that came from an mDNS answer, and an mDNS answer is unauthenticated:
anything on the LAN can claim any RURI. This robot's ``OPENCASTOR_API_TOKEN``
maps to role ``admin`` in :func:`castor.api.verify_token`, so attaching it to
such a relay hands admin on THIS robot to whoever answered the query.

Two properties are pinned here:

1. No outbound relay carries this robot's token. The caller supplies the peer's
   own credential, or the request is refused 401 before any HTTP client exists.
2. Discovery is a hint, not an authorisation. A RURI has to appear under the
   generated config key ``fleet.peers`` as well as in the discovery table, or
   the relay 404s with reason ``peer_not_declared``.

The reason codes are asserted against the gateway's standard error envelope
(``{"error": ..., "code": "HTTP_401", ...}``), which is what a caller actually
sees: ``register_error_handlers`` flattens an HTTPException's dict detail into
that ``error`` string.
"""

import collections
import contextlib
import hashlib
import time

import pytest
from starlette.testclient import TestClient

MASTER_TOKEN = "master-admin-token-do-not-lend"  # this robot's own static token
PEER_TOKEN = "peer-supplied-token"  # the credential a caller must bring
PEER_RURI = "rcan:declared-peer"
UNDECLARED_RURI = "rcan:mdns-stranger"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _RecordingAudit:
    """Stands in for the AuditLog singleton; keeps entries in memory."""

    def __init__(self):
        self.entries = []

    def log(self, event, source="system", **kwargs):
        entry = {"event": event, "source": source}
        entry.update(kwargs)
        self.entries.append(entry)

    def relays(self):
        return [e for e in self.entries if e["event"] == "fleet_relay"]


class _RecordingAsyncClient:
    """httpx.AsyncClient stand-in that records every outbound header dict."""

    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _record(self, method, url, headers):
        type(self).calls.append(
            {"method": method, "url": url, "headers": dict(headers or {})}
        )
        return _FakeResponse()

    async def post(self, url, json=None, headers=None):
        return self._record("POST", url, headers)

    async def get(self, url, headers=None):
        return self._record("GET", url, headers)


class _FakeResponse:
    def json(self):
        return {"ok": True}


class _FakeBrowser:
    """Stand-in for the mDNS browser: it answers for anything asked of it."""

    def __init__(self, peers):
        self.peers = peers


def _mdns_peers(*ruris):
    return {
        ruri: {
            "ruri": ruri,
            "addresses": ["192.0.2.7"],
            "port": 8000,
        }
        for ruri in ruris
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)
    monkeypatch.delenv("OPENCASTOR_CONFIG", raising=False)
    monkeypatch.setenv("OPENCASTOR_API_TOKEN", MASTER_TOKEN)

    import castor.api as api_mod

    api_mod.state.config = None
    api_mod.state.mdns_browser = None
    api_mod.state.brain = None
    api_mod.state.driver = None
    api_mod.state.fs = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.state.boot_time = time.time()
    api_mod.API_TOKEN = MASTER_TOKEN

    _RecordingAsyncClient.calls = []
    yield
    api_mod.API_TOKEN = None
    api_mod.state.config = None
    api_mod.state.mdns_browser = None


@pytest.fixture()
def audit(monkeypatch):
    """Swap the audit singleton so nothing writes a log file during tests."""
    import castor.api as api_mod

    recorder = _RecordingAudit()
    monkeypatch.setattr(api_mod, "get_audit", lambda: recorder)
    return recorder


@pytest.fixture()
def http_calls(monkeypatch):
    """Record every outbound httpx request the handlers make."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _RecordingAsyncClient)
    return _RecordingAsyncClient.calls


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
def declared_peer():
    """Declare PEER_RURI in config and have mDNS answer for it."""
    import castor.api as api_mod

    api_mod.state.config = {"fleet": {"peers": [PEER_RURI]}}
    api_mod.state.mdns_browser = _FakeBrowser(_mdns_peers(PEER_RURI))
    return PEER_RURI


_AUTH = {"Authorization": f"Bearer {MASTER_TOKEN}"}


# ---------------------------------------------------------------------------
# 1. A relay without a peer credential is refused, before any HTTP happens
# ---------------------------------------------------------------------------
def test_fleet_command_without_peer_token_returns_401(
    client, audit, http_calls, declared_peer
):
    resp = client.post(
        f"/api/fleet/{declared_peer}/command",
        json={"instruction": "drive forward"},
        headers=_AUTH,
    )

    assert resp.status_code == 401
    assert "no_peer_credential" in resp.json()["error"]
    assert http_calls == [], "refused relay must not reach the network"

    refusal = audit.relays()[-1]
    assert refusal["outcome"] == "refused"
    assert refusal["reason"] == "no_peer_credential"
    assert refusal["initiator"] == "api"
    assert refusal["target_ruri"] == declared_peer


def test_fleet_status_without_peer_token_returns_401(
    client, audit, http_calls, declared_peer
):
    resp = client.get(f"/api/fleet/{declared_peer}/status", headers=_AUTH)

    assert resp.status_code == 401
    assert "no_peer_credential" in resp.json()["error"]
    assert http_calls == [], "refused relay must not reach the network"

    refusal = audit.relays()[-1]
    assert refusal["outcome"] == "refused"
    assert refusal["reason"] == "no_peer_credential"


# ---------------------------------------------------------------------------
# 2. No outbound request ever carries this robot's own token
# ---------------------------------------------------------------------------
def test_outbound_request_never_carries_api_token(
    client, audit, http_calls, declared_peer
):
    instruction = "drive forward"

    cmd = client.post(
        f"/api/fleet/{declared_peer}/command",
        json={"instruction": instruction, "token": PEER_TOKEN},
        headers=_AUTH,
    )
    assert cmd.status_code == 200

    st_query = client.get(
        f"/api/fleet/{declared_peer}/status",
        params={"peer_token": PEER_TOKEN},
        headers=_AUTH,
    )
    assert st_query.status_code == 200

    st_header = client.get(
        f"/api/fleet/{declared_peer}/status",
        headers={**_AUTH, "X-Peer-Token": PEER_TOKEN},
    )
    assert st_header.status_code == 200

    assert len(http_calls) == 3
    for call in http_calls:
        flattened = " ".join(f"{k}: {v}" for k, v in call["headers"].items())
        assert MASTER_TOKEN not in flattened, (
            f"this robot's own token leaked to a peer in {call['headers']}"
        )
        assert call["headers"]["Authorization"] == f"Bearer {PEER_TOKEN}"

    allowed = [e for e in audit.relays() if e["outcome"] == "allowed"]
    assert len(allowed) == 3
    assert allowed[0]["target_address"] == "192.0.2.7:8000"
    assert (
        allowed[0]["instruction_sha256"]
        == hashlib.sha256(instruction.encode("utf-8")).hexdigest()
    )


# ---------------------------------------------------------------------------
# 3. A discovery answer alone does not make a peer reachable
# ---------------------------------------------------------------------------
def test_mdns_peer_absent_from_fleet_peers_returns_404(client, audit, http_calls):
    import castor.api as api_mod

    # mDNS offers a stranger; config declares only the real peer.
    api_mod.state.config = {"fleet": {"peers": [PEER_RURI]}}
    api_mod.state.mdns_browser = _FakeBrowser(_mdns_peers(PEER_RURI, UNDECLARED_RURI))

    cmd = client.post(
        f"/api/fleet/{UNDECLARED_RURI}/command",
        json={"instruction": "drive forward", "token": PEER_TOKEN},
        headers=_AUTH,
    )
    assert cmd.status_code == 404
    assert "peer_not_declared" in cmd.json()["error"]

    st = client.get(
        f"/api/fleet/{UNDECLARED_RURI}/status",
        params={"peer_token": PEER_TOKEN},
        headers=_AUTH,
    )
    assert st.status_code == 404
    assert "peer_not_declared" in st.json()["error"]

    assert http_calls == [], "an undeclared peer must never be contacted"
    assert all(e["outcome"] == "refused" for e in audit.relays())


def test_empty_fleet_peers_declares_nobody(client, audit, http_calls):
    """The generated default (`fleet: {peers: []}`) relays to nobody."""
    import castor.api as api_mod

    api_mod.state.config = {"fleet": {"peers": []}}
    api_mod.state.mdns_browser = _FakeBrowser(_mdns_peers(PEER_RURI))

    resp = client.post(
        f"/api/fleet/{PEER_RURI}/command",
        json={"instruction": "drive forward", "token": PEER_TOKEN},
        headers=_AUTH,
    )
    assert resp.status_code == 404
    assert http_calls == []


# ---------------------------------------------------------------------------
# 4. agent_tools has no hardcoded peer map
# ---------------------------------------------------------------------------
def test_agent_tools_peer_urls_empty_without_config(monkeypatch):
    from castor import agent_tools

    import castor.main

    monkeypatch.setattr(castor.main, "get_shared_fs", lambda: None)
    assert agent_tools._get_peer_urls() == {}


# ---------------------------------------------------------------------------
# 5. `fleet.peers` exists by generation on every archetype
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("archetype", ["rc-car", "microduck"])
def test_generated_config_declares_fleet_peers(archetype, tmp_path):
    import yaml

    from castor.up import UpPlan, render

    plan = UpPlan(
        name="testbot",
        home=tmp_path / "testbot",
        archetype=archetype,
        rrn="RRN-LOCAL-0123456789",
        robot_uuid="00000000-0000-0000-0000-000000000000",
        base_port=8000,
    )
    rendered = yaml.safe_load(render("robot.rcan.yaml.tmpl", plan))
    assert "fleet" in rendered, f"{archetype}: generated config has no fleet block"
    assert rendered["fleet"]["peers"] == []
