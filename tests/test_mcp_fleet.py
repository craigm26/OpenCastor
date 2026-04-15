"""Tests for fleet-level MCP tools (castor/mcp_fleet.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── fixtures ──────────────────────────────────────────────────────────────────

FLEET_RESPONSE = {
    "fleet": [
        {
            "rrn": "RRN-000000000001",
            "name": "Bob",
            "online": True,
            "version": "v2026.3.29.1",
            "telemetry": {
                "system": {"cpu_temp_c": 48.5, "ram_used_pct": 62.0},
                "model_runtime": {
                    "active_model": "claude-opus-4-6",
                    "provider": "anthropic",
                },
                "brain_active_model": "claude-opus-4-6",
                "opencastor_version": "2026.3.29.1",
            },
            "loa_enforcement": True,
        },
        {
            "rrn": "RRN-000000000002",
            "name": "Alex",
            "online": False,
            "version": "v2026.3.17.13",
            "telemetry": {},
            "loa_enforcement": False,
        },
    ]
}

ESTOP_OK = {"status": "ok", "stopped": True}
COMMAND_OK = {"cmd_id": "abc123", "status": "accepted"}


def _mock_get(url: str, **kwargs):  # noqa: ANN001
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    if "/api/fleet" in url:
        mock.json.return_value = FLEET_RESPONSE
    elif "/api/status" in url:
        mock.json.return_value = {"status": {"online": True}}
    else:
        mock.json.return_value = {}
    return mock


def _mock_post(url: str, **kwargs):  # noqa: ANN001
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    if "/api/estop" in url:
        mock.json.return_value = ESTOP_OK
    elif "/api/command" in url:
        mock.json.return_value = COMMAND_OK
    return mock


# ── fleet_status ──────────────────────────────────────────────────────────────


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
@patch("httpx.get", side_effect=_mock_get)
def test_fleet_status_returns_summary(mock_get, mock_url):
    from castor.mcp_fleet import fleet_status

    result = fleet_status()
    assert "fleet" in result
    assert "summary" in result
    assert result["summary"]["total"] == 2
    assert result["summary"]["online"] == 1
    assert result["summary"]["offline"] == 1


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
@patch("httpx.get", side_effect=_mock_get)
def test_fleet_status_per_robot_fields(mock_get, mock_url):
    from castor.mcp_fleet import fleet_status

    result = fleet_status()
    bob = next(r for r in result["fleet"] if r["rrn"] == "RRN-000000000001")
    assert bob["name"] == "Bob"
    assert bob["online"] is True
    assert bob["cpu_temp_c"] == 48.5
    assert bob["active_model"] == "claude-opus-4-6"


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
@patch("httpx.get", side_effect=Exception("conn refused"))
def test_fleet_status_gateway_error(mock_get, mock_url):
    from castor.mcp_fleet import fleet_status

    result = fleet_status()
    assert "error" in result


# ── fleet_estop ───────────────────────────────────────────────────────────────


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
@patch("castor.mcp_fleet._fleet_robots", return_value=FLEET_RESPONSE["fleet"])
@patch("httpx.post", side_effect=_mock_post)
def test_fleet_estop_all_online(mock_post, mock_fleet, mock_url):
    from castor.mcp_fleet import fleet_estop

    result = fleet_estop()
    # Only online robots (Bob) should be stopped
    assert result["summary"]["stopped"] == 1


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
@patch("httpx.post", side_effect=_mock_post)
def test_fleet_estop_specific_rrn(mock_post, mock_url):
    from castor.mcp_fleet import fleet_estop

    result = fleet_estop(rrns=["RRN-000000000001"])
    assert len(result["results"]) == 1
    assert result["results"][0]["rrn"] == "RRN-000000000001"
    assert result["results"][0]["ok"] is True


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
@patch("castor.mcp_fleet._fleet_robots", return_value=FLEET_RESPONSE["fleet"])
@patch("httpx.post", side_effect=_mock_post)
def test_fleet_estop_loa_0_always_passes(mock_post, mock_fleet, mock_url):
    """ESTOP must work even if _CLIENT_LOA is 0."""
    import castor.mcp_server as srv

    original = srv._CLIENT_LOA
    try:
        srv._CLIENT_LOA = 0
        from castor.mcp_fleet import fleet_estop

        result = fleet_estop(rrns=["RRN-000000000001"])
        assert result["results"][0]["ok"] is True
    finally:
        srv._CLIENT_LOA = original


# ── fleet_broadcast ───────────────────────────────────────────────────────────


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
@patch("castor.mcp_fleet._fleet_robots", return_value=FLEET_RESPONSE["fleet"])
@patch("castor.mcp_server._CLIENT_LOA", 1)
def test_fleet_broadcast_dispatches_to_online(mock_fleet, mock_url):
    import castor.mcp_server as srv

    srv._CLIENT_LOA = 1

    async def _fake_send(base, rrn, instruction, scope):
        return {"rrn": rrn, "ok": True, "result": {}}

    with patch("castor.mcp_fleet._send_command_async", side_effect=_fake_send):
        from castor.mcp_fleet import fleet_broadcast

        result = fleet_broadcast(instruction="STATUS", scope="status")
        assert result["summary"]["sent"] == 1  # only Bob is online
        assert result["summary"]["ok"] == 1


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
def test_fleet_broadcast_requires_loa1(mock_url):
    import castor.mcp_server as srv

    srv._CLIENT_LOA = 0
    from castor.mcp_fleet import fleet_broadcast

    with pytest.raises(PermissionError):
        fleet_broadcast(instruction="STATUS")

    srv._CLIENT_LOA = 3  # restore


# ── fleet_navigate ────────────────────────────────────────────────────────────


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
def test_fleet_navigate_empty_waypoints(mock_url):
    import castor.mcp_server as srv

    srv._CLIENT_LOA = 1
    from castor.mcp_fleet import fleet_navigate

    result = fleet_navigate({})
    assert result["results"] == []


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
def test_fleet_navigate_dispatches_per_robot(mock_url):
    import castor.mcp_server as srv

    srv._CLIENT_LOA = 1

    async def _fake_send(base, rrn, instruction, scope):
        assert "navigate" in instruction
        return {"rrn": rrn, "ok": True, "result": {}}

    with patch("castor.mcp_fleet._send_command_async", side_effect=_fake_send):
        from castor.mcp_fleet import fleet_navigate

        result = fleet_navigate({"RRN-000000000001": {"x": 1.0, "y": 2.0, "heading": 45.0}})
        assert result["summary"]["dispatched"] == 1
        assert result["summary"]["ok"] == 1


@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
def test_fleet_navigate_requires_loa1(mock_url):
    import castor.mcp_server as srv

    srv._CLIENT_LOA = 0
    from castor.mcp_fleet import fleet_navigate

    with pytest.raises(PermissionError):
        fleet_navigate({"RRN-000000000001": {"x": 0, "y": 0, "heading": 0}})

    srv._CLIENT_LOA = 3  # restore


@patch("castor.mcp_fleet._fleet_robots", return_value=[])
@patch("castor.mcp_fleet._gateway_url", return_value="http://localhost:8001")
def test_fleet_estop_no_robots(mock_url, mock_fleet):
    from castor.mcp_fleet import fleet_estop

    result = fleet_estop()
    assert result["results"] == []
    assert "note" in result
