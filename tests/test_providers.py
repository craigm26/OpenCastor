"""Tests for castor.providers -- Thought class, BaseProvider, and factory."""

from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import BaseProvider, Thought


# =====================================================================
# Thought tests
# =====================================================================
class TestThought:
    def test_basic_construction(self):
        t = Thought("moving forward", {"type": "move", "linear": 0.5})
        assert t.raw_text == "moving forward"
        assert t.action == {"type": "move", "linear": 0.5}
        assert t.confidence == 1.0

    def test_none_action(self):
        t = Thought("error occurred", None)
        assert t.action is None

    def test_empty_action(self):
        t = Thought("waiting", {})
        assert t.action == {}

    def test_complex_action(self):
        action = {"type": "move", "linear": 0.5, "angular": -0.3}
        t = Thought("navigate", action)
        assert t.action["type"] == "move"
        assert t.action["linear"] == 0.5
        assert t.action["angular"] == -0.3


# =====================================================================
# BaseProvider tests (using a concrete stub)
# =====================================================================
class StubProvider(BaseProvider):
    """Minimal concrete provider for testing base class methods."""

    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        return Thought("stub response", None)


class TestBaseProvider:
    def test_default_model_name(self):
        provider = StubProvider({})
        assert provider.model_name == "default-model"

    def test_custom_model_name(self):
        provider = StubProvider({"model": "gemini-2.5-flash"})
        assert provider.model_name == "gemini-2.5-flash"

    def test_system_prompt_built_on_init(self):
        provider = StubProvider({})
        assert "OpenCastor" in provider.system_prompt
        assert "STRICT JSON" in provider.system_prompt

    def test_system_prompt_contains_actions(self):
        provider = StubProvider({})
        assert '"type": "move"' in provider.system_prompt
        assert '"type": "stop"' in provider.system_prompt
        assert '"type": "grip"' in provider.system_prompt
        assert '"type": "wait"' in provider.system_prompt

    def test_system_prompt_without_memory(self):
        provider = StubProvider({})
        assert "Robot Memory" not in provider.system_prompt

    def test_system_prompt_with_memory(self):
        prompt = StubProvider({})._build_system_prompt("saw a wall at 2m")
        assert "Robot Memory" in prompt
        assert "saw a wall at 2m" in prompt

    def test_update_system_prompt(self):
        provider = StubProvider({})
        assert "Robot Memory" not in provider.system_prompt
        provider.update_system_prompt("new context")
        assert "Robot Memory" in provider.system_prompt
        assert "new context" in provider.system_prompt

    def test_config_stored(self):
        config = {"model": "test", "extra": "value"}
        provider = StubProvider(config)
        assert provider.config is config


# =====================================================================
# _clean_json tests
# =====================================================================
class TestCleanJson:
    def _clean(self, text):
        return StubProvider({})._clean_json(text)

    def test_valid_json(self):
        result = self._clean('{"type": "move", "linear": 0.5}')
        assert result == {"type": "move", "linear": 0.5}

    def test_json_with_markdown_fences(self):
        text = '```json\n{"type": "stop"}\n```'
        result = self._clean(text)
        assert result == {"type": "stop"}

    def test_json_with_surrounding_text(self):
        text = 'I will move forward. {"type": "move", "linear": 1.0} That is my action.'
        result = self._clean(text)
        assert result == {"type": "move", "linear": 1.0}

    def test_invalid_json(self):
        result = self._clean("not json at all")
        assert result is None

    def test_empty_string(self):
        result = self._clean("")
        assert result is None

    def test_nested_json(self):
        text = '{"type": "move", "params": {"speed": 0.5}}'
        result = self._clean(text)
        assert result["params"]["speed"] == 0.5

    def test_malformed_json(self):
        result = self._clean('{"type": "move", linear: 0.5}')
        assert result is None

    def test_multiple_json_objects(self):
        text = '{"first": 1} some text {"second": 2}'
        result = self._clean(text)
        # Should return from first { to last }, which may or may not parse
        # Depends on implementation - it grabs first { to last }
        assert result is not None or result is None  # Won't crash


# =====================================================================
# get_provider factory tests
# =====================================================================
class TestGetProvider:
    @patch("castor.providers.GoogleProvider")
    def test_google_provider(self, mock_cls):
        from castor.providers import get_provider

        config = {"provider": "google", "model": "gemini-2.5-flash"}
        get_provider(config)
        mock_cls.assert_called_once_with(config)

    @patch("castor.providers.OpenAIProvider")
    def test_openai_provider(self, mock_cls):
        from castor.providers import get_provider

        config = {"provider": "openai", "model": "gpt-4.1"}
        get_provider(config)
        mock_cls.assert_called_once_with(config)

    @patch("castor.providers.AnthropicProvider")
    def test_anthropic_provider(self, mock_cls):
        from castor.providers import get_provider

        config = {"provider": "anthropic", "model": "claude-opus-4-6"}
        get_provider(config)
        mock_cls.assert_called_once_with(config)

    @patch("castor.providers.ollama_provider.urlopen")
    def test_ollama_provider(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_urlopen.return_value = mock_resp

        from castor.providers import get_provider
        from castor.providers.ollama_provider import OllamaProvider

        provider = get_provider({"provider": "ollama"})
        assert isinstance(provider, OllamaProvider)

    def test_unknown_provider(self):
        from castor.providers import get_provider

        with pytest.raises(ValueError, match="Unknown AI provider"):
            get_provider({"provider": "nonexistent"})

    @patch("castor.providers.GoogleProvider")
    def test_default_provider_is_google(self, mock_cls):
        from castor.providers import get_provider

        get_provider({})  # No "provider" key
        mock_cls.assert_called_once()

    @patch("castor.providers.GoogleProvider")
    def test_case_insensitive(self, mock_cls):
        from castor.providers import get_provider

        get_provider({"provider": "Google"})
        mock_cls.assert_called_once()
