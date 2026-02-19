"""Tests for SisyphusLoop orchestration."""

from castor.learner.episode import Episode
from castor.learner.sisyphus import ImprovementResult, SisyphusLoop


def _failed_grasp_episode():
    return Episode(
        goal="grasp the cup",
        actions=[
            {"type": "detect", "result": {"success": True}},
            {"type": "grasp", "result": {"success": False, "error": "slip"}},
        ],
        success=False,
        duration_s=5.0,
    )


def _successful_episode():
    return Episode(
        goal="navigate to dock",
        actions=[{"type": "move", "result": {"success": True}}] * 3,
        success=True,
        duration_s=5.0,
    )


def _no_improvement_episode():
    return Episode(
        goal="navigate home",
        actions=[{"type": "move", "result": {"success": True}}],
        success=True,
        duration_s=2.0,
    )


class TestSisyphusLoop:
    def setup_method(self):
        self.loop = SisyphusLoop()

    def test_run_episode_returns_result(self, tmp_path):
        loop = SisyphusLoop(config={"config_dir": str(tmp_path)})
        loop.apply_stage.config_dir = tmp_path
        loop.apply_stage.history_file = tmp_path / "history.json"
        loop.apply_stage.behaviors_file = tmp_path / "behaviors.yaml"
        result = loop.run_episode(_failed_grasp_episode())
        assert isinstance(result, ImprovementResult)
        assert result.episode_id

    def test_failed_episode_produces_report(self, tmp_path):
        loop = SisyphusLoop()
        loop.apply_stage.config_dir = tmp_path
        loop.apply_stage.history_file = tmp_path / "history.json"
        result = loop.run_episode(_failed_grasp_episode())
        assert result.report is not None
        assert result.report.failure_point is not None

    def test_failed_episode_generates_patch(self, tmp_path):
        loop = SisyphusLoop()
        loop.apply_stage.config_dir = tmp_path
        loop.apply_stage.history_file = tmp_path / "history.json"
        result = loop.run_episode(_failed_grasp_episode())
        assert result.patch is not None

    def test_successful_simple_episode(self, tmp_path):
        loop = SisyphusLoop()
        loop.apply_stage.config_dir = tmp_path
        loop.apply_stage.history_file = tmp_path / "history.json"
        result = loop.run_episode(_no_improvement_episode())
        assert result.report is not None

    def test_stats_updated(self, tmp_path):
        loop = SisyphusLoop()
        loop.apply_stage.config_dir = tmp_path
        loop.apply_stage.history_file = tmp_path / "history.json"
        loop.run_episode(_failed_grasp_episode())
        assert loop.stats.episodes_analyzed == 1

    def test_batch_processing(self, tmp_path):
        loop = SisyphusLoop()
        loop.apply_stage.config_dir = tmp_path
        loop.apply_stage.history_file = tmp_path / "history.json"
        episodes = [_failed_grasp_episode(), _successful_episode(), _no_improvement_episode()]
        results = loop.run_batch(episodes)
        assert len(results) == 3
        assert all(isinstance(r, ImprovementResult) for r in results)

    def test_result_to_dict(self, tmp_path):
        loop = SisyphusLoop()
        loop.apply_stage.config_dir = tmp_path
        loop.apply_stage.history_file = tmp_path / "history.json"
        result = loop.run_episode(_failed_grasp_episode())
        d = result.to_dict()
        assert "episode_id" in d
        assert "report" in d
        assert "applied" in d

    def test_no_error_on_success(self, tmp_path):
        loop = SisyphusLoop()
        loop.apply_stage.config_dir = tmp_path
        loop.apply_stage.history_file = tmp_path / "history.json"
        result = loop.run_episode(_successful_episode())
        assert result.error is None

    def test_multiple_failed_episodes(self, tmp_path):
        loop = SisyphusLoop()
        loop.apply_stage.config_dir = tmp_path
        loop.apply_stage.history_file = tmp_path / "history.json"
        for _ in range(3):
            loop.run_episode(_failed_grasp_episode())
        assert loop.stats.episodes_analyzed == 3
