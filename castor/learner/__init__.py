"""castor.learner — Self-Improving Loop (Sisyphus Pattern) for OpenCastor.

Implements a four-stage continuous improvement cycle that analyzes recorded
robot episodes and automatically proposes config/behavior patches:

  ``Record → PM (Analyze) → Dev (Patch) → QA (Test) → Apply``

Key classes:

- :class:`EpisodeStore` — Persistent store for observation/action/outcome
  tuples recorded during the perception-action loop.
- :class:`SisyphusLoop` — Orchestrates the full improvement cycle. Run
  with ``castor improve --enable`` or instantiate directly.
- :class:`PMStage` — Product-manager stage: analyzes recent episodes and
  produces an :class:`AnalysisReport` identifying failure patterns.
- :class:`DevStage` — Developer stage: proposes :class:`Patch` objects
  (config changes, prompt changes, behavior tweaks) from the report.
- :class:`QAStage` — Quality-assurance stage: validates patches against
  recorded episodes before promotion.
- :class:`ApplyStage` — Applies approved patches to the live RCAN config
  and/or the running robot system.
- :class:`ALMAConsolidation` — Aggregates patches across swarm members
  and resolves conflicts before applying globally.

Enable via RCAN config::

    learner:
      enabled: true
      cycle_interval_s: 300   # run every 5 minutes
      min_episodes: 10        # episodes required before first cycle
"""

from .alma import ALMAConsolidation
from .apply_stage import ApplyStage
from .dev_stage import DevStage
from .episode import Episode
from .episode_store import EpisodeStore
from .patches import BehaviorPatch, ConfigPatch, Patch, PromptPatch
from .pm_stage import AnalysisReport, PMStage
from .qa_stage import QAResult, QAStage
from .sisyphus import SisyphusLoop

__all__ = [
    "ALMAConsolidation",
    "AnalysisReport",
    "ApplyStage",
    "BehaviorPatch",
    "ConfigPatch",
    "DevStage",
    "Episode",
    "EpisodeStore",
    "Patch",
    "PMStage",
    "PromptPatch",
    "QAResult",
    "QAStage",
    "SisyphusLoop",
]
