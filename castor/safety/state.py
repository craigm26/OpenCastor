"""
OpenCastor Safety State Telemetry — real-time safety health snapshots.

Exposes a composite view of the safety subsystem's state, available
via ``/proc/safety`` in the virtual filesystem.
"""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Safety.State")


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
    """Captures safety state snapshots from a SafetyLayer.

    Supports optional persistence to a rolling JSONL file so operators can
    review historical safety score trends across sessions.
    """

    #: Max lines kept in the rolling log file (older lines are trimmed on rotate).
    MAX_LOG_LINES = 10_000

    def __init__(
        self,
        start_time: Optional[float] = None,
        log_path: Optional[str] = None,
    ):
        self._start_time = start_time or time.time()
        self._log_path = log_path or os.path.join(
            os.path.expanduser("~/.opencastor"), "safety_telemetry.jsonl"
        )
        self._log_enabled = False
        self._log_interval = 5.0  # seconds between persisted snapshots
        self._last_persisted: float = 0.0

    def enable_persistence(self, log_path: Optional[str] = None, interval_seconds: float = 5.0) -> None:
        """Enable rolling-file persistence for snapshot history.

        Args:
            log_path: Path to the JSONL file (default: ~/.opencastor/safety_telemetry.jsonl).
            interval_seconds: Minimum seconds between writes to avoid I/O saturation.
        """
        if log_path:
            self._log_path = log_path
        self._log_interval = interval_seconds
        self._log_enabled = True
        os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
        logger.info(f"Safety telemetry persistence enabled: {self._log_path}")

    def _persist(self, snap: "SafetyStateSnapshot") -> None:
        """Append the snapshot to the rolling JSONL log file."""
        if not self._log_enabled:
            return
        now = time.time()
        if now - self._last_persisted < self._log_interval:
            return
        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(snap.to_dict()) + "\n")
            self._last_persisted = now
            self._rotate_if_needed()
        except Exception as exc:
            logger.warning(f"Safety telemetry write failed: {exc}")

    def _rotate_if_needed(self) -> None:
        """Trim the log file to MAX_LOG_LINES by discarding oldest entries."""
        try:
            with open(self._log_path, "r") as f:
                lines = f.readlines()
            if len(lines) > self.MAX_LOG_LINES:
                with open(self._log_path, "w") as f:
                    f.writelines(lines[-self.MAX_LOG_LINES :])
        except Exception:
            pass

    def read_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Read the last ``limit`` snapshots from the rolling log file."""
        if not os.path.exists(self._log_path):
            return []
        try:
            with open(self._log_path, "r") as f:
                lines = f.readlines()
            result = []
            for line in lines[-limit:]:
                line = line.strip()
                if line:
                    try:
                        result.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return result
        except Exception:
            return []

    def snapshot(self, safety_layer: Any) -> "SafetyStateSnapshot":
        snap = self._snapshot_impl(safety_layer)
        self._persist(snap)
        return snap

    def _snapshot_impl(self, safety_layer: Any) -> "SafetyStateSnapshot":
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
                        if entry.get("t", 0) > cutoff and "subversion" in entry.get("event", ""):
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

