"""Comprehensive tests for the Ollama provider.

All HTTP calls are mocked — no running Ollama instance required.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from castor.providers.ollama_provider import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_MODEL_ALIASES,
    OllamaConnectionError,
    OllamaModelNotFoundError,
    OllamaProvider,
    _http_request,
    _is_vision_model,
    _ModelCache,
    _resolve_host,
    _resolve_model_alias,
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

    def test_named_profile(self):
        config = {
            "ollama_profiles": {
                "homeserver": {"host": "http://192.168.1.50:11434"},
                "work": {"host": "http://10.0.0.5:11434"},
            }
        }
        assert _resolve_host(config, profile="homeserver") == "http://192.168.1.50:11434"
        assert _resolve_host(config, profile="work") == "http://10.0.0.5:11434"

    def test_unknown_profile_falls_back(self):
        config = {"ollama_host": "http://fallback:1234"}
        assert _resolve_host(config, profile="nonexistent") == "http://fallback:1234"

    def test_profile_strips_trailing_slash(self):
        config = {"ollama_profiles": {"s": {"host": "http://h:1234/"}}}
        assert _resolve_host(config, profile="s") == "http://h:1234"


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


class TestResolveModelAlias:
    def test_known_alias(self):
        assert _resolve_model_alias("vision", DEFAULT_MODEL_ALIASES) == "llava:latest"
        assert _resolve_model_alias("fast", DEFAULT_MODEL_ALIASES) == "llama3.2:1b"

    def test_unknown_alias_passthrough(self):
        assert _resolve_model_alias("mistral:7b", DEFAULT_MODEL_ALIASES) == "mistral:7b"

    def test_custom_aliases(self):
        aliases = {"my-model": "custom:v2"}
        assert _resolve_model_alias("my-model", aliases) == "custom:v2"
        assert _resolve_model_alias("other", aliases) == "other"


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
    def test_connection_refused_remote(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = ConnectionRefusedError("refused")
        with pytest.raises(OllamaConnectionError) as exc_info:
            _http_request("http://192.168.1.50:11434/")
        msg = str(exc_info.value)
        assert "remote" in msg.lower() or "server may be down" in msg.lower()

    @patch("castor.providers.ollama_provider.urlopen")
    def test_url_error(self, mock_urlopen_fn):
        from urllib.error import URLError

        mock_urlopen_fn.side_effect = URLError("unreachable")
        with pytest.raises(OllamaConnectionError):
            _http_request("http://localhost:11434/")

    @patch("castor.providers.ollama_provider.urlopen")
    def test_timeout_error(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = OSError("Connection timed out")
        with pytest.raises(OllamaConnectionError) as exc_info:
            _http_request("http://localhost:11434/")
        assert "timed out" in str(exc_info.value).lower()

    @patch("castor.providers.ollama_provider.urlopen")
    def test_empty_response(self, mock_urlopen_fn):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_urlopen_fn.return_value = mock_resp
        result = _http_request("http://localhost:11434/")
        assert result == {}

    @patch("castor.providers.ollama_provider.urlopen")
    def test_malformed_json_response(self, mock_urlopen_fn):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json at all {{"
        mock_urlopen_fn.return_value = mock_resp
        with pytest.raises(ValueError, match="invalid JSON"):
            _http_request("http://localhost:11434/api/chat")

    @patch("castor.providers.ollama_provider.urlopen")
    def test_partial_json_salvaged(self, mock_urlopen_fn):
        mock_resp = MagicMock()
        # Partial JSON with valid object embedded
        mock_resp.read.return_value = b'garbage {"status": "ok"} trailing'
        mock_urlopen_fn.return_value = mock_resp
        result = _http_request("http://localhost:11434/")
        assert result == {"status": "ok"}

    @patch("castor.providers.ollama_provider.urlopen")
    def test_stream_returns_raw_response(self, mock_urlopen_fn):
        mock_resp = MagicMock()
        mock_urlopen_fn.return_value = mock_resp
        result = _http_request("http://localhost:11434/api/chat", data={}, stream=True)
        assert result is mock_resp
        mock_resp.read.assert_not_called()


# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------


class TestModelCache:
    def test_initially_empty(self):
        cache = _ModelCache(ttl=60)
        assert cache.get() is None
        assert cache.expired is True

    def test_set_and_get(self):
        cache = _ModelCache(ttl=60)
        models = [{"name": "llava:13b"}]
        cache.set(models)
        assert cache.get() == models
        assert cache.expired is False

    def test_expired_after_ttl(self):
        cache = _ModelCache(ttl=0.01)
        cache.set([{"name": "test"}])
        time.sleep(0.02)
        assert cache.expired is True
        assert cache.get() is None

    def test_invalidate(self):
        cache = _ModelCache(ttl=60)
        cache.set([{"name": "test"}])
        cache.invalidate()
        assert cache.get() is None

    def test_model_names(self):
        cache = _ModelCache()
        cache.set([{"name": "a:1b"}, {"name": "b:latest"}])
        assert cache.model_names() == ["a:1b", "b:latest"]

    def test_model_names_empty(self):
        cache = _ModelCache()
        assert cache.model_names() == []


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
        assert provider.is_vision is True
        assert provider.is_available is True

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_model(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama", "model": "mistral:7b"})
        assert provider.model_name == "mistral:7b"
        assert provider.is_vision is False

    @patch("castor.providers.ollama_provider.urlopen")
    def test_model_alias_resolved(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama", "model": "vision"})
        assert provider.model_name == "llava:latest"
        assert provider.is_vision is True

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_model_alias(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({
            "provider": "ollama",
            "model": "mybot",
            "model_aliases": {"mybot": "mistral:7b"},
        })
        assert provider.model_name == "mistral:7b"

    @patch("castor.providers.ollama_provider.urlopen")
    def test_vision_override_via_config(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({
            "provider": "ollama",
            "model": "custom-model",
            "vision_enabled": True,
        })
        assert provider.is_vision is True

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_host(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({
            "provider": "ollama",
            "ollama_host": "http://192.168.1.50:11434",
        })
        assert provider.host == "http://192.168.1.50:11434"

    @patch("castor.providers.ollama_provider.urlopen")
    def test_connection_failure_warns_but_doesnt_raise(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = ConnectionRefusedError("refused")
        provider = OllamaProvider({"provider": "ollama"})
        assert provider.host == DEFAULT_HOST
        assert provider.is_available is False

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_timeout(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama", "timeout": 300})
        assert provider.timeout == 300

    @patch("castor.providers.ollama_provider.urlopen")
    def test_default_timeouts(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama"})
        assert provider.timeout == 30
        assert provider.health_timeout == 5

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_health_timeout(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama", "health_timeout": 10})
        assert provider.health_timeout == 10

    @patch("castor.providers.ollama_provider.urlopen")
    def test_connection_profile(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({
            "provider": "ollama",
            "ollama_profile": "homeserver",
            "ollama_profiles": {
                "homeserver": {"host": "http://192.168.1.50:11434"},
            },
        })
        assert provider.host == "http://192.168.1.50:11434"

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_system_prompt(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({
            "provider": "ollama",
            "system_prompt": "You are a helpful robot.",
        })
        assert provider.system_prompt == "You are a helpful robot."

    @patch("castor.providers.ollama_provider.urlopen")
    def test_auto_pull_default_false(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama"})
        assert provider.auto_pull is False

    @patch("castor.providers.ollama_provider.urlopen")
    def test_auto_pull_enabled(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama", "auto_pull": True})
        assert provider.auto_pull is True


# ---------------------------------------------------------------------------
# Inference: think()
# ---------------------------------------------------------------------------


class TestOllamaThink:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_text_only(self, mock_urlopen_fn):
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
            _mock_urlopen({"status": "ok"}),
            mock_stream,
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
# Model listing (with cache)
# ---------------------------------------------------------------------------


MODELS_RESPONSE = {
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


class TestOllamaListModels:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_list_models(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(MODELS_RESPONSE),
        ]

        provider = OllamaProvider({"provider": "ollama"})
        models = provider.list_models()
        assert len(models) == 2
        assert models[0]["name"] == "llava:13b"
        assert models[1]["name"] == "mistral:7b"
        assert len(models[0]["digest"]) <= 12

    @patch("castor.providers.ollama_provider.urlopen")
    def test_list_models_cached(self, mock_urlopen_fn):
        """Second call should use cache, not hit API again."""
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(MODELS_RESPONSE),
        ]

        provider = OllamaProvider({"provider": "ollama"})
        models1 = provider.list_models()
        models2 = provider.list_models()
        assert models1 == models2
        # Only 2 calls: ping + one list_models (second is cached)
        assert mock_urlopen_fn.call_count == 2

    @patch("castor.providers.ollama_provider.urlopen")
    def test_list_models_cache_expired(self, mock_urlopen_fn):
        """After TTL expires, should re-fetch."""
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(MODELS_RESPONSE),
            _mock_urlopen(MODELS_RESPONSE),
        ]

        provider = OllamaProvider({"provider": "ollama", "model_cache_ttl": 0.01})
        provider.list_models()
        time.sleep(0.02)
        provider.list_models()
        assert mock_urlopen_fn.call_count == 3  # ping + 2 list calls

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
# Auto-pull
# ---------------------------------------------------------------------------


class TestAutoPull:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_ensure_model_available_found(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(MODELS_RESPONSE),
        ]
        provider = OllamaProvider({"provider": "ollama"})
        # Should not raise
        provider._ensure_model_available("llava:13b")

    @patch("castor.providers.ollama_provider.urlopen")
    def test_ensure_model_not_found_no_auto_pull(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(MODELS_RESPONSE),
            _mock_urlopen(MODELS_RESPONSE),  # second list_models call in error
        ]
        provider = OllamaProvider({"provider": "ollama", "auto_pull": False})
        with pytest.raises(OllamaModelNotFoundError) as exc_info:
            provider._ensure_model_available("nonexistent:latest")
        assert "nonexistent:latest" in str(exc_info.value)
        assert "ollama pull" in str(exc_info.value)

    @patch("castor.providers.ollama_provider.urlopen")
    def test_ensure_model_auto_pulls(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),       # ping
            _mock_urlopen({"models": []}),          # list_models (empty)
            _mock_urlopen({"status": "success"}),   # pull
        ]
        provider = OllamaProvider({"provider": "ollama", "auto_pull": True})
        # Should pull and not raise
        provider._ensure_model_available("llama3:8b")

    @patch("castor.providers.ollama_provider.urlopen")
    def test_ensure_model_base_name_match(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen(MODELS_RESPONSE),
        ]
        provider = OllamaProvider({"provider": "ollama"})
        # "mistral" should match "mistral:7b"
        provider._ensure_model_available("mistral")


# ---------------------------------------------------------------------------
# Pull model with progress
# ---------------------------------------------------------------------------


class TestOllamaPullModel:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_pull_model(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen({"status": "success"}),
        ]
        provider = OllamaProvider({"provider": "ollama"})
        provider.pull_model("llama3:8b")

    @patch("castor.providers.ollama_provider.urlopen")
    def test_pull_model_with_progress(self, mock_urlopen_fn):
        chunks = [
            json.dumps({"status": "pulling manifest", "total": 0, "completed": 0}).encode(),
            json.dumps({"status": "downloading", "total": 1000, "completed": 500}).encode(),
            json.dumps({"status": "downloading", "total": 1000, "completed": 1000}).encode(),
            json.dumps({"status": "success"}).encode(),
        ]
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter(chunks))

        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            mock_stream,
        ]

        progress_calls = []
        provider = OllamaProvider({"provider": "ollama"})
        provider.pull_model("llama3:8b", progress_callback=lambda s, f: progress_calls.append((s, f)))

        assert len(progress_calls) >= 2
        assert progress_calls[-1][0] == "success"

    @patch("castor.providers.ollama_provider.urlopen")
    def test_pull_model_instance_callback(self, mock_urlopen_fn):
        chunks = [
            json.dumps({"status": "success"}).encode(),
        ]
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter(chunks))

        mock_urlopen_fn.side_effect = [
            _mock_urlopen({"status": "ok"}),
            mock_stream,
        ]

        progress_calls = []
        provider = OllamaProvider({"provider": "ollama"})
        provider.set_pull_progress_callback(lambda s, f: progress_calls.append((s, f)))
        provider.pull_model("llama3:8b")
        assert len(progress_calls) == 1


# ---------------------------------------------------------------------------
# OllamaConnectionError
# ---------------------------------------------------------------------------


class TestOllamaConnectionError:
    def test_message_localhost(self):
        err = OllamaConnectionError(
            "http://localhost:11434",
            original=ConnectionRefusedError("refused"),
        )
        msg = str(err)
        assert "localhost:11434" in msg
        assert "ollama serve" in msg.lower()

    def test_message_remote(self):
        err = OllamaConnectionError(
            "http://192.168.1.50:11434",
            original=ConnectionRefusedError("refused"),
        )
        msg = str(err)
        assert "192.168.1.50" in msg
        assert "remote" in msg.lower() or "server" in msg.lower()

    def test_timeout_message(self):
        err = OllamaConnectionError(
            "http://localhost:11434",
            original=OSError("Connection timed out"),
        )
        assert "timed out" in str(err).lower()

    def test_with_original(self):
        orig = ConnectionRefusedError("refused")
        err = OllamaConnectionError("http://localhost:11434", original=orig)
        assert err.original is orig
        assert err.host == "http://localhost:11434"

    def test_is_connection_error(self):
        err = OllamaConnectionError("http://localhost:11434")
        assert isinstance(err, ConnectionError)


class TestOllamaModelNotFoundError:
    def test_message(self):
        err = OllamaModelNotFoundError("llama3:70b", ["llava:13b", "mistral:7b"])
        msg = str(err)
        assert "llama3:70b" in msg
        assert "llava:13b" in msg
        assert "ollama pull" in msg

    def test_no_available(self):
        err = OllamaModelNotFoundError("test")
        assert "none" in str(err)


# ---------------------------------------------------------------------------
# Model alias resolution
# ---------------------------------------------------------------------------


class TestProviderAliasResolution:
    @patch("castor.providers.ollama_provider.urlopen")
    def test_resolve_alias_method(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({"provider": "ollama"})
        assert provider.resolve_alias("vision") == "llava:latest"
        assert provider.resolve_alias("fast") == "llama3.2:1b"
        assert provider.resolve_alias("mistral:7b") == "mistral:7b"

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_alias_overrides_default(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({
            "provider": "ollama",
            "model_aliases": {"vision": "minicpm-v:latest"},
        })
        assert provider.resolve_alias("vision") == "minicpm-v:latest"


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

    @patch("castor.providers.ollama_provider.urlopen")
    def test_custom_system_prompt_config(self, mock_urlopen_fn):
        mock_urlopen_fn.return_value = _mock_urlopen({"status": "ok"})
        provider = OllamaProvider({
            "provider": "ollama",
            "system_prompt": "You are a friendly helper bot.",
        })
        assert provider.system_prompt == "You are a friendly helper bot."
        assert "OpenCastor" not in provider.system_prompt
