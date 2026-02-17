"""
OpenCastor Safety State Telemetry — real-time safety health snapshots.

Exposes a composite view of the safety subsystem's state, available
via ``/proc/safety`` in the virtual filesystem.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SafetyStateSnapshot:
    """Point-in-time snapshot of safety subsystem health."""

    timestamp: float = 0.0
    estop_active: bool = False
    locked_out_principals: List[str] = field(default_factory=list)
    active_violations: Dict[str, int] = field(default_factory=dict)
    motor_rate_usage: float = 0.0  # 0.0–1.0 fraction of rate limit used
    active_work_orders: int = 0
    anti_subversion_flags: int = 0
    uptime_seconds: float = 0.0
    safety_score: float = 1.0  # 0.0–1.0 composite health

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SafetyStateSnapshot":
        """Deserialize from a dict, ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


def compute_safety_score(snap: SafetyStateSnapshot) -> float:
    """Compute a composite safety score from 0.0 (critical) to 1.0 (healthy).

    Scoring breakdown:
    - E-stop active: -0.5
    - Each locked-out principal: -0.1 (max -0.3)
    - Motor rate usage > 80%: -0.1
    - Any active violations: -0.05 per principal with violations (max -0.2)
    - Anti-subversion flags > 0: -0.1
    """
    score = 1.0

    if snap.estop_active:
        score -= 0.5

    lockout_penalty = min(len(snap.locked_out_principals) * 0.1, 0.3)
    score -= lockout_penalty

    if snap.motor_rate_usage > 0.8:
        score -= 0.1

    violation_penalty = min(len(snap.active_violations) * 0.05, 0.2)
    score -= violation_penalty

    if snap.anti_subversion_flags > 0:
        score -= 0.1

    return max(0.0, min(1.0, round(score, 4)))


class SafetyTelemetry:
    """Captures safety state snapshots from a SafetyLayer."""

    def __init__(self, start_time: Optional[float] = None):
        self._start_time = start_time or time.time()

    def snapshot(self, safety_layer: Any) -> SafetyStateSnapshot:
        """Capture a point-in-time snapshot from the given SafetyLayer.

        Args:
            safety_layer: A :class:`~castor.fs.safety.SafetyLayer` instance.
        """
        now = time.time()

        # Locked-out principals
        locked = []
        for principal, until in getattr(safety_layer, "_lockouts", {}).items():
            if now < until:
                locked.append(principal)

        # Active violations
        violations = dict(getattr(safety_layer, "_violations", {}))

        # Motor rate usage (fraction of limit used in last 1s window)
        motor_ts = getattr(safety_layer, "_motor_timestamps", [])
        max_hz = safety_layer.limits.get("motor_rate_hz", 20.0)
        recent = [t for t in motor_ts if now - t < 1.0]
        rate_usage = len(recent) / max_hz if max_hz > 0 else 0.0

        # Anti-subversion flags (from /var/log/safety if available)
        anti_sub = 0
        try:
            safety_log = safety_layer.ns.read("/var/log/safety")
            if isinstance(safety_log, list):
                cutoff = now - 300  # last 5 minutes
                for entry in safety_log:
                    if isinstance(entry, dict):
                        if entry.get("t", 0) > cutoff and "subversion" in entry.get(
                            "event", ""
                        ):
                            anti_sub += 1
        except Exception:
            pass

        # Active work orders (from /var/log/actions if available)
        work_orders = 0
        try:
            actions = safety_layer.ns.read("/var/log/actions")
            if isinstance(actions, list):
                work_orders = len(actions)
        except Exception:
            pass

        snap = SafetyStateSnapshot(
            timestamp=now,
            estop_active=getattr(safety_layer, "_estop", False),
            locked_out_principals=locked,
            active_violations=violations,
            motor_rate_usage=min(1.0, rate_usage),
            active_work_orders=work_orders,
            anti_subversion_flags=anti_sub,
            uptime_seconds=now - self._start_time,
        )
        snap.safety_score = compute_safety_score(snap)
        return snap

    def snapshot_dict(self, safety_layer: Any) -> Dict[str, Any]:
        """Capture and return as a serializable dict."""
        return self.snapshot(safety_layer).to_dict()
