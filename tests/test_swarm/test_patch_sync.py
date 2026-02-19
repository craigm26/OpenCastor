"""Tests for PatchSync."""

from __future__ import annotations

import time

import pytest

from castor.swarm.patch_sync import PatchSync, SyncedPatch
from castor.swarm.shared_memory import SharedMemory


def _mem(robot_id: str = "robot-A") -> SharedMemory:
    return SharedMemory(robot_id=robot_id, persist_path="/dev/null/unused")


def _sync(robot_id: str = "robot-A", mem: SharedMemory | None = None) -> PatchSync:
    if mem is None:
        mem = _mem(robot_id)
    return PatchSync(robot_id=robot_id, shared_memory=mem)


# ---------------------------------------------------------------------------
# SyncedPatch serialization
# ---------------------------------------------------------------------------


class TestSyncedPatch:
    def test_roundtrip(self):
        patch = SyncedPatch(
            patch_id="p1",
            source_robot_id="robot-A",
            patch_type="config",
            patch_data={"key": "max_speed", "value": 1.5},
            rationale="Speed limit increased",
            created_at=time.time(),
            qa_passed=True,
            applied_by=["robot-B"],
        )
        d = patch.to_dict()
        restored = SyncedPatch.from_dict(d)
        assert restored.patch_id == patch.patch_id
        assert restored.source_robot_id == patch.source_robot_id
        assert restored.patch_type == patch.patch_type
        assert restored.patch_data == patch.patch_data
        assert restored.rationale == patch.rationale
        assert restored.qa_passed == patch.qa_passed
        assert restored.applied_by == patch.applied_by


# ---------------------------------------------------------------------------
# publish_patch
# ---------------------------------------------------------------------------


class TestPublishPatch:
    def test_publish_returns_patch_id(self):
        ps = _sync("robot-A")
        pid = ps.publish_patch("config", {"k": "v"}, "reason", True)
        assert isinstance(pid, str)
        assert len(pid) > 0

    def test_published_patch_is_stored(self):
        ps = _sync("robot-A")
        pid = ps.publish_patch("behavior", {"rule": "x"}, "test", False)
        patch = ps.get_patch(pid)
        assert patch is not None
        assert patch.patch_id == pid
        assert patch.source_robot_id == "robot-A"
        assert patch.patch_type == "behavior"
        assert patch.qa_passed is False

    def test_each_publish_gets_unique_id(self):
        ps = _sync("robot-A")
        ids = {ps.publish_patch("config", {}, "r", True) for _ in range(10)}
        assert len(ids) == 10

    def test_stored_under_correct_prefix(self):
        ps = _sync("robot-A")
        pid = ps.publish_patch("prompt", {}, "r", True)
        assert ps._mem.get(f"swarm_patch:{pid}") is not None


# ---------------------------------------------------------------------------
# get_available_patches
# ---------------------------------------------------------------------------


class TestGetAvailablePatches:
    def test_excludes_own_patches(self):
        shared = _mem("robot-A")
        ps_a = PatchSync("robot-A", shared)
        ps_a.publish_patch("config", {}, "r", True)
        assert ps_a.get_available_patches() == []

    def test_includes_other_robots_patches(self):
        shared = _mem("robot-A")
        ps_a = PatchSync("robot-A", shared)
        ps_b = PatchSync("robot-B", shared)

        pid = ps_b.publish_patch("config", {"k": "v"}, "reason", True)
        available = ps_a.get_available_patches()
        assert len(available) == 1
        assert available[0].patch_id == pid

    def test_excludes_already_applied(self):
        shared = _mem("robot-A")
        ps_a = PatchSync("robot-A", shared)
        ps_b = PatchSync("robot-B", shared)

        pid = ps_b.publish_patch("config", {}, "r", True)
        ps_a.mark_applied(pid)
        assert ps_a.get_available_patches() == []

    def test_multiple_sources(self):
        shared = _mem("robot-A")
        ps_a = PatchSync("robot-A", shared)
        ps_b = PatchSync("robot-B", shared)
        ps_c = PatchSync("robot-C", shared)

        ps_b.publish_patch("config", {}, "r", True)
        ps_c.publish_patch("behavior", {}, "r", True)
        ps_a.publish_patch("prompt", {}, "r", True)  # own — excluded

        available = ps_a.get_available_patches()
        assert len(available) == 2
        sources = {p.source_robot_id for p in available}
        assert sources == {"robot-B", "robot-C"}


# ---------------------------------------------------------------------------
# mark_applied
# ---------------------------------------------------------------------------


class TestMarkApplied:
    def test_mark_applied_adds_to_applied_by(self):
        shared = _mem("robot-A")
        ps_a = PatchSync("robot-A", shared)
        ps_b = PatchSync("robot-B", shared)

        pid = ps_b.publish_patch("config", {}, "r", True)
        ps_a.mark_applied(pid)

        patch = ps_a.get_patch(pid)
        assert "robot-A" in patch.applied_by

    def test_mark_applied_idempotent(self):
        shared = _mem("robot-A")
        ps_a = PatchSync("robot-A", shared)
        ps_b = PatchSync("robot-B", shared)

        pid = ps_b.publish_patch("config", {}, "r", True)
        ps_a.mark_applied(pid)
        ps_a.mark_applied(pid)

        patch = ps_a.get_patch(pid)
        assert patch.applied_by.count("robot-A") == 1

    def test_mark_applied_nonexistent_is_safe(self):
        ps = _sync("robot-A")
        ps.mark_applied("does-not-exist")  # should not raise


# ---------------------------------------------------------------------------
# get_patch
# ---------------------------------------------------------------------------


class TestGetPatch:
    def test_get_existing_patch(self):
        ps = _sync("robot-A")
        pid = ps.publish_patch("config", {"k": "v"}, "reason", True)
        patch = ps.get_patch(pid)
        assert patch is not None
        assert patch.patch_id == pid

    def test_get_nonexistent_returns_none(self):
        ps = _sync("robot-A")
        assert ps.get_patch("nope") is None


# ---------------------------------------------------------------------------
# prune_old_patches
# ---------------------------------------------------------------------------


class TestPruneOldPatches:
    def test_prune_removes_old_patches(self):
        shared = _mem("robot-A")
        ps = PatchSync("robot-A", shared)
        pid = ps.publish_patch("config", {}, "r", True)
        # Manually backdate the patch
        patch = ps.get_patch(pid)
        patch.created_at = time.time() - 90000
        shared.put(f"swarm_patch:{pid}", patch.to_dict())

        count = ps.prune_old_patches(max_age_s=86400)
        assert count == 1
        assert ps.get_patch(pid) is None

    def test_prune_keeps_recent_patches(self):
        ps = _sync("robot-A")
        pid = ps.publish_patch("config", {}, "r", True)
        count = ps.prune_old_patches(max_age_s=86400)
        assert count == 0
        assert ps.get_patch(pid) is not None

    def test_prune_returns_count(self):
        shared = _mem("robot-A")
        ps = PatchSync("robot-A", shared)

        for _ in range(3):
            pid = ps.publish_patch("config", {}, "r", True)
            patch = ps.get_patch(pid)
            patch.created_at = time.time() - 90000
            shared.put(f"swarm_patch:{pid}", patch.to_dict())

        count = ps.prune_old_patches(max_age_s=86400)
        assert count == 3
