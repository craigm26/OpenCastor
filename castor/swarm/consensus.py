"""SwarmConsensus — distributed task ownership via shared memory claims."""

from __future__ import annotations

import time
from dataclasses import dataclass

from castor.swarm.peer import SwarmPeer
from castor.swarm.shared_memory import SharedMemory

_CLAIM_PREFIX = "consensus:"


@dataclass
class TaskClaim:
    """Represents a robot's claim on a specific task."""

    task_id: str
    robot_id: str
    claimed_at: float
    ttl_s: float = 30.0

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.claimed_at) > self.ttl_s

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "robot_id": self.robot_id,
            "claimed_at": self.claimed_at,
            "ttl_s": self.ttl_s,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TaskClaim:
        return cls(
            task_id=d["task_id"],
            robot_id=d["robot_id"],
            claimed_at=float(d["claimed_at"]),
            ttl_s=float(d.get("ttl_s", 30.0)),
        )


class SwarmConsensus:
    """Simple distributed consensus for task ownership.

    Claims are stored in SharedMemory under ``consensus:<task_id>``.
    No Paxos — just optimistic locking with TTL-based expiry.
    """

    def __init__(self, robot_id: str, shared_memory: SharedMemory) -> None:
        self.robot_id = robot_id
        self._mem = shared_memory

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, task_id: str) -> str:
        return f"{_CLAIM_PREFIX}{task_id}"

    def _get_claim(self, task_id: str) -> TaskClaim | None:
        raw = self._mem.get(self._key(task_id))
        if raw is None:
            return None
        if isinstance(raw, dict):
            claim = TaskClaim.from_dict(raw)
        else:
            claim = raw  # already a TaskClaim (in-process usage)
        if claim.is_expired:
            self._mem.delete(self._key(task_id))
            return None
        return claim

    def _store_claim(self, claim: TaskClaim) -> None:
        self._mem.put(self._key(claim.task_id), claim.to_dict(), ttl_s=claim.ttl_s)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def claim_task(self, task_id: str, ttl_s: float = 30.0) -> bool:
        """Attempt to claim a task.

        Returns True if the claim succeeded (no existing live claim by
        another robot). Idempotent: re-claiming our own task refreshes TTL.
        """
        existing = self._get_claim(task_id)
        if existing is not None and existing.robot_id != self.robot_id:
            return False  # claimed by someone else
        claim = TaskClaim(
            task_id=task_id,
            robot_id=self.robot_id,
            claimed_at=time.time(),
            ttl_s=ttl_s,
        )
        self._store_claim(claim)
        return True

    def release_task(self, task_id: str) -> None:
        """Release our claim on a task (if we hold it)."""
        existing = self._get_claim(task_id)
        if existing is not None and existing.robot_id == self.robot_id:
            self._mem.delete(self._key(task_id))

    def is_claimed_by_me(self, task_id: str) -> bool:
        """True if we currently hold the claim for task_id."""
        claim = self._get_claim(task_id)
        return claim is not None and claim.robot_id == self.robot_id

    def is_claimed_by_other(self, task_id: str) -> bool:
        """True if another robot holds a live claim for task_id."""
        claim = self._get_claim(task_id)
        return claim is not None and claim.robot_id != self.robot_id

    def renew_claim(self, task_id: str) -> bool:
        """Extend TTL on our claim. Returns False if we don't own it."""
        existing = self._get_claim(task_id)
        if existing is None or existing.robot_id != self.robot_id:
            return False
        renewed = TaskClaim(
            task_id=task_id,
            robot_id=self.robot_id,
            claimed_at=time.time(),
            ttl_s=existing.ttl_s,
        )
        self._store_claim(renewed)
        return True

    def get_claimant(self, task_id: str) -> str | None:
        """Return the robot_id of the current owner, or None."""
        claim = self._get_claim(task_id)
        return claim.robot_id if claim else None

    def elect_leader(self, peers: list[SwarmPeer]) -> str:
        """Deterministic leader election.

        The robot with the lexicographically smallest robot_id wins.
        Includes self in the candidate pool.
        """
        candidates = [p.robot_id for p in peers] + [self.robot_id]
        return min(candidates)
