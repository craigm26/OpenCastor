"""Tests for SwarmConsensus."""

from __future__ import annotations

import time

import pytest

from castor.swarm.consensus import SwarmConsensus, TaskClaim
from castor.swarm.peer import SwarmPeer
from castor.swarm.shared_memory import SharedMemory


def _mem(robot_id: str = "robot-A") -> SharedMemory:
    return SharedMemory(robot_id=robot_id, persist_path="/dev/null/unused")


def _consensus(robot_id: str = "robot-A", mem: SharedMemory | None = None) -> SwarmConsensus:
    if mem is None:
        mem = _mem(robot_id)
    return SwarmConsensus(robot_id=robot_id, shared_memory=mem)


def _peer(robot_id: str, load_score: float = 0.0) -> SwarmPeer:
    return SwarmPeer(
        robot_id=robot_id,
        robot_name=robot_id,
        host="10.0.0.1",
        port=8000,
        capabilities=[],
        last_seen=time.time(),
        load_score=load_score,
    )


# ---------------------------------------------------------------------------
# TaskClaim
# ---------------------------------------------------------------------------


class TestTaskClaim:
    def test_not_expired_when_fresh(self):
        claim = TaskClaim(task_id="t1", robot_id="r1", claimed_at=time.time(), ttl_s=30.0)
        assert claim.is_expired is False

    def test_expired_after_ttl(self):
        claim = TaskClaim(task_id="t1", robot_id="r1", claimed_at=time.time() - 31.0, ttl_s=30.0)
        assert claim.is_expired is True

    def test_default_ttl_is_30(self):
        claim = TaskClaim(task_id="t1", robot_id="r1", claimed_at=time.time())
        assert claim.ttl_s == 30.0


# ---------------------------------------------------------------------------
# claim_task
# ---------------------------------------------------------------------------


class TestClaimTask:
    def test_claim_succeeds_when_unclaimed(self):
        c = _consensus("robot-A")
        assert c.claim_task("task-1") is True

    def test_claim_blocked_by_other_robot(self):
        shared = _mem("robot-A")
        ca = SwarmConsensus("robot-A", shared)
        cb = SwarmConsensus("robot-B", shared)

        assert ca.claim_task("task-1") is True
        assert cb.claim_task("task-1") is False

    def test_reclaim_own_task_succeeds(self):
        c = _consensus("robot-A")
        c.claim_task("task-1")
        assert c.claim_task("task-1") is True  # idempotent

    def test_expired_claim_can_be_reclaimed_by_other(self):
        shared = _mem("robot-A")
        ca = SwarmConsensus("robot-A", shared)
        cb = SwarmConsensus("robot-B", shared)

        # robot-A claims with tiny TTL
        ca.claim_task("task-1", ttl_s=0.01)
        time.sleep(0.05)

        # After expiry, robot-B can claim
        assert cb.claim_task("task-1") is True


# ---------------------------------------------------------------------------
# release_task
# ---------------------------------------------------------------------------


class TestReleaseTask:
    def test_release_removes_claim(self):
        c = _consensus("robot-A")
        c.claim_task("task-1")
        c.release_task("task-1")
        assert c.get_claimant("task-1") is None

    def test_release_by_non_owner_does_nothing(self):
        shared = _mem("robot-A")
        ca = SwarmConsensus("robot-A", shared)
        cb = SwarmConsensus("robot-B", shared)

        ca.claim_task("task-1")
        cb.release_task("task-1")  # should not remove A's claim
        assert ca.is_claimed_by_me("task-1") is True

    def test_release_unclaimed_task_is_safe(self):
        c = _consensus("robot-A")
        c.release_task("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# is_claimed_by_me / is_claimed_by_other
# ---------------------------------------------------------------------------


class TestClaimStatus:
    def test_is_claimed_by_me_after_claim(self):
        c = _consensus("robot-A")
        c.claim_task("t1")
        assert c.is_claimed_by_me("t1") is True
        assert c.is_claimed_by_other("t1") is False

    def test_is_claimed_by_other(self):
        shared = _mem("robot-A")
        ca = SwarmConsensus("robot-A", shared)
        cb = SwarmConsensus("robot-B", shared)

        ca.claim_task("t1")
        assert cb.is_claimed_by_other("t1") is True
        assert cb.is_claimed_by_me("t1") is False

    def test_unclaimed_is_neither(self):
        c = _consensus("robot-A")
        assert c.is_claimed_by_me("t1") is False
        assert c.is_claimed_by_other("t1") is False


# ---------------------------------------------------------------------------
# renew_claim
# ---------------------------------------------------------------------------


class TestRenewClaim:
    def test_renew_succeeds_for_owner(self):
        c = _consensus("robot-A")
        c.claim_task("t1", ttl_s=5.0)
        assert c.renew_claim("t1") is True

    def test_renew_resets_expiry(self):
        shared = _mem("robot-A")
        ca = SwarmConsensus("robot-A", shared)
        cb = SwarmConsensus("robot-B", shared)

        ca.claim_task("t1", ttl_s=0.05)
        time.sleep(0.02)
        ca.renew_claim("t1")  # refresh before expiry
        time.sleep(0.04)
        # Should still be alive (renewed ~0.04s ago with ttl 0.05s)
        # Actually let's just verify robot-B still can't claim
        # The renewed claim should still block B
        assert cb.claim_task("t1") is False

    def test_renew_fails_for_non_owner(self):
        shared = _mem("robot-A")
        ca = SwarmConsensus("robot-A", shared)
        cb = SwarmConsensus("robot-B", shared)

        ca.claim_task("t1")
        assert cb.renew_claim("t1") is False

    def test_renew_fails_for_unclaimed(self):
        c = _consensus("robot-A")
        assert c.renew_claim("nope") is False


# ---------------------------------------------------------------------------
# get_claimant
# ---------------------------------------------------------------------------


class TestGetClaimant:
    def test_returns_owner_robot_id(self):
        shared = _mem("robot-A")
        c = SwarmConsensus("robot-A", shared)
        c.claim_task("t1")
        assert c.get_claimant("t1") == "robot-A"

    def test_returns_none_when_unclaimed(self):
        c = _consensus()
        assert c.get_claimant("nope") is None

    def test_returns_none_after_release(self):
        c = _consensus("robot-A")
        c.claim_task("t1")
        c.release_task("t1")
        assert c.get_claimant("t1") is None


# ---------------------------------------------------------------------------
# elect_leader
# ---------------------------------------------------------------------------


class TestElectLeader:
    def test_lex_smallest_wins(self):
        c = _consensus("robot-C")
        peers = [_peer("robot-A"), _peer("robot-B")]
        leader = c.elect_leader(peers)
        assert leader == "robot-A"

    def test_self_included_in_election(self):
        c = _consensus("robot-A")
        peers = [_peer("robot-B"), _peer("robot-C")]
        leader = c.elect_leader(peers)
        assert leader == "robot-A"

    def test_self_wins_when_only_candidate(self):
        c = _consensus("robot-A")
        assert c.elect_leader([]) == "robot-A"

    def test_election_is_deterministic(self):
        c = _consensus("robot-Z")
        peers = [_peer("robot-M"), _peer("robot-A"), _peer("robot-B")]
        results = {c.elect_leader(peers) for _ in range(5)}
        assert results == {"robot-A"}

    def test_election_uses_lexicographic_order(self):
        c = _consensus("z")
        peers = [_peer("b"), _peer("a"), _peer("c")]
        assert c.elect_leader(peers) == "a"
