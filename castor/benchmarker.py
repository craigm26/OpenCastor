"""castor.benchmarker — measure AI provider latency and throughput."""
from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List

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
                r.provider, r.model, str(r.n),
                f"{r.mean_ms:.0f}", f"{r.min_ms:.0f}", f"{r.max_ms:.0f}",
                f"{r.p95_ms:.0f}", str(r.errors), f"{r.success_rate*100:.0f}%",
            )
        con.print(t)
    else:
        for r in results:
            print(f"{r.provider}/{r.model}: mean={r.mean_ms:.0f}ms p95={r.p95_ms:.0f}ms errors={r.errors}")
