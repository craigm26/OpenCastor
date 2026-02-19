"""Tests for Patch types."""

import uuid

from castor.learner.patches import BehaviorPatch, ConfigPatch, Patch, PromptPatch


class TestBasePatch:
    def test_defaults(self):
        p = Patch()
        assert p.type == "base"
        assert p.rationale == ""
        assert p.applied is False
        uuid.UUID(p.id)

    def test_to_dict(self):
        p = Patch(rationale="test reason")
        d = p.to_dict()
        assert d["type"] == "base"
        assert d["rationale"] == "test reason"
        assert d["applied"] is False

    def test_from_dict_base(self):
        p = Patch.from_dict({"type": "base", "rationale": "r"})
        assert isinstance(p, Patch)
        assert p.rationale == "r"

    def test_from_dict_dispatches_config(self):
        p = Patch.from_dict({"type": "config", "key": "max_velocity"})
        assert isinstance(p, ConfigPatch)

    def test_from_dict_dispatches_behavior(self):
        p = Patch.from_dict({"type": "behavior", "rule_name": "r1"})
        assert isinstance(p, BehaviorPatch)

    def test_from_dict_dispatches_prompt(self):
        p = Patch.from_dict({"type": "prompt", "layer": "system"})
        assert isinstance(p, PromptPatch)


class TestConfigPatch:
    def test_fields(self):
        p = ConfigPatch(key="max_velocity", old_value=1.0, new_value=2.0, file="config.yaml")
        assert p.type == "config"
        assert p.key == "max_velocity"
        assert p.new_value == 2.0

    def test_serialization_roundtrip(self):
        p = ConfigPatch(key="k", old_value=1, new_value=2, rationale="r", file="f.yaml")
        d = p.to_dict()
        restored = ConfigPatch.from_dict(d)
        assert restored.key == "k"
        assert restored.old_value == 1
        assert restored.new_value == 2
        assert restored.file == "f.yaml"

    def test_to_dict_includes_base_fields(self):
        p = ConfigPatch(key="k")
        d = p.to_dict()
        assert "id" in d
        assert "type" in d
        assert "rationale" in d
        assert "key" in d

    def test_default_file_empty(self):
        p = ConfigPatch(key="x")
        assert p.file == ""

    def test_applied_default_false(self):
        p = ConfigPatch(key="x")
        assert p.applied is False


class TestBehaviorPatch:
    def test_fields(self):
        p = BehaviorPatch(rule_name="avoid_loop", conditions={"x": 1}, action={"y": 2}, priority=5)
        assert p.type == "behavior"
        assert p.rule_name == "avoid_loop"
        assert p.priority == 5

    def test_serialization_roundtrip(self):
        p = BehaviorPatch(rule_name="r", conditions={"a": 1}, action={"b": 2}, priority=3)
        restored = BehaviorPatch.from_dict(p.to_dict())
        assert restored.rule_name == "r"
        assert restored.conditions == {"a": 1}
        assert restored.priority == 3

    def test_default_priority(self):
        p = BehaviorPatch()
        assert p.priority == 0

    def test_empty_conditions(self):
        p = BehaviorPatch(rule_name="r")
        assert p.conditions == {}

    def test_to_dict_has_behavior_fields(self):
        p = BehaviorPatch(rule_name="r")
        d = p.to_dict()
        assert "rule_name" in d
        assert "conditions" in d
        assert "action" in d
        assert "priority" in d


class TestPromptPatch:
    def test_fields(self):
        p = PromptPatch(layer="system", old_template="old", new_template="new")
        assert p.type == "prompt"
        assert p.layer == "system"

    def test_serialization_roundtrip(self):
        p = PromptPatch(layer="L", old_template="a", new_template="b", rationale="r")
        restored = PromptPatch.from_dict(p.to_dict())
        assert restored.layer == "L"
        assert restored.old_template == "a"
        assert restored.new_template == "b"

    def test_defaults(self):
        p = PromptPatch()
        assert p.layer == ""
        assert p.old_template == ""
        assert p.new_template == ""

    def test_to_dict_has_prompt_fields(self):
        d = PromptPatch(layer="x").to_dict()
        assert "layer" in d
        assert "old_template" in d
        assert "new_template" in d

    def test_type_field(self):
        assert PromptPatch().type == "prompt"
