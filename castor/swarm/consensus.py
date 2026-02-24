"""SwarmConsensus — distributed task ownership via shared memory claims."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

from castor.swarm.peer import SwarmPeer
from castor.swarm.shared_memory import SharedMemory

_CLAIM_PREFIX = "consensus:"
_INTENT_PREFIX = "intent:"
_HANDOFF_PREFIX = "handoff:"


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


@dataclass
class DelegatedIntent:
    """Intent delegated from one peer to another with policy constraints."""

    intent_id: str
    task_id: str
    origin_robot_id: str
    assigned_robot_id: str
    action: str
    params: dict
    policy_constraints: dict
    issued_at: float
    ttl_s: float = 120.0
    parent_intent_id: str | None = None
    provenance: list[str] = field(default_factory=list)
    signature: str = ""

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.issued_at) > self.ttl_s

    def canonical_payload(self) -> dict:
        return {
            "intent_id": self.intent_id,
            "task_id": self.task_id,
            "origin_robot_id": self.origin_robot_id,
            "assigned_robot_id": self.assigned_robot_id,
            "action": self.action,
            "params": self.params,
            "policy_constraints": self.policy_constraints,
            "issued_at": self.issued_at,
            "ttl_s": self.ttl_s,
            "parent_intent_id": self.parent_intent_id,
            "provenance": list(self.provenance),
        }

    def to_dict(self) -> dict:
        payload = self.canonical_payload()
        payload["signature"] = self.signature
        return payload

    @classmethod
    def from_dict(cls, d: dict) -> DelegatedIntent:
        return cls(
            intent_id=d["intent_id"],
            task_id=d["task_id"],
            origin_robot_id=d["origin_robot_id"],
            assigned_robot_id=d["assigned_robot_id"],
            action=d["action"],
            params=dict(d.get("params", {})),
            policy_constraints=dict(d.get("policy_constraints", {})),
            issued_at=float(d["issued_at"]),
            ttl_s=float(d.get("ttl_s", 120.0)),
            parent_intent_id=d.get("parent_intent_id"),
            provenance=list(d.get("provenance", [])),
            signature=d.get("signature", ""),
        )


@dataclass
class HandoffRecord:
    """Intent-aware task handoff payload with a world-model snapshot."""

    task_id: str
    from_robot_id: str
    to_robot_id: str
    intent_id: str
    world_snapshot: dict
    context: dict
    handed_off_at: float
    signature: str

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "from_robot_id": self.from_robot_id,
            "to_robot_id": self.to_robot_id,
            "intent_id": self.intent_id,
            "world_snapshot": self.world_snapshot,
            "context": self.context,
            "handed_off_at": self.handed_off_at,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> HandoffRecord:
        return cls(
            task_id=d["task_id"],
            from_robot_id=d["from_robot_id"],
            to_robot_id=d["to_robot_id"],
            intent_id=d["intent_id"],
            world_snapshot=dict(d.get("world_snapshot", {})),
            context=dict(d.get("context", {})),
            handed_off_at=float(d["handed_off_at"]),
            signature=d["signature"],
        )


class SwarmConsensus:
    """Simple distributed consensus for task ownership and intent provenance.

    Claims are stored in SharedMemory under ``consensus:<task_id>``.
    No Paxos — just optimistic locking with TTL-based expiry.
    """

    def __init__(self, robot_id: str, shared_memory: SharedMemory, signing_secret: str | None = None) -> None:
        self.robot_id = robot_id
        self._mem = shared_memory
        self._signing_secret = signing_secret or f"swarm-secret:{robot_id}"

    def _sign(self, payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._signing_secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, task_id: str) -> str:
        return f"{_CLAIM_PREFIX}{task_id}"

    def _intent_key(self, intent_id: str) -> str:
        return f"{_INTENT_PREFIX}{intent_id}"

    def _handoff_key(self, task_id: str) -> str:
        return f"{_HANDOFF_PREFIX}{task_id}"

    def _get_claim(self, task_id: str) -> TaskClaim | None:
        raw = self._mem.get(self._key(task_id))
        if raw is None:
            return None
        if isinstance(raw, dict):
            claim = TaskClaim.from_dict(raw)
        else:
            claim = raw
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
        existing = self._get_claim(task_id)
        if existing is not None and existing.robot_id != self.robot_id:
            return False
        claim = TaskClaim(task_id=task_id, robot_id=self.robot_id, claimed_at=time.time(), ttl_s=ttl_s)
        self._store_claim(claim)
        return True

    def release_task(self, task_id: str) -> None:
        existing = self._get_claim(task_id)
        if existing is not None and existing.robot_id == self.robot_id:
            self._mem.delete(self._key(task_id))

    def is_claimed_by_me(self, task_id: str) -> bool:
        claim = self._get_claim(task_id)
        return claim is not None and claim.robot_id == self.robot_id

    def is_claimed_by_other(self, task_id: str) -> bool:
        claim = self._get_claim(task_id)
        return claim is not None and claim.robot_id != self.robot_id

    def renew_claim(self, task_id: str) -> bool:
        existing = self._get_claim(task_id)
        if existing is None or existing.robot_id != self.robot_id:
            return False
        renewed = TaskClaim(task_id=task_id, robot_id=self.robot_id, claimed_at=time.time(), ttl_s=existing.ttl_s)
        self._store_claim(renewed)
        return True

    def get_claimant(self, task_id: str) -> str | None:
        claim = self._get_claim(task_id)
        return claim.robot_id if claim else None

    def record_delegated_intent(self, intent: DelegatedIntent) -> DelegatedIntent:
        if not intent.provenance:
            intent.provenance = [intent.origin_robot_id]
        payload = intent.canonical_payload()
        intent.signature = self._sign(payload)
        self._mem.put(self._intent_key(intent.intent_id), intent.to_dict(), ttl_s=intent.ttl_s)
        return intent

    def get_intent(self, intent_id: str) -> DelegatedIntent | None:
        raw = self._mem.get(self._intent_key(intent_id))
        if raw is None:
            return None
        intent = DelegatedIntent.from_dict(raw if isinstance(raw, dict) else raw.to_dict())
        if intent.is_expired:
            self._mem.delete(self._intent_key(intent_id))
            return None
        return intent

    def verify_intent(self, intent: DelegatedIntent) -> bool:
        expected = self._sign(intent.canonical_payload())
        return hmac.compare_digest(expected, intent.signature)

    def record_handoff(
        self,
        task_id: str,
        to_robot_id: str,
        intent_id: str,
        world_snapshot: dict,
        context: dict,
    ) -> HandoffRecord:
        payload = {
            "task_id": task_id,
            "from_robot_id": self.robot_id,
            "to_robot_id": to_robot_id,
            "intent_id": intent_id,
            "world_snapshot": world_snapshot,
            "context": context,
            "handed_off_at": time.time(),
        }
        signature = self._sign(payload)
        record = HandoffRecord(signature=signature, **payload)
        self._mem.put(self._handoff_key(task_id), record.to_dict(), ttl_s=300.0)
        return record

    def consume_handoff(self, task_id: str) -> HandoffRecord | None:
        raw = self._mem.get(self._handoff_key(task_id))
        if raw is None:
            return None
        record = HandoffRecord.from_dict(raw if isinstance(raw, dict) else raw.to_dict())
        payload = record.to_dict()
        signature = payload.pop("signature")
        if not hmac.compare_digest(signature, self._sign(payload)):
            return None
        return record

    def elect_leader(self, peers: list[SwarmPeer]) -> str:
        candidates = [p.robot_id for p in peers] + [self.robot_id]
        return min(candidates)
