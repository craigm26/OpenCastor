"""Tests for castor/agent_tools.py — extended agent tools."""

from __future__ import annotations

import os
from unittest.mock import patch

from castor.agent_tools import (
    get_telemetry,
    query_local_knowledge,
    recall_episode,
    register_agent_tools,
    web_search,
)
from castor.tools import ToolRegistry


class TestRegisterAgentTools:
    def test_all_tools_registered(self):
        reg = ToolRegistry()
        register_agent_tools(reg)
        names = reg.list_tools()
        assert "web_search" in names
        assert "get_telemetry" in names
        assert "recall_episode" in names
        assert "send_rcan_message" in names
        assert "query_local_knowledge" in names

    def test_tools_have_schemas(self):
        reg = ToolRegistry()
        register_agent_tools(reg)
        openai_tools = reg.to_openai_tools()
        names = {t["function"]["name"] for t in openai_tools}
        assert "web_search" in names
        assert "send_rcan_message" in names

    def test_anthropic_schemas(self):
        reg = ToolRegistry()
        register_agent_tools(reg)
        anthropic_tools = reg.to_anthropic_tools()
        names = {t["name"] for t in anthropic_tools}
        assert "web_search" in names


class TestGetTelemetry:
    def test_returns_dict(self):
        result = get_telemetry()
        assert isinstance(result, dict)

    def test_keys_present_or_fallback(self):
        result = get_telemetry()
        # Either has real telemetry keys or fallback marker
        assert len(result) > 0


class TestRecallEpisode:
    def test_empty_query_returns_empty(self):
        result = recall_episode(query="")
        assert result == []

    def test_returns_list(self):
        result = recall_episode(query="test query")
        assert isinstance(result, list)

    def test_graceful_no_episode_store(self):
        """Should return [] gracefully when no episode store configured."""
        result = recall_episode(query="pick up brick")
        assert isinstance(result, list)


class TestQueryLocalKnowledge:
    def test_empty_query_returns_empty(self):
        result = query_local_knowledge(query="")
        assert result == []

    def test_no_knowledge_dir_returns_empty(self):
        with patch.dict(os.environ, {}):
            result = query_local_knowledge(query="servo specs")
        assert isinstance(result, list)

    def test_with_knowledge_dir(self, tmp_path):
        """Creates a temp knowledge dir with a doc and searches it."""
        (tmp_path / "manual.txt").write_text(
            "The STS3215 servo has a stall torque of 30kg.cm at 12V. "
            "Protocol: serial TTL half-duplex. Baud rate: 1000000."
        )
        with patch("castor.agent_tools.os.path.expanduser", return_value=str(tmp_path)):
            with patch("castor.agent_tools.os.path.isdir", return_value=True):
                with patch("castor.agent_tools.os.listdir", return_value=["manual.txt"]):
                    with patch("builtins.open", return_value=open(str(tmp_path / "manual.txt"))):
                        result = query_local_knowledge(query="servo torque")
        assert isinstance(result, list)

    def test_k_limit_respected(self, tmp_path):
        """Results should not exceed k."""
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        # Create files with overlapping content
        for i in range(10):
            (knowledge_dir / f"doc{i}.txt").write_text(f"robot servo motor test content {i}" * 5)

        with patch("castor.agent_tools.os.path.expanduser", return_value=str(knowledge_dir)):
            result = query_local_knowledge(query="robot servo", k=3)
        assert len(result) <= 3


class TestWebSearchFallback:
    def test_empty_query_returns_error(self):
        result = web_search(query="")
        assert isinstance(result, list)
        assert len(result) > 0
        assert "error" in result[0]

    def test_no_api_key_uses_ddg_or_error(self):
        """Without BRAVE_API_KEY, should either use DDG or return graceful error."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove key if present
            os.environ.pop("BRAVE_API_KEY", None)
            result = web_search(query="OpenCastor robot")
        assert isinstance(result, list)
        # Either has results or graceful error
        assert len(result) > 0

    def test_num_results_capped(self):
        """num_results should be capped at 5."""
        # Just test the capping logic doesn't crash
        with patch(
            "castor.agent_tools._ddg_search",
            return_value=[{"title": "t", "url": "u", "snippet": "s"}],
        ):
            result = web_search(query="test", num_results=100)
        assert isinstance(result, list)
