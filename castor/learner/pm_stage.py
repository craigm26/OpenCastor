"""PM Stage — analyzes episodes and produces improvement reports.

Cache-safe forking note (Claude Code lesson):
  If this stage is extended to call an LLM, it must share the parent
  Sisyphus session's cached system-prompt prefix.
  # CACHE NOTE: System prompt intentionally matches parent session prefix.
  # Do NOT add stage-specific content to system prompt — use user messages instead.
  # Per Claude Code: fork operations must share the parent's cached prefix.
  # TODO: When adding LLM calls here, use build_cached_system_prompt() from
  #       castor.prompt_cache with the same base_prompt as the parent session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .episode import Episode


@dataclass
class ImprovementSuggestion:
    """A single suggested improvement."""

    type: str = "config"  # "config" | "behavior" | "prompt"
    description: str = ""
    config_key: str = ""
    current_value: Any = None
    suggested_value: Any = None
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "description": self.description,
            "config_key": self.config_key,
            "current_value": self.current_value,
            "suggested_value": self.suggested_value,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImprovementSuggestion:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass
class AnalysisReport:
    """Output of PM stage analysis."""

    episode_id: str = ""
    outcome: bool = False
    duration: float = 0.0
    efficiency_score: float = 0.0
    failure_point: Optional[dict[str, Any]] = None
    root_cause: str = ""
    suboptimalities: list[str] = field(default_factory=list)
    improvements: list[ImprovementSuggestion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "outcome": self.outcome,
            "duration": self.duration,
            "efficiency_score": self.efficiency_score,
            "failure_point": self.failure_point,
            "root_cause": self.root_cause,
            "suboptimalities": self.suboptimalities,
            "improvements": [i.to_dict() for i in self.improvements],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisReport:
        improvements = [ImprovementSuggestion.from_dict(i) for i in data.get("improvements", [])]
        return cls(
            episode_id=data.get("episode_id", ""),
            outcome=data.get("outcome", False),
            duration=data.get("duration", 0.0),
            efficiency_score=data.get("efficiency_score", 0.0),
            failure_point=data.get("failure_point"),
            root_cause=data.get("root_cause", ""),
            suboptimalities=data.get("suboptimalities", []),
            improvements=improvements,
        )


# Heuristic baseline for "optimal" action counts by goal keyword.
_OPTIMAL_ACTIONS: dict[str, int] = {
    "navigate": 5,
    "grasp": 4,
    "pick": 4,
    "place": 3,
    "inspect": 2,
    "scan": 3,
}

# Known failure patterns (action type → likely root cause).
_FAILURE_HINTS: dict[str, str] = {
    "grasp": "Grasp approach angle or gripper force may need adjustment",
    "navigate": "Path planning parameters may be suboptimal",
    "detect": "Detection confidence threshold may be too high or too low",
    "plan": "Planner timeout or interval may need tuning",
}


class PMStage:
    """Analyzes episode outcomes and identifies improvement opportunities."""

    def analyze(self, episode: Episode) -> AnalysisReport:
        """Analyze a single episode and produce an AnalysisReport."""
        report = AnalysisReport(
            episode_id=episode.id,
            outcome=episode.success,
            duration=episode.duration_s,
        )

        # Compute efficiency score
        report.efficiency_score = self._compute_efficiency(episode)

        # Identify failure points
        if not episode.success:
            report.failure_point = self._find_failure_moment(episode)
            report.root_cause = self._diagnose_root_cause(episode, report.failure_point)

        # Identify suboptimalities even on success
        report.suboptimalities = self._find_inefficiencies(episode)

        # Generate improvement suggestions
        report.improvements = self._suggest_improvements(episode, report)

        return report

    def _compute_efficiency(self, episode: Episode) -> float:
        """Ratio of optimal actions to actual actions (capped at 1.0)."""
        actual = len(episode.actions)
        if actual == 0:
            return 0.0
        optimal = self._estimate_optimal(episode)
        return min(1.0, optimal / actual)

    def _estimate_optimal(self, episode: Episode) -> int:
        goal_lower = episode.goal.lower()
        for keyword, count in _OPTIMAL_ACTIONS.items():
            if keyword in goal_lower:
                return count
        return max(1, len(episode.actions) // 2)

    def _find_failure_moment(self, episode: Episode) -> Optional[dict[str, Any]]:
        """Find the action where things went wrong."""
        for i, action in enumerate(episode.actions):
            result = action.get("result", {})
            if isinstance(result, dict) and not result.get("success", True):
                return {
                    "tick": i,
                    "action": action.get("type", "unknown"),
                    "reason": result.get("error", "unknown error"),
                }
        # If no explicit failure found, last action is suspect
        if episode.actions:
            last = episode.actions[-1]
            return {
                "tick": len(episode.actions) - 1,
                "action": last.get("type", "unknown"),
                "reason": "Episode ended unsuccessfully after this action",
            }
        return None

    def _diagnose_root_cause(
        self, episode: Episode, failure_point: Optional[dict[str, Any]]
    ) -> str:
        if not failure_point:
            return "No failure point identified"
        action_type = failure_point.get("action", "")
        for keyword, hint in _FAILURE_HINTS.items():
            if keyword in action_type.lower():
                return hint
        return f"Action '{action_type}' failed: {failure_point.get('reason', 'unknown')}"

    def _find_inefficiencies(self, episode: Episode) -> list[str]:
        issues: list[str] = []
        if len(episode.actions) > 10:
            issues.append(f"High action count ({len(episode.actions)}) — possible redundant steps")
        if episode.duration_s > 60:
            issues.append(f"Long duration ({episode.duration_s:.1f}s) — may indicate stalling")
        # Check for repeated actions
        action_types = [a.get("type", "") for a in episode.actions]
        for t in set(action_types):
            count = action_types.count(t)
            if count > 3:
                issues.append(f"Action '{t}' repeated {count} times — possible loop")
        return issues

    def _suggest_improvements(
        self, episode: Episode, report: AnalysisReport
    ) -> list[ImprovementSuggestion]:
        suggestions: list[ImprovementSuggestion] = []

        if report.failure_point:
            action_type = report.failure_point.get("action", "").lower()
            if "grasp" in action_type:
                suggestions.append(
                    ImprovementSuggestion(
                        type="config",
                        description="Adjust grasp approach parameters",
                        config_key="grasp_force",
                        current_value=None,
                        suggested_value=None,
                        rationale=report.root_cause,
                    )
                )
            if "navigate" in action_type or "plan" in action_type:
                suggestions.append(
                    ImprovementSuggestion(
                        type="config",
                        description="Tune planner interval",
                        config_key="planner_interval",
                        current_value=None,
                        suggested_value=None,
                        rationale=report.root_cause,
                    )
                )
            if "detect" in action_type:
                suggestions.append(
                    ImprovementSuggestion(
                        type="config",
                        description="Adjust detection confidence threshold",
                        config_key="hailo_confidence",
                        current_value=None,
                        suggested_value=None,
                        rationale=report.root_cause,
                    )
                )

        # Behavior suggestion for repeated actions
        for sub in report.suboptimalities:
            if "repeated" in sub.lower() or "loop" in sub.lower():
                suggestions.append(
                    ImprovementSuggestion(
                        type="behavior",
                        description="Add loop-breaking behavior rule",
                        rationale=sub,
                    )
                )

        if report.efficiency_score < 0.5 and not suggestions:
            suggestions.append(
                ImprovementSuggestion(
                    type="config",
                    description="General efficiency improvement needed",
                    rationale=f"Efficiency score is low ({report.efficiency_score:.2f})",
                )
            )

        return suggestions
