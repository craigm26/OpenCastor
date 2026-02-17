"""Comprehensive tests for the Ollama provider.

All HTTP calls are mocked — no running Ollama instance required.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from castor.providers.ollama_provider import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    OllamaConnectionError,
    OllamaProvider,
    _http_request,
    _is_vision_model,
    _resolve_host,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_urlopen(response_data, status=200):
    """Create a mock urlopen that returns JSON data."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.status = status
    return mock_resp


def _chat_response(content: str) -> dict:
    """Build a standard Ollama /api/chat response."""
    return {
        "model": "llava:13b",
        "message": {"role": "assistant", "content": content},
        "done": True,
    }


SAMPLE_ACTION = '{"type": "move", "linear": 0.5, "angular": 0.0}'
SAMPLE_IMAGE = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # Minimal JPEG-like bytes


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestResolveHost:
    def test_default(self):
        assert _resolve_host({}) == DEFAULT_HOST

    def test_from_config(self):
        assert _resolve_host({"ollama_host": "http://myhost:1234"}) == "http://myhost:1234"

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://envhost:5555")
        assert _resolve_host({}) == "http://envhost:5555"

    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://envhost:5555")
        assert _resolve_host({"ollama_host": "http://cfghost:1234"}) == "http://envhost:5555"

    def test_strips_trailing_slash(self):
        assert _resolve_host({"ollama_host": "http://host:1234/"}) == "http://host:1234"

    def test_endpoint_url_fallback(self):
        assert _resolve_host({"endpoint_url": "http://ep:9999"}) == "http://ep:9999"


class TestIsVisionModel:
    def test_known_vision_models(self):
        assert _is_vision_model("llava:13b") is True
        assert _is_vision_model("llava") is True
        assert _is_vision_model("bakllava") is True
        assert _is_vision_model("moondream") is True

    def test_non_vision_models(self):
        assert _is_vision_model("llama3:8b") is False
        assert _is_vision_model("mistral") is False
        assert _is_vision_model("codellama") is False

    def test_case_insensitive(self):
        assert _is_vision_model("LLaVA:13b") is True
        assert _is_vision_model("MOONDREAM") is True


# ---------------------------------------------------------------------------
# Unit tests: HTTP request helper
# ---------------------------------------------------------------------------


class TestHttpRequest:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_get_request(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        result = _http_request("http://localhost:11434/")
        assert result == {"status": "ok"}

    @patch("castor.providers.ollama_provider.urlopen")
    def test_post_request(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"done": True})
        result = _http_request("http://localhost:11434/api/chat", data={"model": "test"})
        assert result == {"done": True}

    @patch("castor.providers.ollama_provider.urlopen")
    def test_connection_refused(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = ConnectionRefusedError("refused")
        with pytest.raises(OllamaConnectionError) as exc_info:
            _http_request("http://localhost:11434/")
        assert "ollama serve" in str(exc_info.value).lower()

    @patch("castor.providers.ollama_provider.urlopen")
    def test_url_error(self, mock_urlopen_fn):
        from urllib.error import URLError

        mock_urlopen_fn.side_effect = URLError("unreachable")
        with pytest.raises(OllamaConnectionError):
            _http_request("http://localhost:11434/")

    @patch("castor.providers.ollama_provider.urlopen")
    def test_empty_response(self, mock_urlopen_fn):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_urlopen_fn.return_value = mock_resp
        result = _http_request("http://localhost:11434/")
        assert result == {}

    @patch("castor.providers.ollama_provider.urlopen")
    def test_stream_returns_raw_response(self, mock_urlopen_fn):
        mock_resp = MagicMock()
        mock_urlopen_fn.return_value = mock_resp
        result = _http_request("http://localhost:11434/api/chat", data={}, stream=True)
        assert result is mock_resp
        mock_resp.read.assert_not_called()


# ---------------------------------------------------------------------------
# Provider initialization
# ---------------------------------------------------------------------------


class TestOllamaProviderInit:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_default_init(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama"})
        assert provider.model_name == DEFAULT_MODEL
        assert provider.host == DEFAULT_HOST
        assert provider.is_vision is True  # llava:13b is a vision model

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_model(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        assert provider.model_name == "mistral:7b"
        assert provider.is_vision is False

    @patch("castor.providers.ollama_provider.urlopen")
    def test_vision_override_via_config(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider(
            {
                "provider": "ollama",
                "model": "custom-model",
                "vision_enabled": True,
            }
        )
        assert provider.is_vision is True

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_host(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider(
            {
                "provider": "ollama",
                "ollama_host": "http://192.168.1.50:11434",
            }
        )
        assert provider.host == "http://192.168.1.50:11434"

    @patch("castor.providers.ollama_provider.urlopen")
    def test_connection_failure_warns_but_doesnt_raise(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = ConnectionRefusedError("refused")
        # Should not raise — just logs a warning
        provider = OllamaProvider({"provider": "ollama"})
        assert provider.host == DEFAULT_HOST

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_timeout(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama", "timeout": 300})
        assert provider.timeout == 300


# ---------------------------------------------------------------------------
# Inference: think()
# ---------------------------------------------------------------------------


class TestOllamaThink:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_text_only(self, mock_urlopen_fn):
        # First call: ping, second call: chat
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(_chat_response(SAMPLE_ACTION)),
        ]
        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        thought = provider.think(b"", "What do you see?")
        assert thought.raw_text == SAMPLE_ACTION
        assert thought.action is not None
        assert thought.action["type"] == "move"

    @patch("castor.providers.ollama_provider.urlopen")
    def test_vision_with_image(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(_chat_response(SAMPLE_ACTION)),
        ]
        provider = OllamaProvider({"provider": "ollama", "model": "llava:13b"})
        thought = provider.think(SAMPLE_IMAGE, "Describe the scene")
        assert thought.raw_text == SAMPLE_ACTION
        assert thought.action["type"] == "move"

    @patch("castor.providers.ollama_provider.urlopen")
    def test_vision_model_with_empty_image_uses_text(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(_chat_response('{"type": "stop"}')),
        ]
        provider = OllamaProvider({"provider": "ollama", "model": "llava:13b"})
        thought = provider.think(b"", "What should I do?")
        assert thought.action["type"] == "stop"

    @patch("castor.providers.ollama_provider.urlopen")
    def test_think_with_non_json_response(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(_chat_response("I see a wall ahead, turning right.")),
        ]
        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        thought = provider.think(b"", "What do you see?")
        assert thought.raw_text == "I see a wall ahead, turning right."
        assert thought.action is None

    @patch("castor.providers.ollama_provider.urlopen")
    def test_think_connection_error_propagates(self, mock_urlopen_fn):
        # Ping succeeds, chat fails
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            ConnectionRefusedError("refused"),
        ]
        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        with pytest.raises(OllamaConnectionError):
            provider.think(b"", "Hello")

    @patch("castor.providers.ollama_provider.urlopen")
    def test_think_generic_error_returns_error_thought(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            ValueError("bad data"),
        ]
        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        thought = provider.think(b"", "Hello")
        assert "Error" in thought.raw_text
        assert thought.action is None

    @patch("castor.providers.ollama_provider.urlopen")
    def test_think_empty_response(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen({"message": {}}),
        ]
        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        thought = provider.think(b"", "Hello")
        assert thought.raw_text == ""
        assert thought.action is None


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class TestOllamaStreaming:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_stream_text(self, mock_urlopen_fn):
        chunks = [
            json.dumps({"message": {"content": "Hello"}, "done": False}).encode(),
            json.dumps({"message": {"content": " world"}, "done": False}).encode(),
            json.dumps({"message": {"content": ""}, "done": True}).encode(),
        ]
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter(chunks))

        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),  # ping
            mock_stream,  # stream
        ]

        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        result = list(provider.think_stream(b"", "Hello"))
        assert result == ["Hello", " world"]

    @patch("castor.providers.ollama_provider.urlopen")
    def test_stream_handles_invalid_json(self, mock_urlopen_fn):
        chunks = [
            b"not json",
            json.dumps({"message": {"content": "ok"}}).encode(),
        ]
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter(chunks))

        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            mock_stream,
        ]

        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        result = list(provider.think_stream(b"", "Hello"))
        assert result == ["ok"]

    @patch("castor.providers.ollama_provider.urlopen")
    def test_stream_empty_lines(self, mock_urlopen_fn):
        chunks = [b"", b"", json.dumps({"message": {"content": "data"}}).encode()]
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter(chunks))

        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            mock_stream,
        ]

        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        result = list(provider.think_stream(b"", "Hello"))
        assert result == ["data"]

    @patch("castor.providers.ollama_provider.urlopen")
    def test_stream_with_vision(self, mock_urlopen_fn):
        chunks = [json.dumps({"message": {"content": "I see a cat"}}).encode()]
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter(chunks))

        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            mock_stream,
        ]

        provider = OllamaProvider({"provider": "ollama", "model": "llava:13b"})
        result = list(provider.think_stream(SAMPLE_IMAGE, "Describe"))
        assert result == ["I see a cat"]


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------


class TestOllamaListModels:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_list_models(self, mock_urlopen_fn):
        models_resp = {
            "models": [
                {
                    "name": "llava:13b",
                    "size": 8_000_000_000,
                    "modified_at": "2024-01-01T00:00:00Z",
                    "digest": "abc123def456",
                    "details": {"family": "llama"},
                },
                {
                    "name": "mistral:7b",
                    "size": 4_000_000_000,
                    "modified_at": "2024-02-01T00:00:00Z",
                    "digest": "def456abc789",
                    "details": {},
                },
            ]
        }
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),  # ping
            _mock_urlopen(models_resp),  # list
        ]

        provider = OllamaProvider({"provider": "ollama"})
        models = provider.list_models()
        assert len(models) == 2
        assert models[0]["name"] == "llava:13b"
        assert models[1]["name"] == "mistral:7b"
        assert len(models[0]["digest"]) <= 12

    @patch("castor.providers.ollama_provider.urlopen")
    def test_list_models_empty(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen({"models": []}),
        ]
        provider = OllamaProvider({"provider": "ollama"})
        assert provider.list_models() == []

    @patch("castor.providers.ollama_provider.urlopen")
    def test_list_models_connection_error(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            ConnectionRefusedError("refused"),
        ]
        provider = OllamaProvider({"provider": "ollama"})
        with pytest.raises(OllamaConnectionError):
            provider.list_models()


# ---------------------------------------------------------------------------
# OllamaConnectionError
# ---------------------------------------------------------------------------


class TestOllamaConnectionError:
    def test_message(self):
        err = OllamaConnectionError("http://localhost:11434")
        assert "localhost:11434" in str(err)
        assert "ollama serve" in str(err)

    def test_with_original(self):
        orig = ConnectionRefusedError("refused")
        err = OllamaConnectionError("http://localhost:11434", original=orig)
        assert err.original is orig
        assert err.host == "http://localhost:11434"

    def test_is_connection_error(self):
        err = OllamaConnectionError("http://localhost:11434")
        assert isinstance(err, ConnectionError)


# ---------------------------------------------------------------------------
# Provider factory integration
# ---------------------------------------------------------------------------


class TestProviderFactory:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_get_provider_ollama(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        from castor.providers import get_provider

        provider = get_provider({"provider": "ollama"})
        assert isinstance(provider, OllamaProvider)


# ---------------------------------------------------------------------------
# Pull model
# ---------------------------------------------------------------------------


class TestOllamaPullModel:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_pull_model(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),  # ping
            _mock_urlopen({"status": "success"}),  # pull
        ]
        provider = OllamaProvider({"provider": "ollama"})
        provider.pull_model("llama3:8b")  # Should not raise


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_system_prompt_set(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama"})
        assert "OpenCastor" in provider.system_prompt
        assert "JSON" in provider.system_prompt

    @patch("castor.providers.ollama_provider.urlopen")
    def test_update_system_prompt(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama"})
        provider.update_system_prompt("I remember the kitchen.")
        assert "kitchen" in provider.system_prompt
