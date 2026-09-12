"""OC-M-01 — a stop is only acknowledged once the robot has actually received it.

Two defects are pinned here:

1. Route drift. ``castor/cloud/bridge.py`` dispatched the cloud stop to
   ``POST /api/estop`` and the cloud resume to ``POST /api/resume``. Neither
   route has ever been defined by ``castor/api.py`` (it serves ``/api/stop``
   and ``/api/runtime/resume``), so every cloud stop 404'd.

2. A manufactured acknowledgement. The bridge wrote
   ``ack_qos='acknowledged'`` to the command document BEFORE the dispatch,
   so the datastore said the stop was acknowledged while the stop itself was
   404ing. The acknowledgement must now come only from the dispatch result.

The literal values are a contract with the shipped clients (opencastor-client
commits bd50baa / 71036c4): only ``acknowledged`` reads as confirmed, only
``stop_not_confirmed`` reads as not confirmed, and anything else (including
``queued``) reads as still queued.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

import castor.cloud.bridge as bridge_mod
from castor.cloud.bridge import ESTOP_ACK_DEADLINE_S, CastorBridge

BRIDGE_SOURCE = Path(bridge_mod.__file__).read_text()

# The three values the clients read literally. Nothing else may be written
# to ack_qos without a matching client change.
ACK_QOS_LITERALS = {"queued", "acknowledged", "stop_not_confirmed"}


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

MINIMAL_CONFIG = {
    "rrn": "RRN-00000042",
    "metadata": {"name": "TestBot", "ruri": "rcan://test/bot"},
    "firebase_uid": "uid-test-owner",
    "owner": "rrn://test-owner",
}


def _make_bridge() -> CastorBridge:
    bridge = CastorBridge(config=MINIMAL_CONFIG, firebase_project="test-project")
    bridge._db = MagicMock()
    bridge._consent = MagicMock()
    bridge._consent.is_authorized.return_value = (True, "ok")
    return bridge


def _estop_doc(sender_type: str = "human", **kwargs: Any) -> dict[str, Any]:
    return {
        "scope": "safety",
        "instruction": "ESTOP",
        "issued_at": time.time(),
        "sender_type": sender_type,
        "status": "pending",
        **kwargs,
    }


class _FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int, body: Optional[dict[str, Any]] = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = {"content-type": "application/json"}
        self.text = str(self._body)

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("POST", "http://127.0.0.1:8000/api/stop")
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=request, response=self  # type: ignore[arg-type]
            )


class _FakeClient:
    """Context-manager stand-in for httpx.Client that records every POST."""

    def __init__(self, calls: list[dict[str, Any]], response: Any, raises: Any = None) -> None:
        self._calls = calls
        self._response = response
        self._raises = raises

    def __call__(self, *args: Any, **kwargs: Any) -> "_FakeClient":
        self._calls.append({"client_kwargs": kwargs})
        return self

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def post(self, url: str, **kwargs: Any) -> Any:
        self._calls.append({"method": "POST", "url": url, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._response

    def get(self, url: str, **kwargs: Any) -> Any:
        self._calls.append({"method": "GET", "url": url, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._response


def _run_command(
    bridge: CastorBridge,
    doc: dict[str, Any],
    response: Any = None,
    raises: Any = None,
) -> tuple[MagicMock, list[dict[str, Any]]]:
    """Run _execute_command with httpx stubbed; return (cmd_ref, http calls)."""
    cmd_id = str(uuid.uuid4())
    cmd_ref = MagicMock()
    cmd_ref.update = MagicMock()
    bridge._commands_ref = MagicMock(return_value=MagicMock())
    bridge._commands_ref().document = MagicMock(return_value=cmd_ref)

    calls: list[dict[str, Any]] = []
    fake = _FakeClient(calls, response if response is not None else _FakeResponse(200), raises)
    with patch("httpx.Client", fake):
        bridge._execute_command(cmd_id, doc)
    return cmd_ref, calls


def _updates(cmd_ref: MagicMock) -> list[dict[str, Any]]:
    return [call[0][0] if call[0] else {} for call in cmd_ref.update.call_args_list]


def _ack_values(cmd_ref: MagicMock) -> list[str]:
    return [u["ack_qos"] for u in _updates(cmd_ref) if "ack_qos" in u]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Route drift — the dispatch must name routes the runtime actually serves
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatchRoutesResolve:
    def test_estop_route_exists_in_app(self) -> None:
        """Every gateway route the bridge dispatches to exists on the app.

        This is the assertion that turns route drift into a test failure
        rather than a stop that silently 404s. It fails on the pre-fix code
        for /api/estop and /api/resume.
        """
        from castor.api import app

        served = {getattr(route, "path", None) for route in app.routes}
        dispatched = sorted(set(re.findall(r"\{self\.gateway_url\}(/api/[\w/]+)", BRIDGE_SOURCE)))

        assert dispatched, "no gateway routes found in bridge.py — regex drifted"
        missing = [path for path in dispatched if path not in served]
        assert missing == [], f"bridge.py dispatches to routes castor.api does not serve: {missing}"

    def test_stop_route_is_the_one_the_runtime_serves(self) -> None:
        """The literal /api/estop and /api/resume are gone from the dispatch."""
        dispatched = set(re.findall(r"\{self\.gateway_url\}(/api/[\w/]+)", BRIDGE_SOURCE))
        assert "/api/estop" not in dispatched
        assert "/api/resume" not in dispatched
        assert "/api/stop" in dispatched
        assert "/api/runtime/resume" in dispatched

    def test_estop_dispatch_posts_to_api_stop(self) -> None:
        bridge = _make_bridge()
        _, calls = _run_command(
            bridge, _estop_doc(), response=_FakeResponse(200, {"status": "stopped"})
        )
        posts = [c for c in calls if c.get("method") == "POST"]
        assert posts, "the ESTOP was never dispatched"
        assert posts[0]["url"].endswith("/api/stop"), posts[0]["url"]

    def test_resume_dispatch_posts_to_runtime_resume(self) -> None:
        bridge = _make_bridge()
        doc = _estop_doc()
        doc["instruction"] = "RESUME"
        _, calls = _run_command(bridge, doc, response=_FakeResponse(200, {"paused": False}))
        posts = [c for c in calls if c.get("method") == "POST"]
        assert posts, "the resume was never dispatched"
        assert posts[0]["url"].endswith("/api/runtime/resume"), posts[0]["url"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. The acknowledgement comes from the dispatch, never from a datastore write
# ─────────────────────────────────────────────────────────────────────────────


class TestAckComesFromDispatch:
    def test_ack_is_not_written_before_dispatch(self) -> None:
        """A dispatch that raises must never leave 'acknowledged' behind."""
        import httpx

        bridge = _make_bridge()
        cmd_ref, calls = _run_command(
            bridge, _estop_doc(), raises=httpx.ConnectError("gateway is down")
        )
        acks = _ack_values(cmd_ref)

        assert "acknowledged" not in acks, f"manufactured acknowledgement: {acks}"
        assert "stop_not_confirmed" in acks, acks
        not_confirmed = [u for u in _updates(cmd_ref) if u.get("ack_qos") == "stop_not_confirmed"]
        assert not_confirmed[0]["ack_qos_error"] == "ConnectError"

    def test_404_from_the_gateway_is_not_confirmed(self) -> None:
        """The exact pre-fix failure: the stop route answers 404."""
        bridge = _make_bridge()
        cmd_ref, _ = _run_command(bridge, _estop_doc(), response=_FakeResponse(404, {}))
        acks = _ack_values(cmd_ref)

        assert "acknowledged" not in acks, f"a 404 was reported as acknowledged: {acks}"
        assert acks[-1] == "stop_not_confirmed", acks
        not_confirmed = [u for u in _updates(cmd_ref) if u.get("ack_qos") == "stop_not_confirmed"]
        assert not_confirmed[-1]["ack_qos_error"] == "http_404"

    def test_ack_carries_receipt_on_success(self) -> None:
        bridge = _make_bridge()
        cmd_ref, _ = _run_command(
            bridge, _estop_doc(), response=_FakeResponse(200, {"status": "stopped"})
        )
        acknowledged = [u for u in _updates(cmd_ref) if u.get("ack_qos") == "acknowledged"]

        assert acknowledged, f"no acknowledgement written: {_ack_values(cmd_ref)}"
        assert acknowledged[-1]["stop_receipt"] == {"status": "stopped"}
        assert "ack_qos_at" in acknowledged[-1]

    def test_queued_is_written_before_the_dispatch(self) -> None:
        """The pre-dispatch write says queued, and it says it first."""
        bridge = _make_bridge()
        cmd_ref, _ = _run_command(
            bridge, _estop_doc(), response=_FakeResponse(200, {"status": "stopped"})
        )
        acks = _ack_values(cmd_ref)
        assert acks[0] == "queued", acks
        assert acks[-1] == "acknowledged", acks

    def test_only_the_three_agreed_literals_are_written(self) -> None:
        """The client maps literals; an unknown one would silently read queued."""
        written = set(re.findall(r'"ack_qos":\s*"([a-z_]+)"', BRIDGE_SOURCE))
        assert written <= ACK_QOS_LITERALS, f"unknown ack_qos literal(s): {written - ACK_QOS_LITERALS}"

    def test_non_estop_command_writes_no_ack_qos(self) -> None:
        bridge = _make_bridge()
        doc = _estop_doc()
        doc["scope"] = "chat"
        doc["instruction"] = "say hello"
        cmd_ref, _ = _run_command(bridge, doc, response=_FakeResponse(200, {"ok": True}))
        assert _ack_values(cmd_ref) == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. A stop that never left the bridge is not confirmed either
# ─────────────────────────────────────────────────────────────────────────────


class TestRefusedStopIsNotConfirmed:
    def test_r2ram_denied_estop_is_not_confirmed(self) -> None:
        """An anonymous ESTOP is refused by R2RAM and never dispatched."""
        bridge = _make_bridge()
        bridge._consent.is_authorized.return_value = (False, "anonymous_estop_blocked")
        cmd_ref, calls = _run_command(bridge, _estop_doc(), response=_FakeResponse(200))

        assert not [c for c in calls if c.get("method") == "POST"], "a denied stop was dispatched"
        acks = _ack_values(cmd_ref)
        assert "acknowledged" not in acks, acks
        assert acks[-1] == "stop_not_confirmed", acks

    def test_federation_denied_estop_is_not_confirmed(self) -> None:
        bridge = _make_bridge()
        with patch.object(bridge, "_check_federation", return_value=False):
            cmd_ref, calls = _run_command(bridge, _estop_doc(), response=_FakeResponse(200))

        assert not [c for c in calls if c.get("method") == "POST"]
        assert _ack_values(cmd_ref)[-1] == "stop_not_confirmed"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Ordering property — no gate stands between an ESTOP and the robot
# ─────────────────────────────────────────────────────────────────────────────


class TestOrderingProperty:
    def test_offline_mode_does_not_block_the_stop(self) -> None:
        """OC-M-01 S3: the offline gate must not suppress an ESTOP."""
        bridge = _make_bridge()
        bridge._offline_mode = True
        cmd_ref, calls = _run_command(
            bridge, _estop_doc(), response=_FakeResponse(200, {"status": "stopped"})
        )

        posts = [c for c in calls if c.get("method") == "POST"]
        assert posts and posts[0]["url"].endswith("/api/stop")
        assert _ack_values(cmd_ref)[-1] == "acknowledged"

    def test_offline_mode_blocks_a_non_estop_command(self) -> None:
        bridge = _make_bridge()
        bridge._offline_mode = True
        doc = _estop_doc()
        doc["scope"] = "chat"
        doc["instruction"] = "say hello"
        cmd_ref, calls = _run_command(bridge, doc, response=_FakeResponse(200))

        assert not [c for c in calls if c.get("method") == "POST"]
        assert any(u.get("status") == "denied" for u in _updates(cmd_ref))

    def test_estop_dispatch_timeout_is_the_deadline(self) -> None:
        """ESTOP_ACK_DEADLINE_S is the transport deadline, not a certificate."""
        bridge = _make_bridge()
        _, calls = _run_command(
            bridge, _estop_doc(), response=_FakeResponse(200, {"status": "stopped"})
        )
        client_kwargs = [c["client_kwargs"] for c in calls if "client_kwargs" in c]
        assert client_kwargs, calls
        assert client_kwargs[0].get("timeout") == ESTOP_ACK_DEADLINE_S


# ─────────────────────────────────────────────────────────────────────────────
# 5. The fleet path — a cloud-relayed stop gets exactly the same treatment
# ─────────────────────────────────────────────────────────────────────────────


class TestFleetPath:
    def test_cloud_relayed_estop_is_not_confirmed_on_error(self) -> None:
        """A fleet stop arrives as sender_type='cloud_function'."""
        bridge = _make_bridge()
        cmd_ref, _ = _run_command(
            bridge,
            _estop_doc(sender_type="cloud_function"),
            response=_FakeResponse(500, {}),
        )
        not_confirmed = [u for u in _updates(cmd_ref) if u.get("ack_qos") == "stop_not_confirmed"]

        assert "acknowledged" not in _ack_values(cmd_ref)
        assert not_confirmed, _updates(cmd_ref)
        assert not_confirmed[-1]["cloud_relay"] is True
        assert not_confirmed[-1]["ack_qos_error"] == "http_500"

    def test_cloud_relayed_estop_acknowledged_on_2xx(self) -> None:
        bridge = _make_bridge()
        cmd_ref, _ = _run_command(
            bridge,
            _estop_doc(sender_type="cloud_function"),
            response=_FakeResponse(200, {"status": "stopped"}),
        )
        acknowledged = [u for u in _updates(cmd_ref) if u.get("ack_qos") == "acknowledged"]

        assert acknowledged, _updates(cmd_ref)
        assert acknowledged[-1]["cloud_relay"] is True
        assert acknowledged[-1]["stop_receipt"] == {"status": "stopped"}


# ─────────────────────────────────────────────────────────────────────────────
# 6. The failure-describer
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc,expected",
    [
        (ValueError("nope"), "ValueError"),
        (TimeoutError(), "TimeoutError"),
    ],
)
def test_describe_dispatch_failure_names_the_class(exc: Exception, expected: str) -> None:
    assert bridge_mod._describe_dispatch_failure(exc) == expected
