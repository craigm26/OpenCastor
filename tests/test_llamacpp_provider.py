"""Tests for llama.cpp provider."""

import json
from unittest.mock import MagicMock, patch


class TestLlamaCppProvider:
    def test_init_ollama_mode(self):
        from castor.providers.llamacpp_provider import LlamaCppProvider

        with patch("urllib.request.urlopen"):
            p = LlamaCppProvider({"model": "gemma3:1b"})
            assert p._use_ollama is True
            assert p._direct_model is None

    def test_init_custom_base_url(self):
        from castor.providers.llamacpp_provider import LlamaCppProvider

        with patch("urllib.request.urlopen"):
            p = LlamaCppProvider({"model": "test", "base_url": "http://remote:8080/v1"})
            assert p._base_url == "http://remote:8080/v1"

    def test_think_ollama_parses_json(self):
        from castor.providers.llamacpp_provider import LlamaCppProvider

        with patch("urllib.request.urlopen") as mock_url:
            p = LlamaCppProvider({"model": "test"})

            response_data = {"choices": [{"message": {"content": '{"type": "stop"}'}}]}
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(response_data).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_url.return_value = mock_resp

            thought = p.think(b"\x00" * 100, "test instruction")
            assert thought.action is not None
            assert thought.action["type"] == "stop"

    def test_think_error_returns_thought(self):
        from castor.providers.llamacpp_provider import LlamaCppProvider

        with patch("urllib.request.urlopen") as mock_url:
            p = LlamaCppProvider({"model": "test"})
            mock_url.side_effect = Exception("connection refused")

            thought = p.think(b"\x00" * 100, "test")
            assert thought.action is None
            assert "Error" in thought.raw_text

    def test_provider_factory(self):
        from castor.providers import get_provider

        with patch("urllib.request.urlopen"):
            p = get_provider({"provider": "llamacpp", "model": "test"})
            assert p.__class__.__name__ == "LlamaCppProvider"

    def test_provider_factory_aliases(self):
        from castor.providers import get_provider

        for name in ["llamacpp", "llama.cpp", "llama-cpp"]:
            with patch("urllib.request.urlopen"):
                p = get_provider({"provider": name, "model": "test"})
                assert p.__class__.__name__ == "LlamaCppProvider"
