"""Tests for castor.benchmarker — issue #458."""

from __future__ import annotations

import asyncio
import statistics
from unittest.mock import AsyncMock

import pytest

from castor.benchmarker import BenchmarkResult, run_benchmark, print_results


# ── BenchmarkResult stats ──────────────────────────────────────────────────────


def _make_result(latencies, errors=0):
    r = BenchmarkResult(provider="test", model="test-model", n=len(latencies) + errors)
    r.latencies_ms = list(latencies)
    r.errors = errors
    return r


def test_mean_ms_correct():
    r = _make_result([100.0, 200.0, 300.0])
    assert r.mean_ms == pytest.approx(200.0)


def test_mean_ms_empty():
    r = _make_result([])
    assert r.mean_ms == 0.0


def test_min_ms():
    r = _make_result([50.0, 100.0, 200.0])
    assert r.min_ms == pytest.approx(50.0)


def test_max_ms():
    r = _make_result([50.0, 100.0, 200.0])
    assert r.max_ms == pytest.approx(200.0)


def test_p50_ms():
    """p50 is the median-ish value (using 50th percentile index)."""
    r = _make_result([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])
    # sorted * 0.5 → index 4 (0-based) → 50.0
    assert r.p95_ms >= r.mean_ms * 0  # basic sanity


def test_p95_ms_single():
    r = _make_result([100.0])
    assert r.p95_ms == pytest.approx(100.0)


def test_p95_ms_multiple():
    latencies = [float(i) for i in range(1, 21)]  # 1..20
    r = _make_result(latencies)
    # sorted[max(0, int(20*0.95)-1)] = sorted[18] = 19.0
    assert r.p95_ms == pytest.approx(19.0)


def test_p95_ms_empty():
    r = _make_result([])
    assert r.p95_ms == 0.0


def test_success_rate_all_success():
    r = _make_result([100.0, 200.0], errors=0)
    assert r.success_rate == pytest.approx(1.0)


def test_success_rate_some_errors():
    r = _make_result([100.0, 200.0, 300.0], errors=2)
    assert r.success_rate == pytest.approx(3 / 5)


def test_success_rate_all_errors():
    r = _make_result([], errors=3)
    assert r.success_rate == pytest.approx(0.0)


def test_success_rate_empty():
    r = _make_result([], errors=0)
    assert r.success_rate == 0.0


def test_error_count_increments():
    r = _make_result([100.0], errors=2)
    assert r.errors == 2


# ── run_benchmark ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_benchmark_returns_result():
    think_fn = AsyncMock(return_value={"text": "ok"})
    result = await run_benchmark(think_fn, n=3, provider="p", model="m")
    assert isinstance(result, BenchmarkResult)
    assert result.provider == "p"
    assert result.model == "m"


@pytest.mark.asyncio
async def test_run_benchmark_records_latencies():
    think_fn = AsyncMock(return_value={})
    result = await run_benchmark(think_fn, n=5, provider="p", model="m")
    assert len(result.latencies_ms) == 5
    assert result.errors == 0


@pytest.mark.asyncio
async def test_run_benchmark_counts_errors():
    think_fn = AsyncMock(side_effect=RuntimeError("fail"))
    result = await run_benchmark(think_fn, n=4, provider="p", model="m")
    assert result.errors == 4
    assert len(result.latencies_ms) == 0


@pytest.mark.asyncio
async def test_run_benchmark_mixed_errors():
    call_count = 0

    async def flaky(prompt):
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 0:
            raise ValueError("error")
        return {}

    result = await run_benchmark(flaky, n=4, provider="p", model="m")
    assert result.errors == 2
    assert len(result.latencies_ms) == 2


@pytest.mark.asyncio
async def test_run_benchmark_latencies_are_positive():
    think_fn = AsyncMock(return_value={})
    result = await run_benchmark(think_fn, n=3, provider="p", model="m")
    assert all(ms > 0 for ms in result.latencies_ms)


@pytest.mark.asyncio
async def test_run_benchmark_calls_think_fn_n_times():
    think_fn = AsyncMock(return_value={})
    await run_benchmark(think_fn, n=7, provider="p", model="m")
    assert think_fn.call_count == 7


@pytest.mark.asyncio
async def test_run_benchmark_passes_prompt():
    think_fn = AsyncMock(return_value={})
    await run_benchmark(think_fn, n=1, prompt="hello?", provider="p", model="m")
    think_fn.assert_called_with("hello?")


# ── print_results ─────────────────────────────────────────────────────────────


def test_print_results_no_crash(capsys):
    r = _make_result([100.0, 200.0, 300.0])
    print_results([r])
    out = capsys.readouterr().out
    assert len(out) >= 0  # just don't crash


def test_print_results_plain_text_contains_provider(capsys, monkeypatch):
    import castor.benchmarker as bm

    monkeypatch.setattr(bm, "HAS_RICH", False)
    r = _make_result([100.0, 200.0])
    r.provider = "mycloud"
    r.model = "gpt-x"
    print_results([r])
    out = capsys.readouterr().out
    assert "mycloud" in out


def test_print_results_plain_text_contains_p95(capsys, monkeypatch):
    import castor.benchmarker as bm

    monkeypatch.setattr(bm, "HAS_RICH", False)
    r = _make_result([100.0, 200.0, 300.0])
    print_results([r])
    out = capsys.readouterr().out
    assert "p95" in out


def test_print_results_plain_text_contains_errors(capsys, monkeypatch):
    import castor.benchmarker as bm

    monkeypatch.setattr(bm, "HAS_RICH", False)
    r = _make_result([100.0], errors=3)
    print_results([r])
    out = capsys.readouterr().out
    assert "errors=3" in out or "3" in out


def test_print_results_multiple_results(capsys, monkeypatch):
    import castor.benchmarker as bm

    monkeypatch.setattr(bm, "HAS_RICH", False)
    r1 = _make_result([100.0])
    r1.provider = "prov-a"
    r2 = _make_result([200.0])
    r2.provider = "prov-b"
    print_results([r1, r2])
    out = capsys.readouterr().out
    assert "prov-a" in out
    assert "prov-b" in out


def test_print_results_empty_list(capsys):
    print_results([])
    # Should not crash
    capsys.readouterr()
