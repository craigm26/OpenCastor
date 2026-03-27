"""tests/test_llmfit.py — Tests for castor/llmfit.py LLMFit module."""

from __future__ import annotations

import pytest


class TestCheckFit:
    def test_gemma3_4b_fits_8gb(self):
        from castor.llmfit import check_fit

        r = check_fit("gemma3:4b", context_tokens=8192, device_ram_gb=8.0)
        assert r.fits
        assert r.weights_gb == pytest.approx(3.3, abs=0.2)
        assert r.kv_compression == "none"
        assert r.kv_cache_gb > 0

    def test_turboquant_reduces_kv_cache(self):
        from castor.llmfit import check_fit

        r_base = check_fit(
            "qwen3:8b", context_tokens=8192, kv_compression="none", device_ram_gb=8.0
        )
        r_tq = check_fit(
            "qwen3:8b", context_tokens=8192, kv_compression="turboquant", device_ram_gb=8.0
        )
        assert r_tq.kv_cache_gb < r_base.kv_cache_gb
        assert r_tq.kv_compression_ratio == pytest.approx(2.6, abs=0.1)

    def test_turboquant_does_not_change_weights(self):
        from castor.llmfit import check_fit

        r_base = check_fit(
            "gemma3:4b", context_tokens=8192, kv_compression="none", device_ram_gb=8.0
        )
        r_tq = check_fit(
            "gemma3:4b", context_tokens=8192, kv_compression="turboquant", device_ram_gb=8.0
        )
        assert r_base.weights_gb == r_tq.weights_gb, "TurboQuant must not change weight size"

    def test_turboquant_increases_max_context(self):
        from castor.llmfit import check_fit

        r_base = check_fit(
            "gemma3:4b", context_tokens=8192, kv_compression="none", device_ram_gb=8.0
        )
        r_tq = check_fit(
            "gemma3:4b", context_tokens=8192, kv_compression="turboquant", device_ram_gb=8.0
        )
        assert r_tq.max_context_tokens > r_base.max_context_tokens

    def test_vllm_provider_shows_supported(self):
        from castor.llmfit import check_fit

        r = check_fit("qwen3:8b", kv_compression="turboquant", provider="vllm", device_ram_gb=48.0)
        assert r.tq_status == "supported"
        assert r.tq_runtime == "vllm"

    def test_ollama_provider_shows_pending(self):
        from castor.llmfit import check_fit

        r = check_fit(
            "gemma3:4b", kv_compression="turboquant", provider="ollama", device_ram_gb=8.0
        )
        assert r.tq_status == "upstream-pending"
        assert "llamacpp" in r.tq_runtime

    def test_mlx_provider_shows_supported(self):
        from castor.llmfit import check_fit

        r = check_fit(
            "qwen3.5:35b-a3b", kv_compression="turboquant", provider="mlx", device_ram_gb=64.0
        )
        assert r.tq_status == "supported"
        assert r.tq_runtime == "mlx"

    def test_moe_model_partial_compression(self):
        from castor.llmfit import check_fit

        check_fit("qwen3:8b", context_tokens=8192, kv_compression="turboquant", device_ram_gb=16.0)
        r_moe = check_fit(
            "qwen3.5:35b-a3b", context_tokens=8192, kv_compression="turboquant", device_ram_gb=64.0
        )
        # MoE warning about partial compression
        moe_warns = [
            w
            for w in r_moe.warnings
            if "MoE" in w or "partial" in w.lower() or "fraction" in w.lower()
        ]
        assert len(moe_warns) >= 1

    def test_unknown_model_fallback(self):
        from castor.llmfit import check_fit

        r = check_fit("mymodel:99b", context_tokens=4096, device_ram_gb=8.0)
        assert r.weights_gb > 0  # heuristic kicks in

    def test_no_fit_suggests_smaller_model(self):
        from castor.llmfit import check_fit

        r = check_fit("llama3.3:70b", context_tokens=32768, device_ram_gb=8.0)
        assert not r.fits
        assert len(r.warnings) >= 1

    def test_summary_string(self):
        from castor.llmfit import check_fit

        r = check_fit("gemma3:4b", context_tokens=4096, device_ram_gb=8.0)
        summary = r.summary()
        assert "gemma3:4b" in summary
        assert "GB" in summary

    def test_fit_result_fields(self):
        from castor.llmfit import check_fit

        r = check_fit("gemma3:4b", context_tokens=4096, device_ram_gb=8.0)
        assert isinstance(r.fits, bool)
        assert r.max_context_tokens > 0
        assert r.kv_cache_gb_baseline >= r.kv_cache_gb  # baseline >= compressed
        assert r.overhead_gb == 0.5


class TestEcosystemStatus:
    def test_ecosystem_status_keys(self):
        from castor.llmfit import turboquant_ecosystem_status

        eco = turboquant_ecosystem_status()
        assert "runtimes" in eco
        assert "huggingface_models" in eco
        for name in ("vllm", "mlx", "llamacpp", "ollama"):
            assert name in eco["runtimes"]

    def test_vllm_is_supported(self):
        from castor.llmfit import turboquant_ecosystem_status

        eco = turboquant_ecosystem_status()
        assert eco["runtimes"]["vllm"]["status"] == "supported"

    def test_ollama_is_pending(self):
        from castor.llmfit import turboquant_ecosystem_status

        eco = turboquant_ecosystem_status()
        assert eco["runtimes"]["ollama"]["status"] == "upstream-pending"


class TestDeviceRam:
    def test_get_device_ram_returns_float(self):
        from castor.llmfit import get_device_ram_gb, get_total_ram_gb

        assert get_device_ram_gb() > 0
        assert get_total_ram_gb() >= get_device_ram_gb()
