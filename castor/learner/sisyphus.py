"""Sisyphus Loop — orchestrates the PM→Dev→QA→Apply improvement cycle.

Cache-safe forking note (Claude Code lesson):
  When Sisyphus spawns PM→Dev→QA→Apply sub-operations, each stage should
  share the parent session's cached system-prompt prefix.
  - Do NOT build a fresh system prompt per stage.
  - Stage-specific context must go into USER messages, not the system prompt.
  - This ensures Anthropic's prompt cache prefix remains valid across all forks.
  See: castor/prompt_cache.py — build_cached_system_prompt, build_sensor_reminder
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .apply_stage import MAX_RETRIES, ApplyStage
from .dev_stage import DevStage
from .episode import Episode
from .patches import Patch
from .pm_stage import AnalysisReport, PMStage
from .qa_stage import QAResult, QAStage

logger = logging.getLogger(__name__)


@dataclass
class ImprovementResult:
    """Outcome of running the Sisyphus loop on one episode."""

    episode_id: str = ""
    report: Optional[AnalysisReport] = None
    patch: Optional[Patch] = None
    qa_result: Optional[QAResult] = None
    applied: bool = False
    retries: int = 0
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    stage_durations: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "report": self.report.to_dict() if self.report else None,
            "patch": self.patch.to_dict() if self.patch else None,
            "qa_result": self.qa_result.to_dict() if self.qa_result else None,
            "applied": self.applied,
            "retries": self.retries,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "stage_durations": self.stage_durations,
        }


@dataclass
class SisyphusStats:
    """Running statistics for the loop."""

    episodes_analyzed: int = 0
    improvements_applied: int = 0
    improvements_rejected: int = 0
    total_duration_ms: float = 0.0

    @property
    def avg_duration_ms(self) -> float:
        """Average duration per episode in milliseconds."""
        if self.episodes_analyzed == 0:
            return 0.0
        return self.total_duration_ms / self.episodes_analyzed


class SisyphusLoop:
    """Orchestrates the self-improving PM→Dev→QA→Apply loop."""

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        provider: Optional[Any] = None,
    ) -> None:
        self.config = config or {}
        self.provider = provider
        self.pm = PMStage(provider=provider)
        self.dev = DevStage(provider=provider)
        self.qa = QAStage(provider=provider)
        self.apply_stage = ApplyStage()
        self.stats = SisyphusStats()

    def run_episode(self, episode: Episode) -> ImprovementResult:
        """Run the full improvement loop on a single episode."""
        result = ImprovementResult(episode_id=episode.id)
        episode_start = time.monotonic()

        try:
            # PM: Analyze
            t0 = time.monotonic()
            report = self.pm.analyze(episode)
            result.stage_durations["pm_ms"] = round((time.monotonic() - t0) * 1000, 1)
            result.report = report
            self.stats.episodes_analyzed += 1

            if not report.improvements:
                logger.info("No improvements suggested for episode %s", episode.id)
                result.duration_ms = round((time.monotonic() - episode_start) * 1000, 1)
                return result

            # Dev→QA→Apply loop with retries
            patch = None
            qa_result = None

            for attempt in range(MAX_RETRIES):
                result.retries = attempt

                # Dev: Generate fix
                t0 = time.monotonic()
                patch = self.dev.generate_fix(
                    report,
                    previous_attempt=patch,
                    qa_feedback=qa_result,
                )
                result.stage_durations[f"dev_ms_attempt{attempt}"] = round(
                    (time.monotonic() - t0) * 1000, 1
                )
                if not patch:
                    logger.info("Dev stage produced no patch on attempt %d", attempt)
                    break

                result.patch = patch

                # QA: Verify
                t0 = time.monotonic()
                qa_result = self.qa.verify(patch, episode)
                result.stage_durations[f"qa_ms_attempt{attempt}"] = round(
                    (time.monotonic() - t0) * 1000, 1
                )
                result.qa_result = qa_result

                if qa_result.approved:
                    # Apply
                    t0 = time.monotonic()
                    applied = self.apply_stage.apply(patch, qa_result)
                    result.stage_durations["apply_ms"] = round((time.monotonic() - t0) * 1000, 1)
                    result.applied = applied
                    if applied:
                        self.stats.improvements_applied += 1
                    else:
                        self.stats.improvements_rejected += 1
                    result.duration_ms = round((time.monotonic() - episode_start) * 1000, 1)
                    self.stats.total_duration_ms += result.duration_ms
                    return result

                if not qa_result.retry_suggested:
                    logger.info("QA rejected patch without retry suggestion")
                    break

            # Exhausted retries
            if not result.applied:
                self.stats.improvements_rejected += 1

        except Exception as e:
            logger.error("Sisyphus loop error for episode %s: %s", episode.id, e)
            result.error = str(e)

        result.duration_ms = round((time.monotonic() - episode_start) * 1000, 1)
        self.stats.total_duration_ms += result.duration_ms
        return result

    def run_batch(self, episodes: list[Episode]) -> list[ImprovementResult]:
        """Run the improvement loop on a batch of episodes."""
        return [self.run_episode(ep) for ep in episodes]
