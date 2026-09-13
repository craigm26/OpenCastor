"""Tests for castor.audit -- append-only event log."""

import json
from datetime import datetime, timedelta

from castor.audit import AuditLog


# =====================================================================
# AuditLog.log
# =====================================================================
class TestAuditLogLog:
    def test_log_writes_json_line_to_file(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        audit.log("test_event", source="unit_test", detail="hello")

        with open(log_file) as f:
            lines = f.readlines()
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["event"] == "test_event"
        assert entry["source"] == "unit_test"
        assert entry["detail"] == "hello"
        assert "ts" in entry


# =====================================================================
# AuditLog.log_motor_command
# =====================================================================
class TestAuditLogMotorCommand:
    def test_log_motor_command_creates_correct_entry(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        action = {"type": "move", "linear": 0.5, "angular": 0.2}
        audit.log_motor_command(action, source="brain")

        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["event"] == "motor_command"
        assert entry["source"] == "brain"
        assert entry["action_type"] == "move"
        assert entry["linear"] == 0.5
        assert entry["angular"] == 0.2


# =====================================================================
# AuditLog.log_approval
# =====================================================================
class TestAuditLogApproval:
    def test_log_approval_creates_correct_entry(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        audit.log_approval(42, "granted", source="cli")

        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["event"] == "approval"
        assert entry["id"] == 42
        assert entry["decision"] == "granted"
        assert entry["source"] == "cli"


# =====================================================================
# AuditLog.log_error
# =====================================================================
class TestAuditLogError:
    def test_log_error_truncates_long_messages(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        long_message = "x" * 1000
        audit.log_error(long_message, source="runtime")

        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["event"] == "error"
        assert len(entry["message"]) <= 500

    def test_log_error_short_message_unchanged(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        audit.log_error("something broke", source="runtime")

        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["message"] == "something broke"


# =====================================================================
# AuditLog.log_startup / log_shutdown
# =====================================================================
class TestAuditLogStartupShutdown:
    def test_log_startup(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        audit.log_startup("/path/to/robot.rcan.yaml")

        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["event"] == "startup"
        assert entry["source"] == "runtime"
        assert entry["config"] == "/path/to/robot.rcan.yaml"

    def test_log_shutdown(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        audit.log_shutdown(reason="user_request")

        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["event"] == "shutdown"
        assert entry["source"] == "runtime"
        assert entry["reason"] == "user_request"

    def test_log_shutdown_default_reason(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        audit.log_shutdown()

        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["reason"] == "normal"


# =====================================================================
# AuditLog.read
# =====================================================================
class TestAuditLogRead:
    def test_read_returns_entries(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        audit.log("event_a", source="test")
        audit.log("event_b", source="test")
        audit.log("event_c", source="test")

        entries = audit.read()
        assert len(entries) == 3
        assert entries[0]["event"] == "event_a"
        assert entries[2]["event"] == "event_c"

    def test_read_with_since_filter(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        # Write an entry with a timestamp in the past
        old_entry = {
            "ts": (datetime.now() - timedelta(hours=48)).isoformat(),
            "event": "old_event",
            "source": "test",
        }
        recent_entry = {
            "ts": datetime.now().isoformat(),
            "event": "recent_event",
            "source": "test",
        }
        with open(log_file, "w") as f:
            f.write(json.dumps(old_entry) + "\n")
            f.write(json.dumps(recent_entry) + "\n")

        entries = audit.read(since="24h")
        assert len(entries) == 1
        assert entries[0]["event"] == "recent_event"

    def test_read_with_event_filter(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        audit.log("motor_command", source="brain")
        audit.log("error", source="runtime")
        audit.log("motor_command", source="brain")

        entries = audit.read(event="motor_command")
        assert len(entries) == 2
        for e in entries:
            assert e["event"] == "motor_command"

    def test_read_with_limit(self, tmp_path):
        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        for i in range(10):
            audit.log(f"event_{i}", source="test")

        entries = audit.read(limit=3)
        assert len(entries) == 3
        # Should return the 3 most recent
        assert entries[0]["event"] == "event_7"
        assert entries[2]["event"] == "event_9"

    def test_read_when_file_does_not_exist(self, tmp_path):
        log_file = str(tmp_path / "nonexistent_audit.log")
        audit = AuditLog(log_path=log_file)

        entries = audit.read()
        assert entries == []


class TestAuditLogWatermarkIndex:
    def test_log_motor_command_stores_watermark_in_entry(self, tmp_path):
        from castor.audit import AuditLog

        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        action = {"type": "move", "linear": 0.3, "angular": 0.0}
        token = "rcan-wm-v1:" + "a" * 32
        audit.log_motor_command(action, watermark_token=token)

        import json

        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["watermark_token"] == token

    def test_watermark_index_updated_after_log(self, tmp_path):
        from castor.audit import AuditLog

        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        action = {"type": "move", "linear": 0.3, "angular": 0.0}
        token = "rcan-wm-v1:" + "b" * 32
        audit.log_motor_command(action, watermark_token=token)

        assert token in audit._watermark_index
        assert audit._watermark_index[token]["watermark_token"] == token

    def test_watermark_index_built_from_existing_log(self, tmp_path):
        import json

        from castor.audit import AuditLog

        log_file = str(tmp_path / "audit.log")
        token = "rcan-wm-v1:" + "c" * 32
        entry = {
            "ts": "2026-04-10T00:00:00",
            "event": "motor_command",
            "source": "brain",
            "prev_hash": "GENESIS",
            "watermark_token": token,
        }
        with open(log_file, "w") as f:
            f.write(json.dumps(entry) + "\n")

        audit = AuditLog(log_path=log_file)
        assert token in audit._watermark_index

    def test_no_watermark_token_no_index_entry(self, tmp_path):
        from castor.audit import AuditLog

        log_file = str(tmp_path / "audit.log")
        audit = AuditLog(log_path=log_file)

        action = {"type": "move", "linear": 0.1, "angular": 0.0}
        audit.log_motor_command(action)  # no watermark_token

        assert len(audit._watermark_index) == 0


# =====================================================================
# OC-06: the verifier must tell "no log" from "chain intact", the path
# must be absolute, an unlinked entry must be a break, and the
# POST_TOOL_USE hook must write into the same chain.
# =====================================================================
import os  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from argparse import Namespace  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

import castor.audit as audit_mod  # noqa: E402
from castor.audit import (  # noqa: E402
    CHAIN_BROKEN,
    CHAIN_NO_LOG,
    CHAIN_OK,
    _hash_entry,
    orphaned_legacy_log,
    resolve_audit_path,
)


class TestVerifyReportsMissingLog:
    def test_verify_reports_missing_log(self, tmp_path):
        """A log that does not exist is `no_log`, never "intact".

        This is the defect the item names: `castor audit --verify` in an empty
        directory printed that the chain was intact and exited 0.
        """
        audit = AuditLog(log_path=str(tmp_path / "never-written.log"))

        assert audit.verify_chain_state() == (CHAIN_NO_LOG, None)
        assert audit.verify_chain() == (False, None)

        # An existing but empty file is a different answer: it is a log.
        open(audit._path, "w").close()
        assert audit.verify_chain_state() == (CHAIN_OK, None)

    def test_cli_verify_exits_non_zero_with_no_log(self, tmp_path, monkeypatch, capsys):
        from castor.cli import cmd_audit

        missing = tmp_path / "nowhere" / "audit.log"
        monkeypatch.setattr(audit_mod, "_audit", AuditLog(log_path=str(missing)))
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as excinfo:
            cmd_audit(Namespace(verify=True, art11=False, allow_unchained=False))

        assert excinfo.value.code != 0
        out = capsys.readouterr().out
        assert "No audit log found" in out
        assert "intact" not in out

    def test_cli_verify_exits_one_on_a_break(self, tmp_path, monkeypatch, capsys):
        from castor.cli import cmd_audit

        audit = AuditLog(log_path=str(tmp_path / "audit.log"))
        audit.log("one")
        audit.log("two")
        lines = Path(audit._path).read_text().splitlines()
        entry = json.loads(lines[0])
        entry["event"] = "TAMPERED"
        lines[0] = json.dumps(entry)
        Path(audit._path).write_text("\n".join(lines) + "\n")

        monkeypatch.setattr(audit_mod, "_audit", audit)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as excinfo:
            cmd_audit(Namespace(verify=True, art11=False, allow_unchained=False))
        assert excinfo.value.code == 1
        assert "broken" in capsys.readouterr().out


class TestUnchainedTail:
    def test_unchained_tail_is_a_break(self, tmp_path):
        """A fabricated tail with no prev_hash used to verify clean."""
        audit = AuditLog(log_path=str(tmp_path / "audit.log"))
        audit.log("real_1")
        audit.log("real_2")
        assert audit.verify_chain() == (True, None)

        with open(audit._path, "a") as f:
            f.write(json.dumps({"ts": "2026-01-01T00:00:00", "event": "fabricated"}) + "\n")

        state, idx = audit.verify_chain_state()
        assert state == CHAIN_BROKEN
        assert idx == 2

    def test_allow_unchained_is_the_only_way_back(self, tmp_path):
        audit = AuditLog(log_path=str(tmp_path / "audit.log"))
        with open(audit._path, "w") as f:
            f.write(json.dumps({"ts": "2024-01-01T00:00:00", "event": "legacy"}) + "\n")

        assert audit.verify_chain_state() == (CHAIN_BROKEN, 0)
        assert audit.verify_chain_state(allow_unchained=True) == (CHAIN_OK, None)


class TestAbsoluteAuditPath:
    def test_absolute_audit_path_is_stable_across_cwd(self, tmp_path, monkeypatch):
        """The path must not depend on where anyone happened to be standing."""
        robot_home = tmp_path / "robot-home"
        robot_home.mkdir()
        monkeypatch.delenv("OPENCASTOR_AUDIT_LOG", raising=False)
        monkeypatch.setenv("ROBOT_HOME", str(robot_home))

        here = tmp_path / "here"
        there = tmp_path / "there"
        here.mkdir()
        there.mkdir()

        monkeypatch.chdir(here)
        from_here = resolve_audit_path()
        monkeypatch.chdir(there)
        from_there = resolve_audit_path()

        assert from_here == from_there
        assert os.path.isabs(from_here)
        assert from_here == str(robot_home / "audit.log")

    def test_module_default_is_absolute(self):
        assert os.path.isabs(audit_mod.AUDIT_LOG_PATH)
        assert audit_mod.AUDIT_LOG_PATH != audit_mod.LEGACY_AUDIT_FILE

    def test_explicit_override_wins(self, tmp_path, monkeypatch):
        target = tmp_path / "elsewhere" / "audit.log"
        monkeypatch.setenv("OPENCASTOR_AUDIT_LOG", str(target))
        monkeypatch.setenv("ROBOT_HOME", str(tmp_path / "ignored"))
        assert resolve_audit_path() == str(target)

    def test_default_creates_its_directory(self, tmp_path):
        target = tmp_path / "fresh" / "deep" / "audit.log"
        audit = AuditLog(log_path=str(target))
        audit.log("hello")
        assert target.exists()

    def test_orphaned_legacy_log_is_named_not_moved(self, tmp_path):
        """The migration note. We say where the old file is; we never touch it."""
        legacy = tmp_path / audit_mod.LEGACY_AUDIT_FILE
        legacy.write_text("{}\n")

        found = orphaned_legacy_log(cwd=str(tmp_path))
        assert found == str(legacy)
        assert legacy.exists()
        assert legacy.read_text() == "{}\n"

        assert orphaned_legacy_log(cwd=str(tmp_path / "empty-dir")) is None


class TestPostToolHook:
    def test_post_tool_hook_writes_a_chained_line(self, tmp_path, monkeypatch):
        """The generated hook must emit parseable JSON into the one chain.

        Two defects met here: run_post_tool() had no caller so the hook never
        fired, and the script echoed a double-quoted brace holding the payload
        unescaped, so a `$` or a backtick in a tool argument was expanded by
        the shell on the way to disk.
        """
        import castor.hooks.default_hooks as dh
        from castor.hooks.runner import HookEvent, HookRunner

        audit_path = tmp_path / "home" / "audit.log"
        audit_path.parent.mkdir(parents=True)
        monkeypatch.setattr(dh, "_HOOKS_DIR", tmp_path / "hooks")
        monkeypatch.setenv("OPENCASTOR_AUDIT_LOG", str(audit_path))

        hooks = dh.get_default_hooks()
        post = [h for h in hooks if h.event == HookEvent.POST_TOOL_USE]
        assert post, "the default set must still install a POST_TOOL_USE audit hook"

        runner = HookRunner(hooks)
        # A payload full of every character the old echo would have eaten.
        runner.run_post_tool("robot_move", {"note": "$HOME `id` $(whoami) \"quoted\""})
        runner.run_post_tool("robot_stop", {"note": "second"})

        lines = [ln for ln in audit_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2

        first, second = (json.loads(ln) for ln in lines)
        # jq-parseable, and it says what the tool call actually said.
        assert first["prev_hash"] == "GENESIS"
        assert first["payload"]["tool"] == "robot_move"
        assert "$(whoami)" in first["payload"]["result"]["note"]
        # Chained the way castor.audit chains, into the same file.
        assert second["prev_hash"] == _hash_entry(lines[0])

        audit = AuditLog(log_path=str(audit_path))
        assert audit.verify_chain() == (True, None)
        # And the runtime's own next line continues the hook's chain.
        audit.log("runtime_event")
        assert audit.verify_chain() == (True, None)

    def test_hook_script_is_rewritten_when_the_marker_is_stale(self, tmp_path, monkeypatch):
        import castor.hooks.default_hooks as dh

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        monkeypatch.setattr(dh, "_HOOKS_DIR", hooks_dir)

        stale = hooks_dir / "audit_log.sh"
        stale.write_text("#!/usr/bin/env bash\n# opencastor-hook-version: 0\nexit 0\n")
        dh.get_default_hooks()
        assert f"{dh._MARKER_PREFIX} {dh.HOOK_SCRIPT_VERSION}" in stale.read_text()

        # An unmarked script is somebody's own edit and is left alone.
        mine = hooks_dir / "safety_check.sh"
        mine.write_text("#!/usr/bin/env bash\n# mine\nexit 0\n")
        dh.get_default_hooks()
        assert mine.read_text() == "#!/usr/bin/env bash\n# mine\nexit 0\n"

    def test_generated_script_is_valid_bash(self, tmp_path, monkeypatch):
        import castor.hooks.default_hooks as dh

        monkeypatch.setattr(dh, "_HOOKS_DIR", tmp_path / "hooks")
        dh.get_default_hooks()
        for name in ("audit_log.sh", "safety_check.sh"):
            proc = subprocess.run(
                ["bash", "-n", str(tmp_path / "hooks" / name)],
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, proc.stderr

    def test_generated_script_resolves_robot_home_at_run_time(self, tmp_path, monkeypatch):
        """The hook must read the path when it runs, not when it was written.

        Baking AUDIT_LOG_PATH in as the only value reopens the divergence this
        change closes: the process that generated the script would decide the
        path forever, and because its version marker is current nothing would
        ever rewrite it. A script generated with no ROBOT_HOME must still land
        in $ROBOT_HOME/audit.log when the runtime that runs it has one.
        """
        import castor.hooks.default_hooks as dh

        monkeypatch.delenv("OPENCASTOR_AUDIT_LOG", raising=False)
        monkeypatch.delenv("ROBOT_HOME", raising=False)
        monkeypatch.setattr(dh, "_HOOKS_DIR", tmp_path / "hooks")
        dh.get_default_hooks()
        script = tmp_path / "hooks" / "audit_log.sh"

        robot_home = tmp_path / "robot-home"
        robot_home.mkdir()
        env = dict(os.environ)
        env.pop("OPENCASTOR_AUDIT_LOG", None)
        env["ROBOT_HOME"] = str(robot_home)
        subprocess.run(
            ["bash", str(script)],
            input=json.dumps({"tool": "robot_move", "result": {}}),
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )

        landed = robot_home / "audit.log"
        assert landed.exists(), "hook wrote somewhere other than $ROBOT_HOME/audit.log"
        assert json.loads(landed.read_text().strip())["prev_hash"] == "GENESIS"

    def test_hook_and_runtime_do_not_break_the_chain_when_interleaved(
        self, tmp_path, monkeypatch
    ):
        """Two writers, one file, one chain.

        Unifying the path made the hook a second process appending to the file
        the runtime appends to, and a threading.Lock does not see another
        process. Without a cross-process lock, one writer's read-last-line can
        straddle the other's append and produce a prev_hash pointing at a line
        that is no longer last: a break the verifier reports as tampering when
        nothing was tampered with.
        """
        import threading

        import castor.hooks.default_hooks as dh

        monkeypatch.setattr(dh, "_HOOKS_DIR", tmp_path / "hooks")
        dh.get_default_hooks()
        script = tmp_path / "hooks" / "audit_log.sh"

        log_path = tmp_path / "home" / "audit.log"
        log_path.parent.mkdir(parents=True)
        env = dict(os.environ)
        env["OPENCASTOR_AUDIT_LOG"] = str(log_path)

        audit = AuditLog(log_path=str(log_path))

        def runtime_writes():
            for i in range(24):
                audit.log(f"runtime_{i}", source="test")
                time.sleep(0.004)

        def hook_writes():
            for i in range(24):
                subprocess.run(
                    ["bash", str(script)],
                    input=json.dumps({"tool": f"t{i}", "result": {}}),
                    capture_output=True,
                    text=True,
                    env=env,
                )

        threads = [threading.Thread(target=runtime_writes), threading.Thread(target=hook_writes)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 48
        state, idx = audit.verify_chain_state()
        assert state == CHAIN_OK, f"chain broke at entry {idx} with two writers"
