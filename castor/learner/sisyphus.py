"""Sisyphus Loop — orchestrates the PM→Dev→QA→Apply improvement cycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "report": self.report.to_dict() if self.report else None,
            "patch": self.patch.to_dict() if self.patch else None,
            "qa_result": self.qa_result.to_dict() if self.qa_result else None,
            "applied": self.applied,
            "retries": self.retries,
            "error": self.error,
        }


@dataclass
class SisyphusStats:
    """Running statistics for the loop."""

    episodes_analyzed: int = 0
    improvements_applied: int = 0
    improvements_rejected: int = 0


class SisyphusLoop:
    """Orchestrates the self-improving PM→Dev→QA→Apply loop."""

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        provider: Optional[Any] = None,
    ) -> None:
        self.config = config or {}
        self.provider = provider
        self.pm = PMStage()
        self.dev = DevStage()
        self.qa = QAStage()
        self.apply_stage = ApplyStage()
        self.stats = SisyphusStats()

    def run_episode(self, episode: Episode) -> ImprovementResult:
        """Run the full improvement loop on a single episode."""
        result = ImprovementResult(episode_id=episode.id)

        try:
            # PM: Analyze
            report = self.pm.analyze(episode)
            result.report = report
            self.stats.episodes_analyzed += 1

            if not report.improvements:
                logger.info("No improvements suggested for episode %s", episode.id)
                return result

            # Dev→QA→Apply loop with retries
            patch = None
            qa_result = None

            for attempt in range(MAX_RETRIES):
                result.retries = attempt

                # Dev: Generate fix
                patch = self.dev.generate_fix(
                    report,
                    previous_attempt=patch,
                    qa_feedback=qa_result,
                )
                if not patch:
                    logger.info("Dev stage produced no patch on attempt %d", attempt)
                    break

                result.patch = patch

                # QA: Verify
                qa_result = self.qa.verify(patch, episode)
                result.qa_result = qa_result

                if qa_result.approved:
                    # Apply
                    applied = self.apply_stage.apply(patch, qa_result)
                    result.applied = applied
                    if applied:
                        self.stats.improvements_applied += 1
                    else:
                        self.stats.improvements_rejected += 1
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

        return result

    def run_batch(self, episodes: list[Episode]) -> list[ImprovementResult]:
        """Run the improvement loop on a batch of episodes."""
        return [self.run_episode(ep) for ep in episodes]
