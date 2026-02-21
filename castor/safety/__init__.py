"""OpenCastor Safety — anti-subversion, authorization, bounds, state, and protocol modules."""

from castor.safety.anti_subversion import ScanResult, ScanVerdict, check_input_safety, scan_input
from castor.safety.authorization import DestructiveActionDetector, WorkAuthority, WorkOrder
from castor.safety.bounds import (
    BoundsChecker,
    BoundsResult,
    BoundsStatus,
    ForceBounds,
    JointBounds,
    WorkspaceBounds,
)
from castor.safety.state import SafetyStateSnapshot, SafetyTelemetry, compute_safety_score

__all__ = [
    "ScanResult",
    "ScanVerdict",
    "scan_input",
    "check_input_safety",
    "WorkOrder",
    "WorkAuthority",
    "DestructiveActionDetector",
    "BoundsChecker",
    "BoundsResult",
    "BoundsStatus",
    "ForceBounds",
    "JointBounds",
    "WorkspaceBounds",
    "SafetyStateSnapshot",
    "SafetyTelemetry",
    "compute_safety_score",
]
