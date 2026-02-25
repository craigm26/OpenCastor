"""SwarmCoordinator — assigns tasks across the robot fleet."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import uuid4

from castor.swarm.consensus import DelegatedIntent, HandoffRecord, SwarmConsensus
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
    status: str  # assigned, completed, failed, handed_off
    intent_id: str | None = None
    intent_context: dict = field(default_factory=dict)


class SwarmCoordinator:
    """Coordinates task assignment across the robot fleet."""

    def __init__(
        self, my_robot_id: str, shared_memory: SharedMemory, consensus: SwarmConsensus
    ) -> None:
        self.my_robot_id = my_robot_id
        self._mem = shared_memory
        self._consensus = consensus

        self._peers: dict[str, SwarmPeer] = {}
        self._tasks: dict[str, SwarmTask] = {}
        self._assignments: dict[str, Assignment] = {}

    def add_peer(self, peer: SwarmPeer) -> None:
        self._peers[peer.robot_id] = peer

    def remove_peer(self, robot_id: str) -> None:
        self._peers.pop(robot_id, None)

    def update_peer(self, peer: SwarmPeer) -> None:
        self._peers[peer.robot_id] = peer

    def get_peers(self) -> list[SwarmPeer]:
        return list(self._peers.values())

    def available_peers(self) -> list[SwarmPeer]:
        return [p for p in self._peers.values() if p.is_available]

    def discover_capability(self, capability: str) -> list[SwarmPeer]:
        """Return all peers advertising a single capability."""
        return [p for p in self.available_peers() if p.can_do(capability)]

    def discover_peers(
        self,
        *,
        capabilities: list[str] | None = None,
        constraints: dict[str, float | str | bool | tuple] | None = None,
    ) -> list[SwarmPeer]:
        """Query peers by capability set and metric constraints.

        Example constraints: ``{"battery": (">", 40), "mode": "indoor"}``.
        """
        peers = self.available_peers()
        if capabilities:
            peers = [p for p in peers if p.supports_all(capabilities)]
        if constraints:
            peers = [p for p in peers if p.matches_constraints(constraints)]
        return peers

    def submit_task(self, task: SwarmTask) -> str:
        self._tasks[task.task_id] = task
        return task.task_id

    def _pending_tasks(self) -> list[SwarmTask]:
        assigned_ids = {
            a.task.task_id
            for a in self._assignments.values()
            if a.status in {"assigned", "handed_off"}
        }
        pending = [t for t in self._tasks.values() if t.task_id not in assigned_ids]
        pending.sort(key=lambda t: (-t.priority, t.created_at))
        return pending

    def assign_next(self) -> Assignment | None:
        pending = self._pending_tasks()
        if not pending:
            return None

        available = self.available_peers()
        if not available:
            return None

        for task in pending:
            candidates = available
            if task.required_capability:
                candidates = [p for p in available if p.can_do(task.required_capability)]
            if not candidates:
                continue

            best = min(candidates, key=lambda p: p.load_score)
            if not self._consensus.claim_task(task.task_id):
                continue

            assignment = Assignment(
                task=task, assigned_to=best, assigned_at=time.time(), status="assigned"
            )
            self._assignments[task.task_id] = assignment
            return assignment
        return None

    def delegate_intent(
        self,
        task_id: str,
        action: str,
        params: dict,
        *,
        required_capabilities: list[str] | None = None,
        constraints: dict | None = None,
        ttl_s: float = 120.0,
    ) -> Assignment | None:
        """Assign by issuing a signed delegated intent with constraints."""
        task = self._tasks.get(task_id)
        if task is None:
            return None

        candidates = self.discover_peers(
            capabilities=required_capabilities, constraints=constraints
        )
        if task.required_capability:
            candidates = [p for p in candidates if p.can_do(task.required_capability)]
        if not candidates:
            return None

        target = min(candidates, key=lambda p: p.load_score)
        if not self._consensus.claim_task(task.task_id):
            return None

        intent = DelegatedIntent(
            intent_id=f"intent-{uuid4().hex}",
            task_id=task.task_id,
            origin_robot_id=self.my_robot_id,
            assigned_robot_id=target.robot_id,
            action=action,
            params=params,
            policy_constraints={
                "required_capabilities": required_capabilities or [],
                **(constraints or {}),
            },
            issued_at=time.time(),
            ttl_s=ttl_s,
        )
        intent = self._consensus.record_delegated_intent(intent)

        assignment = Assignment(
            task=task,
            assigned_to=target,
            assigned_at=time.time(),
            status="assigned",
            intent_id=intent.intent_id,
            intent_context={
                "action": action,
                "params": params,
                "policy_constraints": intent.policy_constraints,
            },
        )
        self._assignments[task.task_id] = assignment
        return assignment

    def handoff_task(
        self, task_id: str, to_robot_id: str, world_snapshot: dict, context: dict
    ) -> HandoffRecord | None:
        """Handoff an in-progress task while preserving intent + world state."""
        assignment = self._assignments.get(task_id)
        if assignment is None or assignment.intent_id is None:
            return None
        recipient = self._peers.get(to_robot_id)
        if recipient is None or not recipient.is_available:
            return None

        handoff = self._consensus.record_handoff(
            task_id=task_id,
            to_robot_id=to_robot_id,
            intent_id=assignment.intent_id,
            world_snapshot=world_snapshot,
            context={**assignment.intent_context, **context},
        )
        assignment.assigned_to = recipient
        assignment.assigned_at = time.time()
        assignment.status = "handed_off"
        return handoff

    def reassign_unhealthy(self) -> list[Assignment]:
        """Auto-reassign tasks from degraded/disconnected assignees."""
        reassigned: list[Assignment] = []
        for task_id, assignment in list(self._assignments.items()):
            if assignment.status not in {"assigned", "handed_off"}:
                continue
            peer = assignment.assigned_to
            if not (peer.is_degraded or peer.is_disconnected):
                continue

            candidates = [
                p
                for p in self.available_peers()
                if p.robot_id != peer.robot_id
                and (
                    assignment.task.required_capability is None
                    or p.can_do(assignment.task.required_capability)
                )
            ]
            if not candidates:
                continue
            replacement = min(candidates, key=lambda p: p.load_score)

            if assignment.intent_id:
                handoff = self._consensus.record_handoff(
                    task_id=task_id,
                    to_robot_id=replacement.robot_id,
                    intent_id=assignment.intent_id,
                    world_snapshot=self._world_model_snapshot(),
                    context={"reason": "auto_reassign", **assignment.intent_context},
                )
                _ = handoff

            assignment.assigned_to = replacement
            assignment.assigned_at = time.time()
            assignment.status = "assigned"
            reassigned.append(assignment)
        return reassigned

    def complete_task(self, task_id: str, success: bool) -> None:
        assignment = self._assignments.get(task_id)
        if assignment is not None:
            assignment.status = "completed" if success else "failed"
        self._consensus.release_task(task_id)

    def _world_model_snapshot(self) -> dict:
        return {k: v.to_dict() for k, v in self._mem.snapshot().items()}

    def fleet_status(self) -> dict:
        assigned_count = sum(
            1 for a in self._assignments.values() if a.status in {"assigned", "handed_off"}
        )
        pending_count = len(self._pending_tasks())
        unhealthy = sum(1 for p in self._peers.values() if p.is_degraded or p.is_disconnected)
        return {
            "peers": len(self._peers),
            "available": len(self.available_peers()),
            "unhealthy": unhealthy,
            "tasks_pending": pending_count,
            "tasks_assigned": assigned_count,
        }

    def is_solo_mode(self) -> bool:
        return len(self._peers) == 0
