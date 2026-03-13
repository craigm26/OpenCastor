"""OpenCastor MCP server.

Provides a minimal HTTP transport for MCP-style tool calls and reuses
existing API/CLI business logic for runtime operations.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from castor import cli as castor_cli

app = FastAPI(title="OpenCastor MCP Server", version="0.1.0")


class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    method: str
    params: dict[str, Any] = {}


def _jsonrpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class _FakeClient:
    host = "127.0.0.1"


class _FakeState:
    jwt_role = "admin"


class _FakeRequest:
    client = _FakeClient()
    state = _FakeState()
    query_params: dict[str, str] = {}


def _require_mcp_auth(auth_header: Optional[str]) -> None:
    expected = os.getenv("OPENCASTOR_MCP_TOKEN", "")
    if not expected:
        return
    if auth_header != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid or missing MCP token")


async def _tool_status_health(arguments: dict[str, Any]) -> dict[str, Any]:
    from castor import api as gateway_api

    fake_request = _FakeRequest()
    return {
        "health": await gateway_api.health(),
        "status": await gateway_api.get_status(fake_request),
    }


async def _tool_command_dispatch(arguments: dict[str, Any]) -> dict[str, Any]:
    instruction = (arguments or {}).get("instruction", "").strip()
    if not instruction:
        raise ValueError("'instruction' is required")

    from castor import api as gateway_api

    req = gateway_api.CommandRequest(
        instruction=instruction,
        image_base64=(arguments or {}).get("image_base64"),
    )
    return await gateway_api.send_command(req, _FakeRequest())


async def _tool_stop_estop(arguments: dict[str, Any]) -> dict[str, Any]:
    from castor import api as gateway_api

    mode = (arguments or {}).get("mode", "stop")
    if mode == "clear":
        return await gateway_api.clear_estop()
    return await gateway_api.emergency_stop()


async def _tool_recent_episodes_telemetry(arguments: dict[str, Any]) -> dict[str, Any]:
    from castor import api as gateway_api

    limit = int((arguments or {}).get("limit", 20))
    source = (arguments or {}).get("source")

    episodes = await gateway_api.list_episodes(limit=limit, source=source)
    telemetry = None
    try:
        telemetry = await gateway_api.fs_proc()
    except HTTPException:
        telemetry = {"available": False}

    return {"episodes": episodes, "telemetry": telemetry}


def _run_cli_handler(handler, *, config_path: str) -> dict[str, Any]:
    args = argparse.Namespace(config=config_path, category=None, json=False, strict=False)
    buf = io.StringIO()
    exit_code = 0
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            handler(args)
        except SystemExit as exc:
            exit_code = int(exc.code) if isinstance(exc.code, int) else 1
    return {"exit_code": exit_code, "output": buf.getvalue()}


async def _tool_config_validate_lint(arguments: dict[str, Any]) -> dict[str, Any]:
    config_path = (arguments or {}).get("config", "robot.rcan.yaml")
    lint = _run_cli_handler(castor_cli.cmd_lint, config_path=config_path)
    validate = _run_cli_handler(castor_cli.cmd_validate, config_path=config_path)
    return {
        "config": config_path,
        "lint": lint,
        "validate": validate,
    }


_TOOLS = {
    "status_health": _tool_status_health,
    "command_dispatch": _tool_command_dispatch,
    "stop_estop": _tool_stop_estop,
    "recent_episodes_telemetry": _tool_recent_episodes_telemetry,
    "config_validate_lint": _tool_config_validate_lint,
}

_TOOL_SCHEMAS = [
    {
        "name": "status_health",
        "description": "Read runtime health and status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "command_dispatch",
        "description": "Dispatch a natural-language instruction through the active brain.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string"},
                "image_base64": {"type": "string"},
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "stop_estop",
        "description": "Stop motors immediately, or clear an e-stop latch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["stop", "clear"],
                    "default": "stop",
                }
            },
        },
    },
    {
        "name": "recent_episodes_telemetry",
        "description": "Fetch recent memory episodes and /proc telemetry snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "source": {"type": "string"},
            },
        },
    },
    {
        "name": "config_validate_lint",
        "description": "Run CLI config lint and RCAN validation for a config file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config": {"type": "string", "default": "robot.rcan.yaml"},
            },
        },
    },
]


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "castor-mcp"}


@app.post("/mcp")
async def mcp_endpoint(payload: MCPRequest, authorization: str | None = Header(default=None)):
    _require_mcp_auth(authorization)

    try:
        if payload.method == "initialize":
            return _jsonrpc_result(
                payload.id,
                {
                    "protocolVersion": "2025-03-26",
                    "serverInfo": {"name": "castor-mcp", "version": "0.1.0"},
                },
            )

        if payload.method == "tools/list":
            return _jsonrpc_result(payload.id, {"tools": _TOOL_SCHEMAS})

        if payload.method == "tools/call":
            params = payload.params or {}
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            tool = _TOOLS.get(tool_name)
            if tool is None:
                return _jsonrpc_error(payload.id, -32601, f"Unknown tool: {tool_name}")
            result = await tool(arguments)
            return _jsonrpc_result(
                payload.id,
                {"content": [{"type": "text", "text": str(result)}], "structuredContent": result},
            )

        return _jsonrpc_error(payload.id, -32601, f"Unsupported method: {payload.method}")
    except ValueError as exc:
        return _jsonrpc_error(payload.id, -32602, str(exc))
    except HTTPException as exc:
        return _jsonrpc_error(payload.id, exc.status_code, str(exc.detail))
    except Exception as exc:  # pragma: no cover - defensive error bridge
        return _jsonrpc_error(payload.id, -32000, f"Tool execution failed: {exc}")


def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run("castor.mcp_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
