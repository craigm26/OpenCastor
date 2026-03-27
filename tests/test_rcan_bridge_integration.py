"""Integration tests for RCAN bridge components.

Covers:
  - sdk_bridge.parse_inbound() with spec and internal formats
  - ruri_to_robot_uri() / robot_uri_to_ruri() round-trip
  - http_transport.send_message() with a mock HTTP server
  - action_to_commitment_record() creates valid records
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# parse_inbound — spec vs internal format
# ---------------------------------------------------------------------------


def test_parse_inbound_spec_format():
    """parse_inbound should return a rcan.RCANMessage for spec-format bodies."""
    from rcan import RCANMessage as SpecMsg

    from castor.rcan.sdk_bridge import parse_inbound

    body = {
        "rcan": "1.2",
        "cmd": "status",
        "target": "rcan://registry.rcan.dev/acme/arm/v1/unit-001",
        "sender": "rcan://registry.rcan.dev/ops/console/v1/cli-001",
        "params": {},
    }
    result = parse_inbound(body)
    assert isinstance(result, SpecMsg)
    assert result.cmd == "status"


def test_parse_inbound_internal_format():
    """parse_inbound should return an OpenCastor RCANMessage for internal-format bodies."""
    from castor.rcan.message import MessageType
    from castor.rcan.message import RCANMessage as OCMessage
    from castor.rcan.sdk_bridge import parse_inbound

    body = {
        "type": "COMMAND",
        "source": "rcan://local/unknown/arm/so-arm101",
        "target": "rcan://local/unknown/arm/so-arm101",
        "payload": {"cmd": "move", "params": {}},
    }
    result = parse_inbound(body)
    assert isinstance(result, OCMessage)
    assert result.type == MessageType.COMMAND


def test_parse_inbound_spec_returns_spec_object():
    """Spec-format should round-trip through parse_inbound without data loss."""
    from rcan import RCANMessage as SpecMsg

    from castor.rcan.sdk_bridge import parse_inbound

    body = {
        "rcan": "1.2",
        "cmd": "arm_pose",
        "target": "rcan://registry.rcan.dev/acme/arm/v1/unit-001",
        "sender": "rcan://registry.rcan.dev/ops/console/v1/cli-001",
        "params": {"joint_positions": {"j1": 0.5}},
        "confidence": 0.92,
    }
    result = parse_inbound(body)
    assert isinstance(result, SpecMsg)
    assert result.params == {"joint_positions": {"j1": 0.5}}
    assert result.confidence == 0.92


# ---------------------------------------------------------------------------
# ruri_to_robot_uri / robot_uri_to_ruri round-trip
# ---------------------------------------------------------------------------


def test_round_trip_ruri_to_robot_uri_and_back():
    """RURI → RobotURI → RURI should preserve manufacturer, model, instance."""
    from castor.rcan.ruri import RURI
    from castor.rcan.sdk_bridge import robot_uri_to_ruri, ruri_to_robot_uri

    original = RURI(manufacturer="acme", model="arm", instance="unit-001")
    robot_uri = ruri_to_robot_uri(original)
    recovered = robot_uri_to_ruri(robot_uri)

    assert recovered.manufacturer == original.manufacturer
    assert recovered.model == original.model
    assert recovered.instance == original.instance


def test_round_trip_preserves_spec_string():
    """Spec URI string → RobotURI → RURI → ruri_to_robot_uri should be stable."""
    from rcan import RobotURI

    from castor.rcan.sdk_bridge import robot_uri_to_ruri, ruri_to_robot_uri

    uri_str = "rcan://registry.rcan.dev/acme/arm/v2/unit-001"
    robot_uri = RobotURI.parse(uri_str)
    ruri = robot_uri_to_ruri(robot_uri)
    back = ruri_to_robot_uri(ruri)

    assert back.manufacturer == "acme"
    assert back.model == "arm"
    assert back.device_id == "unit-001"


def test_ruri_to_robot_uri_from_string():
    """ruri_to_robot_uri should handle spec-format string input directly."""
    from castor.rcan.sdk_bridge import ruri_to_robot_uri

    result = ruri_to_robot_uri("rcan://registry.rcan.dev/acme/arm/v1/unit-007")
    assert result.manufacturer == "acme"
    assert result.device_id == "unit-007"


# ---------------------------------------------------------------------------
# http_transport.send_message — mock HTTP server
# ---------------------------------------------------------------------------


def test_send_message_success():
    """send_message should POST JSON and return parsed response dict."""
    from castor.rcan.http_transport import send_message

    mock_response_data = json.dumps({"status": "ok", "received": True}).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = mock_response_data

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = send_message("robot.local", {"cmd": "status", "rcan": "1.2"})

    assert result is not None
    assert result["status"] == "ok"


def test_send_message_network_error_returns_none():
    """send_message should return None on network failure (graceful degradation)."""
    import urllib.error

    from castor.rcan.http_transport import send_message

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("unreachable")):
        result = send_message("unreachable.local", {"cmd": "ping"})

    assert result is None


def test_send_message_http_error_returns_none():
    """send_message should return None on HTTP error response."""
    import urllib.error

    from castor.rcan.http_transport import send_message

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.HTTPError(None, 403, "Forbidden", {}, None),
    ):
        result = send_message("robot.local", {"cmd": "admin"})

    assert result is None


# ---------------------------------------------------------------------------
# action_to_commitment_record — creates valid records
# ---------------------------------------------------------------------------


def test_action_to_commitment_record_basic():
    """action_to_commitment_record should return a CommitmentRecord with correct fields."""
    from rcan import CommitmentRecord

    from castor.rcan.sdk_bridge import action_to_commitment_record

    record = action_to_commitment_record(
        action_type="move_forward",
        params={"speed": 0.3},
        robot_uri_str="rcan://registry.rcan.dev/acme/arm/v1/unit-001",
        confidence=0.85,
        model_identity="gpt-4o",
        operator="admin",
        safety_approved=True,
    )

    assert isinstance(record, CommitmentRecord)
    assert record.action == "move_forward"
    assert record.params == {"speed": 0.3}
    assert record.confidence == 0.85
    assert record.safety_approved is True


def test_action_to_commitment_record_safety_blocked():
    """action_to_commitment_record should correctly record a safety-blocked action."""
    from castor.rcan.sdk_bridge import action_to_commitment_record

    record = action_to_commitment_record(
        action_type="high_speed_move",
        params={"speed": 2.0},
        robot_uri_str="rcan://registry.rcan.dev/acme/arm/v1/unit-001",
        confidence=0.91,
        safety_approved=False,
        safety_reason="Velocity exceeds safe threshold",
    )

    assert record.safety_approved is False
    assert "Velocity" in record.safety_reason


def test_action_to_commitment_record_has_timestamp():
    """CommitmentRecord should have a non-zero timestamp."""
    from castor.rcan.sdk_bridge import action_to_commitment_record

    record = action_to_commitment_record(
        action_type="probe",
        params={},
        robot_uri_str="rcan://registry.rcan.dev/acme/arm/v1/unit-001",
    )

    assert record.timestamp > 0
