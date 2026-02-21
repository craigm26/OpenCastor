"""Tests for castor/tools.py — LLM function/tool calling registry (issue #97)."""

import json

import pytest

from castor.tools import ToolDefinition, ToolRegistry, ToolResult


# ── ToolDefinition ────────────────────────────────────────────────────────────

def test_to_openai_schema():
    td = ToolDefinition(
        name="move",
        description="Move the robot",
        fn=lambda linear, angular: None,
        parameters={
            "linear": {"type": "number", "description": "Speed -1 to 1", "required": True},
            "angular": {"type": "number", "description": "Turn rate", "required": False},
        },
    )
    schema = td.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "move"
    assert "linear" in schema["function"]["parameters"]["properties"]
    assert "linear" in schema["function"]["parameters"]["required"]
    assert "angular" not in schema["function"]["parameters"]["required"]


def test_to_anthropic_schema():
    td = ToolDefinition(
        name="ping",
        description="Ping the robot",
        fn=lambda: "pong",
    )
    schema = td.to_anthropic_schema()
    assert schema["name"] == "ping"
    assert "input_schema" in schema


# ── ToolResult ────────────────────────────────────────────────────────────────

def test_tool_result_ok():
    r = ToolResult("ping", "pong")
    assert r.ok is True
    assert r.result == "pong"
    assert r.error is None


def test_tool_result_error():
    r = ToolResult("ping", None, error="not found")
    assert r.ok is False
    assert "ping" in repr(r)


def test_tool_result_to_dict():
    r = ToolResult("move", {"type": "move"}, duration_ms=50.5)
    d = r.to_dict()
    assert d["tool"] == "move"
    assert d["ok"] is True
    assert d["duration_ms"] == 50.5


# ── ToolRegistry ──────────────────────────────────────────────────────────────

def test_register_and_call():
    reg = ToolRegistry()
    reg.register("double", fn=lambda x: x * 2, description="double x",
                 parameters={"x": {"type": "number", "description": "input"}})
    result = reg.call("double", x=5)
    assert result.ok
    assert result.result == 10


def test_call_unknown_tool():
    reg = ToolRegistry()
    result = reg.call("does_not_exist")
    assert not result.ok
    assert "Unknown tool" in result.error


def test_call_raises_gracefully():
    reg = ToolRegistry()
    reg.register("fail", fn=lambda: 1 / 0, description="always fails")
    result = reg.call("fail")
    assert not result.ok
    assert "division by zero" in result.error


def test_call_from_dict_openai_style():
    reg = ToolRegistry()
    reg.register("add", fn=lambda a, b: a + b, description="add two numbers",
                 parameters={"a": {"type": "number"}, "b": {"type": "number"}})
    result = reg.call_from_dict({
        "name": "add",
        "arguments": json.dumps({"a": 3, "b": 4}),
    })
    assert result.result == 7


def test_call_from_dict_anthropic_style():
    reg = ToolRegistry()
    reg.register("greet", fn=lambda name: f"hello {name}", description="greet",
                 parameters={"name": {"type": "string"}})
    result = reg.call_from_dict({
        "name": "greet",
        "input": {"name": "world"},
    })
    assert result.result == "hello world"


def test_to_openai_tools():
    reg = ToolRegistry()
    tools = reg.to_openai_tools()
    assert isinstance(tools, list)
    assert all(t["type"] == "function" for t in tools)


def test_to_anthropic_tools():
    reg = ToolRegistry()
    tools = reg.to_anthropic_tools()
    assert isinstance(tools, list)
    assert all("input_schema" in t for t in tools)


def test_builtins_registered():
    reg = ToolRegistry()
    assert "get_status" in reg.list_tools()
    assert "take_snapshot" in reg.list_tools()
    assert "announce_text" in reg.list_tools()
    assert "get_distance" in reg.list_tools()


def test_len():
    reg = ToolRegistry()
    count = len(reg)
    assert count >= 4  # at least the 4 builtins
    reg.register("extra", fn=lambda: None, description="extra")
    assert len(reg) == count + 1


def test_register_from_config():
    cfg = {
        "tools": [
            {"name": "custom_tool", "description": "A custom tool", "returns": "string"},
            {"name": "get_status"},  # already registered — should be skipped
        ]
    }
    reg = ToolRegistry(config=cfg)
    assert "custom_tool" in reg.list_tools()
    # get_status should still be the real built-in, not overwritten
    result = reg.call("get_status")
    assert result.ok  # real builtin returns without error
