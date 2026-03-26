"""
castor/harness/prompt_guard.py — Prompt injection detector.

Scans incoming instructions for prompt injection patterns before they reach
the model.  Uses compiled regex patterns and returns a ``GuardResult`` with
a risk score.  No ML dependencies.

RCAN config::

    prompt_guard:
      enabled: true
      block_threshold: 0.5    # risk_score >= this → block
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["PromptGuard", "GuardResult", "INJECTION_PATTERNS"]

# ── Default injection pattern library ─────────────────────────────────────────

INJECTION_PATTERNS: list[str] = [
    # Ignore previous instructions
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(your\s+)?(previous|prior|system)\s+(instructions?|prompt)",
    # Role switching
    r"you\s+are\s+now\s+(a\s+)?(DAN|jailbreak|unrestricted|evil)",
    r"act\s+as\s+(if\s+you\s+are\s+)?(a\s+)?(different|unrestricted|evil|an?\s+unrestricted)",
    r"act\s+as\s+an?\s+unrestricted",
    # System prompt leakage
    r"(print|reveal|show|output|repeat)\s+(your\s+)?(system\s+prompt|instructions?|prompt)",
    # RCAN scope escalation via prompt
    r"grant\s+(yourself\s+)?(system|safety|control)\s+scope",
    r"set\s+scope\s*=\s*(system|safety)",
    # Estop bypass via prompt
    r"(bypass|disable|ignore)\s+(estop|emergency.stop|p66|safety)",
    # Credential extraction
    r"(print|show|reveal)\s+(api[_\s]key|token|password|secret)",
]

# Each pattern has equal weight; risk_score = matched / total
_PATTERN_WEIGHT = 1.0 / len(INJECTION_PATTERNS)


@dataclass
class GuardResult:
    """Result of a prompt guard check."""

    blocked: bool
    matched_patterns: list[str] = field(default_factory=list)
    risk_score: float = 0.0


class PromptGuard:
    """Scans instructions for prompt injection patterns.

    Args:
        config: ``prompt_guard`` section from RCAN config.
                Expected keys: ``enabled`` (bool), ``block_threshold`` (float).
    """

    def __init__(self, config: dict) -> None:
        self._threshold: float = float(config.get("block_threshold", 0.5))
        # Compile all patterns case-insensitively
        self._compiled: list[tuple[str, re.Pattern]] = [
            (p, re.compile(p, re.IGNORECASE | re.DOTALL)) for p in INJECTION_PATTERNS
        ]

    def check(self, instruction: str) -> GuardResult:
        """Scan ``instruction`` for injection patterns.

        Returns:
            GuardResult with ``blocked=True`` if risk_score >= threshold.
        """
        matched: list[str] = []
        for pattern_str, compiled in self._compiled:
            if compiled.search(instruction):
                matched.append(pattern_str)

        risk_score = len(matched) / len(self._compiled) if self._compiled else 0.0
        blocked = risk_score >= self._threshold

        return GuardResult(blocked=blocked, matched_patterns=matched, risk_score=risk_score)

    def add_pattern(self, pattern: str) -> None:
        """Add a custom injection pattern at runtime."""
        compiled = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        self._compiled.append((pattern, compiled))
        # Recalculate effective weight is automatic since risk_score uses len()
