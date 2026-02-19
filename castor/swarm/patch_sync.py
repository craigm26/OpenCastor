"""PatchSync — broadcast Sisyphus-generated patches across the swarm."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from castor.swarm.shared_memory import SharedMemory

_PATCH_PREFIX = "swarm_patch:"


@dataclass
class SyncedPatch:
    """A patch that has been published to the swarm."""

    patch_id: str
    source_robot_id: str
    patch_type: str  # "config", "behavior", "prompt"
    patch_data: dict  # serialized patch content
    rationale: str
    created_at: float
    qa_passed: bool
    applied_by: list[str]  # robot_ids that applied it

    def to_dict(self) -> dict:
        return {
            "patch_id": self.patch_id,
            "source_robot_id": self.source_robot_id,
            "patch_type": self.patch_type,
            "patch_data": self.patch_data,
            "rationale": self.rationale,
            "created_at": self.created_at,
            "qa_passed": self.qa_passed,
            "applied_by": list(self.applied_by),
        }

    @classmethod
    def from_dict(cls, d: dict) -> SyncedPatch:
        return cls(
            patch_id=d["patch_id"],
            source_robot_id=d["source_robot_id"],
            patch_type=d["patch_type"],
            patch_data=dict(d.get("patch_data", {})),
            rationale=d.get("rationale", ""),
            created_at=float(d["created_at"]),
            qa_passed=bool(d.get("qa_passed", False)),
            applied_by=list(d.get("applied_by", [])),
        )


class PatchSync:
    """Synchronise improvement patches across the robot fleet.

    Patches are stored in SharedMemory under ``swarm_patch:<patch_id>``.
    """

    def __init__(self, robot_id: str, shared_memory: SharedMemory) -> None:
        self.robot_id = robot_id
        self._mem = shared_memory

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, patch_id: str) -> str:
        return f"{_PATCH_PREFIX}{patch_id}"

    def _all_patches(self) -> list[SyncedPatch]:
        """Return all patches currently in shared memory."""
        patches = []
        for key in list(self._mem._store.keys()):
            if not key.startswith(_PATCH_PREFIX):
                continue
            raw = self._mem.get(key)
            if raw is None:
                continue
            if isinstance(raw, dict):
                try:
                    patches.append(SyncedPatch.from_dict(raw))
                except (KeyError, TypeError):
                    continue
            elif isinstance(raw, SyncedPatch):
                patches.append(raw)
        return patches

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish_patch(
        self,
        patch_type: str,
        patch_data: dict,
        rationale: str,
        qa_passed: bool,
    ) -> str:
        """Publish a patch from this robot to the swarm.

        Stores in shared_memory under ``swarm_patch:<patch_id>``.
        Returns patch_id.
        """
        patch_id = str(uuid.uuid4())
        patch = SyncedPatch(
            patch_id=patch_id,
            source_robot_id=self.robot_id,
            patch_type=patch_type,
            patch_data=patch_data,
            rationale=rationale,
            created_at=time.time(),
            qa_passed=qa_passed,
            applied_by=[],
        )
        self._mem.put(self._key(patch_id), patch.to_dict())
        return patch_id

    def get_available_patches(self) -> list[SyncedPatch]:
        """Return patches from OTHER robots that we haven't applied yet."""
        result = []
        for patch in self._all_patches():
            if patch.source_robot_id == self.robot_id:
                continue  # skip our own
            if self.robot_id in patch.applied_by:
                continue  # already applied
            result.append(patch)
        return result

    def mark_applied(self, patch_id: str) -> None:
        """Record that this robot has applied the given patch."""
        patch = self.get_patch(patch_id)
        if patch is None:
            return
        if self.robot_id not in patch.applied_by:
            patch.applied_by.append(self.robot_id)
        self._mem.put(self._key(patch_id), patch.to_dict())

    def get_patch(self, patch_id: str) -> SyncedPatch | None:
        """Retrieve a single patch by ID, or None if not found."""
        raw = self._mem.get(self._key(patch_id))
        if raw is None:
            return None
        if isinstance(raw, dict):
            try:
                return SyncedPatch.from_dict(raw)
            except (KeyError, TypeError):
                return None
        if isinstance(raw, SyncedPatch):
            return raw
        return None

    def prune_old_patches(self, max_age_s: float = 86400) -> int:
        """Remove patches older than max_age_s. Returns count pruned."""
        now = time.time()
        pruned = 0
        for patch in self._all_patches():
            if (now - patch.created_at) > max_age_s:
                self._mem.delete(self._key(patch.patch_id))
                pruned += 1
        return pruned
