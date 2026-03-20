"""Work unit data classes for contribute skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WORK_UNIT_TYPE_HARNESS_EVAL = "harness_eval"
WORK_UNIT_TYPE_BOINC = "boinc"
WORK_UNIT_TYPE_SIMULATED = "simulated"


@dataclass
class WorkUnit:
    work_unit_id: str
    project: str
    coordinator_url: str
    model_format: str
    input_data: Any
    timeout_seconds: int = 30
    priority: int = 0
    hardware_tier: str | None = None


@dataclass
class WorkUnitResult:
    work_unit_id: str
    output: Any
    latency_ms: float
    hw_profile: dict = field(default_factory=dict)
    status: str = "complete"
    error: str | None = None
