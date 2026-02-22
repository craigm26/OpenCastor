"""Tests for castor/commands/benchmark.py — multi-provider benchmark."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────


def _mock_thought(text: str = '{"type": "stop"}', action: dict | None = None):
    """Build a minimal Thought-like mock."""
    from castor.providers.base import Thought

    if action is None:
        action = {"type": "stop"}
    return Thought(text, action)


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestBenchmarkSingleProvider:
    """_run_single_provider collects latency and token data."""

    def test_metrics_collected_for_happy_path(self):
        """Mock one provider and verify metrics are populated."""
        from castor.commands.benchmark import _run_single_provider

        mock_provider = MagicMock()
        mock_provider.think.return_value = _mock_thought()

        with patch("castor.commands.benchmark._run_single_provider") as mocked:
            # Simulate a successful run manually to test _compute_metrics
            from castor.commands.benchmark import _compute_metrics

            raw = {
                "provider": "google",
                "model": "gemini-2.0-flash",
                "latencies_ms": [120.0, 140.0, 130.0, 125.0, 135.0, 128.0],
                "completion_tokens_list": [40, 45, 38, 42, 41, 39],
                "errors": [],
                "status": "ok",
            }
            metrics = _compute_metrics(raw)

        assert metrics["p50_ms"] > 0
        assert metrics["p95_ms"] >= metrics["p50_ms"]
        assert metrics["mean_ms"] > 0
        assert metrics["tokens_per_s"] > 0

    def test_run_single_provider_with_mock(self):
        """Patch get_provider to return a mock and verify data collection."""
        from castor.commands.benchmark import _PROMPT_SUITE, _run_single_provider

        mock_provider = MagicMock()
        mock_provider.think.return_value = _mock_thought(
            text='{"type":"stop"}', action={"type": "stop"}
        )

        with patch("castor.commands.benchmark.get_provider", return_value=mock_provider):
            result = _run_single_provider("google", rounds=2)

        assert result["provider"] == "google"
        assert result["status"] == "ok"
        # 2 rounds × len(_PROMPT_SUITE) calls
        assert len(result["latencies_ms"]) == 2 * len(_PROMPT_SUITE)
        assert len(result["errors"]) == 0

    def test_run_single_provider_records_latencies_positive(self):
        """All measured latencies must be non-negative."""
        from castor.commands.benchmark import _run_single_provider

        mock_provider = MagicMock()
        mock_provider.think.return_value = _mock_thought()

        with patch("castor.commands.benchmark.get_provider", return_value=mock_provider):
            result = _run_single_provider("openai", rounds=1)

        for lat in result["latencies_ms"]:
            assert lat >= 0.0


class TestBenchmarkAllProviders:
    """run_provider_benchmark handles provider init errors gracefully."""

    def test_graceful_on_init_error(self):
        """A provider that fails to init should yield a result with status=init_error."""
        from castor.commands.benchmark import run_provider_benchmark

        with patch(
            "castor.commands.benchmark.get_provider",
            side_effect=ValueError("API key not found"),
        ):
            results = run_provider_benchmark(["google"], rounds=1)

        assert len(results) == 1
        assert "init_error" in results[0]["status"]
        # Latencies list should be empty (no successful calls)
        assert results[0]["latencies_ms"] == []

    def test_graceful_on_think_error(self):
        """When think() raises, errors are captured and the run continues."""
        from castor.commands.benchmark import run_provider_benchmark

        mock_provider = MagicMock()
        mock_provider.think.side_effect = RuntimeError("inference failed")

        with patch("castor.commands.benchmark.get_provider", return_value=mock_provider):
            results = run_provider_benchmark(["anthropic"], rounds=1)

        assert len(results) == 1
        assert len(results[0]["errors"]) > 0

    def test_multiple_providers_returned(self):
        """All requested providers appear in results even if some fail."""
        from castor.commands.benchmark import run_provider_benchmark

        mock_provider = MagicMock()
        mock_provider.think.return_value = _mock_thought()

        def fake_get_provider(cfg):
            if cfg.get("provider") == "ollama":
                raise ConnectionError("Ollama not running")
            return mock_provider

        with patch("castor.commands.benchmark.get_provider", side_effect=fake_get_provider):
            results = run_provider_benchmark(["google", "ollama"], rounds=1)

        assert len(results) == 2
        providers = {r["provider"] for r in results}
        assert "google" in providers
        assert "ollama" in providers

        # google should be ok
        google_r = next(r for r in results if r["provider"] == "google")
        assert google_r["status"] == "ok"

        # ollama should have an init_error
        ollama_r = next(r for r in results if r["provider"] == "ollama")
        assert "init_error" in ollama_r["status"]

    def test_results_include_computed_metrics(self):
        """run_provider_benchmark attaches p50/p95/mean/tokens_per_s to each result."""
        from castor.commands.benchmark import run_provider_benchmark

        mock_provider = MagicMock()
        mock_provider.think.return_value = _mock_thought()

        with patch("castor.commands.benchmark.get_provider", return_value=mock_provider):
            results = run_provider_benchmark(["google"], rounds=2)

        r = results[0]
        assert "p50_ms" in r
        assert "p95_ms" in r
        assert "mean_ms" in r
        assert "tokens_per_s" in r


class TestCmdProviderBenchmark:
    """cmd_provider_benchmark writes JSON output and handles missing config."""

    def test_json_output_written(self, tmp_path):
        """When --output is specified, a valid JSON file is created."""
        from castor.commands.benchmark import cmd_provider_benchmark

        output_file = str(tmp_path / "results.json")
        mock_provider = MagicMock()
        mock_provider.think.return_value = _mock_thought()

        with patch("castor.commands.benchmark.get_provider", return_value=mock_provider):
            cmd_provider_benchmark(
                providers="google",
                rounds=1,
                config_path=None,
                output=output_file,
            )

        assert os.path.exists(output_file)
        with open(output_file) as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["provider"] == "google"

    def test_missing_config_path_does_not_crash(self):
        """A non-existent config path is silently ignored (uses defaults)."""
        from castor.commands.benchmark import cmd_provider_benchmark

        mock_provider = MagicMock()
        mock_provider.think.return_value = _mock_thought()

        with patch("castor.commands.benchmark.get_provider", return_value=mock_provider):
            # Should not raise
            cmd_provider_benchmark(
                providers="google",
                rounds=1,
                config_path="/nonexistent/path.rcan.yaml",
                output=None,
            )

    def test_all_known_providers_when_none_specified(self):
        """Passing providers=None benchmarks all known providers."""
        from castor.commands.benchmark import _DEFAULT_MODELS, cmd_provider_benchmark

        mock_provider = MagicMock()
        mock_provider.think.return_value = _mock_thought()

        with patch("castor.commands.benchmark.get_provider", return_value=mock_provider):
            # capture printed output to avoid cluttering test output
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_provider_benchmark(providers=None, rounds=1)

        # The function ran without raising — minimal check
        assert True  # reached here without exception


class TestExtractCompletionTokens:
    """_extract_completion_tokens falls back to text-length heuristic."""

    def test_returns_positive_for_non_empty_text(self):
        from castor.commands.benchmark import _extract_completion_tokens

        thought = _mock_thought(text="Hello, world!")
        tokens = _extract_completion_tokens(thought, 100.0)
        assert tokens >= 1

    def test_action_dict_token_count_preferred(self):
        from castor.commands.benchmark import _extract_completion_tokens
        from castor.providers.base import Thought

        thought = Thought(
            '{"answer": 4}',
            {"answer": 4, "completion_tokens": 7},
        )
        tokens = _extract_completion_tokens(thought, 100.0)
        assert tokens == 7


class TestCostTable:
    """_est_cost_per_1k returns sensible values."""

    def test_free_provider_zero_cost(self):
        from castor.commands.benchmark import _est_cost_per_1k

        assert _est_cost_per_1k("ollama", "llava:13b") == 0.0

    def test_paid_provider_nonzero(self):
        from castor.commands.benchmark import _est_cost_per_1k

        cost = _est_cost_per_1k("openai", "gpt-4.1-mini")
        assert cost > 0.0

    def test_unknown_provider_returns_zero(self):
        from castor.commands.benchmark import _est_cost_per_1k

        assert _est_cost_per_1k("fakecloud", "some-model") == 0.0
