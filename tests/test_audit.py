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
