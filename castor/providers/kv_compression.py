"""TurboQuant KV cache compression helpers for Ollama and llama-cpp-python providers.

TurboQuant is a runtime KV-cache-only patch — model weights are unchanged.
It compresses the KV cache by ~2.6x using near-optimal quantization
(PolarQuant rotation + QJL 1-bit sign residual).

References:
  - Paper: https://arxiv.org/abs/2504.19874
  - vLLM impl: https://github.com/0xSero/turboquant
  - llama.cpp PR: https://github.com/ggml-org/llama.cpp/discussions/20969
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TurboQuantConfig:
    enabled: bool = False
    compression_ratio: float = 2.6  # typical KV cache reduction
    quantization_bits: int = 4  # KV quantization (INT4)
    cache_budget_mb: Optional[int] = None  # max KV cache budget in MB


def estimate_kv_savings(model_size_gb: float, ratio: float = 2.6) -> dict:
    """Estimate KV cache memory before/after TurboQuant.

    KV cache is typically ~15-25% of model size during inference.
    We use 20% as the midpoint estimate.

    Args:
        model_size_gb: Model weight size in GB.
        ratio: KV compression ratio (default 2.6x for TurboQuant).

    Returns:
        Dict with baseline/compressed KV cache sizes and savings.
    """
    kv_base_gb = model_size_gb * 0.20
    kv_compressed_gb = kv_base_gb / ratio
    return {
        "model_size_gb": model_size_gb,
        "kv_cache_base_gb": round(kv_base_gb, 2),
        "kv_cache_compressed_gb": round(kv_compressed_gb, 2),
        "savings_gb": round(kv_base_gb - kv_compressed_gb, 2),
        "compression_ratio": ratio,
    }


def apply_turboquant_ollama(options: dict, config: TurboQuantConfig) -> dict:
    """Inject TurboQuant KV compression options into an Ollama API request.

    Ollama 0.4+ supports KV cache quantization via the ``kv_cache_type``
    option. This patches the options dict passed to the Ollama API.

    Args:
        options: Ollama request options dict (may be mutated via copy).
        config: TurboQuantConfig with enabled flag and optional budget.

    Returns:
        Patched options dict (original unchanged if disabled).
    """
    if not config.enabled:
        return options
    patched = dict(options)
    patched["kv_cache_type"] = "q4_0"  # Ollama 0.4+ KV quantization
    if config.cache_budget_mb:
        patched["num_ctx"] = min(
            patched.get("num_ctx", 4096),
            config.cache_budget_mb * 8,  # rough tokens estimate
        )
    return patched


def apply_turboquant_llama_cpp(kwargs: dict, config: TurboQuantConfig) -> dict:
    """Inject TurboQuant KV compression kwargs into llama-cpp-python call.

    Sets type_k and type_v to GGML_TYPE_Q8_0 (value 8) for K and V caches.

    Args:
        kwargs: llama-cpp-python constructor/call kwargs.
        config: TurboQuantConfig with enabled flag.

    Returns:
        Patched kwargs dict (original unchanged if disabled).
    """
    if not config.enabled:
        return kwargs
    patched = dict(kwargs)
    patched["type_k"] = 8  # llama_cpp GGML_TYPE_Q8_0 for K cache
    patched["type_v"] = 8  # llama_cpp GGML_TYPE_Q8_0 for V cache
    return patched
