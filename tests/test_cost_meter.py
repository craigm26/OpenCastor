"""Tests for castor.harness.cost_meter."""

import pytest

from castor.harness.cost_meter import CostMeter, RunCost, PRICE_PER_1K


@pytest.fixture
def meter():
    return CostMeter({
        "budget_usd": 0.05,
        "alert_at": 0.8,
        "model": "gemini-2.0-flash",
    })


def test_record_accumulates(meter):
    rc = meter.record("run-1", input_tokens=1000, output_tokens=500)
    assert rc.input_tokens == 1000
    assert rc.output_tokens == 500
    assert rc.estimated_usd > 0


def test_record_multiple_calls(meter):
    meter.record("run-1", 1000, 500)
    rc = meter.record("run-1", 1000, 500)
    assert rc.input_tokens == 2000
    assert rc.output_tokens == 1000


def test_is_over_budget_not_exceeded(meter):
    meter.record("run-1", 100, 50)  # tiny amount
    assert not meter.is_over_budget("run-1")


def test_is_over_budget_exceeded(meter):
    # 100k input + 100k output with gemini pricing = well over $0.05
    meter.record("run-1", 200_000, 100_000)
    assert meter.is_over_budget("run-1")


def test_is_over_budget_unknown_run(meter):
    assert not meter.is_over_budget("nonexistent-run")


def test_current_cost_new_run(meter):
    rc = meter.current_cost("brand-new-run")
    assert rc.run_id == "brand-new-run"
    assert rc.input_tokens == 0
    assert rc.estimated_usd == 0.0


def test_budget_exceeded_flag(meter):
    rc = meter.record("run-x", 500_000, 500_000)
    assert rc.budget_exceeded


def test_alert_triggered(meter):
    # With budget=0.05 and alert_at=0.8, alert triggers at $0.04
    # gemini-2.0-flash: input=0.00015/1K, output=0.0006/1K
    # Need ~40k input + 40k output to reach $0.04
    rc = meter.record("run-a", 100_000, 100_000)
    # At 100k input: 100 * 0.00015 = $0.015; 100k output: 100 * 0.0006 = $0.06
    # total = $0.075 > budget → both alert and exceeded
    assert rc.alert_triggered or rc.budget_exceeded


def test_total_today(meter):
    meter.record("run-1", 1000, 500)
    meter.record("run-2", 1000, 500)
    total = meter.total_today()
    assert total > 0


def test_pricing_gemini_flash():
    meter = CostMeter({"model": "gemini-2.0-flash", "budget_usd": 1.0})
    rc = meter.record("r", 1000, 1000)
    expected = (1000 / 1000) * 0.00015 + (1000 / 1000) * 0.0006
    assert abs(rc.estimated_usd - expected) < 1e-9


def test_pricing_claude_sonnet():
    meter = CostMeter({"model": "claude-sonnet-4-6", "budget_usd": 10.0})
    rc = meter.record("r", 1000, 1000)
    expected = (1000 / 1000) * 0.003 + (1000 / 1000) * 0.015
    assert abs(rc.estimated_usd - expected) < 1e-9


def test_pricing_default_model():
    meter = CostMeter({"model": "unknown-model-xyz", "budget_usd": 1.0})
    rc = meter.record("r", 1000, 1000)
    expected = (1000 / 1000) * 0.001 + (1000 / 1000) * 0.004
    assert abs(rc.estimated_usd - expected) < 1e-9


def test_run_cost_has_budget_field(meter):
    rc = meter.record("r1", 100, 100)
    assert rc.budget_usd == 0.05


def test_no_budget_never_exceeded():
    meter = CostMeter({"model": "gemini-2.0-flash"})
    rc = meter.record("r", 10_000_000, 10_000_000)
    assert not rc.budget_exceeded


def test_price_per_1k_has_required_models():
    for model in ["gemini-2.0-flash", "claude-opus-4-6", "claude-sonnet-4-6", "default"]:
        assert model in PRICE_PER_1K
        assert "input" in PRICE_PER_1K[model]
        assert "output" in PRICE_PER_1K[model]
