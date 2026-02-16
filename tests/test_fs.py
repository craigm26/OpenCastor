"""Tests for castor.fs -- the Unix-style virtual filesystem."""

import time
import json
import tempfile
import threading
from pathlib import Path

import pytest

from castor.fs import CastorFS, Cap
from castor.fs.namespace import Namespace
from castor.fs.permissions import ACL, PermissionTable
from castor.fs.safety import SafetyLayer
from castor.fs.memory import MemoryStore
from castor.fs.context import ContextWindow, Pipeline
from castor.fs.proc import ProcFS


# =====================================================================
# Namespace tests
# =====================================================================
class TestNamespace:
    def test_mkdir_and_ls(self):
        ns = Namespace()
        assert ns.mkdir("/a/b/c")
        assert ns.ls("/a") == ["b"]
        assert ns.ls("/a/b") == ["c"]

    def test_write_and_read(self):
        ns = Namespace()
        ns.write("/proc/uptime", 42.5)
        assert ns.read("/proc/uptime") == 42.5

    def test_write_creates_parents(self):
        ns = Namespace()
        ns.write("/a/b/c/file", "hello")
        assert ns.read("/a/b/c/file") == "hello"

    def test_read_nonexistent(self):
        ns = Namespace()
        assert ns.read("/does/not/exist") is None

    def test_read_dir_returns_children_stat(self):
        ns = Namespace()
        ns.write("/d/f1", 1)
        ns.write("/d/f2", 2)
        result = ns.read("/d")
        assert isinstance(result, dict)
        assert "f1" in result and "f2" in result

    def test_append(self):
        ns = Namespace()
        ns.append("/log", "entry1")
        ns.append("/log", "entry2")
        assert ns.read("/log") == ["entry1", "entry2"]

    def test_append_converts_non_list(self):
        ns = Namespace()
        ns.write("/val", "single")
        ns.append("/val", "extra")
        assert ns.read("/val") == ["single", "extra"]

    def test_stat(self):
        ns = Namespace()
        ns.write("/f", "data")
        s = ns.stat("/f")
        assert s["name"] == "f"
        assert s["type"] == "file"
        assert s["size"] > 0

    def test_exists(self):
        ns = Namespace()
        ns.write("/x", 1)
        assert ns.exists("/x")
        assert not ns.exists("/y")
        assert ns.exists("/")

    def test_unlink_file(self):
        ns = Namespace()
        ns.write("/tmp/f", "data")
        assert ns.unlink("/tmp/f")
        assert not ns.exists("/tmp/f")

    def test_unlink_nonempty_dir_fails(self):
        ns = Namespace()
        ns.write("/d/f", "data")
        assert not ns.unlink("/d")

    def test_walk(self):
        ns = Namespace()
        ns.write("/a/b", 1)
        ns.write("/a/c", 2)
        paths = ns.walk("/a")
        assert "/a/b" in paths
        assert "/a/c" in paths

    def test_thread_safety(self):
        ns = Namespace()
        errors = []

        def writer(n):
            try:
                for i in range(50):
                    ns.write(f"/t/{n}/{i}", i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# =====================================================================
# Permission tests
# =====================================================================
class TestPermissions:
    def test_acl_check(self):
        acl = ACL({"brain": "rw-", "api": "r--"})
        assert acl.check("brain", "r")
        assert acl.check("brain", "w")
        assert not acl.check("brain", "x")
        assert acl.check("api", "r")
        assert not acl.check("api", "w")

    def test_root_always_passes(self):
        acl = ACL({"brain": "---"})
        assert acl.check("root", "r")
        assert acl.check("root", "w")
        assert acl.check("root", "x")

    def test_missing_principal_denied(self):
        acl = ACL({"brain": "rwx"})
        assert not acl.check("stranger", "r")

    def test_permission_table_defaults(self):
        pt = PermissionTable()
        # /proc is read-only for everyone
        assert pt.check_access("brain", "/proc/uptime", "r")
        assert not pt.check_access("brain", "/proc/uptime", "w")
        # /tmp is full access
        assert pt.check_access("brain", "/tmp/scratch", "w")
        assert pt.check_access("channel", "/tmp/scratch", "w")

    def test_prefix_matching(self):
        pt = PermissionTable()
        # /proc/loop/latency inherits from /proc ACL
        assert pt.check_access("api", "/proc/loop/latency", "r")
        assert not pt.check_access("api", "/proc/loop/latency", "w")

    def test_capability_check(self):
        pt = PermissionTable()
        # Brain has MOTOR_WRITE, channel does not
        assert pt.check_access("brain", "/dev/motor", "w")
        assert not pt.check_access("channel", "/dev/motor", "w")

    def test_grant_and_revoke_cap(self):
        pt = PermissionTable()
        pt.revoke_cap("brain", Cap.MOTOR_WRITE)
        assert not pt.check_access("brain", "/dev/motor", "w")
        pt.grant_cap("brain", Cap.MOTOR_WRITE)
        assert pt.check_access("brain", "/dev/motor", "w")

    def test_dump(self):
        pt = PermissionTable()
        dump = pt.dump()
        assert "acls" in dump
        assert "capabilities" in dump
        assert "/proc" in dump["acls"]


# =====================================================================
# Safety layer tests
# =====================================================================
class TestSafetyLayer:
    def _make_safety(self, **limit_overrides):
        ns = Namespace()
        perms = PermissionTable()
        limits = {"motor_rate_hz": 100.0, **limit_overrides}
        return SafetyLayer(ns, perms, limits=limits), ns, perms

    def test_read_with_permission(self):
        sl, ns, _ = self._make_safety()
        ns.write("/proc/uptime", 10.0)
        assert sl.read("/proc/uptime", principal="brain") == 10.0

    def test_read_denied(self):
        sl, ns, _ = self._make_safety()
        ns.write("/dev/motor", {"linear": 0.5})
        # Channel has no read access to /dev/motor
        assert sl.read("/dev/motor", principal="channel") is None

    def test_write_with_permission(self):
        sl, ns, _ = self._make_safety()
        assert sl.write("/dev/motor", {"linear": 0.5}, principal="brain")

    def test_write_denied(self):
        sl, ns, _ = self._make_safety()
        assert not sl.write("/proc/uptime", 99, principal="brain")

    def test_motor_value_clamping(self):
        sl, ns, _ = self._make_safety()
        sl.write("/dev/motor", {"linear": 5.0, "angular": -3.0}, principal="brain")
        data = ns.read("/dev/motor")
        assert data["linear"] == 1.0
        assert data["angular"] == -1.0

    def test_motor_rate_limiting(self):
        sl, _, _ = self._make_safety(motor_rate_hz=2.0)
        assert sl.write("/dev/motor", {"linear": 0.1}, principal="brain")
        assert sl.write("/dev/motor", {"linear": 0.2}, principal="brain")
        # Third write within 1s should be rate-limited
        assert not sl.write("/dev/motor", {"linear": 0.3}, principal="brain")

    def test_estop_blocks_motor_writes(self):
        sl, _, _ = self._make_safety()
        sl.estop(principal="root")
        assert sl.is_estopped
        assert not sl.write("/dev/motor", {"linear": 0.5}, principal="brain")

    def test_estop_and_clear(self):
        sl, _, _ = self._make_safety()
        sl.estop(principal="root")
        assert sl.is_estopped
        sl.clear_estop(principal="root")
        assert not sl.is_estopped
        assert sl.write("/dev/motor", {"linear": 0.1}, principal="brain")

    def test_estop_requires_cap(self):
        sl, _, _ = self._make_safety()
        # Driver doesn't have CAP_ESTOP by default
        assert not sl.estop(principal="driver")

    def test_audit_logging(self):
        sl, ns, _ = self._make_safety()
        sl.write("/dev/motor", {"linear": 0.5}, principal="brain")
        actions = ns.read("/var/log/actions")
        assert len(actions) > 0
        assert actions[-1]["who"] == "brain"

    def test_lockout_after_violations(self):
        sl, _, _ = self._make_safety(max_violations_before_lockout=3)
        # Channel writing to /dev/motor should be denied (no MOTOR_WRITE cap)
        for _ in range(3):
            sl.write("/dev/motor", {}, principal="channel")
        # After 3 violations, channel should be locked out even from /tmp
        assert sl.write("/tmp/test", "data", principal="channel") is False

    def test_ls_with_permission(self):
        sl, ns, _ = self._make_safety()
        ns.mkdir("/tmp/test")
        ns.write("/tmp/test/a", 1)
        result = sl.ls("/tmp/test", principal="brain")
        assert result == ["a"]

    def test_policy_toggle(self):
        sl, _, _ = self._make_safety()
        assert sl.set_policy("clamp_motor", False, principal="root")
        # Now motor values should not be clamped
        sl.write("/dev/motor", {"linear": 5.0}, principal="brain")


# =====================================================================
# Memory tests
# =====================================================================
class TestMemory:
    def test_episodic_record_and_retrieve(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        mem.record_episode("saw wall", action={"type": "stop"}, outcome="stopped")
        episodes = mem.get_episodes()
        assert len(episodes) == 1
        assert episodes[0]["observation"] == "saw wall"

    def test_episodic_tag_filter(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        mem.record_episode("a", tags=["nav"])
        mem.record_episode("b", tags=["sensor"])
        mem.record_episode("c", tags=["nav"])
        nav_eps = mem.get_episodes(tag="nav")
        assert len(nav_eps) == 2

    def test_episodic_limit(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        mem._limits["episodic"] = 5
        for i in range(10):
            mem.record_episode(f"event_{i}")
        assert mem.get_episode_count() == 5

    def test_semantic_learn_and_recall(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        mem.learn_fact("door.locked", True)
        assert mem.recall_fact("door.locked") is True
        assert mem.recall_fact("nonexistent") is None

    def test_semantic_list_facts(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        mem.learn_fact("a", 1)
        mem.learn_fact("b", 2)
        facts = mem.list_facts()
        assert facts == {"a": 1, "b": 2}

    def test_semantic_forget(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        mem.learn_fact("temp", 22)
        assert mem.forget_fact("temp")
        assert mem.recall_fact("temp") is None

    def test_procedural_store_and_get(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        steps = [{"type": "move", "linear": 1.0}, {"type": "stop"}]
        mem.store_behavior("go_forward", steps, description="move then stop")
        beh = mem.get_behavior("go_forward")
        assert beh["steps"] == steps
        assert beh["description"] == "move then stop"

    def test_procedural_execution_count(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        mem.store_behavior("spin", [{"type": "move", "angular": 1.0}])
        mem.record_execution("spin")
        mem.record_execution("spin")
        beh = mem.get_behavior("spin")
        assert beh["executions"] == 2

    def test_procedural_remove(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        mem.store_behavior("test", [])
        assert mem.remove_behavior("test")
        assert mem.get_behavior("test") is None

    def test_context_summary(self):
        ns = Namespace()
        mem = MemoryStore(ns)
        mem.record_episode("saw obstacle", action={"type": "stop"})
        mem.learn_fact("hallway.clear", False)
        summary = mem.build_context_summary()
        assert "Recent Events" in summary
        assert "Known Facts" in summary
        assert "hallway.clear" in summary

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ns1 = Namespace()
            mem1 = MemoryStore(ns1, persist_dir=tmpdir)
            mem1.learn_fact("persistent", True)
            mem1.record_episode("test event")
            mem1.flush_to_disk()

            # Verify files exist
            assert (Path(tmpdir) / "semantic.json").exists()
            assert (Path(tmpdir) / "episodic.json").exists()

            # Load into fresh namespace
            ns2 = Namespace()
            mem2 = MemoryStore(ns2, persist_dir=tmpdir)
            assert mem2.recall_fact("persistent") is True


# =====================================================================
# Context & pipeline tests
# =====================================================================
class TestContext:
    def test_push_and_get(self):
        ns = Namespace()
        ctx = ContextWindow(ns)
        ctx.push("user", "go forward")
        ctx.push("brain", '{"type": "move", "linear": 1.0}')
        window = ctx.get_window()
        assert len(window) == 2
        assert window[0]["role"] == "user"

    def test_turn_count(self):
        ns = Namespace()
        ctx = ContextWindow(ns)
        ctx.push("user", "a")
        ctx.push("brain", "b")
        assert ctx.get_turn_count() == 2

    def test_summarisation(self):
        ns = Namespace()
        ctx = ContextWindow(ns, max_depth=10, summary_threshold=8)
        for i in range(12):
            ctx.push("user", f"message {i}")
        # Window should be trimmed, summary should exist
        window = ctx.get_window()
        assert len(window) <= 10
        summary = ctx.get_summary()
        assert len(summary) > 0

    def test_clear(self):
        ns = Namespace()
        ctx = ContextWindow(ns)
        ctx.push("user", "test")
        ctx.clear()
        assert ctx.get_window() == []
        assert ctx.get_summary() == ""
        assert ctx.get_turn_count() == 0

    def test_build_prompt_context(self):
        ns = Namespace()
        ctx = ContextWindow(ns)
        ctx.push("user", "turn left")
        ctx.push("brain", "turning")
        prompt = ctx.build_prompt_context()
        assert "user: turn left" in prompt
        assert "brain: turning" in prompt


class TestPipeline:
    def test_basic_pipeline(self):
        ns = Namespace()
        ns.write("/input", 42)
        result = (Pipeline("test", ns)
                  .read("/input")
                  .transform(lambda x: x * 2)
                  .write("/output")
                  .run())
        assert result == 84
        assert ns.read("/output") == 84

    def test_pipeline_with_append(self):
        ns = Namespace()
        ns.write("/log", [])
        (Pipeline("log", ns)
         .transform(lambda _: {"action": "move"})
         .append("/log")
         .run())
        log = ns.read("/log")
        assert len(log) == 1

    def test_pipeline_results(self):
        ns = Namespace()
        ns.write("/x", 10)
        pipe = Pipeline("inspect", ns)
        pipe.read("/x").transform(lambda x: x + 5).run()
        results = pipe.results
        assert len(results) == 2
        assert all(r["ok"] for r in results)

    def test_pipeline_error_handling(self):
        ns = Namespace()

        def fail(_):
            raise ValueError("boom")

        pipe = Pipeline("fail", ns)
        pipe.transform(lambda _: 1).transform(fail).run()
        assert not pipe.results[-1]["ok"]


# =====================================================================
# Proc tests
# =====================================================================
class TestProc:
    def test_bootstrap(self):
        ns = Namespace()
        proc = ProcFS(ns)
        proc.bootstrap()
        assert ns.read("/proc/status") == "booting"
        assert ns.read("/proc/loop/iteration") == 0

    def test_bootstrap_with_config(self):
        ns = Namespace()
        proc = ProcFS(ns)
        proc.bootstrap({"agent": {"provider": "google", "model": "gemini-1.5-flash",
                                   "latency_budget_ms": 500}})
        assert ns.read("/proc/brain/provider") == "google"
        assert ns.read("/proc/loop/budget_ms") == 500

    def test_record_loop_iteration(self):
        ns = Namespace()
        proc = ProcFS(ns)
        proc.bootstrap()
        proc.record_loop_iteration(150.0)
        assert ns.read("/proc/loop/iteration") == 1
        assert ns.read("/proc/loop/latency_ms") == 150.0

    def test_record_thought(self):
        ns = Namespace()
        proc = ProcFS(ns)
        proc.bootstrap()
        proc.record_thought("moving forward", {"type": "move", "linear": 1.0})
        assert ns.read("/proc/brain/thoughts") == 1
        thought = ns.read("/proc/brain/last_thought")
        assert thought["action"]["type"] == "move"

    def test_snapshot(self):
        ns = Namespace()
        proc = ProcFS(ns)
        proc.bootstrap()
        proc.update_status("active")
        snap = proc.snapshot()
        assert snap["status"] == "active"
        assert "loop" in snap
        assert "brain" in snap
        assert "hw" in snap


# =====================================================================
# CastorFS integration tests
# =====================================================================
class TestCastorFS:
    def test_boot(self):
        fs = CastorFS()
        config = {
            "agent": {"provider": "google", "model": "gemini-1.5-flash",
                      "latency_budget_ms": 200},
            "metadata": {"robot_name": "test"},
        }
        fs.boot(config)
        assert fs.read("/proc/status") == "active"
        assert fs.read("/proc/brain/provider") == "google"

    def test_permission_enforcement(self):
        fs = CastorFS()
        fs.boot()
        # Brain can write to /dev/motor
        assert fs.write("/dev/motor", {"linear": 0.5}, principal="brain")
        # Channel cannot
        assert not fs.write("/dev/motor", {"linear": 0.5}, principal="channel")

    def test_estop(self):
        fs = CastorFS()
        fs.boot()
        assert fs.estop(principal="api")
        assert fs.is_estopped
        assert fs.clear_estop(principal="root")
        assert not fs.is_estopped

    def test_memory_integration(self):
        fs = CastorFS()
        fs.boot()
        fs.memory.learn_fact("test", True)
        assert fs.memory.recall_fact("test") is True

    def test_context_integration(self):
        fs = CastorFS()
        fs.boot()
        fs.context.push("user", "hello")
        assert len(fs.context.get_window()) == 1

    def test_pipeline_builder(self):
        fs = CastorFS()
        fs.boot()
        fs.ns.write("/test/input", 10)
        pipe = fs.pipeline("test")
        result = pipe.read("/test/input").transform(lambda x: x * 3).run()
        assert result == 30

    def test_tree_output(self):
        fs = CastorFS()
        fs.boot()
        tree = fs.tree("/proc", depth=1)
        assert "proc/" in tree
        assert "uptime" in tree

    def test_ls(self):
        fs = CastorFS()
        fs.boot()
        children = fs.ls("/", principal="root")
        assert "proc" in children
        assert "dev" in children
        assert "var" in children
        assert "tmp" in children

    def test_full_ooda_cycle(self):
        """Simulate a full observe-orient-decide-act cycle through the FS."""
        fs = CastorFS()
        fs.boot()

        # Observe
        fs.ns.write("/dev/camera", {"t": time.time(), "size": 1024})

        # Orient + Decide (brain writes thought)
        action = {"type": "move", "linear": 0.3, "angular": -0.1}
        fs.proc.record_thought("moving forward cautiously", action)

        # Act (write through safety layer)
        assert fs.write("/dev/motor", action, principal="brain")

        # Record in memory
        fs.memory.record_episode("open hallway", action=action,
                                 outcome="moved forward")
        fs.context.push("brain", "moving forward cautiously", metadata=action)

        # Telemetry
        fs.proc.record_loop_iteration(150.0)

        # Verify state
        assert fs.proc.snapshot()["brain"]["thoughts"] == 1
        assert fs.proc.snapshot()["loop"]["iteration"] == 1
        assert len(fs.memory.get_episodes()) == 1
        assert len(fs.context.get_window()) == 1

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fs1 = CastorFS(persist_dir=tmpdir)
            fs1.boot()
            fs1.memory.learn_fact("key", "value")
            fs1.memory.record_episode("test")
            fs1.shutdown()

            fs2 = CastorFS(persist_dir=tmpdir)
            fs2.boot()
            assert fs2.memory.recall_fact("key") == "value"
