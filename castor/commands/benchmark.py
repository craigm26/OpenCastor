"""
castor/commands/benchmark.py — Multi-provider latency and cost benchmark.

Runs a standard prompt suite against one or more configured AI providers,
measures latency and token throughput, and prints a Rich summary table.

Usage (via CLI)::
    castor benchmark --providers google,openai --rounds 3
    castor benchmark --config robot.rcan.yaml --providers anthropic --rounds 5 --output results.json

Usage (programmatic)::
    from castor.commands.benchmark import run_provider_benchmark
    results = run_provider_benchmark(["google", "openai"], rounds=3)
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from typing import Dict, List, Optional

logger = logging.getLogger("OpenCastor.Benchmark")

# ── Standard prompt suite ──────────────────────────────────────────────────────

# Three prompts that span the most common robot-control scenarios.
# All are text-only (image_bytes is a blank frame) so no camera is needed.
_PROMPT_SUITE: List[str] = [
    'What is 2+2? Reply with JSON: {"answer": 4}',
    (
        "You are a robot. Move forward 1 meter. "
        'Reply with JSON: {"type": "move", "linear": 1.0, "angular": 0}'
    ),
    (
        "Describe what you see and decide an action. "
        'Reply with JSON: {"type": "stop"}'
    ),
]

# Blank JPEG-like bytes — signals providers to skip image encoding.
_BLANK_FRAME: bytes = b"\x00" * 4

# ── Per-provider default model map ────────────────────────────────────────────

_DEFAULT_MODELS: Dict[str, str] = {
    "google": "gemini-2.0-flash",
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-haiku-4-5",
    "huggingface": "meta-llama/Llama-3.3-70B-Instruct",
    "ollama": "llava:13b",
    "llamacpp": "default-model",
    "mlx": "default-model",
    "vertex_ai": "gemini-2.0-flash",
}

# ── Cost estimate helper (per-1k tokens, mirroring castor/usage.py) ───────────

from castor.providers import get_provider  # noqa: E402  (patchable module-level name)
from castor.usage import _COST_TABLE  # noqa: E402  (import after stdlib)


def _est_cost_per_1k(provider: str, model: str) -> float:
    """Return estimated cost per 1k tokens (input+output average) in USD."""
    provider_table = _COST_TABLE.get(provider.lower(), {})
    price_in, price_out = (
        provider_table.get(model.lower())
        or provider_table.get(model.lower().split(":")[0])
        or provider_table.get("default")
        or (0.0, 0.0)
    )
    return (price_in + price_out) / 2.0


# ── Core benchmark logic ───────────────────────────────────────────────────────


def _build_provider_config(provider_name: str, config: Optional[dict] = None) -> dict:
    """Build a minimal provider config dict for initialisation."""
    cfg: dict = dict(config or {})
    cfg.setdefault("provider", provider_name)
    cfg.setdefault("model", _DEFAULT_MODELS.get(provider_name, "default-model"))
    return cfg


def _run_single_provider(
    provider_name: str,
    rounds: int,
    config: Optional[dict] = None,
) -> dict:
    """Benchmark one provider across all prompts × rounds.

    Returns a dict with keys:
      provider, model, latencies_ms, completion_tokens_list, errors, status
    """
    cfg = _build_provider_config(provider_name, config)
    model_name = cfg["model"]

    result: dict = {
        "provider": provider_name,
        "model": model_name,
        "latencies_ms": [],
        "completion_tokens_list": [],
        "errors": [],
        "status": "ok",
    }

    try:
        provider = get_provider(cfg)
    except Exception as exc:
        result["status"] = f"init_error: {exc}"
        logger.debug("Provider init failed for %s: %s", provider_name, exc)
        return result

    for _round in range(rounds):
        for prompt in _PROMPT_SUITE:
            t0 = time.perf_counter()
            try:
                thought = provider.think(_BLANK_FRAME, prompt)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                result["latencies_ms"].append(elapsed_ms)

                # Best-effort token count from thought metadata or heuristic
                completion_tokens = _extract_completion_tokens(thought, elapsed_ms)
                result["completion_tokens_list"].append(completion_tokens)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                result["latencies_ms"].append(elapsed_ms)
                result["completion_tokens_list"].append(0)
                result["errors"].append(str(exc))
                logger.debug("Provider %s round %d error: %s", provider_name, _round, exc)

    if result["errors"] and len(result["errors"]) == rounds * len(_PROMPT_SUITE):
        result["status"] = "all_errors"

    return result


def _extract_completion_tokens(thought, elapsed_ms: float) -> int:
    """Extract completion token count from a Thought, falling back to heuristics."""
    # Some providers attach token metadata to the Thought action dict
    if thought.action and isinstance(thought.action, dict):
        ct = thought.action.get("completion_tokens") or thought.action.get("output_tokens")
        if ct and isinstance(ct, int):
            return ct

    # Fallback: rough estimate from text length (avg ~4 chars/token)
    if thought.raw_text:
        return max(1, len(thought.raw_text) // 4)

    return 0


def _compute_metrics(result: dict) -> dict:
    """Compute p50, p95, mean latency and tokens/s from a raw result dict."""
    lats = result["latencies_ms"]
    toks = result["completion_tokens_list"]

    if not lats:
        return {
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "mean_ms": 0.0,
            "tokens_per_s": 0.0,
        }

    sorted_lats = sorted(lats)
    n = len(sorted_lats)
    p50 = sorted_lats[int(n * 0.50)]
    p95 = sorted_lats[min(int(n * 0.95), n - 1)]
    mean = statistics.mean(lats)

    total_tokens = sum(toks)
    total_s = sum(lats) / 1000.0
    tokens_per_s = total_tokens / total_s if total_s > 0 else 0.0

    return {
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "mean_ms": round(mean, 1),
        "tokens_per_s": round(tokens_per_s, 2),
    }


def run_provider_benchmark(
    providers_to_test: List[str],
    rounds: int = 3,
    config: Optional[dict] = None,
) -> List[dict]:
    """Run the standard prompt suite against each provider and return results.

    Args:
        providers_to_test: List of provider names (e.g. ``["google", "openai"]``).
        rounds:            Number of full prompt-suite repetitions per provider.
        config:            Optional base config dict merged into each provider config.

    Returns:
        List of result dicts, one per provider, each containing raw timings
        and computed metrics.
    """
    all_results: List[dict] = []

    for provider_name in providers_to_test:
        logger.info("Benchmarking provider: %s (%d round(s))", provider_name, rounds)
        raw = _run_single_provider(provider_name, rounds=rounds, config=config)
        metrics = _compute_metrics(raw)
        raw.update(metrics)
        all_results.append(raw)

    return all_results


# ── Rich table output ──────────────────────────────────────────────────────────


def print_benchmark_table(results: List[dict]) -> None:
    """Print a Rich summary table of benchmark results to stdout."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    if not has_rich:
        _print_plain_table(results)
        return

    table = Table(
        title="castor benchmark — Provider Comparison",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Provider",       style="bold",   min_width=12)
    table.add_column("Model",          min_width=20)
    table.add_column("Latency p50",    justify="right", min_width=12)
    table.add_column("Latency p95",    justify="right", min_width=12)
    table.add_column("Tokens/s",       justify="right", min_width=10)
    table.add_column("Est. $/1k tok",  justify="right", min_width=12)
    table.add_column("Status",         min_width=10)

    for r in results:
        provider = r["provider"]
        model = r["model"]
        p50 = r.get("p50_ms", 0.0)
        p95 = r.get("p95_ms", 0.0)
        tps = r.get("tokens_per_s", 0.0)
        cost_per_1k = _est_cost_per_1k(provider, model)
        status = r.get("status", "ok")

        status_style = "[green]ok[/]" if status == "ok" else f"[red]{status[:20]}[/]"
        cost_str = f"${cost_per_1k:.4f}" if cost_per_1k else "free"

        table.add_row(
            provider,
            model,
            f"{p50:.0f} ms",
            f"{p95:.0f} ms",
            f"{tps:.1f}",
            cost_str,
            status_style,
        )

    console.print()
    console.print(table)
    console.print(
        f"\n  Rounds: {results[0].get('rounds', '?') if results else '?'}  |  "
        f"Prompts per round: {len(_PROMPT_SUITE)}\n"
    )


def _print_plain_table(results: List[dict]) -> None:
    """Fallback plain-text output when Rich is not available."""
    header = (
        f"  {'Provider':<14} {'Model':<24} {'p50 ms':>8} {'p95 ms':>8} "
        f"{'Tok/s':>8} {'$/1k':>8} {'Status':<14}"
    )
    sep = "  " + "-" * (len(header) - 2)

    print("\n  castor benchmark — Provider Comparison")
    print(sep)
    print(header)
    print(sep)

    for r in results:
        provider = r["provider"]
        model = r["model"]
        p50 = r.get("p50_ms", 0.0)
        p95 = r.get("p95_ms", 0.0)
        tps = r.get("tokens_per_s", 0.0)
        cost_per_1k = _est_cost_per_1k(provider, model)
        status = r.get("status", "ok")
        cost_str = f"${cost_per_1k:.4f}" if cost_per_1k else "free"

        print(
            f"  {provider:<14} {model:<24} {p50:>7.0f} {p95:>7.0f} "
            f"{tps:>7.1f} {cost_str:>8} {status:<14}"
        )

    print(sep)
    print()


# ── CLI entry point ────────────────────────────────────────────────────────────


def cmd_provider_benchmark(
    providers: Optional[str] = None,
    rounds: int = 3,
    config_path: Optional[str] = None,
    output: Optional[str] = None,
) -> None:
    """Entry point called from :func:`castor.cli.cmd_benchmark_providers`.

    Args:
        providers:   Comma-separated provider names, or None for all known.
        rounds:      Number of prompt-suite repetitions per provider.
        config_path: Optional RCAN config file to merge into each provider config.
        output:      Optional path to write JSON results.
    """
    import yaml as _yaml

    # Resolve provider list
    known_providers = list(_DEFAULT_MODELS.keys())
    if providers:
        providers_to_test = [p.strip() for p in providers.split(",") if p.strip()]
    else:
        providers_to_test = known_providers

    # Optionally load base config
    base_config: dict = {}
    if config_path:
        try:
            with open(config_path) as f:
                loaded = _yaml.safe_load(f) or {}
            base_config = loaded.get("agent", {})
        except Exception as exc:
            logger.warning("Could not load config %s: %s", config_path, exc)

    # Run
    print(f"\n  Running benchmark: {', '.join(providers_to_test)} | {rounds} round(s) per provider\n")
    results = run_provider_benchmark(providers_to_test, rounds=rounds, config=base_config)

    # Attach rounds count to results for display
    for r in results:
        r["rounds"] = rounds

    # Print table
    print_benchmark_table(results)

    # Optionally write JSON
    if output:
        try:
            with open(output, "w") as f:
                json.dump(results, f, indent=2)
            print(f"  Results saved to: {output}\n")
        except Exception as exc:
            logger.error("Could not write output file %s: %s", output, exc)
