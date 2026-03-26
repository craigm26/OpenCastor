"""Tests for castor/skills/loader.py — SkillLoader and SkillSelector."""

from __future__ import annotations

import json

import pytest

from castor.skills.loader import SkillLoader, SkillSelector, _parse_yaml_simple, _split_frontmatter

SAMPLE_SKILL_MD = """\
---
name: test-skill
description: >
  Use when testing skill loading functionality in unit tests.
version: "1.0"
requires:
  - vision
consent: required
tools:
  - get_camera_frame
  - get_distance
max_iterations: 5
---

# Test Skill

## Steps
1. Do something
2. Do something else
"""


@pytest.fixture
def skill_dir(tmp_path):
    """Create a temp skill directory with one skill."""
    skill = tmp_path / "test-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(SAMPLE_SKILL_MD)
    return tmp_path


class TestSkillLoader:
    def test_load_all_finds_skills(self, skill_dir):
        loader = SkillLoader(extra_paths=[skill_dir])
        skills = loader.load_all()
        assert "test-skill" in skills

    def test_skill_fields_parsed(self, skill_dir):
        loader = SkillLoader(extra_paths=[skill_dir])
        skill = loader.load_all()["test-skill"]
        assert skill["name"] == "test-skill"
        assert "testing skill loading" in skill["description"].lower()
        assert skill["version"] == "1.0"
        assert "vision" in skill["requires"]
        assert skill["consent"] == "required"
        assert "get_camera_frame" in skill["tools"]
        assert skill["max_iterations"] == 5
        assert "## Steps" in skill["body"]

    def test_load_skill_single(self, skill_dir):
        loader = SkillLoader()
        skill_path = skill_dir / "test-skill"
        skill = loader.load_skill(skill_path)
        assert skill is not None
        assert skill["name"] == "test-skill"

    def test_missing_name_skipped(self, tmp_path):
        bad = tmp_path / "bad-skill"
        bad.mkdir()
        (bad / "SKILL.md").write_text("---\ndescription: no name\n---\nBody")
        loader = SkillLoader(extra_paths=[tmp_path])
        skills = loader.load_all()
        assert "bad-skill" not in skills

    def test_no_frontmatter_skipped(self, tmp_path):
        bad = tmp_path / "no-front"
        bad.mkdir()
        (bad / "SKILL.md").write_text("# Just a body\nNo frontmatter here")
        loader = SkillLoader(extra_paths=[tmp_path])
        skills = loader.load_all()
        assert "no-front" not in skills

    def test_cache_works(self, skill_dir):
        loader = SkillLoader(extra_paths=[skill_dir])
        s1 = loader.load_all()
        s2 = loader.load_all()
        assert s1 is s2  # same object = cached

    def test_invalidate_cache(self, skill_dir):
        loader = SkillLoader(extra_paths=[skill_dir])
        s1 = loader.load_all()
        loader.invalidate_cache()
        s2 = loader.load_all()
        assert s1 is not s2

    def test_builtin_skills_loadable(self):
        """Built-in skills should all parse without error."""
        loader = SkillLoader()
        skills = loader.load_all()
        # At minimum the 5 built-in skills should be present
        expected = {"web-lookup", "camera-describe", "navigate-to", "arm-manipulate", "peer-coordinate"}
        missing = expected - set(skills.keys())
        assert not missing, f"Missing built-in skills: {missing}"

    def test_builtin_consent_flags(self):
        loader = SkillLoader()
        skills = loader.load_all()
        assert skills["navigate-to"]["consent"] == "required"
        assert skills["arm-manipulate"]["consent"] == "required"
        assert skills["web-lookup"]["consent"] == "none"
        assert skills["peer-coordinate"]["consent"] == "none"


class TestSkillSelector:
    def _skills(self):
        loader = SkillLoader()
        return loader.load_all()

    def test_explicit_trigger(self):
        skills = self._skills()
        selector = SkillSelector()
        result = selector.select("/web-lookup tell me about something", skills)
        assert result is not None
        assert result["name"] == "web-lookup"

    def test_explicit_unknown_returns_none(self):
        skills = self._skills()
        selector = SkillSelector()
        result = selector.select("/nonexistent-skill do something", skills)
        assert result is None

    def test_keyword_match_navigation(self):
        skills = self._skills()
        selector = SkillSelector()
        result = selector.select("go to the table please", skills)
        assert result is not None
        assert result["name"] == "navigate-to"

    def test_keyword_match_arm(self):
        skills = self._skills()
        selector = SkillSelector()
        result = selector.select("pick up the red brick", skills)
        assert result is not None
        assert result["name"] == "arm-manipulate"

    def test_keyword_match_peer(self):
        skills = self._skills()
        selector = SkillSelector()
        result = selector.select("ask Alex what she sees", skills)
        assert result is not None
        assert result["name"] == "peer-coordinate"

    def test_keyword_match_camera(self):
        skills = self._skills()
        selector = SkillSelector()
        result = selector.select("what do you see", skills)
        assert result is not None
        assert result["name"] == "camera-describe"

    def test_keyword_match_web(self):
        skills = self._skills()
        selector = SkillSelector()
        result = selector.select("search for Feetech servo specifications", skills)
        assert result is not None
        assert result["name"] == "web-lookup"

    def test_no_match_returns_none(self):
        skills = self._skills()
        selector = SkillSelector()
        result = selector.select("x", skills)
        assert result is None

    def test_empty_skills_returns_none(self):
        selector = SkillSelector()
        result = selector.select("go to the table", {})
        assert result is None

    def test_capability_filter_no_vision(self):
        """navigate-to should be returned even without vision (only requires: control)."""
        skills = self._skills()
        selector = SkillSelector()
        result = selector.select("drive forward", skills, robot_capabilities=["control", "drive"])
        assert result is not None
        assert result["name"] == "navigate-to"


class TestFrontmatterParsing:
    def test_split_frontmatter(self):
        fm, body = _split_frontmatter(SAMPLE_SKILL_MD)
        assert fm is not None
        assert "name: test-skill" in fm
        assert "# Test Skill" in body

    def test_no_frontmatter(self):
        fm, body = _split_frontmatter("# Just a body")
        assert fm is None
        assert body == "# Just a body"

    def test_parse_yaml_simple(self):
        yaml_text = "name: my-skill\nversion: \"1.0\"\nmax_iterations: 8"
        parsed = _parse_yaml_simple(yaml_text)
        assert parsed["name"] == "my-skill"
        assert parsed["max_iterations"] == 8


class TestEvalJsonFiles:
    """Verify eval.json files are valid for skills that have them."""

    @pytest.mark.parametrize("skill_name", [
        "web-lookup", "navigate-to", "arm-manipulate", "peer-coordinate"
    ])
    def test_eval_json_valid(self, skill_name):
        from castor.skills.loader import _BUILTIN_DIR
        eval_path = _BUILTIN_DIR / skill_name / "tests" / "eval.json"
        assert eval_path.exists(), f"eval.json missing for {skill_name}"
        cases = json.loads(eval_path.read_text())
        assert isinstance(cases, list)
        assert len(cases) >= 8
        for case in cases:
            assert "id" in case
            assert "prompt" in case
            assert "should_trigger" in case
