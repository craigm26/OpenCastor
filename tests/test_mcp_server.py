import asyncio

import pytest
from fastapi.testclient import TestClient

from castor import mcp_server


@pytest.fixture
def client():
    return TestClient(mcp_server.app)


def test_tools_list_contains_expected_tools(client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 200
    body = response.json()
    names = [t["name"] for t in body["result"]["tools"]]
    assert "status_health" in names
    assert "command_dispatch" in names
    assert "stop_estop" in names
    assert "recent_episodes_telemetry" in names
    assert "config_validate_lint" in names


def test_unknown_tool_returns_jsonrpc_error(client):
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "does_not_exist", "arguments": {}},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == -32601


def test_command_dispatch_requires_instruction():
    with pytest.raises(ValueError):
        asyncio.run(mcp_server._tool_command_dispatch({}))
