"""Tests for SwarmCoordinator."""

from __future__ import annotations

import time

import pytest

from castor.swarm.consensus import SwarmConsensus
from castor.swarm.coordinator import Assignment, SwarmCoordinator, SwarmTask
from castor.swarm.peer import SwarmPeer
from castor.swarm.shared_memory import SharedMemory


def _mem(robot_id: str = "coordinator") -> SharedMemory:
    return SharedMemory(robot_id=robot_id, persist_path="/dev/null/unused")


def _make_coordinator(robot_id: str = "coordinator") -> SwarmCoordinator:
    mem = _mem(robot_id)
    consensus = SwarmConsensus(robot_id, mem)
    return SwarmCoordinator(my_robot_id=robot_id, shared_memory=mem, consensus=consensus)


def _make_peer(
    robot_id: str = "robot-1",
    capabilities: list[str] | None = None,
    load_score: float = 0.0,
    last_seen: float | None = None,
) -> SwarmPeer:
    if capabilities is None:
        capabilities = []
    if last_seen is None:
        last_seen = time.time()
    return SwarmPeer(
        robot_id=robot_id,
        robot_name=robot_id,
        host="10.0.0.1",
        port=8000,
        capabilities=capabilities,
        last_seen=last_seen,
        load_score=load_score,
    )


def _make_task(
    task_id: str = "task-1",
    task_type: str = "explore",
    goal: str = "Go somewhere",
    required_capability: str | None = None,
    priority: int = 5,
) -> SwarmTask:
    return SwarmTask(
        task_id=task_id,
        task_type=task_type,
        goal=goal,
        required_capability=required_capability,
        priority=priority,
        created_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Peer management
# ---------------------------------------------------------------------------


class TestPeerManagement:
    def test_add_peer(self):
        coord = _make_coordinator()
        peer = _make_peer("robot-1")
        coord.add_peer(peer)
        assert "robot-1" in [p.robot_id for p in coord.get_peers()]

    def test_remove_peer(self):
        coord = _make_coordinator()
        peer = _make_peer("robot-1")
        coord.add_peer(peer)
        coord.remove_peer("robot-1")
        assert coord.get_peers() == []

    def test_remove_nonexistent_peer_is_safe(self):
        coord = _make_coordinator()
        coord.remove_peer("ghost")  # should not raise

    def test_update_peer(self):
        coord = _make_coordinator()
        peer = _make_peer("robot-1", load_score=0.1)
        coord.add_peer(peer)
        updated = _make_peer("robot-1", load_score=0.9)
        coord.update_peer(updated)
        stored = [p for p in coord.get_peers() if p.robot_id == "robot-1"][0]
        assert stored.load_score == pytest.approx(0.9)

    def test_get_peers_returns_all(self):
        coord = _make_coordinator()
        for i in range(5):
            coord.add_peer(_make_peer(f"robot-{i}"))
        assert len(coord.get_peers()) == 5

    def test_available_peers_filters_unavailable(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("available", load_score=0.0))
        coord.add_peer(_make_peer("overloaded", load_score=0.9))
        coord.add_peer(_make_peer("stale", last_seen=time.time() - 100))
        avail = coord.available_peers()
        assert len(avail) == 1
        assert avail[0].robot_id == "available"


# ---------------------------------------------------------------------------
# submit_task
# ---------------------------------------------------------------------------


class TestSubmitTask:
    def test_submit_returns_task_id(self):
        coord = _make_coordinator()
        task = _make_task("t1")
        result = coord.submit_task(task)
        assert result == "t1"

    def test_submitted_task_appears_in_pending(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        task = _make_task("t1")
        coord.submit_task(task)
        status = coord.fleet_status()
        assert status["tasks_pending"] == 1


# ---------------------------------------------------------------------------
# assign_next
# ---------------------------------------------------------------------------


class TestAssignNext:
    def test_assign_to_capable_peer(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1", capabilities=["nav"]))
        task = _make_task(required_capability="nav")
        coord.submit_task(task)
        assignment = coord.assign_next()
        assert assignment is not None
        assert assignment.assigned_to.robot_id == "robot-1"
        assert assignment.status == "assigned"

    def test_no_assignment_when_no_capable_peer(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1", capabilities=["vision"]))
        task = _make_task(required_capability="nav")
        coord.submit_task(task)
        assignment = coord.assign_next()
        assert assignment is None

    def test_skip_unavailable_peers(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("stale", last_seen=time.time() - 100))
        task = _make_task()
        coord.submit_task(task)
        assert coord.assign_next() is None

    def test_no_assignment_when_no_tasks(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        assert coord.assign_next() is None

    def test_assign_task_without_required_capability(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        task = _make_task(required_capability=None)
        coord.submit_task(task)
        assignment = coord.assign_next()
        assert assignment is not None

    def test_highest_priority_assigned_first(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        low = _make_task("low-priority", priority=1)
        high = _make_task("high-priority", priority=10)
        coord.submit_task(low)
        coord.submit_task(high)
        assignment = coord.assign_next()
        assert assignment is not None
        assert assignment.task.task_id == "high-priority"

    def test_load_balancing_picks_least_loaded(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("heavy", capabilities=["nav"], load_score=0.7))
        coord.add_peer(_make_peer("light", capabilities=["nav"], load_score=0.1))
        task = _make_task(required_capability="nav")
        coord.submit_task(task)
        assignment = coord.assign_next()
        assert assignment is not None
        assert assignment.assigned_to.robot_id == "light"

    def test_assigned_task_not_reassigned(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        task = _make_task("t1")
        coord.submit_task(task)
        a1 = coord.assign_next()
        assert a1 is not None
        # No more tasks — should be None
        a2 = coord.assign_next()
        assert a2 is None


# ---------------------------------------------------------------------------
# complete_task
# ---------------------------------------------------------------------------


class TestCompleteTask:
    def test_complete_success_marks_completed(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        task = _make_task("t1")
        coord.submit_task(task)
        coord.assign_next()
        coord.complete_task("t1", success=True)
        assignment = coord._assignments["t1"]
        assert assignment.status == "completed"

    def test_complete_failure_marks_failed(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        task = _make_task("t1")
        coord.submit_task(task)
        coord.assign_next()
        coord.complete_task("t1", success=False)
        assignment = coord._assignments["t1"]
        assert assignment.status == "failed"

    def test_complete_nonexistent_task_is_safe(self):
        coord = _make_coordinator()
        coord.complete_task("ghost", success=True)  # should not raise


# ---------------------------------------------------------------------------
# fleet_status
# ---------------------------------------------------------------------------


class TestFleetStatus:
    def test_empty_fleet(self):
        coord = _make_coordinator()
        status = coord.fleet_status()
        assert status == {"peers": 0, "available": 0, "tasks_pending": 0, "tasks_assigned": 0}

    def test_status_reflects_peers(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("r1", load_score=0.0))
        coord.add_peer(_make_peer("r2", load_score=0.9))  # unavailable
        status = coord.fleet_status()
        assert status["peers"] == 2
        assert status["available"] == 1

    def test_status_reflects_tasks(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        t1 = _make_task("t1")
        t2 = _make_task("t2")
        coord.submit_task(t1)
        coord.submit_task(t2)
        status = coord.fleet_status()
        assert status["tasks_pending"] == 2
        assert status["tasks_assigned"] == 0

    def test_status_after_assignment(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        task = _make_task("t1")
        coord.submit_task(task)
        coord.assign_next()
        status = coord.fleet_status()
        assert status["tasks_pending"] == 0
        assert status["tasks_assigned"] == 1


# ---------------------------------------------------------------------------
# solo_mode
# ---------------------------------------------------------------------------


class TestSoloMode:
    def test_solo_mode_with_no_peers(self):
        coord = _make_coordinator()
        assert coord.is_solo_mode() is True

    def test_not_solo_mode_with_peers(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        assert coord.is_solo_mode() is False

    def test_solo_mode_after_all_peers_removed(self):
        coord = _make_coordinator()
        coord.add_peer(_make_peer("robot-1"))
        coord.remove_peer("robot-1")
        assert coord.is_solo_mode() is True
