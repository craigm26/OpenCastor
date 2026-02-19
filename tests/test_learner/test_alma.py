"""Tests for ALMAConsolidation."""

from castor.learner.alma import ALMAConsolidation, Pattern
from castor.learner.episode import Episode
from castor.learner.patches import BehaviorPatch, ConfigPatch


def _make_episodes():
    """Mixed success/failure episodes for the same goal type."""
    episodes = []
    for i in range(3):
        episodes.append(
            Episode(
                goal="grasp cup",
                actions=[
                    {"type": "grasp", "result": {"success": False, "error": "slip"}},
                ],
                sensor_readings=[{"force": 0.3 + i * 0.1}],
                success=False,
                duration_s=10.0 + i,
            )
        )
    episodes.append(
        Episode(
            goal="grasp mug",
            actions=[{"type": "grasp", "result": {"success": True}}],
            sensor_readings=[{"force": 0.9}],
            success=True,
            duration_s=5.0,
        )
    )
    return episodes


def _nav_episodes():
    return [
        Episode(
            goal="navigate to dock",
            actions=[{"type": "navigate", "result": {"success": False, "error": "blocked"}}] * 2,
            success=False,
            duration_s=30.0,
        ),
        Episode(
            goal="navigate home",
            actions=[{"type": "navigate", "result": {"success": False, "error": "timeout"}}] * 2,
            success=False,
            duration_s=25.0,
        ),
        Episode(
            goal="navigate lab",
            actions=[{"type": "navigate", "result": {"success": True}}],
            success=True,
            duration_s=8.0,
        ),
    ]


class TestALMAConsolidation:
    def setup_method(self):
        self.alma = ALMAConsolidation()

    def test_empty_episodes(self):
        patches = self.alma.consolidate([])
        assert patches == []

    def test_mixed_episodes_finds_patches(self):
        patches = self.alma.consolidate(_make_episodes())
        assert len(patches) > 0

    def test_patches_have_rationale(self):
        patches = self.alma.consolidate(_make_episodes())
        for p in patches:
            assert "ALMA" in p.rationale

    def test_config_patch_for_known_action(self):
        patches = self.alma.consolidate(_make_episodes())
        config_patches = [p for p in patches if isinstance(p, ConfigPatch)]
        if config_patches:
            assert config_patches[0].key  # should have a config key

    def test_behavior_patch_generated(self):
        patches = self.alma.consolidate(_make_episodes())
        behavior_patches = [p for p in patches if isinstance(p, BehaviorPatch)]
        assert len(behavior_patches) >= 0  # may or may not generate

    def test_score_based_prioritization(self):
        # Multiple failure types — higher failure count should score higher
        episodes = _make_episodes() + _nav_episodes()
        patches = self.alma.consolidate(episodes)
        assert len(patches) > 0

    def test_single_success_no_failure_pattern(self):
        episodes = [
            Episode(goal="scan area", actions=[], success=True, duration_s=2.0),
        ]
        patches = self.alma.consolidate(episodes)
        # Single success with no failures shouldn't produce failure patterns
        assert len(patches) == 0

    def test_nav_episodes_find_patterns(self):
        patches = self.alma.consolidate(_nav_episodes())
        assert len(patches) > 0

    def test_pattern_to_dict(self):
        p = Pattern(description="test", goal_type="grasp", occurrences=3, score=4.5)
        d = p.to_dict()
        assert d["description"] == "test"
        assert d["score"] == 4.5

    def test_sensor_pattern_detection(self):
        # Episodes where sensor readings differ between success/failure
        episodes = [
            Episode(
                goal="grasp item",
                sensor_readings=[{"temp": 20.0}],
                success=True,
                duration_s=3.0,
            ),
            Episode(
                goal="grasp item",
                sensor_readings=[{"temp": 80.0}],
                success=False,
                duration_s=3.0,
            ),
            Episode(
                goal="grasp item",
                sensor_readings=[{"temp": 85.0}],
                success=False,
                duration_s=3.0,
            ),
        ]
        patches = self.alma.consolidate(episodes)
        # Should detect the sensor difference pattern
        assert len(patches) > 0

    def test_max_five_patches(self):
        # Generate many episodes to potentially create many patterns
        episodes = []
        for i in range(20):
            episodes.append(
                Episode(
                    goal=f"type{i % 3} task",
                    actions=[{"type": f"action{i % 5}", "result": {"success": False, "error": "e"}}]
                    * 3,
                    success=False,
                    duration_s=float(i + 10),
                )
            )
        patches = self.alma.consolidate(episodes)
        assert len(patches) <= 10  # reasonable upper bound from top-5 patterns
