from __future__ import annotations

import tempfile

from castor.harness.pattern import (
    InitializerExecutor,
    MultiAgent,
    SingleAgentSupervisor,
    get_pattern,
)


def test_single_agent_supervisor_run():
    p = SingleAgentSupervisor(max_retries=5)
    result = p.run()
    assert result["pattern"] == "single_agent_supervisor"
    assert result["status"] == "ok"
    assert result["max_retries"] == 5


def test_initializer_executor_ledger_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = InitializerExecutor(ledger_dir=tmpdir)
        data = {"task": "write code", "context": "python"}
        p.write_ledger("sess_001", data)
        loaded = p.read_ledger("sess_001")
        assert loaded == data


def test_multi_agent_parallel_mode():
    p = MultiAgent(roles=["planner", "executor"], mode="parallel")
    result = p.run()
    assert result["pattern"] == "multi_agent"
    assert result["mode"] == "parallel"
    assert "planner" in result["roles"]


def test_get_pattern_registry():
    p = get_pattern({"name": "multi_agent", "mode": "sequential"})
    assert isinstance(p, MultiAgent)
    assert p.mode == "sequential"


def test_initializer_executor_missing_ledger():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = InitializerExecutor(ledger_dir=tmpdir)
        result = p.read_ledger("nonexistent_session")
        assert result == {}
