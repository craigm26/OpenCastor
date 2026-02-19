"""Tests for DevStage patch generation."""

from castor.learner.dev_stage import DevStage
from castor.learner.patches import BehaviorPatch, ConfigPatch
from castor.learner.pm_stage import AnalysisReport, ImprovementSuggestion
from castor.learner.qa_stage import QACheck, QAResult


def _config_report():
    return AnalysisReport(
        episode_id="ep1",
        improvements=[
            ImprovementSuggestion(
                type="config",
                description="Adjust grasp force",
                config_key="grasp_force",
                current_value=0.5,
                suggested_value=0.8,
                rationale="Too weak",
            )
        ],
    )


def _behavior_report():
    return AnalysisReport(
        episode_id="ep2",
        improvements=[
            ImprovementSuggestion(
                type="behavior",
                description="Add loop breaking rule",
                config_key="loop_breaker",
                rationale="Repeated actions",
            )
        ],
    )


def _empty_report():
    return AnalysisReport(episode_id="ep3", improvements=[])


class TestDevStage:
    def setup_method(self):
        self.dev = DevStage()

    def test_config_suggestion_produces_config_patch(self):
        patch = self.dev.generate_fix(_config_report())
        assert isinstance(patch, ConfigPatch)
        assert patch.key == "grasp_force"

    def test_behavior_suggestion_produces_behavior_patch(self):
        patch = self.dev.generate_fix(_behavior_report())
        assert isinstance(patch, BehaviorPatch)
        assert patch.rule_name == "loop_breaker"

    def test_empty_report_returns_none(self):
        patch = self.dev.generate_fix(_empty_report())
        assert patch is None

    def test_config_patch_has_rationale(self):
        patch = self.dev.generate_fix(_config_report())
        assert patch.rationale

    def test_retry_with_safety_feedback(self):
        report = _config_report()
        original = self.dev.generate_fix(report)
        # Simulate QA rejection for safety bounds
        feedback = QAResult(
            approved=False,
            checks=[QACheck(name="safety_bounds", passed=False, detail="out of bounds")],
            retry_suggested=True,
        )
        retry_patch = self.dev.generate_fix(report, previous_attempt=original, qa_feedback=feedback)
        assert isinstance(retry_patch, ConfigPatch)
        assert "Retry" in retry_patch.rationale

    def test_retry_with_type_feedback(self):
        report = _config_report()
        original = ConfigPatch(key="grasp_force", old_value=0.5, new_value="bad")
        feedback = QAResult(
            approved=False,
            checks=[QACheck(name="type_check", passed=False, detail="mismatch")],
            retry_suggested=True,
        )
        retry_patch = self.dev.generate_fix(report, previous_attempt=original, qa_feedback=feedback)
        assert isinstance(retry_patch, ConfigPatch)

    def test_behavior_patch_has_priority(self):
        patch = self.dev.generate_fix(_behavior_report())
        assert isinstance(patch, BehaviorPatch)
        assert patch.priority == 5

    def test_config_patch_preserves_old_value(self):
        patch = self.dev.generate_fix(_config_report())
        assert isinstance(patch, ConfigPatch)
        assert patch.old_value == 0.5

    def test_unknown_type_defaults_to_config(self):
        report = AnalysisReport(
            episode_id="x",
            improvements=[
                ImprovementSuggestion(type="unknown", description="something", config_key="k")
            ],
        )
        patch = self.dev.generate_fix(report)
        assert isinstance(patch, ConfigPatch)
