"""Tests for MLX provider (Apple Silicon)."""

import json
from unittest.mock import MagicMock, patch


class TestMLXProvider:
    def test_init_server_mode(self):
        from castor.providers.mlx_provider import MLXProvider

        p = MLXProvider({"model": "test", "base_url": "http://localhost:8000/v1"})
        assert p._use_server is True
        assert p._base_url == "http://localhost:8000/v1"

    def test_init_server_mode_from_env(self):
        from castor.providers.mlx_provider import MLXProvider

        with patch.dict("os.environ", {"MLX_BASE_URL": "http://myserver:9000/v1"}):
            p = MLXProvider({"model": "test"})
            assert p._use_server is True
            assert p._base_url == "http://myserver:9000/v1"

    def test_think_server_parses_json(self):
        from castor.providers.mlx_provider import MLXProvider

        p = MLXProvider({"model": "test", "base_url": "http://localhost:8000/v1"})

        response_data = {"choices": [{"message": {"content": '{"type": "stop"}'}}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            thought = p.think(b"\x00" * 100, "test")
            assert thought.action is not None
            assert thought.action["type"] == "stop"

    def test_think_server_with_vision(self):
        from castor.providers.mlx_provider import MLXProvider

        p = MLXProvider(
            {
                "model": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
                "base_url": "http://localhost:8000/v1",
                "vision_enabled": True,
            }
        )
        assert p.is_vision is True

    def test_think_error_returns_thought(self):
        from castor.providers.mlx_provider import MLXProvider

        p = MLXProvider({"model": "test", "base_url": "http://localhost:8000/v1"})
        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            thought = p.think(b"\x00" * 100, "test")
            assert thought.action is None
            assert "Error" in thought.raw_text

    def test_provider_factory_aliases(self):
        from castor.providers import get_provider

        for name in ["mlx", "mlx-lm", "vllm-mlx"]:
            p = get_provider(
                {
                    "provider": name,
                    "model": "test",
                    "base_url": "http://localhost:8000/v1",
                }
            )
            assert p.__class__.__name__ == "MLXProvider"

    def test_vision_model_detection(self):
        from castor.providers.mlx_provider import MLXProvider

        p = MLXProvider(
            {
                "model": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
                "base_url": "http://localhost:8000/v1",
            }
        )
        assert p.is_vision is True

    def test_non_vision_model(self):
        from castor.providers.mlx_provider import MLXProvider

        p = MLXProvider(
            {
                "model": "mlx-community/Llama-3.3-8B-Instruct-4bit",
                "base_url": "http://localhost:8000/v1",
            }
        )
        assert p.is_vision is False
