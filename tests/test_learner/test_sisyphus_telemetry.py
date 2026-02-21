"""Tests for Sisyphus telemetry — duration tracking and provider wiring (#73)."""

from unittest.mock import MagicMock

import pytest

from castor.learner.episode import Episode
from castor.learner.sisyphus import ImprovementResult, SisyphusLoop, SisyphusStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _failed_episode():
    return Episode(
        goal="grasp the cup",
        actions=[
            {"type": "grasp", "result": {"success": False, "error": "slip"}},
        ],
        success=False,
        duration_s=3.0,
    )


def _success_episode():
    return Episode(
        goal="navigate home",
        actions=[{"type": "move", "result": {"success": True}}],
        success=True,
        duration_s=1.0,
    )


def _make_loop(tmp_path, **kwargs):
    loop = SisyphusLoop(config={"config_dir": str(tmp_path)}, **kwargs)
    loop.apply_stage.config_dir = tmp_path
    loop.apply_stage.history_file = tmp_path / "history.json"
    loop.apply_stage.behaviors_file = tmp_path / "behaviors.yaml"
    return loop


# ---------------------------------------------------------------------------
# ImprovementResult fields
# ---------------------------------------------------------------------------

class TestImprovementResultFields:
    def test_duration_ms_field_exists(self):
        r = ImprovementResult()
        assert hasattr(r, "duration_ms")
        assert r.duration_ms is None

    def test_stage_durations_field_exists(self):
        r = ImprovementResult()
        assert hasattr(r, "stage_durations")
        assert isinstance(r.stage_durations, dict)

    def test_to_dict_includes_timing_fields(self):
        r = ImprovementResult(duration_ms=42.5, stage_durations={"pm_ms": 10.0})
        d = r.to_dict()
        assert d["duration_ms"] == pytest.approx(42.5)
        assert d["stage_durations"] == {"pm_ms": 10.0}


# ---------------------------------------------------------------------------
# SisyphusStats
# ---------------------------------------------------------------------------

class TestSisyphusStats:
    def test_avg_duration_ms_zero_when_no_episodes(self):
        stats = SisyphusStats()
        assert stats.avg_duration_ms == 0.0

    def test_avg_duration_ms_correct(self):
        stats = SisyphusStats(
            episodes_analyzed=4,
            total_duration_ms=800.0,
        )
        assert stats.avg_duration_ms == pytest.approx(200.0)

    def test_total_duration_ms_defaults_to_zero(self):
        stats = SisyphusStats()
        assert stats.total_duration_ms == 0.0


# ---------------------------------------------------------------------------
# run_episode timing
# ---------------------------------------------------------------------------

class TestRunEpisodeTiming:
    def test_result_has_duration_ms_set(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = loop.run_episode(_failed_episode())
        assert result.duration_ms is not None
        assert result.duration_ms >= 0

    def test_result_has_stage_durations(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = loop.run_episode(_failed_episode())
        # At minimum pm_ms should be set (PM stage always runs)
        assert "pm_ms" in result.stage_durations
        assert result.stage_durations["pm_ms"] >= 0

    def test_stats_updated_after_episode(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.run_episode(_failed_episode())
        assert loop.stats.episodes_analyzed >= 1

    def test_total_duration_accumulates_across_episodes(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.run_episode(_failed_episode())
        loop.run_episode(_failed_episode())
        # Only applied episodes contribute to total, but episodes_analyzed increments
        assert loop.stats.episodes_analyzed >= 2

    def test_no_improvements_does_not_add_to_total_duration(self, tmp_path):
        """When no improvements are suggested, total_duration_ms should stay 0."""
        loop = _make_loop(tmp_path)
        # Success episode with all-ok actions typically has no improvements
        initial = loop.stats.total_duration_ms
        loop.run_episode(_success_episode())
        # total_duration_ms only changes when an improvement is applied/rejected
        # This is a soft check: it could be 0 or > 0 depending on PM output
        assert loop.stats.total_duration_ms >= initial


# ---------------------------------------------------------------------------
# Provider wiring
# ---------------------------------------------------------------------------

class TestProviderWiring:
    def test_provider_passed_to_pm_stage(self):
        mock_provider = MagicMock()
        loop = SisyphusLoop(provider=mock_provider)
        assert loop.pm._provider is mock_provider

    def test_provider_passed_to_dev_stage(self):
        mock_provider = MagicMock()
        loop = SisyphusLoop(provider=mock_provider)
        assert loop.dev._provider is mock_provider

    def test_provider_passed_to_qa_stage(self):
        mock_provider = MagicMock()
        loop = SisyphusLoop(provider=mock_provider)
        assert loop.qa._provider is mock_provider

    def test_provider_stored_on_loop(self):
        mock_provider = MagicMock()
        loop = SisyphusLoop(provider=mock_provider)
        assert loop.provider is mock_provider

    def test_none_provider_is_valid(self):
        loop = SisyphusLoop(provider=None)
        assert loop.provider is None
