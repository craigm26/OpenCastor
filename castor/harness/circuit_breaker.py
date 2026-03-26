"""
castor/harness/circuit_breaker.py — Per-skill circuit breaker.

Tracks consecutive failures per skill_id.  After ``failure_threshold``
consecutive failures the circuit opens (skill disabled) for ``cooldown_s``
seconds.  If ``half_open_probe`` is true, one probe call is allowed during
cooldown so recovery can be detected.

State is in-memory (lost on restart) — lightweight and sufficient for the
transient failure use-case.

RCAN config::

    circuit_breaker:
      enabled: true
      failure_threshold: 3
      cooldown_s: 300
      half_open_probe: true
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

__all__ = ["CircuitBreaker"]

_STATE_CLOSED = "closed"
_STATE_OPEN = "open"
_STATE_HALF_OPEN = "half_open"


@dataclass
class _SkillState:
    failures: int = 0
    disabled_until: Optional[float] = None
    probe_allowed: bool = False  # one probe during half-open
    in_half_open: bool = False  # True once we've transitioned to half-open


class CircuitBreaker:
    """Per-skill circuit breaker.

    Args:
        config: The ``circuit_breaker`` section from the RCAN config dict.
    """

    def __init__(self, config: dict) -> None:
        self._threshold: int = int(config.get("failure_threshold", 3))
        self._cooldown_s: float = float(config.get("cooldown_s", 300))
        self._half_open_probe: bool = bool(config.get("half_open_probe", True))
        self._states: dict[str, _SkillState] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def record_success(self, skill_id: str) -> None:
        """Reset failure count on success.  Transitions open → closed."""
        s = self._get(skill_id)
        s.failures = 0
        s.disabled_until = None
        s.probe_allowed = False
        s.in_half_open = False

    def record_failure(self, skill_id: str) -> None:
        """Increment failure count.  Opens circuit after threshold."""
        s = self._get(skill_id)
        s.failures += 1
        if s.failures >= self._threshold and s.disabled_until is None:
            s.disabled_until = time.monotonic() + self._cooldown_s
            s.probe_allowed = self._half_open_probe

    def is_open(self, skill_id: str) -> bool:
        """Return True if the circuit is open (skill should be disabled).

        In half-open state, one probe is allowed; subsequent calls return
        True until ``record_success`` is called.
        """
        st = self.state(skill_id)
        if st == _STATE_CLOSED:
            return False
        if st == _STATE_HALF_OPEN:
            s = self._get(skill_id)
            if s.probe_allowed:
                s.probe_allowed = False  # consume the one probe token
                return False  # allow this single call through
            return True
        return True  # _STATE_OPEN

    def state(self, skill_id: str) -> str:
        """Return "closed" | "open" | "half_open"."""
        s = self._get(skill_id)
        if s.disabled_until is None:
            return _STATE_CLOSED
        remaining = s.disabled_until - time.monotonic()
        if remaining <= 0:
            # Cooldown expired → half-open (if probe enabled) or closed
            if self._half_open_probe:
                # probe_allowed is set once in record_failure() — don't overwrite here
                return _STATE_HALF_OPEN
            # No probe — auto-close
            s.disabled_until = None
            s.failures = 0
            return _STATE_CLOSED
        return _STATE_OPEN

    def reset(self, skill_id: str) -> None:
        """Manually reset a skill's circuit to closed."""
        self._states.pop(skill_id, None)

    def status_all(self) -> dict[str, str]:
        """Return {skill_id: state} for all tracked skills."""
        return {sid: self.state(sid) for sid in list(self._states)}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(self, skill_id: str) -> _SkillState:
        if skill_id not in self._states:
            self._states[skill_id] = _SkillState()
        return self._states[skill_id]
