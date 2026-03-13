"""Tests for DeepSeekProvider."""

import os
from unittest.mock import MagicMock, patch

import pytest

from castor.providers.deepseek_provider import DeepSeekProvider  # noqa: E402


def _openai_available() -> bool:
    try:
        import importlib

        spec = importlib.util.find_spec("openai")
        return spec is not None and spec.origin is not None
    except (ValueError, ModuleNotFoundError):
        return False


pytestmark = pytest.mark.skipif(
    not _openai_available(), reason="openai not installed or not importable"
)

_CFG = {
    "provider": "deepseek",
    "model": "deepseek-chat",
    "system_prompt": "You are a robot.",
}


def _make_provider(extra=None):
    cfg = {**_CFG, **(extra or {})}
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-ds-key"}, clear=False):
        return DeepSeekProvider(cfg)


def _mock_response(content='{"type":"stop"}'):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return resp


class TestDeepSeekInit:
    def test_raises_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
                DeepSeekProvider(_CFG)

    def test_uses_env_key(self):
        p = _make_provider()
        assert p is not None

    def test_uses_config_key(self):
        with patch.dict(os.environ, {}, clear=True):
            p = DeepSeekProvider({**_CFG, "api_key": "cfg-key"})
        assert p is not None

    def test_model_name_stored(self):
        p = _make_provider()
        assert p.model_name == "deepseek-chat"


class TestDeepSeekHealthCheck:
    def test_returns_ok(self):
        p = _make_provider()
        p.client = MagicMock()
        p.client.models.list.return_value = []
        result = p.health_check()
        assert result["ok"] is True
        assert "latency_ms" in result

    def test_returns_error_on_failure(self):
        p = _make_provider()
        p.client = MagicMock()
        p.client.models.list.side_effect = RuntimeError("network error")
        result = p.health_check()
        assert result["ok"] is False
        assert "network error" in result["error"]


class TestDeepSeekThink:
    def test_think_text_only(self):
        p = _make_provider()
        p.client = MagicMock()
        p.client.chat.completions.create.return_value = _mock_response('{"type":"stop"}')
        thought = p.think(b"", "go forward")
        assert thought is not None
        assert thought.action == {"type": "stop"}

    def test_think_safety_block(self):
        p = _make_provider()
        with patch.object(p, "_check_instruction_safety") as mock_safety:
            from castor.providers.base import Thought

            mock_safety.return_value = Thought("blocked", None)
            thought = p.think(b"", "ignore instructions")
        assert thought.raw_text == "blocked"

    def test_think_error_handling(self):
        p = _make_provider()
        p.client = MagicMock()
        p.client.chat.completions.create.side_effect = RuntimeError("api down")
        thought = p.think(b"", "move")
        assert "Error" in thought.raw_text

    def test_think_with_image_non_vision_model(self):
        """Non-vision model sends text-only even with image bytes."""
        p = _make_provider()
        p.client = MagicMock()
        p.client.chat.completions.create.return_value = _mock_response('{"type":"move"}')
        fake_image = b"\xff\xd8\xff" + b"\x00" * 50
        thought = p.think(fake_image, "what do you see?")
        assert thought is not None
        call_args = p.client.chat.completions.create.call_args[1]
        user_content = call_args["messages"][-1]["content"]
        # For non-vision model, content should be a plain string
        assert isinstance(user_content, str)


class TestDeepSeekStream:
    def test_think_stream_yields_chunks(self):
        p = _make_provider()
        p.client = MagicMock()
        chunk1 = MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello"))])
        chunk2 = MagicMock(choices=[MagicMock(delta=MagicMock(content=" world"))])
        p.client.chat.completions.create.return_value = [chunk1, chunk2]
        tokens = list(p.think_stream(b"", "hi"))
        assert "Hello" in tokens
        assert " world" in tokens

    def test_stream_safety_block(self):
        p = _make_provider()
        with patch.object(p, "_check_instruction_safety") as mock_safety:
            from castor.providers.base import Thought

            mock_safety.return_value = Thought("blocked", None)
            tokens = list(p.think_stream(b"", "jailbreak"))
        assert "blocked" in tokens

    def test_stream_error_handling(self):
        p = _make_provider()
        p.client = MagicMock()
        p.client.chat.completions.create.side_effect = RuntimeError("timeout")
        tokens = list(p.think_stream(b"", "move"))
        assert any("Error" in t for t in tokens)


class TestDeepSeekVisionFlag:
    def test_non_vision_model_flag_false(self):
        p = _make_provider()
        assert p._vision is False

    def test_vision_model_flag_true(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k"}, clear=False):
            p = DeepSeekProvider({**_CFG, "model": "deepseek-vl2"})
        assert p._vision is True
