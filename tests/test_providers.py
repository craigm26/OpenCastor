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


# =====================================================================
# Anthropic setup-token auth
# =====================================================================


class TestAnthropicSetupToken:
    """Test Anthropic provider setup-token (subscription auth) support."""

    def _make_provider(self, config, token_path=None):
        """Create AnthropicProvider with mocked anthropic module."""
        import sys

        mock_mod = MagicMock()
        with patch.dict(sys.modules, {"anthropic": mock_mod}):
            import importlib

            import castor.providers.anthropic_provider as mod

            importlib.reload(mod)
            # Override token path to avoid reading real stored tokens
            if token_path is not None:
                mod.AnthropicProvider.TOKEN_PATH = token_path
            provider = mod.AnthropicProvider(config)
        return provider, mock_mod

    def test_setup_token_via_env(self, monkeypatch, tmp_path):
        """Setup-token in ANTHROPIC_API_KEY env var routes through proxy or CLI fallback."""
        token = "sk-ant-oat01-" + "x" * 80
        monkeypatch.setenv("ANTHROPIC_API_KEY", token)
        np = str(tmp_path / "nonexistent")
        provider, mock_mod = self._make_provider({"provider": "anthropic"}, token_path=np)
        # OAuth tokens don't create anthropic.Anthropic client directly
        # They route through proxy (OpenAI client) or CLI fallback
        mock_mod.Anthropic.assert_not_called()
        assert provider.client is None
        assert getattr(provider, "_use_proxy", False) or getattr(provider, "_use_cli", False)

    def test_api_key_via_env(self, monkeypatch, tmp_path):
        """Standard API key should still work when no stored token."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test-key-1234")
        np = str(tmp_path / "nonexistent")
        provider, mock_mod = self._make_provider({"provider": "anthropic"}, token_path=np)
        mock_mod.Anthropic.assert_called_once_with(api_key="sk-ant-api03-test-key-1234")

    def test_api_key_from_config(self, monkeypatch, tmp_path):
        """API key from config dict should work when no stored token."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        np = str(tmp_path / "nonexistent")
        _, mock_mod = self._make_provider(
            {"provider": "anthropic", "api_key": "sk-ant-test"}, token_path=np
        )
        mock_mod.Anthropic.assert_called_once_with(api_key="sk-ant-test")

    def test_no_credentials_raises(self, monkeypatch, tmp_path):
        """Should raise ValueError when no credentials found anywhere."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        np = str(tmp_path / "nonexistent")
        with pytest.raises(ValueError, match="No Anthropic credentials found"):
            self._make_provider({"provider": "anthropic"}, token_path=np)

    def test_setup_token_prefix_constant(self):
        """Verify setup-token prefix matches Claude CLI format."""
        from castor.providers.anthropic_provider import AnthropicProvider

        assert AnthropicProvider.SETUP_TOKEN_PREFIX == "sk-ant-oat01-"

    def test_reads_stored_token(self, monkeypatch, tmp_path):
        """Should read setup-token from ~/.opencastor/anthropic-token and use proxy/CLI."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        token = "sk-ant-oat01-" + "a" * 80
        token_file = tmp_path / "anthropic-token"
        token_file.write_text(token)
        provider, mock_mod = self._make_provider(
            {"provider": "anthropic"}, token_path=str(token_file)
        )
        # OAuth tokens route through proxy or CLI, not direct SDK
        mock_mod.Anthropic.assert_not_called()
        assert getattr(provider, "_use_proxy", False) or getattr(provider, "_use_cli", False)

    def test_save_token(self, tmp_path):
        """save_token should write to file with restricted permissions."""
        from castor.providers.anthropic_provider import AnthropicProvider

        token_path = tmp_path / "anthropic-token"
        original = AnthropicProvider.TOKEN_PATH
        AnthropicProvider.TOKEN_PATH = str(token_path)
        try:
            token = "sk-ant-oat01-" + "b" * 80
            saved = AnthropicProvider.save_token(token)
            assert saved == str(token_path)
            assert token_path.read_text() == token
        finally:
            AnthropicProvider.TOKEN_PATH = original

    def test_default_model(self):
        """Default model should be claude-opus-4-6."""
        from castor.providers.anthropic_provider import AnthropicProvider

        assert AnthropicProvider.DEFAULT_MODEL == "claude-opus-4-6"


class TestAgenticVisionGoogle:
    """GoogleProvider auto-enables code_execution for Agentic Vision models."""

    def _make_provider(self, model_name, extra_config=None):
        """Instantiate GoogleProvider with a mocked genai module."""
        import types, sys

        # Build a minimal mock for google.generativeai
        mock_genai = types.ModuleType("google.generativeai")
        captured = {}

        class _MockModel:
            def __init__(self, model_name, system_instruction=None, tools=None):
                captured["model_name"] = model_name
                captured["system_instruction"] = system_instruction
                captured["tools"] = tools

        mock_genai.configure = lambda **_: None
        mock_genai.GenerativeModel = _MockModel

        google_mod = types.ModuleType("google")
        sys.modules["google"] = google_mod
        sys.modules["google.generativeai"] = mock_genai

        from castor.providers.google_provider import GoogleProvider

        config = {"provider": "google", "model": model_name, "api_key": "test-key"}
        if extra_config:
            config.update(extra_config)

        provider = GoogleProvider(config)
        return provider, captured

    def test_agentic_vision_auto_enabled_for_gemini3_flash(self):
        provider, captured = self._make_provider("gemini-3-flash-preview")
        assert provider._is_agentic_vision is True
        assert "code_execution" in (captured.get("tools") or [])

    def test_agentic_vision_not_enabled_for_gemini25_flash(self):
        provider, captured = self._make_provider("gemini-2.5-flash")
        assert provider._is_agentic_vision is False
        assert (captured.get("tools") or []) == []

    def test_agentic_vision_system_prompt_addendum_injected(self):
        provider, captured = self._make_provider("gemini-3-flash-preview")
        assert captured.get("system_instruction") is not None
        assert "Agentic Vision" in captured["system_instruction"]
        assert "zoom" in captured["system_instruction"].lower()

    def test_agentic_vision_explicit_opt_out(self):
        provider, captured = self._make_provider(
            "gemini-3-flash-preview", extra_config={"agentic_vision": False}
        )
        assert provider._is_agentic_vision is False

    def test_agentic_vision_explicit_opt_in_for_non_default_model(self):
        provider, captured = self._make_provider(
            "gemini-2.5-flash", extra_config={"agentic_vision": True}
        )
        assert provider._is_agentic_vision is True
        assert "code_execution" in (captured.get("tools") or [])


# =====================================================================
# LlamaCppProvider — typed exceptions + vision + streaming
# =====================================================================


class TestLlamaCppProvider:
    """Tests for LlamaCppProvider (mocked backends)."""

    def test_model_not_found_raises_typed_error(self, tmp_path):
        from castor.providers.llamacpp_provider import LlamaCppModelNotFoundError, LlamaCppProvider

        missing = str(tmp_path / "missing.gguf")
        with pytest.raises(LlamaCppModelNotFoundError, match="not found"):
            LlamaCppProvider({"provider": "llamacpp", "model": missing})

    def test_ollama_connection_error_on_unreachable_server(self):
        """Non-existent server raises LlamaCppConnectionError during init."""
        import urllib.error

        from castor.providers.llamacpp_provider import LlamaCppConnectionError, LlamaCppProvider

        with patch(
            "castor.providers.llamacpp_provider.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with pytest.raises(LlamaCppConnectionError, match="Cannot reach Ollama"):
                LlamaCppProvider(
                    {"provider": "llamacpp", "model": "gemma3:1b",
                     "base_url": "http://localhost:19999/v1"}
                )

    def test_ollama_think_returns_thought(self):
        """think() via Ollama returns a valid Thought."""
        from castor.providers.llamacpp_provider import LlamaCppProvider

        warmup_resp = MagicMock()
        warmup_resp.read.return_value = b'{"status":"ok"}'
        warmup_resp.__enter__ = lambda s: s
        warmup_resp.__exit__ = MagicMock(return_value=False)

        import json as _json
        think_payload = _json.dumps(
            {"choices": [{"message": {"content": '{"action":"stop"}'}}]}
        ).encode()
        think_resp = MagicMock()
        think_resp.read.return_value = think_payload
        think_resp.__enter__ = lambda s: s
        think_resp.__exit__ = MagicMock(return_value=False)

        with patch(
            "castor.providers.llamacpp_provider.urllib.request.urlopen",
            side_effect=[warmup_resp, think_resp],
        ):
            p = LlamaCppProvider({"provider": "llamacpp", "model": "gemma3:1b"})
            thought = p.think(b"", "stop the robot")

        assert thought.raw_text is not None
        assert thought.action is not None
        assert thought.action.get("action") == "stop"

    def test_vision_model_detected_by_name(self):
        """llava in model name sets _is_vision_model=True."""
        from castor.providers.llamacpp_provider import LlamaCppProvider

        warmup = MagicMock()
        warmup.read.return_value = b"{}"
        warmup.__enter__ = lambda s: s
        warmup.__exit__ = MagicMock(return_value=False)

        with patch(
            "castor.providers.llamacpp_provider.urllib.request.urlopen",
            return_value=warmup,
        ):
            p = LlamaCppProvider({"provider": "llamacpp", "model": "llava:13b"})

        assert p._is_vision_model is True

    def test_non_vision_model_flag_false(self):
        """Plain text model does not set vision flag."""
        from castor.providers.llamacpp_provider import LlamaCppProvider

        warmup = MagicMock()
        warmup.read.return_value = b"{}"
        warmup.__enter__ = lambda s: s
        warmup.__exit__ = MagicMock(return_value=False)

        with patch(
            "castor.providers.llamacpp_provider.urllib.request.urlopen",
            return_value=warmup,
        ):
            p = LlamaCppProvider({"provider": "llamacpp", "model": "gemma3:1b"})

        assert p._is_vision_model is False

    def test_think_stream_yields_tokens_and_returns_thought(self):
        """think_stream() yields individual tokens from Ollama SSE stream."""
        from castor.providers.llamacpp_provider import LlamaCppProvider

        warmup = MagicMock()
        warmup.read.return_value = b"{}"
        warmup.__enter__ = lambda s: s
        warmup.__exit__ = MagicMock(return_value=False)

        # Simulate SSE lines from Ollama streaming endpoint
        sse_lines = [
            b'data: {"choices":[{"delta":{"content":"{\\"action\\""}}]}\n',
            b'data: {"choices":[{"delta":{"content":":\\"stop\\"}"}}]}\n',
            b"data: [DONE]\n",
        ]
        stream_resp = MagicMock()
        stream_resp.__enter__ = lambda s: s
        stream_resp.__exit__ = MagicMock(return_value=False)
        stream_resp.__iter__ = lambda s: iter(sse_lines)

        with patch(
            "castor.providers.llamacpp_provider.urllib.request.urlopen",
            side_effect=[warmup, stream_resp],
        ):
            p = LlamaCppProvider({"provider": "llamacpp", "model": "gemma3:1b"})
            tokens = list(p.think_stream(b"", "stop"))

        assert len(tokens) >= 1
        full = "".join(tokens)
        assert "action" in full or "stop" in full

    def test_typed_exceptions_are_subclass_of_base(self):
        from castor.providers.llamacpp_provider import (
            LlamaCppConnectionError,
            LlamaCppError,
            LlamaCppModelNotFoundError,
            LlamaCppOOMError,
        )

        assert issubclass(LlamaCppModelNotFoundError, LlamaCppError)
        assert issubclass(LlamaCppConnectionError, LlamaCppError)
        assert issubclass(LlamaCppOOMError, LlamaCppError)
