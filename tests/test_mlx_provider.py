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


class TestMLXProviderStreaming:
    """Tests for think_stream() in server mode."""

    def _make_provider(self):
        from castor.providers.mlx_provider import MLXProvider

        return MLXProvider({"model": "test", "base_url": "http://localhost:8000/v1"})

    def _sse_lines(self, tokens):
        """Build fake SSE bytes from token list."""
        import json as _json

        lines = []
        for tok in tokens:
            chunk = {"choices": [{"delta": {"content": tok}}]}
            lines.append(f"data: {_json.dumps(chunk)}\n".encode())
        lines.append(b"data: [DONE]\n")
        return lines

    def test_stream_yields_tokens(self):
        p = self._make_provider()
        mock_resp = MagicMock()
        mock_resp.__iter__ = lambda s: iter(self._sse_lines(["move", " forward"]))
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            tokens = list(p.think_stream(b"", "go"))

        assert "move" in tokens
        assert " forward" in tokens

    def test_stream_returns_thought_via_stopiteration(self):
        p = self._make_provider()
        mock_resp = MagicMock()
        mock_resp.__iter__ = lambda s: iter(
            self._sse_lines(['{"type":"stop"}'])
        )
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            gen = p.think_stream(b"", "stop now")
            collected = []
            try:
                while True:
                    collected.append(next(gen))
            except StopIteration as exc:
                thought = exc.value

        assert thought is not None
        assert thought.action == {"type": "stop"}

    def test_stream_empty_response(self):
        p = self._make_provider()
        mock_resp = MagicMock()
        mock_resp.__iter__ = lambda s: iter([b"data: [DONE]\n"])
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            tokens = list(p.think_stream(b"", "test"))

        assert tokens == []

    def test_stream_error_yields_error_token(self):
        p = self._make_provider()
        with patch("urllib.request.urlopen", side_effect=Exception("conn refused")):
            tokens = list(p.think_stream(b"", "test"))
        assert any("Error" in t for t in tokens)


class TestMLXProviderVisionCI:
    """Mocked vision tests — no Apple Silicon required."""

    def test_vision_sends_image_url_content(self):
        """Server-mode vision: large image bytes trigger image_url content."""
        from castor.providers.mlx_provider import MLXProvider

        p = MLXProvider(
            {
                "model": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
                "base_url": "http://localhost:8000/v1",
                "vision_enabled": True,
            }
        )

        captured_payload = {}

        def fake_urlopen(req, timeout=30):
            import json as _json

            captured_payload["body"] = _json.loads(req.data.decode())
            mock = MagicMock()
            mock.read.return_value = _json.dumps(
                {"choices": [{"message": {"content": '{"type":"stop"}'}}]}
            ).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            p.think(b"\xff" * 2000, "what do you see?")

        user_msg = captured_payload["body"]["messages"][1]
        content = user_msg["content"]
        assert isinstance(content, list)
        types = [c["type"] for c in content]
        assert "image_url" in types

    def test_vision_small_image_sends_text_only(self):
        """Images <= 1024 bytes are sent as text-only (no base64)."""
        from castor.providers.mlx_provider import MLXProvider

        p = MLXProvider(
            {
                "model": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
                "base_url": "http://localhost:8000/v1",
                "vision_enabled": True,
            }
        )

        captured_payload = {}

        def fake_urlopen(req, timeout=30):
            import json as _json

            captured_payload["body"] = _json.loads(req.data.decode())
            mock = MagicMock()
            mock.read.return_value = _json.dumps(
                {"choices": [{"message": {"content": "ok"}}]}
            ).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            p.think(b"\xff" * 100, "test")  # < 1024 bytes

        user_msg = captured_payload["body"]["messages"][1]
        assert isinstance(user_msg["content"], str)

    def test_del_clears_model_references(self):
        """__del__ should clear _mlx_model and _mlx_tokenizer."""
        from castor.providers.mlx_provider import MLXProvider

        p = MLXProvider({"model": "test", "base_url": "http://localhost:8000/v1"})
        p._mlx_model = MagicMock()
        p._mlx_tokenizer = MagicMock()
        p.__del__()
        assert p._mlx_model is None
        assert p._mlx_tokenizer is None
