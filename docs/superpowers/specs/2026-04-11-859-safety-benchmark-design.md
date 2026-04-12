# Design: Safety Benchmark CLI — `castor safety benchmark` (craigm26/OpenCastor#859)

**Date:** 2026-04-11
**Issue:** craigm26/OpenCastor#859
**Status:** Approved — pending implementation plan
**Scope:** craigm26/OpenCastor

---

## 1. Problem Statement

OpenCastor has safety gates (ESTOP, HiTL, ConfidenceGate, BoundsChecker) that *enforce* latency constraints but do not *measure* whether they meet them. The EU AI Act requires quantified evidence that safety-critical paths perform within declared limits — a notified body reviewing a FRIA needs real numbers, not just assertions. There is also no pre-deployment validation tool that can fail a CI pipeline if a safety path regresses.

This design closes that gap: `castor safety benchmark` measures the four safety-critical software paths, writes a signed JSON artifact, and optionally inlines the results into the FRIA document produced by `castor fria generate`.

---

## 2. Approach

**Dedicated `castor/safety_benchmark.py` module** — mirrors the `castor/fria.py` pattern from #858. One module owns all benchmark logic: synthetic timing harness, live probe dispatch, threshold checking, and JSON serialization. The CLI handler (`cmd_safety_benchmark` in `castor/cli.py`) calls the module. `build_fria_document` in `castor/fria.py` gains an optional `benchmark_path` param to inline results.

---

## 3. Architecture

| Component | Responsibility |
|---|---|
| `castor/safety_benchmark.py` | `run_safety_benchmark()`, `SafetyBenchmarkResult`, `SafetyBenchmarkReport`, threshold checking, JSON serialization |
| `castor/cli.py` | `cmd_safety_benchmark` + `castor safety benchmark` subparser; add `--benchmark` to `cmd_fria_generate` |
| `castor/fria.py` | `build_fria_document` gains optional `benchmark_path` param; inlines report if file exists |

Data flows in one direction: CLI → `safety_benchmark.py` (run harness → check thresholds → serialize) → writes `safety-benchmark-{date}.json`.

---

## 4. CLI Interface

```bash
castor safety benchmark \
  [--config bot.rcan.yaml]          # RCAN config; default: auto-detect
  [--output safety-benchmark.json]  # default: safety-benchmark-{date}.json
  [--iterations 20]                 # runs per path; default: 20
  [--live]                          # connect to live robot instead of synthetic
  [--fail-fast]                     # exit 1 on first threshold breach (CI mode)
  [--json]                          # machine-readable output only (no Rich table)
```

**FRIA integration:**

```bash
castor fria generate \
  --config bot.rcan.yaml \
  --annex-iii safety_component \
  --intended-use "Indoor navigation" \
  --benchmark safety-benchmark-20260411.json
```

---

## 5. Paths Benchmarked

| Path | What is measured | Default P95 threshold |
|---|---|---|
| `estop` | Time from `SafetyLayer.emergency_stop()` call to halt state confirmed | 100 ms |
| `bounds_check` | Time to evaluate a motor command against all configured BoundsChecker limits | 5 ms |
| `confidence_gate` | Time to evaluate a confidence value through `ConfidenceGateEnforcer.evaluate()` | 2 ms |
| `full_pipeline` | Time from command received to safety-cleared or blocked (full SafetyLayer path). Always synthetic — hardware round-trip requires firmware loop (§10 out-of-scope). | 50 ms |

The 100 ms ESTOP threshold matches the existing `MOTION_003` rule in `castor/safety/protocol.py`. All thresholds are overridable via config (`safety.benchmark_thresholds.*`).

**Synthetic mode** (default): calls the Python code paths directly with mock inputs; no hardware or running robot required; works in CI. All four paths run in synthetic mode.

**Live mode** (`--live`): affects `estop` only — connects to a running robot via the configured RCAN URI. `full_pipeline` always runs synthetic (hardware round-trip requires a firmware loop; see §10 out-of-scope). `bounds_check` and `confidence_gate` are pure computation and always run synthetic. Live paths are skipped gracefully (marked `"skipped": true` in output) if the robot is unreachable.

---

## 6. `castor/safety_benchmark.py` Module

```python
BENCHMARK_SCHEMA_VERSION = "rcan-safety-benchmark-v1"

DEFAULT_THRESHOLDS = {
    "estop_p95_ms": 100.0,
    "bounds_check_p95_ms": 5.0,
    "confidence_gate_p95_ms": 2.0,
    "full_pipeline_p95_ms": 50.0,
}


@dataclass
class SafetyBenchmarkResult:
    path: str                    # "estop" | "bounds_check" | "confidence_gate" | "full_pipeline"
    iterations: int
    latencies_ms: list[float]
    threshold_p95_ms: float

    @property
    def min_ms(self) -> float: ...
    @property
    def mean_ms(self) -> float: ...
    @property
    def p95_ms(self) -> float: ...
    @property
    def p99_ms(self) -> float: ...
    @property
    def max_ms(self) -> float: ...
    @property
    def passed(self) -> bool:
        return self.p95_ms <= self.threshold_p95_ms

    def to_dict(self) -> dict: ...


@dataclass
class SafetyBenchmarkReport:
    schema: str
    generated_at: str
    mode: str                    # "synthetic" | "live"
    iterations: int
    thresholds: dict[str, float]
    results: dict[str, SafetyBenchmarkResult]

    @property
    def overall_pass(self) -> bool:
        return all(r.passed for r in self.results.values())

    def to_dict(self) -> dict: ...


def run_safety_benchmark(
    config: dict,
    iterations: int = 20,
    live: bool = False,
) -> SafetyBenchmarkReport:
    """Run all four safety path benchmarks. Returns a SafetyBenchmarkReport."""


def _bench_estop(config: dict, iterations: int, live: bool) -> SafetyBenchmarkResult:
    """Benchmark ESTOP software path."""


def _bench_bounds_check(config: dict, iterations: int) -> SafetyBenchmarkResult:
    """Benchmark BoundsChecker evaluation."""


def _bench_confidence_gate(config: dict, iterations: int) -> SafetyBenchmarkResult:
    """Benchmark ConfidenceGateEnforcer evaluation."""


def _bench_full_pipeline(config: dict, iterations: int) -> SafetyBenchmarkResult:
    """Benchmark full SafetyLayer pipeline."""
```

---

## 7. Output JSON Schema

```json
{
  "schema": "rcan-safety-benchmark-v1",
  "generated_at": "2026-04-11T09:00:00.000Z",
  "mode": "synthetic",
  "iterations": 20,
  "thresholds": {
    "estop_p95_ms": 100.0,
    "bounds_check_p95_ms": 5.0,
    "confidence_gate_p95_ms": 2.0,
    "full_pipeline_p95_ms": 50.0
  },
  "results": {
    "estop": {
      "min_ms": 0.3, "mean_ms": 1.2, "p95_ms": 4.1,
      "p99_ms": 7.2, "max_ms": 9.8, "pass": true
    },
    "bounds_check": {
      "min_ms": 0.1, "mean_ms": 0.4, "p95_ms": 0.9,
      "p99_ms": 1.1, "max_ms": 1.4, "pass": true
    },
    "confidence_gate": {
      "min_ms": 0.05, "mean_ms": 0.1, "p95_ms": 0.3,
      "p99_ms": 0.4, "max_ms": 0.5, "pass": true
    },
    "full_pipeline": {
      "min_ms": 0.4, "mean_ms": 1.8, "p95_ms": 5.2,
      "p99_ms": 8.1, "max_ms": 11.0, "pass": true
    }
  },
  "overall_pass": true
}
```

---

## 8. FRIA Integration

`build_fria_document` in `castor/fria.py` gains an optional `benchmark_path: str | None = None` parameter.

When `benchmark_path` is provided and the file exists, its contents are validated (must have `schema == "rcan-safety-benchmark-v1"`) and inlined under `safety_benchmarks` in the FRIA document:

```json
"safety_benchmarks": {
  "ref": "safety-benchmark-20260411.json",
  "generated_at": "2026-04-11T09:00:00.000Z",
  "mode": "synthetic",
  "overall_pass": true,
  "results": { ... }
}
```

When `benchmark_path` is absent or the file is missing, `safety_benchmarks` is omitted from the FRIA document (no error).

`cmd_fria_generate` gains `--benchmark FILE` argument, passed through to `build_fria_document`.

---

## 9. Testing

| Test file | What it covers |
|---|---|
| `tests/test_safety_benchmark.py` (new) | `run_safety_benchmark` returns `SafetyBenchmarkReport` with correct schema; all 4 paths present in `results`; `passed` is True when p95 ≤ threshold, False when exceeded; `overall_pass` reflects all paths; `to_dict()` produces valid JSON-serializable dict; live mode skipped gracefully when robot unreachable |
| `tests/test_fria.py` (modify) | `build_fria_document` with valid `benchmark_path` inlines `safety_benchmarks` block; missing file silently omitted; invalid schema raises `ValueError` |
| `tests/test_cli.py` (modify) | `castor safety benchmark --help` exits 0; `--fail-fast` exits 1 when `overall_pass` is False |

---

## 10. Out of Scope

- Hardware ESTOP signal timing (physical button → GPIO → software halt) — requires live hardware
- Historical benchmark trending / storage beyond one file per run
- rcan-spec §23 (benchmark schema as a dedicated spec section) — follow-on if needed
- Streaming telemetry during benchmark (use `castor/safety_telemetry.py` for runtime event counting)
- Benchmarking HiTL gate authorization round-trip (requires messaging channel; deferred)
