"""QA Stage — verifies patches before they are applied.

Cache-safe forking note (Claude Code lesson):
  If this stage is extended to call an LLM for verification, it must
  share the parent Sisyphus session's cached system-prompt prefix.
  # CACHE NOTE: System prompt intentionally matches parent session prefix.
  # Do NOT add stage-specific content to system prompt — use user messages instead.
  # Per Claude Code: fork operations must share the parent's cached prefix.
  # TODO: When adding LLM calls here, use build_cached_system_prompt() from
  #       castor.prompt_cache with the same base_prompt as the parent session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .episode import Episode
from .patches import BehaviorPatch, ConfigPatch, Patch, PromptPatch

_log = logging.getLogger("OpenCastor.Learner.QAStage")

# Safety bounds: key → (min, max)
SAFETY_BOUNDS: dict[str, tuple[float, float]] = {
    "max_velocity": (0.0, 3.0),
    "min_obstacle_m": (0.1, 2.0),
    "hailo_confidence": (0.1, 0.99),
    "planner_interval": (1.0, 100.0),
}


@dataclass
class QACheck:
    """A single quality-assurance check result."""

    name: str = ""
    passed: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class QAResult:
    """Aggregate result of QA verification."""

    approved: bool = False
    checks: list[QACheck] = field(default_factory=list)
    retry_suggested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "checks": [c.to_dict() for c in self.checks],
            "retry_suggested": self.retry_suggested,
        }


class QAStage:
    """Verifies patches meet safety and consistency requirements.

    If a *provider* is supplied (any ``BaseProvider`` instance), the stage
    will additionally call the LLM to flag any semantic issues the heuristic
    checks may have missed.  Gracefully degrades to heuristic-only when
    provider is ``None`` or when the LLM call fails.
    """

    def __init__(self, provider=None):
        self._provider = provider

    def verify(self, patch: Patch, episode: Episode) -> QAResult:
        """Run all QA checks on a patch."""
        checks: list[QACheck] = []

        checks.append(self._check_safety_bounds(patch))
        checks.append(self._check_consistency(patch))
        checks.append(self._check_types(patch))

        all_passed = all(c.passed for c in checks)
        retry = not all_passed and any(
            c.name in ("safety_bounds", "type_check") and not c.passed for c in checks
        )

        # LLM semantic check (additive — only adds checks, never removes existing ones)
        if self._provider is not None and all_passed:
            llm_check = self._llm_semantic_check(patch, episode)
            if llm_check is not None:
                checks.append(llm_check)
                if not llm_check.passed:
                    all_passed = False
                    retry = True

        return QAResult(
            approved=all_passed,
            checks=checks,
            retry_suggested=retry,
        )

    # ------------------------------------------------------------------
    # LLM semantic check (cache-safe: context injected as user message)
    # ------------------------------------------------------------------

    def _llm_semantic_check(self, patch: Patch, episode: Episode) -> Optional[QACheck]:
        """Ask the LLM whether the patch makes semantic sense for this episode.

        Returns a ``QACheck`` or ``None`` if the LLM call fails.
        """
        import json

        try:
            patch_summary = {}
            if isinstance(patch, ConfigPatch):
                patch_summary = {
                    "type": "config",
                    "key": patch.key,
                    "old_value": patch.old_value,
                    "new_value": patch.new_value,
                    "rationale": patch.rationale,
                }
            instruction = (
                f"Evaluate whether this robot config patch is safe and sensible. "
                f"Patch: {json.dumps(patch_summary)}. "
                f"Episode goal: '{episode.goal}', success: {episode.success}. "
                f"Return ONLY valid JSON: "
                f'{{\"approved\": true|false, \"reason\": \"<brief>\"}}'
            )
            thought = self._provider.think(b"", instruction)
            if thought.action and isinstance(thought.action, dict):
                approved = bool(thought.action.get("approved", True))
                reason = thought.action.get("reason", "LLM check")
                return QACheck(
                    name="llm_semantic",
                    passed=approved,
                    detail=reason,
                )
        except Exception as exc:
            _log.debug("LLM semantic check skipped: %s", exc)
        return None

    def _check_safety_bounds(self, patch: Patch) -> QACheck:
        """Verify config values are within safety bounds."""
        if not isinstance(patch, ConfigPatch):
            return QACheck(name="safety_bounds", passed=True, detail="N/A for non-config patch")

        key = patch.key
        value = patch.new_value

        if key in SAFETY_BOUNDS and isinstance(value, (int, float)):
            lo, hi = SAFETY_BOUNDS[key]
            if not (lo <= float(value) <= hi):
                return QACheck(
                    name="safety_bounds",
                    passed=False,
                    detail=f"{key}={value} outside bounds [{lo}, {hi}]",
                )

        return QACheck(name="safety_bounds", passed=True, detail="Within bounds")

    def _check_consistency(self, patch: Patch) -> QACheck:
        """Check that the patch doesn't conflict with itself."""
        if isinstance(patch, ConfigPatch):
            if patch.old_value is not None and patch.old_value == patch.new_value:
                return QACheck(
                    name="consistency",
                    passed=False,
                    detail="Patch changes nothing (old == new)",
                )
        if isinstance(patch, BehaviorPatch):
            if not patch.rule_name:
                return QACheck(
                    name="consistency",
                    passed=False,
                    detail="Behavior patch has no rule name",
                )
        if isinstance(patch, PromptPatch):
            if patch.old_template == patch.new_template:
                return QACheck(
                    name="consistency",
                    passed=False,
                    detail="Prompt patch changes nothing",
                )
        return QACheck(name="consistency", passed=True, detail="Consistent")

    def _check_types(self, patch: Patch) -> QACheck:
        """Verify value types are correct."""
        if not isinstance(patch, ConfigPatch):
            return QACheck(name="type_check", passed=True, detail="N/A for non-config patch")

        if patch.old_value is not None and patch.new_value is not None:
            if type(patch.old_value) is not type(patch.new_value):
                return QACheck(
                    name="type_check",
                    passed=False,
                    detail=f"Type mismatch: {type(patch.old_value).__name__} → {type(patch.new_value).__name__}",
                )

        return QACheck(name="type_check", passed=True, detail="Types OK")
