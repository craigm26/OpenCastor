"""
castor/harness/cost_meter.py — Per-run token cost tracker and budget enforcer.

Tracks token spend per harness run.  Halts execution gracefully when the
per-run ``budget_usd`` cap is exceeded.

RCAN config::

    cost_meter:
      enabled: true
      budget_usd: 0.05          # per-run budget cap (USD)
      alert_at: 0.8             # warn at 80% of budget
      model: gemini-2.5-flash   # used for pricing lookup
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

__all__ = ["CostMeter", "RunCost", "PRICE_PER_1K"]

logger = logging.getLogger("OpenCastor.CostMeter")

# Approximate pricing per 1 000 tokens (USD).
# Configurable: users can override via config['pricing'].
PRICE_PER_1K: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.00015, "output": 0.0006},
    "gemini-2.5-pro": {"input": 0.00125, "output": 0.010},
    "gemini-2.5-flash-lite": {"input": 0.000075, "output": 0.0003},
    # Legacy aliases (kept for backward compat with old RCAN configs)
    "gemini-2.0-flash": {"input": 0.00015, "output": 0.0006},
    "gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
    "claude-opus-4-6": {"input": 0.015, "output": 0.075},
    "claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    "claude-haiku": {"input": 0.00025, "output": 0.00125},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "default": {"input": 0.001, "output": 0.004},
}


@dataclass
class RunCost:
    """Cost accounting for a single harness run."""

    run_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_usd: float = 0.0
    budget_usd: Optional[float] = None
    budget_exceeded: bool = False
    alert_triggered: bool = False


class CostMeter:
    """Tracks token spend per run and enforces budget caps.

    Args:
        config: ``cost_meter`` section from RCAN config.
    """

    def __init__(self, config: dict) -> None:
        self._budget_usd: Optional[float] = (
            float(config["budget_usd"]) if "budget_usd" in config else None
        )
        self._alert_at: float = float(config.get("alert_at", 0.8))
        model_name = str(config.get("model", "default"))
        self._pricing = self._resolve_pricing(model_name, config.get("pricing", {}))
        # In-memory ledger: run_id → RunCost
        self._runs: dict[str, RunCost] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def record(self, run_id: str, input_tokens: int, output_tokens: int) -> RunCost:
        """Record token usage for ``run_id`` and return updated RunCost."""
        rc = self._get(run_id)
        rc.input_tokens += input_tokens
        rc.output_tokens += output_tokens
        rc.estimated_usd = self._compute_cost(rc.input_tokens, rc.output_tokens)

        if self._budget_usd is not None:
            alert_threshold = self._alert_at * self._budget_usd
            if not rc.alert_triggered and rc.estimated_usd >= alert_threshold:
                rc.alert_triggered = True
                logger.warning(
                    "CostMeter: run=%s at %.0f%% of budget ($%.4f / $%.4f)",
                    run_id,
                    self._alert_at * 100,
                    rc.estimated_usd,
                    self._budget_usd,
                )
            rc.budget_exceeded = rc.estimated_usd >= self._budget_usd

        return rc

    def is_over_budget(self, run_id: str) -> bool:
        """Return True if the run has exceeded its budget cap."""
        rc = self._runs.get(run_id)
        if rc is None:
            return False
        return rc.budget_exceeded

    def current_cost(self, run_id: str) -> RunCost:
        """Return the current cost accumulator for ``run_id``."""
        return self._get(run_id)

    def total_today(self) -> float:
        """Return estimated USD spent today across all in-memory runs.

        Note: only covers runs in the current process lifetime.
        For persistent totals, query the trajectories DB.
        """
        today = time.strftime("%Y-%m-%d")
        _ = today  # placeholder for future DB-backed implementation
        return sum(rc.estimated_usd for rc in self._runs.values())

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(self, run_id: str) -> RunCost:
        if run_id not in self._runs:
            self._runs[run_id] = RunCost(run_id=run_id, budget_usd=self._budget_usd)
        return self._runs[run_id]

    def _compute_cost(self, input_tokens: int, output_tokens: int) -> float:
        in_price = self._pricing["input"]
        out_price = self._pricing["output"]
        return (input_tokens / 1000) * in_price + (output_tokens / 1000) * out_price

    @staticmethod
    def _resolve_pricing(model_name: str, overrides: dict) -> dict[str, float]:
        """Return pricing for ``model_name``, with optional user overrides."""
        if overrides:
            return {
                "input": float(overrides.get("input", 0.001)),
                "output": float(overrides.get("output", 0.004)),
            }
        model_lower = model_name.lower()
        for key, pricing in PRICE_PER_1K.items():
            if key in model_lower:
                return pricing
        return PRICE_PER_1K["default"]
