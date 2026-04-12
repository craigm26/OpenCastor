# Safety Benchmark CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `castor safety benchmark` — a CLI that measures latency of the four safety-critical software paths, writes a signed JSON artifact, and optionally inlines results into the FRIA document.

**Architecture:** A new `castor/safety_benchmark.py` module owns all benchmark logic (dataclasses, timing harness, threshold checking, serialization). The CLI handler `cmd_safety_benchmark` in `castor/cli.py` replaces the stub `cmd_safety`. `build_fria_document` in `castor/fria.py` gains an optional `benchmark_path` parameter.

**Tech Stack:** Python 3.10+, `statistics` stdlib (percentile), `time.perf_counter` for timing, `castor.safety.protocol.SafetyProtocol`, `castor.safety.bounds.BoundsResult`/`BoundsStatus`, `castor.confidence_gate.ConfidenceGate`/`ConfidenceGateEnforcer`/`GateOutcome`, Rich (optional, for table display).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `castor/safety_benchmark.py` | Create | All benchmark logic: dataclasses, timing harness, threshold checking, JSON serialization |
| `tests/test_safety_benchmark.py` | Create | Unit tests for the module |
| `castor/cli.py` | Modify | Replace stub `cmd_safety`; wire `cmd_safety_benchmark`; refactor safety subparser; add `--benchmark` to `cmd_fria_generate` |
| `castor/fria.py` | Modify | `build_fria_document` gains optional `benchmark_path` param |
| `tests/test_fria.py` | Modify | Tests for benchmark inlining in `build_fria_document` |
| `tests/test_cli.py` | Modify | CLI smoke tests for `castor safety benchmark` |

---

## Task 1: Dataclasses + `_bench_bounds_check` + `_bench_confidence_gate`

**Files:**
- Create: `castor/safety_benchmark.py`
- Create: `tests/test_safety_benchmark.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_safety_benchmark.py
"""Tests for castor.safety_benchmark."""
from __future__ import annotations

import pytest

from castor.safety_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    DEFAULT_THRESHOLDS,
    SafetyBenchmarkReport,
    SafetyBenchmarkResult,
    _bench_bounds_check,
    _bench_confidence_gate,
)


# ---------------------------------------------------------------------------
# SafetyBenchmarkResult
# ---------------------------------------------------------------------------


def _make_result(latencies: list[float], threshold: float = 100.0) -> SafetyBenchmarkResult:
    return SafetyBenchmarkResult(
        path="estop",
        iterations=len(latencies),
        latencies_ms=latencies,
        threshold_p95_ms=threshold,
    )


class TestSafetyBenchmarkResult:
    def test_min_ms(self):
        r = _make_result([3.0, 1.0, 2.0])
        assert r.min_ms == pytest.approx(1.0)

    def test_max_ms(self):
        r = _make_result([3.0, 1.0, 2.0])
        assert r.max_ms == pytest.approx(3.0)

    def test_mean_ms(self):
        r = _make_result([2.0, 4.0])
        assert r.mean_ms == pytest.approx(3.0)

    def test_p95_ms_exact(self):
        # 20 values: p95 = 95th percentile
        latencies = [float(i) for i in range(1, 21)]  # 1..20
        r = _make_result(latencies)
        # statistics.quantiles uses inclusive method; p95 of 1..20 ~ 19.05
        assert 18.0 <= r.p95_ms <= 20.0

    def test_p99_ms_gte_p95(self):
        latencies = [float(i) for i in range(1, 21)]
        r = _make_result(latencies)
        assert r.p99_ms >= r.p95_ms

    def test_passed_when_p95_below_threshold(self):
        latencies = [1.0] * 20  # p95 = 1.0
        r = _make_result(latencies, threshold=5.0)
        assert r.passed is True

    def test_failed_when_p95_exceeds_threshold(self):
        latencies = [200.0] * 20  # p95 = 200.0
        r = _make_result(latencies, threshold=100.0)
        assert r.passed is False

    def test_passed_at_exact_threshold(self):
        latencies = [5.0] * 20
        r = _make_result(latencies, threshold=5.0)
        assert r.passed is True

    def test_to_dict_keys(self):
        r = _make_result([1.0, 2.0, 3.0])
        d = r.to_dict()
        assert set(d.keys()) == {"min_ms", "mean_ms", "p95_ms", "p99_ms", "max_ms", "pass"}

    def test_to_dict_pass_field(self):
        latencies = [1.0] * 20
        r = _make_result(latencies, threshold=5.0)
        assert r.to_dict()["pass"] is True


# ---------------------------------------------------------------------------
# SafetyBenchmarkReport
# ---------------------------------------------------------------------------


def _make_report(all_pass: bool) -> SafetyBenchmarkReport:
    threshold = 100.0
    latencies = [1.0] * 20 if all_pass else [200.0] * 20
    result = SafetyBenchmarkResult(
        path="estop", iterations=20, latencies_ms=latencies, threshold_p95_ms=threshold
    )
    return SafetyBenchmarkReport(
        schema=BENCHMARK_SCHEMA_VERSION,
        generated_at="2026-04-11T00:00:00Z",
        mode="synthetic",
        iterations=20,
        thresholds=dict(DEFAULT_THRESHOLDS),
        results={"estop": result},
    )


class TestSafetyBenchmarkReport:
    def test_overall_pass_true_when_all_results_pass(self):
        report = _make_report(all_pass=True)
        assert report.overall_pass is True

    def test_overall_pass_false_when_any_result_fails(self):
        report = _make_report(all_pass=False)
        assert report.overall_pass is False

    def test_to_dict_has_required_top_level_keys(self):
        report = _make_report(all_pass=True)
        d = report.to_dict()
        for key in ("schema", "generated_at", "mode", "iterations", "thresholds", "results", "overall_pass"):
            assert key in d

    def test_to_dict_results_serialized(self):
        report = _make_report(all_pass=True)
        d = report.to_dict()
        assert "estop" in d["results"]
        assert "p95_ms" in d["results"]["estop"]


# ---------------------------------------------------------------------------
# _bench_bounds_check
# ---------------------------------------------------------------------------


class TestBenchBoundsCheck:
    def test_returns_result_for_bounds_check_path(self):
        result = _bench_bounds_check(config={}, iterations=5)
        assert result.path == "bounds_check"

    def test_iteration_count_matches(self):
        result = _bench_bounds_check(config={}, iterations=7)
        assert result.iterations == 7
        assert len(result.latencies_ms) == 7

    def test_all_latencies_non_negative(self):
        result = _bench_bounds_check(config={}, iterations=10)
        assert all(ms >= 0 for ms in result.latencies_ms)

    def test_threshold_from_defaults(self):
        result = _bench_bounds_check(config={}, iterations=5)
        assert result.threshold_p95_ms == DEFAULT_THRESHOLDS["bounds_check_p95_ms"]

    def test_threshold_from_config_override(self):
        config = {"safety": {"benchmark_thresholds": {"bounds_check_p95_ms": 99.0}}}
        result = _bench_bounds_check(config=config, iterations=5)
        assert result.threshold_p95_ms == pytest.approx(99.0)

    def test_passes_with_default_threshold(self):
        # Pure computation — should be well under 5ms threshold
        result = _bench_bounds_check(config={}, iterations=20)
        assert result.passed is True


# ---------------------------------------------------------------------------
# _bench_confidence_gate
# ---------------------------------------------------------------------------


class TestBenchConfidenceGate:
    def test_returns_result_for_confidence_gate_path(self):
        result = _bench_confidence_gate(config={}, iterations=5)
        assert result.path == "confidence_gate"

    def test_iteration_count_matches(self):
        result = _bench_confidence_gate(config={}, iterations=8)
        assert result.iterations == 8
        assert len(result.latencies_ms) == 8

    def test_all_latencies_non_negative(self):
        result = _bench_confidence_gate(config={}, iterations=10)
        assert all(ms >= 0 for ms in result.latencies_ms)

    def test_threshold_from_defaults(self):
        result = _bench_confidence_gate(config={}, iterations=5)
        assert result.threshold_p95_ms == DEFAULT_THRESHOLDS["confidence_gate_p95_ms"]

    def test_passes_with_default_threshold(self):
        # Pure Python — should be well under 2ms threshold
        result = _bench_confidence_gate(config={}, iterations=20)
        assert result.passed is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_safety_benchmark.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'castor.safety_benchmark'`

- [ ] **Step 3: Create `castor/safety_benchmark.py` with dataclasses and the two pure-compute bench functions**

```python
"""Safety path latency benchmark for EU AI Act evidence (RCAN #859).

Measures the four safety-critical software paths and writes a signed JSON
artifact. Designed to be run in CI (synthetic mode, default) or against a
live robot (--live flag, affects estop and full_pipeline only).
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

BENCHMARK_SCHEMA_VERSION = "rcan-safety-benchmark-v1"

DEFAULT_THRESHOLDS: dict[str, float] = {
    "estop_p95_ms": 100.0,
    "bounds_check_p95_ms": 5.0,
    "confidence_gate_p95_ms": 2.0,
    "full_pipeline_p95_ms": 50.0,
}


def _get_threshold(config: dict, key: str) -> float:
    """Return threshold from config override or DEFAULT_THRESHOLDS."""
    overrides = config.get("safety", {}).get("benchmark_thresholds", {})
    return float(overrides.get(key, DEFAULT_THRESHOLDS[key]))


@dataclass
class SafetyBenchmarkResult:
    path: str          # "estop" | "bounds_check" | "confidence_gate" | "full_pipeline"
    iterations: int
    latencies_ms: list[float]
    threshold_p95_ms: float

    @property
    def min_ms(self) -> float:
        return min(self.latencies_ms)

    @property
    def max_ms(self) -> float:
        return max(self.latencies_ms)

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.latencies_ms)

    @property
    def p95_ms(self) -> float:
        return statistics.quantiles(self.latencies_ms, n=100)[94]

    @property
    def p99_ms(self) -> float:
        return statistics.quantiles(self.latencies_ms, n=100)[98]

    @property
    def passed(self) -> bool:
        return self.p95_ms <= self.threshold_p95_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_ms": round(self.min_ms, 4),
            "mean_ms": round(self.mean_ms, 4),
            "p95_ms": round(self.p95_ms, 4),
            "p99_ms": round(self.p99_ms, 4),
            "max_ms": round(self.max_ms, 4),
            "pass": self.passed,
        }


@dataclass
class SafetyBenchmarkReport:
    schema: str
    generated_at: str
    mode: str          # "synthetic" | "live"
    iterations: int
    thresholds: dict[str, float]
    results: dict[str, SafetyBenchmarkResult]

    @property
    def overall_pass(self) -> bool:
        return all(r.passed for r in self.results.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "mode": self.mode,
            "iterations": self.iterations,
            "thresholds": dict(self.thresholds),
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "overall_pass": self.overall_pass,
        }


def _bench_bounds_check(config: dict, iterations: int) -> SafetyBenchmarkResult:
    """Benchmark BoundsChecker evaluation (pure computation, always synthetic)."""
    from castor.safety.bounds import BoundsResult, BoundsStatus

    threshold = _get_threshold(config, "bounds_check_p95_ms")
    latencies: list[float] = []

    # Realistic action: motor command within safe limits
    action_results = [
        BoundsResult(status=BoundsStatus.OK, details="within limits", margin=0.5),
        BoundsResult(status=BoundsStatus.OK, details="within limits", margin=0.3),
    ]

    for _ in range(iterations):
        t0 = time.perf_counter()
        BoundsResult.combine(action_results)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)

    return SafetyBenchmarkResult(
        path="bounds_check",
        iterations=iterations,
        latencies_ms=latencies,
        threshold_p95_ms=threshold,
    )


def _bench_confidence_gate(config: dict, iterations: int) -> SafetyBenchmarkResult:
    """Benchmark ConfidenceGateEnforcer evaluation (pure computation, always synthetic)."""
    from castor.confidence_gate import ConfidenceGate, ConfidenceGateEnforcer

    threshold = _get_threshold(config, "confidence_gate_p95_ms")
    latencies: list[float] = []

    # Use a realistic gate (CONTROL scope, 0.75 threshold)
    enforcer = ConfidenceGateEnforcer([
        ConfidenceGate(scope="control", min_confidence=0.75, on_fail="block"),
    ])

    for _ in range(iterations):
        t0 = time.perf_counter()
        enforcer.evaluate("control", 0.8)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)

    return SafetyBenchmarkResult(
        path="confidence_gate",
        iterations=iterations,
        latencies_ms=latencies,
        threshold_p95_ms=threshold,
    )


def _bench_estop(
    config: dict, iterations: int, live: bool
) -> SafetyBenchmarkResult:
    """Benchmark ESTOP software path. Live mode: connect to robot via RCAN URI."""
    raise NotImplementedError("implemented in Task 2")


def _bench_full_pipeline(
    config: dict, iterations: int, live: bool
) -> SafetyBenchmarkResult:
    """Benchmark full SafetyLayer pipeline. Live mode: real round-trip latency."""
    raise NotImplementedError("implemented in Task 2")


def run_safety_benchmark(
    config: dict,
    iterations: int = 20,
    live: bool = False,
) -> SafetyBenchmarkReport:
    """Run all four safety path benchmarks. Returns a SafetyBenchmarkReport."""
    raise NotImplementedError("implemented in Task 2")
```

- [ ] **Step 4: Run Task 1 tests (dataclasses + two bench functions)**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_safety_benchmark.py::TestSafetyBenchmarkResult \
       tests/test_safety_benchmark.py::TestSafetyBenchmarkReport \
       tests/test_safety_benchmark.py::TestBenchBoundsCheck \
       tests/test_safety_benchmark.py::TestBenchConfidenceGate \
       -v
```

Expected: all tests pass. If `statistics.quantiles` raises for small lists (needs n >= 4 for 100 quantiles), either ensure iteration count >= 20 in fixture or catch the error in the property.

> **Note on `statistics.quantiles`:** Python's `statistics.quantiles(data, n=100)` requires `len(data) >= 2`. For p95/p99, the quantile index may not exist if `len(data) < 100` — use `min(len(data)-1, index)` guard:
>
> ```python
> @property
> def p95_ms(self) -> float:
>     q = statistics.quantiles(self.latencies_ms, n=100)
>     return q[min(94, len(q) - 1)]
>
> @property
> def p99_ms(self) -> float:
>     q = statistics.quantiles(self.latencies_ms, n=100)
>     return q[min(98, len(q) - 1)]
> ```
>
> Apply this if tests fail with `StatisticsError`.

- [ ] **Step 5: Lint**

```bash
cd /home/craigm26/OpenCastor
ruff check castor/safety_benchmark.py tests/test_safety_benchmark.py --fix
ruff format castor/safety_benchmark.py tests/test_safety_benchmark.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd /home/craigm26/OpenCastor
git add castor/safety_benchmark.py tests/test_safety_benchmark.py
git commit -m "feat(#859): add SafetyBenchmarkResult/Report dataclasses + bounds_check + confidence_gate bench"
```

---

## Task 2: `_bench_estop` + `_bench_full_pipeline` + `run_safety_benchmark`

**Files:**
- Modify: `castor/safety_benchmark.py`
- Modify: `tests/test_safety_benchmark.py`

- [ ] **Step 1: Write the failing tests**

Add these test classes to `tests/test_safety_benchmark.py`:

```python
from castor.safety_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    DEFAULT_THRESHOLDS,
    SafetyBenchmarkReport,
    SafetyBenchmarkResult,
    _bench_bounds_check,
    _bench_confidence_gate,
    _bench_estop,
    _bench_full_pipeline,
    run_safety_benchmark,
)


# ---------------------------------------------------------------------------
# _bench_estop (synthetic mode)
# ---------------------------------------------------------------------------


class TestBenchEstop:
    def test_returns_result_for_estop_path(self):
        result = _bench_estop(config={}, iterations=5, live=False)
        assert result.path == "estop"

    def test_iteration_count_matches(self):
        result = _bench_estop(config={}, iterations=6, live=False)
        assert result.iterations == 6
        assert len(result.latencies_ms) == 6

    def test_all_latencies_non_negative(self):
        result = _bench_estop(config={}, iterations=10, live=False)
        assert all(ms >= 0 for ms in result.latencies_ms)

    def test_threshold_matches_default(self):
        result = _bench_estop(config={}, iterations=5, live=False)
        assert result.threshold_p95_ms == DEFAULT_THRESHOLDS["estop_p95_ms"]

    def test_passes_with_default_threshold(self):
        result = _bench_estop(config={}, iterations=20, live=False)
        assert result.passed is True

    def test_live_skipped_when_no_uri(self):
        """Live mode with no RCAN URI → skipped gracefully (latencies empty list)."""
        result = _bench_estop(config={}, iterations=5, live=True)
        # Either skipped (0 latencies, skipped=True) or ran synthetic fallback
        assert result.path == "estop"


# ---------------------------------------------------------------------------
# _bench_full_pipeline (synthetic mode)
# ---------------------------------------------------------------------------


class TestBenchFullPipeline:
    def test_returns_result_for_full_pipeline_path(self):
        result = _bench_full_pipeline(config={}, iterations=5, live=False)
        assert result.path == "full_pipeline"

    def test_iteration_count_matches(self):
        result = _bench_full_pipeline(config={}, iterations=7, live=False)
        assert result.iterations == 7
        assert len(result.latencies_ms) == 7

    def test_all_latencies_non_negative(self):
        result = _bench_full_pipeline(config={}, iterations=10, live=False)
        assert all(ms >= 0 for ms in result.latencies_ms)

    def test_threshold_matches_default(self):
        result = _bench_full_pipeline(config={}, iterations=5, live=False)
        assert result.threshold_p95_ms == DEFAULT_THRESHOLDS["full_pipeline_p95_ms"]

    def test_passes_with_default_threshold(self):
        result = _bench_full_pipeline(config={}, iterations=20, live=False)
        assert result.passed is True


# ---------------------------------------------------------------------------
# run_safety_benchmark
# ---------------------------------------------------------------------------


class TestRunSafetyBenchmark:
    def test_returns_safety_benchmark_report(self):
        report = run_safety_benchmark(config={}, iterations=5, live=False)
        assert isinstance(report, SafetyBenchmarkReport)

    def test_schema_version_correct(self):
        report = run_safety_benchmark(config={}, iterations=5)
        assert report.schema == BENCHMARK_SCHEMA_VERSION

    def test_all_four_paths_present(self):
        report = run_safety_benchmark(config={}, iterations=5)
        assert set(report.results.keys()) == {
            "estop", "bounds_check", "confidence_gate", "full_pipeline"
        }

    def test_mode_synthetic_by_default(self):
        report = run_safety_benchmark(config={}, iterations=5)
        assert report.mode == "synthetic"

    def test_mode_live_when_live_flag_set(self):
        report = run_safety_benchmark(config={}, iterations=5, live=True)
        assert report.mode == "live"

    def test_overall_pass_reflects_all_paths(self):
        report = run_safety_benchmark(config={}, iterations=20)
        # Pure compute paths are well within thresholds
        assert report.overall_pass is True

    def test_to_dict_produces_json_serializable_output(self):
        import json
        report = run_safety_benchmark(config={}, iterations=5)
        d = report.to_dict()
        # Should not raise
        json.dumps(d)

    def test_overall_pass_false_when_threshold_very_low(self):
        """Force a failure by setting an impossibly low threshold."""
        config = {"safety": {"benchmark_thresholds": {
            "bounds_check_p95_ms": 0.000001,
        }}}
        report = run_safety_benchmark(config=config, iterations=20)
        assert report.overall_pass is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_safety_benchmark.py::TestBenchEstop \
       tests/test_safety_benchmark.py::TestBenchFullPipeline \
       tests/test_safety_benchmark.py::TestRunSafetyBenchmark \
       -v 2>&1 | head -20
```

Expected: `NotImplementedError` for all three functions.

- [ ] **Step 3: Implement the three remaining functions in `castor/safety_benchmark.py`**

Replace the three `raise NotImplementedError` stubs with:

```python
def _bench_estop(
    config: dict, iterations: int, live: bool
) -> SafetyBenchmarkResult:
    """Benchmark ESTOP software path.

    Synthetic: calls _check_estop_response directly with a compliant action dict.
    Live: connects to running robot via RCAN URI (skipped gracefully if unreachable).
    """
    from castor.safety.protocol import _check_estop_response

    threshold = _get_threshold(config, "estop_p95_ms")

    if live:
        rcan_uri = config.get("metadata", {}).get("rcan_uri", "")
        if not rcan_uri:
            # No URI configured — skip live path gracefully
            return SafetyBenchmarkResult(
                path="estop",
                iterations=0,
                latencies_ms=[],
                threshold_p95_ms=threshold,
            )
        try:
            import socket
            import urllib.parse

            parsed = urllib.parse.urlparse(rcan_uri)
            host = parsed.hostname or "localhost"
            port = parsed.port or 8000
            socket.setdefaulttimeout(2.0)
            with socket.create_connection((host, port), timeout=2.0):
                pass
        except OSError:
            # Robot unreachable — skip gracefully
            return SafetyBenchmarkResult(
                path="estop",
                iterations=0,
                latencies_ms=[],
                threshold_p95_ms=threshold,
            )

    # Synthetic: time the pure rule check function directly
    action = {"estop_response_ms": 5.0}
    params = {"max_response_ms": threshold}
    latencies: list[float] = []

    for _ in range(iterations):
        t0 = time.perf_counter()
        _check_estop_response(action, params)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)

    return SafetyBenchmarkResult(
        path="estop",
        iterations=iterations,
        latencies_ms=latencies,
        threshold_p95_ms=threshold,
    )


def _bench_full_pipeline(
    config: dict, iterations: int, live: bool
) -> SafetyBenchmarkResult:
    """Benchmark full SafetyProtocol pipeline (all enabled rules).

    Synthetic: instantiates SafetyProtocol and calls check_action.
    Live: same as synthetic (round-trip to robot hardware is out of scope here).
    """
    from castor.safety.protocol import SafetyProtocol

    threshold = _get_threshold(config, "full_pipeline_p95_ms")
    protocol = SafetyProtocol()

    # Realistic action: motor command with safe values on all rule axes
    action = {
        "linear_velocity": 0.5,       # under MOTION_001 limit (1.0 m/s)
        "angular_velocity": 0.5,      # under MOTION_002 limit (2.0 rad/s)
        "estop_response_ms": 5.0,     # under MOTION_003 limit (100 ms)
        "estop_available": True,      # EMERGENCY_001 satisfied
        "destructive": False,         # PROPERTY_001 not triggered
        "sensor_active": False,       # PRIVACY_001 not triggered
    }

    latencies: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        protocol.check_action(action)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)

    return SafetyBenchmarkResult(
        path="full_pipeline",
        iterations=iterations,
        latencies_ms=latencies,
        threshold_p95_ms=threshold,
    )


def run_safety_benchmark(
    config: dict,
    iterations: int = 20,
    live: bool = False,
) -> SafetyBenchmarkReport:
    """Run all four safety path benchmarks. Returns a SafetyBenchmarkReport."""
    results = {
        "estop": _bench_estop(config, iterations, live),
        "bounds_check": _bench_bounds_check(config, iterations),
        "confidence_gate": _bench_confidence_gate(config, iterations),
        "full_pipeline": _bench_full_pipeline(config, iterations, live),
    }

    return SafetyBenchmarkReport(
        schema=BENCHMARK_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode="live" if live else "synthetic",
        iterations=iterations,
        thresholds={k: _get_threshold(config, k) for k in DEFAULT_THRESHOLDS},
        results=results,
    )
```

- [ ] **Step 4: Run all safety benchmark tests**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_safety_benchmark.py -v
```

Expected: all tests pass.

> **If `_check_estop_response` is not importable** (it's a module-level private function), use this alternative for `_bench_estop` synthetic timing:
>
> ```python
> from castor.safety.protocol import SafetyProtocol
> protocol = SafetyProtocol()
> rule = protocol.get_rule("MOTION_003")
> for _ in range(iterations):
>     t0 = time.perf_counter()
>     rule.evaluate({"estop_response_ms": 5.0})
>     elapsed_ms = (time.perf_counter() - t0) * 1000.0
>     latencies.append(elapsed_ms)
> ```
>
> And remove the `from castor.safety.protocol import _check_estop_response` import from the test file accordingly.

- [ ] **Step 5: Lint**

```bash
cd /home/craigm26/OpenCastor
ruff check castor/safety_benchmark.py tests/test_safety_benchmark.py --fix
ruff format castor/safety_benchmark.py tests/test_safety_benchmark.py
```

- [ ] **Step 6: Commit**

```bash
cd /home/craigm26/OpenCastor
git add castor/safety_benchmark.py tests/test_safety_benchmark.py
git commit -m "feat(#859): implement _bench_estop, _bench_full_pipeline, run_safety_benchmark"
```

---

## Task 3: CLI Wiring

**Files:**
- Modify: `castor/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI tests**

Append to `tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# castor safety benchmark CLI
# ---------------------------------------------------------------------------


class TestSafetyBenchmarkCli:
    def test_safety_benchmark_help_exits_zero(self):
        """castor safety benchmark --help exits 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["safety", "benchmark", "--help"])
        assert exc_info.value.code == 0

    def test_safety_subcommand_registered(self):
        """castor safety benchmark subcommand is registered in the parser."""
        parser = build_parser()
        # Should not raise
        args = parser.parse_args(["safety", "benchmark", "--iterations", "3"])
        assert hasattr(args, "func")

    def test_fail_fast_exits_one_when_overall_pass_false(self, tmp_path, monkeypatch):
        """--fail-fast exits 1 when overall_pass is False."""
        from unittest.mock import MagicMock, patch

        from castor.safety_benchmark import (
            BENCHMARK_SCHEMA_VERSION,
            DEFAULT_THRESHOLDS,
            SafetyBenchmarkReport,
            SafetyBenchmarkResult,
        )

        failing_result = SafetyBenchmarkResult(
            path="bounds_check",
            iterations=5,
            latencies_ms=[999.0] * 5,
            threshold_p95_ms=DEFAULT_THRESHOLDS["bounds_check_p95_ms"],
        )
        mock_report = SafetyBenchmarkReport(
            schema=BENCHMARK_SCHEMA_VERSION,
            generated_at="2026-04-11T00:00:00Z",
            mode="synthetic",
            iterations=5,
            thresholds=dict(DEFAULT_THRESHOLDS),
            results={"bounds_check": failing_result},
        )
        output_file = tmp_path / "bench.json"
        with patch("castor.safety_benchmark.run_safety_benchmark", return_value=mock_report):
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "safety", "benchmark",
                    "--output", str(output_file),
                    "--iterations", "5",
                    "--fail-fast",
                ])
        assert exc_info.value.code == 1

    def test_benchmark_output_file_written(self, tmp_path, monkeypatch):
        """castor safety benchmark writes JSON output file."""
        import json
        from unittest.mock import patch

        from castor.safety_benchmark import run_safety_benchmark

        output_file = tmp_path / "bench.json"
        with patch("castor.safety_benchmark.run_safety_benchmark", wraps=run_safety_benchmark):
            main([
                "safety", "benchmark",
                "--output", str(output_file),
                "--iterations", "3",
            ])
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["schema"] == "rcan-safety-benchmark-v1"
```

> **Note:** The test file likely already imports `main` and `build_parser` from `castor.cli`. If `build_parser` doesn't exist, check the test file's existing imports and use the same pattern already used for other CLI tests (typically `from castor.cli import main` and passing args directly).

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_cli.py::TestSafetyBenchmarkCli -v 2>&1 | head -20
```

Expected: `SystemExit` with code 2 (unknown subcommand) or `AttributeError`.

- [ ] **Step 3: Replace `cmd_safety` stub and refactor the safety subparser in `castor/cli.py`**

**3a — Add `cmd_safety_benchmark` function** (add before `cmd_safety`):

```python
def cmd_safety_benchmark(args) -> None:
    """castor safety benchmark — measure safety path latencies."""
    import json as _json
    import sys
    from datetime import date

    from castor.safety_benchmark import run_safety_benchmark

    config_path = getattr(args, "config", None)
    config = _load_config(config_path) if config_path else {}
    iterations = getattr(args, "iterations", 20)
    live = getattr(args, "live", False)
    fail_fast = getattr(args, "fail_fast", False)
    json_only = getattr(args, "json_output", False)

    output = getattr(args, "output", None)
    if output is None:
        output = f"safety-benchmark-{date.today().isoformat()}.json"

    report = run_safety_benchmark(config=config, iterations=iterations, live=live)

    # Write JSON artifact
    with open(output, "w") as f:
        _json.dump(report.to_dict(), f, indent=2)

    if not json_only:
        try:
            from rich.console import Console
            from rich.table import Table

            console = Console()
            table = Table(title="Safety Benchmark Results", show_header=True)
            table.add_column("Path", style="cyan")
            table.add_column("Iterations", justify="right")
            table.add_column("P95 (ms)", justify="right")
            table.add_column("Threshold (ms)", justify="right")
            table.add_column("Pass", justify="center")

            for path, result in report.results.items():
                if result.iterations == 0:
                    table.add_row(path, "0", "skipped", "-", "⊘")
                    continue
                status = "[green]✓[/green]" if result.passed else "[red]✗[/red]"
                table.add_row(
                    path,
                    str(result.iterations),
                    f"{result.p95_ms:.3f}",
                    f"{result.threshold_p95_ms:.1f}",
                    status,
                )

            console.print(table)
            overall = "[green]PASS[/green]" if report.overall_pass else "[red]FAIL[/red]"
            console.print(f"\nOverall: {overall}")
            console.print(f"Written: {output}")
        except ImportError:
            # Rich not available — print plain summary
            print(f"Overall: {'PASS' if report.overall_pass else 'FAIL'}")
            print(f"Written: {output}")
    else:
        print(_json.dumps(report.to_dict(), indent=2))

    if fail_fast and not report.overall_pass:
        raise SystemExit(1)
```

**3b — Replace `cmd_safety` stub** (find and replace the existing stub):

```python
def cmd_safety(args) -> None:
    """castor safety — safety protocol management."""
    safety_cmd = getattr(args, "safety_cmd", None)
    if safety_cmd == "benchmark":
        cmd_safety_benchmark(args)
    else:
        # Default: print rules
        import sys

        from castor.safety.protocol import SafetyProtocol

        config_path = getattr(args, "config", None)
        config = _load_config(config_path) if config_path else {}
        protocol = SafetyProtocol(config)
        category = getattr(args, "category", None)
        rules = protocol.list_rules()
        if category:
            rules = [r for r in rules if r["rule_id"].startswith(category.upper())]
        for rule in rules:
            status = "enabled" if rule["enabled"] else "disabled"
            print(f"  [{status}] {rule['rule_id']}: {rule['description']}")
```

**3c — Refactor the safety subparser** (in the `build_parser` / argument parser section, replace the `p_safety` block):

Find this block:
```python
    p_safety = sub.add_parser(
        "safety",
        help="Safety protocol management",
        epilog="Examples:\n  castor safety rules\n  castor safety rules --category motion\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_safety.add_argument(
        "safety_action",
        nargs="?",
        default="rules",
        choices=["rules"],
        help="Safety sub-command (default: rules)",
    )
    p_safety.add_argument("--category", default=None, help="Filter by category")
    p_safety.add_argument(
        "--config",
        default=None,
        help="Path to safety protocol YAML config",
    )
```

Replace it with:
```python
    p_safety = sub.add_parser(
        "safety",
        help="Safety protocol management",
        epilog=(
            "Examples:\n"
            "  castor safety rules\n"
            "  castor safety rules --category motion\n"
            "  castor safety benchmark\n"
            "  castor safety benchmark --iterations 50 --fail-fast\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_safety.set_defaults(func=cmd_safety, safety_cmd=None)
    p_safety_sub = p_safety.add_subparsers(dest="safety_cmd")

    # castor safety rules
    p_safety_rules = p_safety_sub.add_parser("rules", help="List safety rules")
    p_safety_rules.add_argument("--category", default=None, help="Filter by category")
    p_safety_rules.add_argument("--config", default=None, help="Path to safety protocol YAML config")
    p_safety_rules.set_defaults(func=cmd_safety)

    # castor safety benchmark
    p_safety_bench = p_safety_sub.add_parser(
        "benchmark",
        help="Measure safety path latencies (P95) against declared thresholds",
    )
    p_safety_bench.add_argument(
        "--config", metavar="FILE", default=None, help="RCAN config file (default: auto-detect)"
    )
    p_safety_bench.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="JSON output path (default: safety-benchmark-{date}.json)",
    )
    p_safety_bench.add_argument(
        "--iterations",
        type=int,
        default=20,
        metavar="N",
        help="Runs per path (default: 20)",
    )
    p_safety_bench.add_argument(
        "--live",
        action="store_true",
        help="Connect to live robot for estop + full_pipeline paths",
    )
    p_safety_bench.add_argument(
        "--fail-fast",
        action="store_true",
        dest="fail_fast",
        help="Exit 1 on first threshold breach (CI mode)",
    )
    p_safety_bench.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Machine-readable output only (no Rich table)",
    )
    p_safety_bench.set_defaults(func=cmd_safety_benchmark)
```

**3d — Add `--benchmark` argument to the FRIA generate subparser**

Find the FRIA generate subparser block and add after the `--skip-sign` argument:

```python
    p_fria_gen.add_argument(
        "--benchmark",
        metavar="FILE",
        dest="benchmark_path",
        default=None,
        help="Path to safety-benchmark-*.json to inline in FRIA document",
    )
```

And update `cmd_fria_generate` to pass it through: find where `build_fria_document` is called and add `benchmark_path=getattr(args, "benchmark_path", None)` to the call. (This will be fully wired in Task 4.)

- [ ] **Step 4: Run CLI tests**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_cli.py::TestSafetyBenchmarkCli -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_cli.py -v -q 2>&1 | tail -10
```

Expected: same pass count as before (no regressions on existing safety tests).

- [ ] **Step 6: Lint**

```bash
cd /home/craigm26/OpenCastor
ruff check castor/cli.py --fix
ruff format castor/cli.py
```

- [ ] **Step 7: Commit**

```bash
cd /home/craigm26/OpenCastor
git add castor/cli.py tests/test_cli.py
git commit -m "feat(#859): wire castor safety benchmark CLI; add --benchmark to fria generate"
```

---

## Task 4: FRIA Integration (`build_fria_document` + `benchmark_path`)

**Files:**
- Modify: `castor/fria.py`
- Modify: `tests/test_fria.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fria.py`:

```python
# ---------------------------------------------------------------------------
# build_fria_document with benchmark_path
# ---------------------------------------------------------------------------


class TestBuildFriaDocumentWithBenchmark:
    def _make_config(self) -> dict:
        return {
            "rcan_version": "1.9.0",
            "metadata": {
                "robot_name": "test-bot",
                "rrn": "RRN-000000000001",
            },
        }

    def _make_benchmark_file(self, tmp_path, overall_pass: bool = True) -> str:
        import json

        data = {
            "schema": "rcan-safety-benchmark-v1",
            "generated_at": "2026-04-11T09:00:00.000Z",
            "mode": "synthetic",
            "iterations": 20,
            "thresholds": {
                "estop_p95_ms": 100.0,
                "bounds_check_p95_ms": 5.0,
                "confidence_gate_p95_ms": 2.0,
                "full_pipeline_p95_ms": 50.0,
            },
            "results": {
                "estop": {"min_ms": 0.3, "mean_ms": 1.2, "p95_ms": 4.1, "p99_ms": 7.2, "max_ms": 9.8, "pass": True},
                "bounds_check": {"min_ms": 0.1, "mean_ms": 0.4, "p95_ms": 0.9, "p99_ms": 1.1, "max_ms": 1.4, "pass": True},
                "confidence_gate": {"min_ms": 0.05, "mean_ms": 0.1, "p95_ms": 0.3, "p99_ms": 0.4, "max_ms": 0.5, "pass": True},
                "full_pipeline": {"min_ms": 0.4, "mean_ms": 1.8, "p95_ms": 5.2, "p99_ms": 8.1, "max_ms": 11.0, "pass": True},
            },
            "overall_pass": overall_pass,
        }
        path = tmp_path / "safety-benchmark-20260411.json"
        path.write_text(json.dumps(data))
        return str(path)

    def test_benchmark_inlined_when_path_provided(self, tmp_path):
        bench_path = self._make_benchmark_file(tmp_path)
        doc = build_fria_document(
            config=self._make_config(),
            annex_iii_basis="safety_component",
            intended_use="Indoor navigation",
            benchmark_path=bench_path,
        )
        assert "safety_benchmarks" in doc

    def test_benchmark_block_has_required_fields(self, tmp_path):
        bench_path = self._make_benchmark_file(tmp_path)
        doc = build_fria_document(
            config=self._make_config(),
            annex_iii_basis="safety_component",
            intended_use="Indoor navigation",
            benchmark_path=bench_path,
        )
        sb = doc["safety_benchmarks"]
        assert "ref" in sb
        assert "generated_at" in sb
        assert "mode" in sb
        assert "overall_pass" in sb
        assert "results" in sb

    def test_benchmark_omitted_when_path_is_none(self):
        doc = build_fria_document(
            config=self._make_config(),
            annex_iii_basis="safety_component",
            intended_use="Indoor navigation",
            benchmark_path=None,
        )
        assert "safety_benchmarks" not in doc

    def test_benchmark_omitted_when_file_missing(self, tmp_path):
        doc = build_fria_document(
            config=self._make_config(),
            annex_iii_basis="safety_component",
            intended_use="Indoor navigation",
            benchmark_path=str(tmp_path / "nonexistent.json"),
        )
        assert "safety_benchmarks" not in doc

    def test_invalid_schema_raises_value_error(self, tmp_path):
        import json

        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps({"schema": "wrong-schema", "results": {}}))
        with pytest.raises(ValueError, match="schema"):
            build_fria_document(
                config=self._make_config(),
                annex_iii_basis="safety_component",
                intended_use="Indoor navigation",
                benchmark_path=str(bad_file),
            )

    def test_ref_field_contains_filename(self, tmp_path):
        bench_path = self._make_benchmark_file(tmp_path)
        doc = build_fria_document(
            config=self._make_config(),
            annex_iii_basis="safety_component",
            intended_use="Indoor navigation",
            benchmark_path=bench_path,
        )
        assert "safety-benchmark-20260411.json" in doc["safety_benchmarks"]["ref"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_fria.py::TestBuildFriaDocumentWithBenchmark -v 2>&1 | head -20
```

Expected: `TypeError: build_fria_document() got an unexpected keyword argument 'benchmark_path'`

- [ ] **Step 3: Add `benchmark_path` parameter to `build_fria_document` in `castor/fria.py`**

Find the `build_fria_document` function signature:
```python
def build_fria_document(
    config: dict,
    annex_iii_basis: str,
    intended_use: str,
    memory_path: str | None = None,
    prerequisite_waived: bool = False,
) -> dict:
```

Replace with:
```python
def build_fria_document(
    config: dict,
    annex_iii_basis: str,
    intended_use: str,
    memory_path: str | None = None,
    prerequisite_waived: bool = False,
    benchmark_path: str | None = None,
) -> dict:
```

Then in the function body, find the `return {` statement and add the `safety_benchmarks` field just before it closes:

Find the closing part of the return dict. The current return ends with:
```python
        "hardware_observations": hardware_observations,
    }
```

Replace with:
```python
        "hardware_observations": hardware_observations,
        **_load_benchmark_block(benchmark_path),
    }
```

Add the helper function above `build_fria_document`:

```python
def _load_benchmark_block(benchmark_path: str | None) -> dict:
    """Load and validate a safety benchmark JSON file.

    Returns ``{"safety_benchmarks": {...}}`` if the file exists and has the
    correct schema, or an empty dict otherwise. Raises ValueError for invalid schema.
    """
    if benchmark_path is None:
        return {}
    if not os.path.exists(benchmark_path):
        return {}

    with open(benchmark_path) as f:
        data = json.load(f)

    if data.get("schema") != "rcan-safety-benchmark-v1":
        raise ValueError(
            f"Invalid safety benchmark schema: {data.get('schema')!r}. "
            "Expected 'rcan-safety-benchmark-v1'."
        )

    return {
        "safety_benchmarks": {
            "ref": os.path.basename(benchmark_path),
            "generated_at": data.get("generated_at", ""),
            "mode": data.get("mode", ""),
            "overall_pass": data.get("overall_pass", False),
            "results": data.get("results", {}),
        }
    }
```

- [ ] **Step 4: Wire `--benchmark` in `cmd_fria_generate`**

In `castor/cli.py`, find the call to `build_fria_document` inside `cmd_fria_generate` and add the `benchmark_path` kwarg:

```python
        doc = build_fria_document(
            config=config,
            annex_iii_basis=annex_iii,
            intended_use=intended_use,
            memory_path=memory_path,
            prerequisite_waived=prerequisite_waived,
            benchmark_path=getattr(args, "benchmark_path", None),
        )
```

- [ ] **Step 5: Run FRIA tests**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_fria.py -v
```

Expected: all tests pass (including the 23 existing tests + the 6 new benchmark tests).

- [ ] **Step 6: Run full test suite**

```bash
cd /home/craigm26/OpenCastor
pytest tests/test_safety_benchmark.py tests/test_fria.py tests/test_cli.py -v -q 2>&1 | tail -10
```

Expected: all tests pass, no regressions.

- [ ] **Step 7: Lint**

```bash
cd /home/craigm26/OpenCastor
ruff check castor/fria.py castor/cli.py --fix
ruff format castor/fria.py castor/cli.py
```

- [ ] **Step 8: Commit**

```bash
cd /home/craigm26/OpenCastor
git add castor/fria.py castor/cli.py tests/test_fria.py
git commit -m "feat(#859): build_fria_document gains benchmark_path; inline safety_benchmarks block"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec requirement | Covered in task |
|---|---|
| `SafetyBenchmarkResult` dataclass with `min_ms`, `mean_ms`, `p95_ms`, `p99_ms`, `max_ms`, `passed`, `to_dict()` | Task 1 |
| `SafetyBenchmarkReport` dataclass with `schema`, `generated_at`, `mode`, `iterations`, `thresholds`, `results`, `overall_pass`, `to_dict()` | Task 1 |
| `_bench_bounds_check` — times `BoundsResult.combine()` | Task 1 |
| `_bench_confidence_gate` — times `ConfidenceGateEnforcer.evaluate()` | Task 1 |
| `_bench_estop` — synthetic: times `_check_estop_response`; live: skipped gracefully | Task 2 |
| `_bench_full_pipeline` — times `SafetyProtocol.check_action()` | Task 2 |
| `run_safety_benchmark` — all 4 paths, returns report | Task 2 |
| `castor safety benchmark` CLI with `--config`, `--output`, `--iterations`, `--live`, `--fail-fast`, `--json` | Task 3 |
| `--benchmark` on `castor fria generate` | Task 3 |
| `build_fria_document` gains `benchmark_path` param | Task 4 |
| Inlines `safety_benchmarks` block when file present and schema valid | Task 4 |
| Silently omits when file missing | Task 4 |
| Raises `ValueError` for invalid schema | Task 4 |
| Output JSON written to file | Task 3 |
| `--fail-fast` exits 1 when overall_pass False | Task 3 |
| Live paths skipped gracefully when robot unreachable | Task 2 |
| Default thresholds: estop 100ms, bounds_check 5ms, confidence_gate 2ms, full_pipeline 50ms P95 | Task 1 |
| Config override via `safety.benchmark_thresholds.*` | Task 1 |
| `BENCHMARK_SCHEMA_VERSION = "rcan-safety-benchmark-v1"` | Task 1 |

**No placeholders found.**

**Type consistency:** `SafetyBenchmarkResult`, `SafetyBenchmarkReport` defined in Task 1 and used consistently in Tasks 2, 3, 4. `_get_threshold(config, key)` helper defined in Task 1, used in Tasks 1 and 2. `_load_benchmark_block` defined and used in Task 4.
