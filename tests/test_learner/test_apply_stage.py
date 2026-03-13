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


class TestApplyStageHistoryCap:
    def test_history_capped_at_max(self, tmp_path):
        """improvement_history.json is capped at MAX_HISTORY_ENTRIES entries."""
        from castor.learner.apply_stage import MAX_HISTORY_ENTRIES

        stage = ApplyStage(config_dir=tmp_path)
        # Pre-populate history with MAX + 5 entries
        n = MAX_HISTORY_ENTRIES + 5
        history = [
            {
                "patch_id": f"p{i}",
                "patch": {},
                "success": True,
                "error": None,
                "timestamp": float(i),
            }
            for i in range(n)
        ]
        stage._save_history(history)

        # Applying one more patch should trigger the cap
        patch = ConfigPatch(key="k", new_value=1)
        stage.apply(patch, _approved())
        saved = json.loads(stage.history_file.read_text())
        assert len(saved) <= MAX_HISTORY_ENTRIES

    def test_history_keeps_most_recent(self, tmp_path):
        """After capping, the newest entries are retained."""
        from castor.learner.apply_stage import MAX_HISTORY_ENTRIES

        stage = ApplyStage(config_dir=tmp_path)
        # Fill to just over the cap
        history = [
            {
                "patch_id": f"p{i}",
                "patch": {},
                "success": True,
                "error": None,
                "timestamp": float(i),
            }
            for i in range(MAX_HISTORY_ENTRIES)
        ]
        stage._save_history(history)

        patch = ConfigPatch(key="new_key", new_value=99)
        stage.apply(patch, _approved())
        saved = json.loads(stage.history_file.read_text())
        # The most recent apply (new_key) should be present
        patch_ids = [e["patch_id"] for e in saved]
        assert patch.id in patch_ids


class TestApplyBehaviorDeduplication:
    def test_duplicate_rule_name_is_replaced_not_appended(self, tmp_path):
        """Applying the same rule_name twice updates the rule, not duplicates it."""
        stage = ApplyStage(config_dir=tmp_path)
        patch1 = BehaviorPatch(rule_name="my_rule", conditions={"v": 1}, action={"a": 1})
        patch2 = BehaviorPatch(rule_name="my_rule", conditions={"v": 2}, action={"a": 2})
        stage.apply(patch1, _approved())
        stage.apply(patch2, _approved())

        import yaml

        behaviors = yaml.safe_load(stage.behaviors_file.read_text())
        rules = behaviors.get("rules", [])
        names = [r["rule_name"] for r in rules]
        assert names.count("my_rule") == 1
        # Second patch's values should be current
        rule = next(r for r in rules if r["rule_name"] == "my_rule")
        assert rule["conditions"] == {"v": 2}

    def test_different_rule_names_both_kept(self, tmp_path):
        """Rules with different names are both retained."""
        stage = ApplyStage(config_dir=tmp_path)
        stage.apply(BehaviorPatch(rule_name="rule_a", conditions={}, action={}), _approved())
        stage.apply(BehaviorPatch(rule_name="rule_b", conditions={}, action={}), _approved())

        import yaml

        behaviors = yaml.safe_load(stage.behaviors_file.read_text())
        names = [r["rule_name"] for r in behaviors.get("rules", [])]
        assert "rule_a" in names
        assert "rule_b" in names
