"""SwarmCoordinator — assigns tasks across the robot fleet."""

from __future__ import annotations

import time
from dataclasses import dataclass

from castor.swarm.consensus import SwarmConsensus
from castor.swarm.peer import SwarmPeer
from castor.swarm.shared_memory import SharedMemory


@dataclass
class SwarmTask:
    """A task to be assigned to a robot in the swarm."""

    task_id: str
    task_type: str
    goal: str
    required_capability: str | None
    priority: int
    created_at: float


@dataclass
class Assignment:
    """A task-to-peer assignment record."""

    task: SwarmTask
    assigned_to: SwarmPeer
    assigned_at: float
    status: str  # "assigned", "completed", "failed"


class SwarmCoordinator:
    """Coordinates task assignment across the robot fleet.

    Uses capability matching and load balancing to pick the best peer.
    Relies on SwarmConsensus to prevent double-assignment.
    """

    def __init__(
        self,
        my_robot_id: str,
        shared_memory: SharedMemory,
        consensus: SwarmConsensus,
    ) -> None:
        self.my_robot_id = my_robot_id
        self._mem = shared_memory
        self._consensus = consensus

        self._peers: dict[str, SwarmPeer] = {}
        self._tasks: dict[str, SwarmTask] = {}
        self._assignments: dict[str, Assignment] = {}  # task_id -> Assignment

    # ------------------------------------------------------------------
    # Peer management
    # ------------------------------------------------------------------

    def add_peer(self, peer: SwarmPeer) -> None:
        self._peers[peer.robot_id] = peer

    def remove_peer(self, robot_id: str) -> None:
        self._peers.pop(robot_id, None)

    def update_peer(self, peer: SwarmPeer) -> None:
        self._peers[peer.robot_id] = peer

    def get_peers(self) -> list[SwarmPeer]:
        return list(self._peers.values())

    def available_peers(self) -> list[SwarmPeer]:
        """Return peers that are currently available (not stale, not overloaded)."""
        return [p for p in self._peers.values() if p.is_available]

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    def submit_task(self, task: SwarmTask) -> str:
        """Register a task for assignment. Returns task_id."""
        self._tasks[task.task_id] = task
        return task.task_id

    def assign_next(self) -> Assignment | None:
        """Assign the highest-priority pending task to the best available peer.

        Selection algorithm:
        1. Sort pending tasks by priority descending (higher = more urgent).
        2. For each task, filter peers by required_capability.
        3. Among capable, available peers pick the one with the lowest load_score.
        4. Claim the task in consensus (skip if another robot beat us).
        5. Return the Assignment.
        """
        assigned_ids = {
            a.task.task_id for a in self._assignments.values() if a.status == "assigned"
        }
        pending = [t for t in self._tasks.values() if t.task_id not in assigned_ids]
        if not pending:
            return None

        # Sort by priority descending, then by created_at ascending (FIFO tie-break)
        pending.sort(key=lambda t: (-t.priority, t.created_at))

        available = self.available_peers()
        if not available:
            return None

        for task in pending:
            candidates = available
            if task.required_capability:
                candidates = [p for p in available if p.can_do(task.required_capability)]
            if not candidates:
                continue

            # Pick the least-loaded peer
            best = min(candidates, key=lambda p: p.load_score)

            # Try to claim the task
            if not self._consensus.claim_task(task.task_id):
                continue  # another robot claimed it

            assignment = Assignment(
                task=task,
                assigned_to=best,
                assigned_at=time.time(),
                status="assigned",
            )
            self._assignments[task.task_id] = assignment
            return assignment

        return None

    def complete_task(self, task_id: str, success: bool) -> None:
        """Mark a task as completed or failed, and release the consensus claim."""
        assignment = self._assignments.get(task_id)
        if assignment is not None:
            assignment.status = "completed" if success else "failed"
        self._consensus.release_task(task_id)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def fleet_status(self) -> dict:
        """Return a summary of fleet and task state."""
        assigned_count = sum(1 for a in self._assignments.values() if a.status == "assigned")
        assigned_ids = {
            a.task.task_id for a in self._assignments.values() if a.status == "assigned"
        }
        pending_count = sum(1 for t in self._tasks.values() if t.task_id not in assigned_ids)
        return {
            "peers": len(self._peers),
            "available": len(self.available_peers()),
            "tasks_pending": pending_count,
            "tasks_assigned": assigned_count,
        }

    def is_solo_mode(self) -> bool:
        """True if no peers are known (single-robot operation)."""
        return len(self._peers) == 0
