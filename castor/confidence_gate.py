"""
OpenCastor Confidence Gate — F2.

Protocol-level check that blocks or escalates commands falling below
configured confidence thresholds per scope.

Config example (RCAN YAML):
    agent:
      confidence_gates:
        - scope: control
          min_confidence: 0.6
          on_fail: escalate
        - scope: nav
          min_confidence: 0.5
          on_fail: block
"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class GateOutcome(Enum):
    PASS = "pass"
    ESCALATE = "escalate"
    BLOCK = "block"
    BYPASS = "bypass"  # on_fail: allow — command proceeds, flagged in audit


@dataclass
class ConfidenceGate:
    scope: str
    min_confidence: float
    on_fail: Literal["escalate", "block", "allow"] = "escalate"


class ConfidenceGateEnforcer:
    """Evaluates confidence gates for given scopes."""

    def __init__(self, gates: list[ConfidenceGate]):
        self._gates: dict[str, ConfidenceGate] = {g.scope: g for g in gates}

    def evaluate(self, scope: str, confidence: Optional[float]) -> GateOutcome:
        """Return the gate outcome for *scope* at *confidence*.

        Args:
            scope:      The gate scope name (e.g. ``"control"``).
            confidence: The confidence value from the Thought, or None.

        Returns:
            :class:`GateOutcome`.PASS if no gate is configured or threshold met.
            Appropriate failure outcome if the gate triggers.
        """
        gate = self._gates.get(scope)
        if gate is None:
            return GateOutcome.PASS
        if confidence is None or confidence < gate.min_confidence:
            if gate.on_fail == "escalate":
                return GateOutcome.ESCALATE
            elif gate.on_fail == "block":
                return GateOutcome.BLOCK
            else:  # allow
                return GateOutcome.BYPASS
        return GateOutcome.PASS
