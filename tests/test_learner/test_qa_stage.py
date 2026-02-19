"""Tests for QAStage verification."""

from castor.learner.episode import Episode
from castor.learner.patches import BehaviorPatch, ConfigPatch, PromptPatch
from castor.learner.qa_stage import SAFETY_BOUNDS, QAStage


def _dummy_episode():
    return Episode(goal="test", success=True)


class TestQAStage:
    def setup_method(self):
        self.qa = QAStage()
        self.ep = _dummy_episode()

    def test_safe_config_approved(self):
        patch = ConfigPatch(key="max_velocity", old_value=1.0, new_value=2.0)
        result = self.qa.verify(patch, self.ep)
        assert result.approved is True

    def test_unsafe_config_rejected(self):
        patch = ConfigPatch(key="max_velocity", old_value=1.0, new_value=999.0)
        result = self.qa.verify(patch, self.ep)
        assert result.approved is False

    def test_safety_bounds_check_detail(self):
        patch = ConfigPatch(key="max_velocity", old_value=1.0, new_value=-1.0)
        result = self.qa.verify(patch, self.ep)
        safety_check = [c for c in result.checks if c.name == "safety_bounds"][0]
        assert not safety_check.passed
        assert "outside bounds" in safety_check.detail

    def test_type_mismatch_rejected(self):
        patch = ConfigPatch(key="max_velocity", old_value=1.0, new_value="fast")
        result = self.qa.verify(patch, self.ep)
        assert result.approved is False
        type_check = [c for c in result.checks if c.name == "type_check"][0]
        assert not type_check.passed

    def test_same_value_rejected(self):
        patch = ConfigPatch(key="some_key", old_value=5, new_value=5)
        result = self.qa.verify(patch, self.ep)
        assert result.approved is False

    def test_behavior_patch_no_name_rejected(self):
        patch = BehaviorPatch(rule_name="")
        result = self.qa.verify(patch, self.ep)
        assert result.approved is False

    def test_behavior_patch_with_name_approved(self):
        patch = BehaviorPatch(rule_name="my_rule", conditions={"a": 1}, action={"b": 2})
        result = self.qa.verify(patch, self.ep)
        assert result.approved is True

    def test_prompt_patch_no_change_rejected(self):
        patch = PromptPatch(layer="sys", old_template="x", new_template="x")
        result = self.qa.verify(patch, self.ep)
        assert result.approved is False

    def test_prompt_patch_with_change_approved(self):
        patch = PromptPatch(layer="sys", old_template="old", new_template="new")
        result = self.qa.verify(patch, self.ep)
        assert result.approved is True

    def test_retry_suggested_on_safety(self):
        patch = ConfigPatch(key="max_velocity", old_value=1.0, new_value=999.0)
        result = self.qa.verify(patch, self.ep)
        assert result.retry_suggested is True

    def test_retry_suggested_on_type(self):
        patch = ConfigPatch(key="x", old_value=1.0, new_value="bad")
        result = self.qa.verify(patch, self.ep)
        assert result.retry_suggested is True

    def test_all_safety_bounds_keys(self):
        for key, (lo, hi) in SAFETY_BOUNDS.items():
            mid = (lo + hi) / 2
            patch = ConfigPatch(key=key, new_value=mid)
            result = self.qa.verify(patch, self.ep)
            safety_check = [c for c in result.checks if c.name == "safety_bounds"][0]
            assert safety_check.passed, f"{key}={mid} should be within bounds"

    def test_result_to_dict(self):
        patch = ConfigPatch(key="max_velocity", old_value=1.0, new_value=2.0)
        result = self.qa.verify(patch, self.ep)
        d = result.to_dict()
        assert "approved" in d
        assert "checks" in d
        assert len(d["checks"]) == 3
