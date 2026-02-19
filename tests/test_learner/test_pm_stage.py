"""Tests for PMStage analysis."""

from castor.learner.episode import Episode
from castor.learner.pm_stage import PMStage


def _successful_episode():
    return Episode(
        goal="navigate to dock",
        actions=[
            {"type": "plan", "result": {"success": True}},
            {"type": "move", "result": {"success": True}},
            {"type": "move", "result": {"success": True}},
        ],
        success=True,
        duration_s=10.0,
    )


def _failed_episode():
    return Episode(
        goal="grasp the cup",
        actions=[
            {"type": "detect", "result": {"success": True}},
            {"type": "grasp", "result": {"success": False, "error": "slip"}},
        ],
        success=False,
        duration_s=8.0,
    )


def _empty_episode():
    return Episode(goal="", actions=[], success=False, duration_s=0.0)


class TestPMStage:
    def setup_method(self):
        self.pm = PMStage()

    def test_analyze_returns_report(self):
        report = self.pm.analyze(_successful_episode())
        assert report.episode_id
        assert report.outcome is True

    def test_successful_episode_no_failure_point(self):
        report = self.pm.analyze(_successful_episode())
        assert report.failure_point is None
        assert report.root_cause == ""

    def test_successful_episode_finds_suboptimalities(self):
        ep = Episode(
            goal="navigate home",
            actions=[{"type": "move", "result": {"success": True}}] * 15,
            success=True,
            duration_s=90.0,
        )
        report = self.pm.analyze(ep)
        assert len(report.suboptimalities) > 0

    def test_failed_episode_has_failure_point(self):
        report = self.pm.analyze(_failed_episode())
        assert report.failure_point is not None
        assert report.failure_point["action"] == "grasp"

    def test_failed_episode_has_root_cause(self):
        report = self.pm.analyze(_failed_episode())
        assert report.root_cause != ""
        assert "grasp" in report.root_cause.lower() or "Grasp" in report.root_cause

    def test_failed_episode_suggests_improvements(self):
        report = self.pm.analyze(_failed_episode())
        assert len(report.improvements) > 0

    def test_empty_episode(self):
        report = self.pm.analyze(_empty_episode())
        assert report.efficiency_score == 0.0

    def test_efficiency_score_range(self):
        report = self.pm.analyze(_successful_episode())
        assert 0.0 <= report.efficiency_score <= 1.0

    def test_report_serialization(self):
        report = self.pm.analyze(_failed_episode())
        d = report.to_dict()
        assert "episode_id" in d
        assert "improvements" in d

    def test_detect_failure_suggests_confidence(self):
        ep = Episode(
            goal="detect object",
            actions=[{"type": "detect", "result": {"success": False, "error": "low conf"}}],
            success=False,
            duration_s=2.0,
        )
        report = self.pm.analyze(ep)
        config_keys = [i.config_key for i in report.improvements]
        assert "hailo_confidence" in config_keys
