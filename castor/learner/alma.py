"""ALMA Consolidation — cross-episode pattern analysis."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .episode import Episode
from .patches import BehaviorPatch, ConfigPatch, Patch

logger = logging.getLogger(__name__)


@dataclass
class Pattern:
    """A recurring pattern found across episodes."""

    description: str = ""
    goal_type: str = ""
    occurrences: int = 0
    success_rate: float = 0.0
    suggested_fix: str = ""
    config_key: str = ""
    suggested_value: Any = None
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "goal_type": self.goal_type,
            "occurrences": self.occurrences,
            "success_rate": self.success_rate,
            "suggested_fix": self.suggested_fix,
            "config_key": self.config_key,
            "suggested_value": self.suggested_value,
            "score": self.score,
        }


class ALMAConsolidation:
    """Cross-episode analysis that finds recurring patterns and generates patches."""

    def consolidate(self, episodes: list[Episode]) -> list[Patch]:
        """Analyze multiple episodes, find patterns, return improvement patches."""
        if not episodes:
            return []

        # Group by goal type (first word of goal)
        groups = self._group_by_goal(episodes)
        patterns = []

        for goal_type, group_episodes in groups.items():
            patterns.extend(self._analyze_group(goal_type, group_episodes))

        # Sort by score (higher = more important)
        patterns.sort(key=lambda p: p.score, reverse=True)

        # Convert top patterns to patches
        return self._patterns_to_patches(patterns)

    def _group_by_goal(self, episodes: list[Episode]) -> dict[str, list[Episode]]:
        groups: dict[str, list[Episode]] = defaultdict(list)
        for ep in episodes:
            goal_type = ep.goal.split()[0].lower() if ep.goal else "unknown"
            groups[goal_type].append(ep)
        return dict(groups)

    def _analyze_group(self, goal_type: str, episodes: list[Episode]) -> list[Pattern]:
        patterns: list[Pattern] = []
        successes = [e for e in episodes if e.success]
        failures = [e for e in episodes if not e.success]
        total = len(episodes)

        if not total:
            return patterns

        success_rate = len(successes) / total

        # Pattern: high failure rate for this goal type
        if len(failures) >= 2 and success_rate < 0.5:
            patterns.append(
                Pattern(
                    description=f"'{goal_type}' tasks fail frequently ({len(failures)}/{total})",
                    goal_type=goal_type,
                    occurrences=len(failures),
                    success_rate=success_rate,
                    suggested_fix=f"Review parameters for {goal_type} tasks",
                    score=len(failures) * (1 - success_rate),
                )
            )

        # Pattern: common failure action types
        failure_actions = self._common_failure_actions(failures)
        for action_type, count in failure_actions.items():
            if count >= 2:
                patterns.append(
                    Pattern(
                        description=f"'{action_type}' action fails in {goal_type} tasks ({count} times)",
                        goal_type=goal_type,
                        occurrences=count,
                        success_rate=success_rate,
                        suggested_fix=f"Tune parameters for {action_type}",
                        config_key=self._action_to_config_key(action_type),
                        score=count * 1.5,
                    )
                )

        # Pattern: successful episodes are significantly faster
        if successes and failures:
            avg_success_dur = sum(e.duration_s for e in successes) / len(successes)
            avg_failure_dur = sum(e.duration_s for e in failures) / len(failures)
            if avg_failure_dur > avg_success_dur * 2:
                patterns.append(
                    Pattern(
                        description=f"Failed {goal_type} tasks take {avg_failure_dur:.1f}s vs {avg_success_dur:.1f}s for successes",
                        goal_type=goal_type,
                        occurrences=len(failures),
                        success_rate=success_rate,
                        suggested_fix="Add timeout or early-exit behavior",
                        score=len(failures) * 1.2,
                    )
                )

        # Pattern: sensor reading correlations
        patterns.extend(self._find_sensor_patterns(goal_type, successes, failures))

        return patterns

    def _common_failure_actions(self, failures: list[Episode]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for ep in failures:
            for action in ep.actions:
                result = action.get("result", {})
                if isinstance(result, dict) and not result.get("success", True):
                    counts[action.get("type", "unknown")] += 1
        return dict(counts)

    def _action_to_config_key(self, action_type: str) -> str:
        mapping = {
            "grasp": "grasp_force",
            "navigate": "max_velocity",
            "detect": "hailo_confidence",
            "plan": "planner_interval",
        }
        for keyword, key in mapping.items():
            if keyword in action_type.lower():
                return key
        return ""

    def _find_sensor_patterns(
        self,
        goal_type: str,
        successes: list[Episode],
        failures: list[Episode],
    ) -> list[Pattern]:
        """Look for sensor reading differences between success/failure."""
        patterns: list[Pattern] = []

        success_readings = self._aggregate_sensor_keys(successes)
        failure_readings = self._aggregate_sensor_keys(failures)

        # Find keys present in both with significantly different averages
        common_keys = set(success_readings.keys()) & set(failure_readings.keys())
        for key in common_keys:
            s_vals = success_readings[key]
            f_vals = failure_readings[key]
            if not s_vals or not f_vals:
                continue
            s_avg = sum(s_vals) / len(s_vals)
            f_avg = sum(f_vals) / len(f_vals)
            if s_avg == 0:
                continue
            diff_ratio = abs(s_avg - f_avg) / max(abs(s_avg), 0.001)
            if diff_ratio > 0.3:
                patterns.append(
                    Pattern(
                        description=(
                            f"Sensor '{key}' differs: avg {s_avg:.2f} (success) vs "
                            f"{f_avg:.2f} (failure) in {goal_type} tasks"
                        ),
                        goal_type=goal_type,
                        occurrences=len(failures),
                        success_rate=len(successes) / max(len(successes) + len(failures), 1),
                        suggested_fix=f"Adjust threshold for sensor '{key}'",
                        score=diff_ratio * len(failures),
                    )
                )

        return patterns

    def _aggregate_sensor_keys(self, episodes: list[Episode]) -> dict[str, list[float]]:
        agg: dict[str, list[float]] = defaultdict(list)
        for ep in episodes:
            for reading in ep.sensor_readings:
                for key, value in reading.items():
                    if isinstance(value, (int, float)):
                        agg[key].append(float(value))
        return dict(agg)

    def _patterns_to_patches(self, patterns: list[Pattern]) -> list[Patch]:
        patches: list[Patch] = []
        seen_keys: set[str] = set()

        for pattern in patterns[:5]:  # Top 5 patterns
            if pattern.config_key and pattern.config_key not in seen_keys:
                seen_keys.add(pattern.config_key)
                patches.append(
                    ConfigPatch(
                        key=pattern.config_key,
                        new_value=pattern.suggested_value,
                        rationale=f"ALMA: {pattern.description} (score={pattern.score:.1f})",
                    )
                )
            elif not pattern.config_key:
                patches.append(
                    BehaviorPatch(
                        rule_name=f"alma_{pattern.goal_type}_{len(patches)}",
                        conditions={"goal_type": pattern.goal_type},
                        action={"fix": pattern.suggested_fix},
                        priority=max(1, int(pattern.score)),
                        rationale=f"ALMA: {pattern.description} (score={pattern.score:.1f})",
                    )
                )

        return patches
