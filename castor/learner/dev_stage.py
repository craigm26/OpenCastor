"""Dev Stage — generates concrete patches from analysis reports."""

from __future__ import annotations

from typing import Any, Optional

from .patches import BehaviorPatch, ConfigPatch, Patch
from .pm_stage import AnalysisReport, ImprovementSuggestion
from .qa_stage import QAResult


class DevStage:
    """Maps improvement suggestions to concrete patches."""

    def generate_fix(
        self,
        report: AnalysisReport,
        previous_attempt: Optional[Patch] = None,
        qa_feedback: Optional[QAResult] = None,
    ) -> Optional[Patch]:
        """Generate a patch from the top improvement suggestion.

        If qa_feedback is provided (retry), adjusts the patch based on
        which checks failed.
        """
        if not report.improvements:
            return None

        suggestion = report.improvements[0]

        # On retry with feedback, try to fix what QA flagged
        if previous_attempt and qa_feedback:
            return self._adjust_for_feedback(previous_attempt, suggestion, qa_feedback)

        return self._suggestion_to_patch(suggestion)

    def _suggestion_to_patch(self, suggestion: ImprovementSuggestion) -> Patch:
        if suggestion.type == "config":
            return ConfigPatch(
                file=suggestion.config_key + ".yaml" if suggestion.config_key else "",
                key=suggestion.config_key,
                old_value=suggestion.current_value,
                new_value=suggestion.suggested_value,
                rationale=suggestion.rationale or suggestion.description,
            )
        if suggestion.type == "behavior":
            return BehaviorPatch(
                rule_name=suggestion.config_key or suggestion.description.replace(" ", "_").lower(),
                conditions={"trigger": suggestion.description},
                action={"response": "apply_fix"},
                priority=5,
                rationale=suggestion.rationale or suggestion.description,
            )
        # Default to config patch
        return ConfigPatch(
            key=suggestion.config_key,
            rationale=suggestion.rationale or suggestion.description,
        )

    def _adjust_for_feedback(
        self,
        previous: Patch,
        suggestion: ImprovementSuggestion,
        feedback: QAResult,
    ) -> Patch:
        """Adjust a patch based on QA feedback."""
        if not isinstance(previous, ConfigPatch):
            return self._suggestion_to_patch(suggestion)

        new_value = previous.new_value
        for check in feedback.checks:
            if not check.passed and "safety_bounds" in check.name:
                # Clamp toward the safe middle
                new_value = self._clamp_to_safe(previous.key, new_value)
                break
            if not check.passed and "type_check" in check.name:
                # Try to cast to the expected type
                new_value = self._fix_type(previous.old_value, new_value)
                break

        return ConfigPatch(
            file=previous.file,
            key=previous.key,
            old_value=previous.old_value,
            new_value=new_value,
            rationale=f"Retry: {previous.rationale} (adjusted for QA feedback)",
        )

    def _clamp_to_safe(self, key: str, value: Any) -> Any:
        """Clamp a value toward the midpoint of safety bounds."""
        from .qa_stage import SAFETY_BOUNDS

        if key in SAFETY_BOUNDS and isinstance(value, (int, float)):
            lo, hi = SAFETY_BOUNDS[key]
            mid = (lo + hi) / 2
            # Move 50% toward midpoint
            return value + (mid - value) * 0.5
        return value

    def _fix_type(self, old_value: Any, new_value: Any) -> Any:
        """Try to cast new_value to the type of old_value."""
        if old_value is None:
            return new_value
        try:
            return type(old_value)(new_value)
        except (TypeError, ValueError):
            return old_value
