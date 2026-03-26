"""Tests for castor contribute skill."""

import threading
import time

import pytest

from castor.contribute.coordinator import SimulatedCoordinator
from castor.contribute.runner import run_work_unit
from castor.contribute.work_unit import WorkUnit, WorkUnitResult
from castor.skills.contribute import ContributeSkill


def test_work_unit_dataclass():
    wu = WorkUnit("wu-001", "climate", "http://example.com", "numpy", {})
    assert wu.work_unit_id == "wu-001"
    assert wu.timeout_seconds == 30


def test_work_unit_result_defaults():
    r = WorkUnitResult("wu-001", output=None, latency_ms=10.0)
    assert r.status == "complete"
    assert r.error is None


def test_simulated_coordinator_returns_work_unit():
    c = SimulatedCoordinator()
    wu = c.fetch_work_unit({}, ["climate"])
    assert wu is not None
    assert wu.project == "climate"


def test_runner_completes():
    wu = WorkUnit("wu-test", "science", "sim://", "numpy", {}, timeout_seconds=1)
    result = run_work_unit(wu)
    assert result.status == "complete"
    assert result.latency_ms > 0


def test_runner_cancellation():
    wu = WorkUnit("wu-cancel", "science", "sim://", "numpy", {}, timeout_seconds=60)
    flag = [False]

    def cancel_after():
        time.sleep(0.3)
        flag[0] = True

    threading.Thread(target=cancel_after, daemon=True).start()
    result = run_work_unit(wu, cancelled_flag=flag)
    assert result.status == "cancelled"


def test_idle_detection():
    skill = ContributeSkill()
    assert skill.is_idle(time.time() - 1000, idle_after_minutes=15) is True
    assert skill.is_idle(time.time() - 60, idle_after_minutes=15) is False


def test_skill_status_returns_dict():
    skill = ContributeSkill()
    s = skill.status()
    assert "enabled" in s
    assert "work_units_total" in s
    assert "active" in s
