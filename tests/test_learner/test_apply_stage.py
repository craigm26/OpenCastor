"""Tests for ApplyStage."""

import json

import pytest

from castor.learner.apply_stage import ApplyStage
from castor.learner.patches import BehaviorPatch, ConfigPatch
from castor.learner.qa_stage import QAResult


@pytest.fixture
def stage(tmp_path):
    return ApplyStage(config_dir=tmp_path)


def _approved():
    return QAResult(approved=True, checks=[])


def _rejected():
    return QAResult(approved=False, checks=[])


class TestApplyStage:
    def test_apply_approved_config(self, stage, tmp_path):
        patch = ConfigPatch(key="max_velocity", new_value=2.0, file="config.yaml")
        result = stage.apply(patch, _approved())
        assert result is True
        config_path = tmp_path / "config.yaml"
        assert config_path.exists()

    def test_apply_rejected_does_not_write(self, stage, tmp_path):
        patch = ConfigPatch(key="max_velocity", new_value=2.0, file="config.yaml")
        result = stage.apply(patch, _rejected())
        assert result is False
        config_path = tmp_path / "config.yaml"
        assert not config_path.exists()

    def test_apply_sets_applied_flag(self, stage):
        patch = ConfigPatch(key="k", new_value=1)
        stage.apply(patch, _approved())
        assert patch.applied is True

    def test_rejected_does_not_set_applied(self, stage):
        patch = ConfigPatch(key="k", new_value=1)
        stage.apply(patch, _rejected())
        assert patch.applied is False

    def test_apply_behavior_patch(self, stage, tmp_path):
        patch = BehaviorPatch(rule_name="test_rule", conditions={"a": 1}, action={"b": 2})
        result = stage.apply(patch, _approved())
        assert result is True
        assert (tmp_path / "learned_behaviors.yaml").exists()

    def test_rollback_config(self, stage, tmp_path):
        patch = ConfigPatch(key="max_velocity", old_value=1.0, new_value=2.0, file="config.yaml")
        stage.apply(patch, _approved())
        success = stage.rollback(patch.id)
        assert success is True
        # Verify old value restored
        config_path = tmp_path / "config.yaml"
        assert config_path.exists()

    def test_rollback_nonexistent(self, stage):
        assert stage.rollback("no-such-id") is False

    def test_history_recorded(self, stage, tmp_path):
        patch = ConfigPatch(key="k", new_value=1)
        stage.apply(patch, _approved())
        history_path = tmp_path / "improvement_history.json"
        assert history_path.exists()
        history = json.loads(history_path.read_text())
        assert len(history) == 1
        assert history[0]["success"] is True

    def test_multiple_applies(self, stage):
        for i in range(3):
            patch = ConfigPatch(key=f"k{i}", new_value=i)
            stage.apply(patch, _approved())
        history = json.loads(stage.history_file.read_text())
        assert len(history) == 3

    def test_rollback_behavior(self, stage):
        patch = BehaviorPatch(rule_name="test_rule", conditions={}, action={})
        stage.apply(patch, _approved())
        success = stage.rollback(patch.id)
        assert success is True
