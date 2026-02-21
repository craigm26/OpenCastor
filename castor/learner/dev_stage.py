"""Dev Stage — generates concrete patches from analysis reports.

Cache-safe forking note (Claude Code lesson):
  If this stage is extended to call an LLM for patch generation, it must
  share the parent Sisyphus session's cached system-prompt prefix.
  # CACHE NOTE: System prompt intentionally matches parent session prefix.
  # Do NOT add stage-specific content to system prompt — use user messages instead.
  # Per Claude Code: fork operations must share the parent's cached prefix.
  # TODO: When adding LLM calls here, use build_cached_system_prompt() from
  #       castor.prompt_cache and inject stage context via build_sensor_reminder()
  #       or a similar user-message block pattern.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .patches import BehaviorPatch, ConfigPatch, Patch
from .pm_stage import AnalysisReport, ImprovementSuggestion
from .qa_stage import QAResult

_log = logging.getLogger("OpenCastor.Learner.DevStage")


class DevStage:
    """Maps improvement suggestions to concrete patches.

    If a *provider* is supplied (any ``BaseProvider`` instance), the stage
    will additionally call the LLM to propose better patch values when the
    heuristic cannot determine a concrete ``new_value``.  Gracefully degrades
    to heuristic-only when provider is ``None`` or when the LLM call fails.
    """

    def __init__(self, provider=None):
        self._provider = provider

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

        patch = self._suggestion_to_patch(suggestion)

        # If the heuristic produced a patch with no concrete value, ask the LLM
        if self._provider is not None and isinstance(patch, ConfigPatch) and patch.new_value is None:
            self._llm_fill_value(patch, suggestion, report)

        return patch

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

    def _llm_fill_value(
        self, patch: ConfigPatch, suggestion: ImprovementSuggestion, report: AnalysisReport
    ) -> None:
        """Ask the LLM to propose a concrete ``new_value`` for a config patch.

        Injects all context as a user message so the provider's cached
        system-prompt prefix remains stable (cache-safe design).
        Modifies *patch* in place.  Silently skips on any error.
        """
        import json

        try:
            instruction = (
                f"Propose a concrete new value for robot config key '{patch.key}'. "
                f"Current value: {json.dumps(patch.old_value)}. "
                f"Rationale: {patch.rationale}. "
                f"Efficiency score: {report.efficiency_score:.2f}. "
                f"Return ONLY valid JSON: "
                f'{{\"new_value\": <value>, \"rationale\": \"<brief explanation>\"}}'
            )
            thought = self._provider.think(b"", instruction)
            if thought.action and isinstance(thought.action, dict):
                val = thought.action.get("new_value")
                if val is not None:
                    patch.new_value = self._fix_type(patch.old_value, val)
                if thought.action.get("rationale"):
                    patch.rationale = thought.action["rationale"]
        except Exception as exc:
            _log.debug("LLM value suggestion skipped: %s", exc)

    def _fix_type(self, old_value: Any, new_value: Any) -> Any:
        """Try to cast new_value to the type of old_value."""
        if old_value is None:
            return new_value
        try:
            return type(old_value)(new_value)
        except (TypeError, ValueError):
            return old_value
