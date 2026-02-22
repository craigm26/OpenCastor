"""Tests for castor/usage.py — provider token usage and cost tracking."""

from __future__ import annotations

import os
import tempfile
import time

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────


def _fresh_tracker(tmp_path):
    """Return a UsageTracker wired to a temporary database."""
    from castor.usage import UsageTracker

    db = str(tmp_path / "usage.db")
    return UsageTracker(db_path=db)


# ── Unit tests ─────────────────────────────────────────────────────────────────


class TestCostEstimation:
    """_estimate_cost returns sensible values for known providers."""

    def test_free_provider_returns_zero(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        cost = tracker._estimate_cost("ollama", "llava:13b", 1000, 500)
        assert cost == 0.0

    def test_openai_gpt4_mini_cost(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        # gpt-4.1-mini: $0.0004/1k in, $0.0016/1k out
        cost = tracker._estimate_cost("openai", "gpt-4.1-mini", 1000, 1000)
        assert cost == pytest.approx(0.0004 + 0.0016, rel=1e-4)

    def test_anthropic_sonnet_cost(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        # claude-sonnet-4-6: $0.003/1k in, $0.015/1k out
        cost = tracker._estimate_cost("anthropic", "claude-sonnet-4-6", 1000, 1000)
        assert cost == pytest.approx(0.003 + 0.015, rel=1e-4)

    def test_unknown_provider_returns_zero(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        cost = tracker._estimate_cost("unknown_provider", "some-model", 1000, 500)
        assert cost == 0.0

    def test_provider_default_fallback(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        # An unknown Google model should fall back to "default" entry
        cost = tracker._estimate_cost("google", "gemini-unknown-model", 1000, 1000)
        # default is (0.075, 0.30) → 0.375
        assert cost == pytest.approx(0.075 + 0.30, rel=1e-4)


class TestLogUsage:
    """log_usage persists rows to the database."""

    def test_log_usage_stores_row(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        tracker.log_usage("google", "gemini-2.0-flash", prompt_tokens=100, completion_tokens=50)

        totals = tracker.get_all_time_totals()
        assert totals["calls"] == 1
        assert totals["tokens_in"] == 100
        assert totals["tokens_out"] == 50
        assert totals["total_tokens"] == 150

    def test_log_usage_explicit_cost(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        tracker.log_usage("openai", "gpt-4.1", 200, 100, cost_usd=0.123)

        totals = tracker.get_all_time_totals()
        assert totals["cost_usd"] == pytest.approx(0.123, rel=1e-5)

    def test_multiple_calls_accumulate(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        for _ in range(5):
            tracker.log_usage("anthropic", "claude-haiku-4-5", 100, 50)

        totals = tracker.get_all_time_totals()
        assert totals["calls"] == 5
        assert totals["tokens_in"] == 500
        assert totals["tokens_out"] == 250

    def test_auto_calculated_cost_nonzero_for_paid_provider(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        tracker.log_usage("openai", "gpt-4.1-mini", 1000, 1000)

        totals = tracker.get_all_time_totals()
        assert totals["cost_usd"] > 0.0


class TestSessionTotals:
    """get_session_totals scopes results to the current session."""

    def test_session_totals_grouped_by_provider(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        tracker.log_usage("google", "gemini-2.0-flash", 100, 50)
        tracker.log_usage("openai", "gpt-4.1-mini", 200, 80)
        tracker.log_usage("google", "gemini-2.0-flash", 150, 60)

        session = tracker.get_session_totals()

        assert "google" in session
        assert "openai" in session
        assert session["google"]["calls"] == 2
        assert session["google"]["tokens_in"] == 250
        assert session["openai"]["calls"] == 1
        assert session["openai"]["tokens_in"] == 200

    def test_session_totals_empty_when_no_calls(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        assert tracker.get_session_totals() == {}

    def test_session_totals_excludes_other_session(self, tmp_path):
        """Two trackers with different session IDs should not see each other's data."""
        from castor.usage import UsageTracker
        import uuid

        db = str(tmp_path / "usage.db")

        t1 = UsageTracker(db_path=db)
        t1._session_id = str(uuid.uuid4())
        t1.log_usage("google", "gemini-2.0-flash", 100, 50)

        t2 = UsageTracker(db_path=db)
        t2._session_id = str(uuid.uuid4())
        t2.log_usage("openai", "gpt-4.1-mini", 200, 80)

        s1 = t1.get_session_totals()
        s2 = t2.get_session_totals()

        assert "google" in s1
        assert "openai" not in s1
        assert "openai" in s2
        assert "google" not in s2


class TestDailyTotals:
    """get_daily_totals returns per-day aggregates."""

    def test_daily_totals_returns_today(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        tracker.log_usage("google", "gemini-2.0-flash", 100, 50)
        tracker.log_usage("openai", "gpt-4.1-mini", 200, 80)

        daily = tracker.get_daily_totals(days=7)
        assert len(daily) >= 1
        today = daily[-1]
        assert today["calls"] == 2
        assert today["tokens_in"] == 300

    def test_daily_totals_empty_for_fresh_db(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        daily = tracker.get_daily_totals(days=7)
        assert daily == []

    def test_daily_totals_date_key_present(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        tracker.log_usage("ollama", "llava:13b", 50, 30)

        daily = tracker.get_daily_totals(days=1)
        assert len(daily) == 1
        assert "date" in daily[0]
        # date is YYYY-MM-DD
        assert len(daily[0]["date"]) == 10


class TestAllTimeTotals:
    """get_all_time_totals aggregates across all sessions."""

    def test_all_time_zero_initially(self, tmp_path):
        tracker = _fresh_tracker(tmp_path)
        totals = tracker.get_all_time_totals()
        assert totals["calls"] == 0
        assert totals["cost_usd"] == 0.0

    def test_all_time_accumulates_multiple_sessions(self, tmp_path):
        from castor.usage import UsageTracker
        import uuid

        db = str(tmp_path / "usage.db")

        for _ in range(3):
            t = UsageTracker(db_path=db)
            t._session_id = str(uuid.uuid4())
            t.log_usage("google", "gemini-2.0-flash", 100, 50)

        t_read = UsageTracker(db_path=db)
        totals = t_read.get_all_time_totals()
        assert totals["calls"] == 3
        assert totals["tokens_in"] == 300


class TestGetTracker:
    """get_tracker() returns a singleton."""

    def test_get_tracker_returns_same_instance(self):
        from castor.usage import get_tracker

        t1 = get_tracker()
        t2 = get_tracker()
        assert t1 is t2

    def test_get_tracker_is_usage_tracker(self):
        from castor.usage import UsageTracker, get_tracker

        assert isinstance(get_tracker(), UsageTracker)
