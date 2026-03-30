"""tests/test_turboquant.py — Tests for TurboQuant KV cache compression (#792)."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# kv_compression module tests
# ---------------------------------------------------------------------------


class TestEstimateKvSavings:
    def test_returns_all_keys(self):
        from castor.providers.kv_compression import estimate_kv_savings

        result = estimate_kv_savings(2.6)
        assert set(result.keys()) == {
            "model_size_gb",
            "kv_cache_base_gb",
            "kv_cache_compressed_gb",
            "savings_gb",
            "compression_ratio",
        }

    def test_qwen3_4b_values(self):
        """Qwen3-4B GGUF is ~2.6 GB → KV base 0.52 GB → compressed ~0.20 GB."""
        from castor.providers.kv_compression import estimate_kv_savings

        result = estimate_kv_savings(2.6)
        assert result["model_size_gb"] == 2.6
        assert result["kv_cache_base_gb"] == pytest.approx(0.52, abs=0.01)
        assert result["kv_cache_compressed_gb"] == pytest.approx(0.20, abs=0.01)
        assert result["savings_gb"] == pytest.approx(0.32, abs=0.01)
        assert result["compression_ratio"] == 2.6

    def test_custom_ratio(self):
        from castor.providers.kv_compression import estimate_kv_savings

        result = estimate_kv_savings(4.0, ratio=4.0)
        assert result["kv_cache_base_gb"] == pytest.approx(0.80, abs=0.01)
        assert result["kv_cache_compressed_gb"] == pytest.approx(0.20, abs=0.01)
        assert result["compression_ratio"] == 4.0

    def test_savings_positive(self):
        from castor.providers.kv_compression import estimate_kv_savings

        result = estimate_kv_savings(8.0)
        assert result["savings_gb"] > 0
        assert result["kv_cache_compressed_gb"] < result["kv_cache_base_gb"]


# ---------------------------------------------------------------------------
# apply_turboquant_ollama tests
# ---------------------------------------------------------------------------


class TestApplyTurboquantOllama:
    def test_injects_kv_cache_type(self):
        from castor.providers.kv_compression import TurboQuantConfig, apply_turboquant_ollama

        config = TurboQuantConfig(enabled=True)
        result = apply_turboquant_ollama({}, config)
        assert result["kv_cache_type"] == "q4_0"

    def test_disabled_returns_unchanged(self):
        from castor.providers.kv_compression import TurboQuantConfig, apply_turboquant_ollama

        original = {"num_ctx": 4096, "temperature": 0.7}
        config = TurboQuantConfig(enabled=False)
        result = apply_turboquant_ollama(original, config)
        assert result == original
        assert result is original  # same object returned when disabled

    def test_does_not_mutate_original(self):
        from castor.providers.kv_compression import TurboQuantConfig, apply_turboquant_ollama

        original = {"num_ctx": 4096}
        config = TurboQuantConfig(enabled=True)
        result = apply_turboquant_ollama(original, config)
        assert "kv_cache_type" not in original
        assert "kv_cache_type" in result

    def test_cache_budget_limits_num_ctx(self):
        from castor.providers.kv_compression import TurboQuantConfig, apply_turboquant_ollama

        config = TurboQuantConfig(enabled=True, cache_budget_mb=256)
        # budget 256 MB * 8 = 2048 tokens → caps at 2048
        result = apply_turboquant_ollama({"num_ctx": 8192}, config)
        assert result["num_ctx"] == 2048

    def test_cache_budget_does_not_increase_num_ctx(self):
        from castor.providers.kv_compression import TurboQuantConfig, apply_turboquant_ollama

        config = TurboQuantConfig(enabled=True, cache_budget_mb=1024)
        # budget 1024 MB * 8 = 8192 — num_ctx 4096 < 8192, keeps 4096
        result = apply_turboquant_ollama({"num_ctx": 4096}, config)
        assert result["num_ctx"] == 4096


# ---------------------------------------------------------------------------
# apply_turboquant_llama_cpp tests
# ---------------------------------------------------------------------------


class TestApplyTurboquantLlamaCpp:
    def test_injects_type_k_and_type_v(self):
        from castor.providers.kv_compression import TurboQuantConfig, apply_turboquant_llama_cpp

        config = TurboQuantConfig(enabled=True)
        result = apply_turboquant_llama_cpp({}, config)
        assert result["type_k"] == 8
        assert result["type_v"] == 8

    def test_disabled_returns_unchanged(self):
        from castor.providers.kv_compression import TurboQuantConfig, apply_turboquant_llama_cpp

        original = {"model_path": "/models/qwen3.gguf", "n_ctx": 4096}
        config = TurboQuantConfig(enabled=False)
        result = apply_turboquant_llama_cpp(original, config)
        assert result == original
        assert result is original

    def test_does_not_mutate_original(self):
        from castor.providers.kv_compression import TurboQuantConfig, apply_turboquant_llama_cpp

        original = {"model_path": "/models/test.gguf"}
        config = TurboQuantConfig(enabled=True)
        result = apply_turboquant_llama_cpp(original, config)
        assert "type_k" not in original
        assert "type_v" not in original
        assert result["type_k"] == 8
        assert result["type_v"] == 8

    def test_preserves_existing_kwargs(self):
        from castor.providers.kv_compression import TurboQuantConfig, apply_turboquant_llama_cpp

        original = {"model_path": "/m.gguf", "n_ctx": 8192, "n_gpu_layers": 32}
        config = TurboQuantConfig(enabled=True)
        result = apply_turboquant_llama_cpp(original, config)
        assert result["model_path"] == "/m.gguf"
        assert result["n_ctx"] == 8192
        assert result["n_gpu_layers"] == 32


# ---------------------------------------------------------------------------
# turboquant_analysis tests
# ---------------------------------------------------------------------------


class TestTurboquantAnalysis:
    def test_returns_required_fields(self):
        from castor.llmfit import turboquant_analysis

        result = turboquant_analysis("qwen3:4b")
        assert "model_name" in result
        assert "model_size_gb" in result
        assert "kv_cache_base_gb" in result
        assert "kv_cache_compressed_gb" in result
        assert "savings_gb" in result
        assert "compression_ratio" in result
        assert "turboquant_eligible" in result

    def test_4b_model_eligible(self):
        from castor.llmfit import turboquant_analysis

        result = turboquant_analysis("qwen3:4b")
        assert result["turboquant_eligible"] is True

    def test_1b_model_not_eligible(self):
        from castor.llmfit import turboquant_analysis

        result = turboquant_analysis("llama3.2:1b")
        assert result["turboquant_eligible"] is False

    def test_model_name_normalised(self):
        from castor.llmfit import turboquant_analysis

        result = turboquant_analysis("Qwen3:4B")
        assert result["model_name"] == "qwen3:4b"

    def test_savings_positive_for_4b(self):
        from castor.llmfit import turboquant_analysis

        result = turboquant_analysis("qwen3:4b")
        assert result["savings_gb"] > 0
        assert result["kv_cache_compressed_gb"] < result["kv_cache_base_gb"]

    def test_unknown_model_uses_heuristic(self):
        """A model not in _MODEL_WEIGHT_GB should still return a result."""
        from castor.llmfit import turboquant_analysis

        result = turboquant_analysis("some-new-model:7b")
        assert result["model_size_gb"] > 0
        assert "turboquant_eligible" in result
