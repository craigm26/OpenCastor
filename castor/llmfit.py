"""
castor/llmfit.py — LLMFit: model-to-hardware fit check for local inference.

TurboQuant is a KV-cache-only runtime patch (not a weight format).
Model weights take the same RAM regardless. The gain is in KV cache:
TurboQuant reduces it by ~2.6x (198 bytes/token vs 512 for bf16).

References:
  - TurboQuant paper: https://arxiv.org/abs/2504.19874
  - 0xSero/turboquant: vLLM Triton integration, 2x context capacity on Qwen3.5-27B
  - flovflo/turboquant-mlx-qwen35-kv: MLX/Apple Silicon, +32% prompt throughput
  - llama.cpp discussion #20969: CPU C implementation, not yet merged
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import psutil

# ---------------------------------------------------------------------------
# Known model weight sizes (GiB) — from Ollama manifest / HuggingFace
# These are weights-only footprint at the quantization level shown.
# ---------------------------------------------------------------------------
_MODEL_WEIGHT_GB: dict[str, float] = {
    # Ollama model IDs → approx weight GB at default quant
    "gemma3:1b": 0.8,
    "gemma3:4b": 3.3,
    "gemma3:12b": 8.1,
    "gemma3:27b": 17.3,
    "qwen3:0.6b": 0.4,
    "qwen3:1.7b": 1.1,
    "qwen3:4b": 2.6,
    "qwen3:8b": 5.2,
    "qwen3:14b": 9.3,
    "qwen3:30b-a3b": 18.0,  # MoE — active params only in memory
    "qwen3.5:35b-a3b": 22.0,  # Qwen3.5 MoE tested with TurboQuant
    "llama3.2:1b": 1.3,
    "llama3.2:3b": 2.0,
    "llama3.3:70b": 43.0,
    "phi4:14b": 9.1,
    "phi4-mini:3.8b": 2.5,
    "mistral:7b": 4.1,
    "mistral-small:22b": 13.4,
    "openvla-7b": 4.2,
    "smollm2:135m": 0.3,
    "smollm2:360m": 0.7,
    "smollm2:1.7b": 1.8,
}

# ---------------------------------------------------------------------------
# Architecture params — (num_layers, hidden_dim, num_kv_heads, head_dim)
# Used to compute KV cache size per token.
# ---------------------------------------------------------------------------
_MODEL_ARCH: dict[str, tuple[int, int, int, int]] = {
    "gemma3:1b": (18, 1152, 1, 256),
    "gemma3:4b": (34, 2560, 4, 256),
    "gemma3:12b": (48, 3840, 8, 256),
    "gemma3:27b": (62, 4608, 16, 256),
    "qwen3:0.6b": (28, 1024, 8, 64),
    "qwen3:1.7b": (28, 2048, 8, 128),
    "qwen3:4b": (36, 2560, 8, 128),
    "qwen3:8b": (36, 4096, 8, 128),
    "qwen3:14b": (40, 5120, 8, 128),
    "qwen3:30b-a3b": (48, 2048, 8, 128),  # MoE
    "qwen3.5:35b-a3b": (64, 4096, 8, 128),  # 16/64 full-attn layers
    "llama3.2:1b": (16, 2048, 8, 64),
    "llama3.2:3b": (28, 3072, 8, 128),
    "llama3.3:70b": (80, 8192, 8, 128),
    "phi4:14b": (40, 5120, 8, 128),
    "phi4-mini:3.8b": (32, 3072, 8, 96),
    "mistral:7b": (32, 4096, 8, 128),
    "mistral-small:22b": (40, 6144, 8, 128),
    "openvla-7b": (32, 4096, 8, 128),
    "smollm2:135m": (30, 576, 3, 64),
    "smollm2:360m": (32, 960, 5, 64),
    "smollm2:1.7b": (24, 2048, 8, 64),
}

# Bytes per token per layer (full-precision bf16/fp16 KV)
# = 2 (K+V) * num_kv_heads * head_dim * 2 bytes (fp16)
_BYTES_PER_TOKEN_PER_LAYER_BF16 = lambda nkv, hdim: 2 * nkv * hdim * 2

# TurboQuant KV bytes per token (3-bit keys, 2-bit values via group quant)
# Measured: 198 bytes/token on Qwen3.5 full-attention layers (vs 512 bf16)
# We use a conservative 2.6x ratio for architectures where it's untested.
_TQ_COMPRESSION_RATIO = 2.6  # bf16 → TurboQuant
_TQ_FULL_ATTN_FRACTION: dict[str, float] = {
    # Models with mixed architectures (MoE/linear-attn): only full-attn layers benefit
    "qwen3:30b-a3b": 0.4,  # ~40% full-attention
    "qwen3.5:35b-a3b": 0.25,  # 16/64 = 25% full-attention
    # Standard transformers: 100% full-attention
}

OVERHEAD_GB = 0.5  # OS + Python process + GPU driver overhead


@dataclass
class LLMFitResult:
    model_id: str
    device_ram_gb: float
    weights_gb: float
    kv_cache_gb: float  # with compression if kv_compression is enabled
    kv_cache_gb_baseline: float  # without any compression
    overhead_gb: float
    total_required_gb: float
    available_gb: float
    fits: bool
    headroom_gb: float
    max_context_tokens: int
    kv_compression: str  # "none" | "turboquant"
    kv_compression_ratio: float
    kv_bits: int
    tq_status: str  # "supported" | "upstream-pending" | "unsupported"
    tq_runtime: str  # "vllm" | "llamacpp-pr" | "mlx" | "ollama-pending" | "none"
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        icon = "✅" if self.fits else "❌"
        tq = (
            f" [TQ {self.kv_compression_ratio:.1f}x]" if self.kv_compression == "turboquant" else ""
        )
        return (
            f"{icon} {self.model_id} on {self.device_ram_gb:.1f}GB RAM\n"
            f"   weights: {self.weights_gb:.1f} GB\n"
            f"   kv_cache: {self.kv_cache_gb:.2f} GB{tq} "
            f"(ctx={self.max_context_tokens:,} tokens)\n"
            f"   overhead: {self.overhead_gb:.1f} GB\n"
            f"   total: {self.total_required_gb:.1f} / {self.device_ram_gb:.1f} GB  "
            f"{'headroom: ' + str(round(self.headroom_gb, 1)) + ' GB' if self.fits else 'EXCEEDS by ' + str(round(-self.headroom_gb, 1)) + ' GB'}"
        )


def get_device_ram_gb() -> float:
    """Return available system RAM in GiB using psutil."""
    try:
        mem = psutil.virtual_memory()
        return round(mem.available / 1e9, 1)
    except Exception:
        return 0.0


def get_total_ram_gb() -> float:
    try:
        return round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        return 0.0


def _detect_npu() -> tuple[str | None, float]:
    """Return (npu_name, tops) or (None, 0.0)."""
    if Path("/dev/hailo0").exists():
        return "hailo-8", 26.0
    if Path("/dev/hailo1").exists():
        return "hailo-8l", 13.0
    return None, 0.0


def _tq_runtime_for_provider(provider: str) -> tuple[str, str]:
    """Return (tq_status, tq_runtime) for a given provider name."""
    provider = provider.lower()
    if provider == "vllm":
        return "supported", "vllm"  # 0xSero/turboquant — working, Triton kernels
    if provider in ("llamacpp", "llama.cpp", "ollama"):
        return "upstream-pending", "llamacpp-pr"  # llama.cpp discussion #20969
    if provider in ("mlx", "mlx_lm", "mlx-lm"):
        return "supported", "mlx"  # flovflo/turboquant-mlx-qwen35-kv
    return "unsupported", "none"


def check_fit(
    model_id: str,
    context_tokens: int = 8192,
    kv_compression: str = "none",  # "none" | "turboquant"
    kv_bits: int = 3,
    provider: str = "ollama",
    device_ram_gb: Optional[float] = None,
) -> LLMFitResult:
    """
    Check whether model_id fits in device RAM with the given KV compression.

    TurboQuant only compresses the KV cache — model weights are unchanged.
    For MoE models, only full-attention layers benefit (partial fraction).

    Args:
        model_id: Ollama model name (e.g. "gemma3:4b") or HF model ID.
        context_tokens: Target context window in tokens.
        kv_compression: "none" or "turboquant".
        kv_bits: KV quantization bits (2 or 3 for TurboQuant).
        provider: Runtime provider name (affects tq_status).
        device_ram_gb: Override device RAM (default: auto-detect available).

    Returns:
        LLMFitResult with all fields populated.
    """
    if device_ram_gb is None:
        device_ram_gb = get_device_ram_gb()

    # Normalise model_id
    mid = model_id.lower().strip()

    # Weight size
    weights_gb = _MODEL_WEIGHT_GB.get(mid)
    if weights_gb is None:
        # Heuristic: try to parse param count from name (e.g. "phi4:14b")
        for part in mid.replace("-", " ").replace("_", " ").split():
            if part.endswith("b") and part[:-1].replace(".", "").isdigit():
                params_b = float(part[:-1])
                # q4 GGUF ≈ 0.55 bytes/param; assume default Ollama q4
                weights_gb = round(params_b * 0.55, 1)
                break
        if weights_gb is None:
            weights_gb = 4.0  # safe default

    # Architecture
    arch = _MODEL_ARCH.get(mid)
    if arch:
        num_layers, hidden_dim, num_kv_heads, head_dim = arch
    else:
        # Estimate from name
        num_layers, hidden_dim, num_kv_heads, head_dim = 32, 4096, 8, 128

    # Full-attention fraction (for MoE models)
    full_attn_frac = _TQ_FULL_ATTN_FRACTION.get(mid, 1.0)
    full_attn_layers = max(1, round(num_layers * full_attn_frac))

    # KV cache bytes per token (baseline bf16)
    bytes_per_tok_per_layer = _BYTES_PER_TOKEN_PER_LAYER_BF16(num_kv_heads, head_dim)
    kv_bytes_baseline = bytes_per_tok_per_layer * full_attn_layers * context_tokens
    kv_cache_gb_baseline = kv_bytes_baseline / 1e9

    # With TurboQuant compression
    if kv_compression == "turboquant":
        ratio = _TQ_COMPRESSION_RATIO
        # For non-full-attention layers, no compression benefit
        non_full_layers = num_layers - full_attn_layers
        kv_non_full = bytes_per_tok_per_layer * non_full_layers * context_tokens / 1e9
        kv_full_tq = (bytes_per_tok_per_layer * full_attn_layers * context_tokens / ratio) / 1e9
        kv_cache_gb = kv_non_full + kv_full_tq
    else:
        kv_cache_gb = kv_cache_gb_baseline
        ratio = 1.0

    total_required_gb = weights_gb + kv_cache_gb + OVERHEAD_GB
    fits = total_required_gb < device_ram_gb * 0.85
    headroom_gb = round(device_ram_gb * 0.85 - total_required_gb, 2)

    # Max context with available RAM (backsolve from fit budget)
    fit_budget_gb = max(0.1, device_ram_gb * 0.85 - weights_gb - OVERHEAD_GB)
    if kv_compression == "turboquant":
        max_bytes = fit_budget_gb * 1e9 * ratio
    else:
        max_bytes = fit_budget_gb * 1e9
    bytes_per_tok_total = bytes_per_tok_per_layer * num_layers
    max_context_tokens = int(max_bytes / max(1, bytes_per_tok_total))

    # TurboQuant runtime compatibility
    tq_status, tq_runtime = _tq_runtime_for_provider(provider)

    warnings: list[str] = []
    if kv_compression == "turboquant" and tq_status == "upstream-pending":
        warnings.append(
            f"TurboQuant not yet merged in {tq_runtime}. "
            "llama.cpp discussion #20969 has a working C implementation under review. "
            "Expected in Ollama once merged."
        )
    if kv_compression == "turboquant" and full_attn_frac < 1.0:
        warnings.append(
            f"{mid} is a MoE model — only {full_attn_layers}/{num_layers} layers use "
            f"full-attention KV cache. TurboQuant benefit is partial ({full_attn_frac * 100:.0f}% of layers)."
        )
    if not fits and kv_compression == "none":
        warnings.append(
            "Model may not fit. Try enabling TurboQuant (kv_compression: turboquant) "
            "to reduce KV cache by ~2.6x."
        )
    if not fits and kv_compression == "turboquant":
        warnings.append(
            f"Still doesn't fit with TurboQuant. Consider a smaller model "
            f"(e.g. {_suggest_smaller(mid, device_ram_gb)})."
        )

    return LLMFitResult(
        model_id=mid,
        device_ram_gb=device_ram_gb,
        weights_gb=weights_gb,
        kv_cache_gb=round(kv_cache_gb, 3),
        kv_cache_gb_baseline=round(kv_cache_gb_baseline, 3),
        overhead_gb=OVERHEAD_GB,
        total_required_gb=round(total_required_gb, 2),
        available_gb=device_ram_gb,
        fits=fits,
        headroom_gb=headroom_gb,
        max_context_tokens=max_context_tokens,
        kv_compression=kv_compression,
        kv_compression_ratio=ratio,
        kv_bits=kv_bits,
        tq_status=tq_status,
        tq_runtime=tq_runtime,
        warnings=warnings,
    )


def _suggest_smaller(model_id: str, ram_gb: float) -> str:
    """Suggest a model that fits in ram_gb."""
    # Ordered by weight size
    candidates = [
        "smollm2:1.7b",
        "phi4-mini:3.8b",
        "gemma3:4b",
        "qwen3:4b",
        "llama3.2:3b",
        "qwen3:8b",
        "gemma3:12b",
        "mistral:7b",
    ]
    for cid in candidates:
        r = check_fit(
            cid, context_tokens=4096, kv_compression="none", provider="ollama", device_ram_gb=ram_gb
        )
        if r.fits:
            return cid
    return "smollm2:360m"


def turboquant_ecosystem_status() -> dict:
    """
    Return current TurboQuant implementation status across providers.

    Based on research as of 2026-03-27:
    - vLLM:     0xSero/turboquant — working Triton kernels, 2x context on Qwen3.5-27B
    - MLX:      flovflo/turboquant-mlx-qwen35-kv — +32% prompt tps, -43.7% KV cache
    - llama.cpp: discussion #20969 — CPU C implementation, under review (not merged)
    - Ollama:   depends on llama.cpp merge — not yet available
    - vLLM upstream: issue #38171 filed 2026-03-26 — requesting official support
    """
    return {
        "spec": "TurboQuant (ICLR 2026) — near-optimal KV cache quantization",
        "paper": "https://arxiv.org/abs/2504.19874",
        "method": "PolarQuant (rotation) + QJL (1-bit sign residual)",
        "compression_ratio": "2.6x (198 bytes/token vs 512 for bf16)",
        "accuracy": "zero perplexity regression on Gemma, Mistral, Qwen3.5",
        "note": "KV cache ONLY — model weights unchanged, no retraining needed",
        "runtimes": {
            "vllm": {
                "status": "supported",
                "impl": "0xSero/turboquant",
                "url": "https://github.com/0xSero/turboquant",
                "notes": "Triton kernels, tested Qwen3.5-27B (4x RTX 3090 TP=4). "
                "2x context capacity. 30GB freed across 4 GPUs.",
                "tested_models": ["qwen3.5:35b-a3b"],
                "requires_gpu": True,
            },
            "mlx": {
                "status": "supported",
                "impl": "flovflo/turboquant-mlx-qwen35-kv",
                "url": "https://huggingface.co/flovflo/turboquant-mlx-qwen35-kv",
                "notes": "+32% prompt tps, +25.7% decode tps, -43.7% KV cache. "
                "Apple Silicon only (M-series). Qwen3.5-35B-A3B-4bit tested.",
                "tested_models": ["qwen3.5:35b-a3b"],
                "requires_gpu": False,  # Apple Silicon unified memory
            },
            "llamacpp": {
                "status": "upstream-pending",
                "impl": "ggml-org/llama.cpp discussion #20969",
                "url": "https://github.com/ggml-org/llama.cpp/discussions/20969",
                "notes": "CPU C implementation (no dependencies) submitted for review. "
                "Not yet merged. Expected in Ollama once merged.",
                "tested_models": [],
                "requires_gpu": False,
            },
            "ollama": {
                "status": "upstream-pending",
                "impl": "depends on llama.cpp merge",
                "notes": "Will be available automatically once llama.cpp #20969 merges "
                "and Ollama cuts a new release.",
                "tested_models": [],
                "requires_gpu": False,
            },
        },
        "huggingface_models": {
            "note": "TurboQuant is NOT a weight format — no special model download needed. "
            "Any model can use TurboQuant at inference time via supported runtimes.",
            "tagged": "https://huggingface.co/models?other=turboquant",
            "examples": [
                {
                    "id": "mlx-community/Qwen3.5-35B-A3B-4bit",
                    "runtime": "mlx + flovflo/turboquant-mlx-qwen35-kv",
                    "result": "+32% prompt tps, -43.7% KV cache",
                },
                {
                    "id": "qwen/Qwen3.5-27B (via vLLM)",
                    "runtime": "0xSero/turboquant",
                    "result": "2x context capacity, 2.6x KV compression",
                },
            ],
        },
        "edge_recommendation": {
            "bob_pi5_hailo8": {
                "status": "pending",
                "path": "Wait for llama.cpp #20969 to merge → Ollama update",
                "interim": "Use vLLM with CPU-only if VRAM not available",
                "best_model_today": "gemma3:4b (fits 8GB RAM with 8k ctx, no TQ needed)",
                "best_model_with_tq": "qwen3:8b (fits with TurboQuant, 16k ctx headroom)",
            },
        },
    }
