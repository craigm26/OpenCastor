"""castor.benchmarker — measure AI provider latency and throughput."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, List

logger = logging.getLogger("OpenCastor.Benchmarker")

try:
    from rich.console import Console
    from rich.table import Table

    HAS_RICH = True
except ImportError:
    HAS_RICH = False


@dataclass
class BenchmarkResult:
    provider: str
    model: str
    n: int
    latencies_ms: List[float] = field(default_factory=list)
    errors: int = 0

    @property
    def min_ms(self) -> float:
        return min(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = max(0, int(len(sorted_l) * 0.95) - 1)
        return sorted_l[idx]

    @property
    def success_rate(self) -> float:
        total = len(self.latencies_ms) + self.errors
        return len(self.latencies_ms) / total if total else 0.0


async def run_benchmark(
    think_fn: Callable[[Any], Awaitable[dict]],
    n: int = 10,
    prompt: str = "What do you see?",
    provider: str = "unknown",
    model: str = "unknown",
) -> BenchmarkResult:
    result = BenchmarkResult(provider=provider, model=model, n=n)
    for i in range(n):
        t0 = time.monotonic()
        try:
            await think_fn(prompt)
            elapsed_ms = (time.monotonic() - t0) * 1000
            result.latencies_ms.append(elapsed_ms)
        except Exception:
            result.errors += 1
        if i < n - 1:
            await asyncio.sleep(0.1)  # small gap between calls
    return result


def print_results(results: List[BenchmarkResult]) -> None:
    if HAS_RICH:
        con = Console()
        t = Table(title="Benchmark Results", show_header=True, header_style="bold dim")
        t.add_column("Provider")
        t.add_column("Model")
        t.add_column("N", justify="right")
        t.add_column("Mean ms", justify="right")
        t.add_column("Min ms", justify="right")
        t.add_column("Max ms", justify="right")
        t.add_column("p95 ms", justify="right")
        t.add_column("Errors", justify="right")
        t.add_column("Success%", justify="right")
        for r in results:
            t.add_row(
                r.provider,
                r.model,
                str(r.n),
                f"{r.mean_ms:.0f}",
                f"{r.min_ms:.0f}",
                f"{r.max_ms:.0f}",
                f"{r.p95_ms:.0f}",
                str(r.errors),
                f"{r.success_rate * 100:.0f}%",
            )
        con.print(t)
    else:
        for r in results:
            print(
                f"{r.provider}/{r.model}: mean={r.mean_ms:.0f}ms p95={r.p95_ms:.0f}ms errors={r.errors}"
            )


def _make_synthetic_image() -> bytes:
    """Generate a small synthetic 64x64 RGB JPEG image for benchmarking."""
    try:
        from PIL import Image

        img = Image.new("RGB", (64, 64), color=(100, 149, 237))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()
    except Exception:
        # Minimal JPEG-like bytes if Pillow not available
        return b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"


def run_embedding_benchmark(config: dict | None = None, backends: list | None = None) -> dict:
    """Benchmark embedding backends for latency.

    Measures p50/p95 latency over 10 calls per backend using a small
    inline test corpus (5 text strings + 3 synthetic 64x64 JPEG images).

    Results are saved to ``~/.opencastor/benchmarks/embedding_YYYY-MM-DD.json``.

    Args:
        config:   Optional config dict passed to each provider.
        backends: List of backend names to test (default: ``["mock"]``).
                  Supported values: ``"mock"``, ``"local"``, ``"gemini"``.

    Returns:
        Dict with keys: ``backends`` (list of result dicts), ``saved_to`` (path).
    """
    cfg = config or {}
    if backends is None:
        backends = ["mock"]

    _corpus_text = [
        "the robot moves forward",
        "an obstacle is detected on the left side",
        "the battery level is at 20 percent",
        "reaching the charging station",
        "navigate to the kitchen table",
    ]
    _images = [_make_synthetic_image() for _ in range(3)]

    results = []

    for backend_name in backends:
        logger.info("Benchmarking embedding backend: %s", backend_name)
        try:
            from .providers.clip_embedding_provider import CLIPEmbeddingProvider
            from .providers.gemini_embedding_provider import GeminiEmbeddingProvider

            if backend_name == "gemini":
                if not os.getenv("GOOGLE_API_KEY"):
                    logger.info("Skipping Gemini benchmark — GOOGLE_API_KEY not set")
                    results.append(
                        {"backend": "gemini", "status": "skipped", "reason": "no API key"}
                    )
                    continue
                provider = GeminiEmbeddingProvider(cfg.get("gemini", {}))
            elif backend_name == "mock":
                provider = CLIPEmbeddingProvider({"model": "mock"})
            else:
                provider = CLIPEmbeddingProvider(cfg.get("local", {}))

            latencies: list[float] = []
            errors = 0

            # Text embed calls (5 texts x 2 = 10 calls)
            for text in _corpus_text:
                t0 = time.perf_counter()
                try:
                    provider.embed(text=text)
                    latencies.append((time.perf_counter() - t0) * 1000)
                except Exception:
                    errors += 1

            # Image embed calls (3 images)
            for img in _images:
                t0 = time.perf_counter()
                try:
                    provider.embed(image_bytes=img)
                    latencies.append((time.perf_counter() - t0) * 1000)
                except Exception:
                    errors += 1

            # Mixed text+image (2 calls)
            for text, img in zip(_corpus_text[:2], _images[:2], strict=False):
                t0 = time.perf_counter()
                try:
                    provider.embed(text=text, image_bytes=img)
                    latencies.append((time.perf_counter() - t0) * 1000)
                except Exception:
                    errors += 1

            sorted_lats = sorted(latencies)
            n = len(sorted_lats)
            p50 = sorted_lats[max(0, int(n * 0.50) - 1)] if n else 0.0
            p95 = sorted_lats[max(0, int(n * 0.95) - 1)] if n else 0.0

            results.append(
                {
                    "backend": backend_name,
                    "backend_name": provider.backend_name,
                    "dimensions": provider.dimensions,
                    "n_calls": len(latencies) + errors,
                    "errors": errors,
                    "p50_ms": round(p50, 2),
                    "p95_ms": round(p95, 2),
                    "mean_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
                }
            )
            logger.info(
                "Backend %s: p50=%.1fms p95=%.1fms errors=%d",
                backend_name,
                p50,
                p95,
                errors,
            )
        except Exception as exc:
            logger.warning("Benchmark failed for backend %s: %s", backend_name, exc)
            results.append({"backend": backend_name, "error": str(exc)})

    # Save results
    today = datetime.now().strftime("%Y-%m-%d")
    save_dir = Path(os.path.expanduser("~/.opencastor/benchmarks"))
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"embedding_{today}.json"
    payload = {
        "date": today,
        "backends": results,
    }
    try:
        save_path.write_text(json.dumps(payload, indent=2))
        logger.info("Embedding benchmark results saved to %s", save_path)
    except Exception as exc:
        logger.warning("Could not save benchmark results: %s", exc)

    return {**payload, "saved_to": str(save_path)}
