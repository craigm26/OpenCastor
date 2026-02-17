"""Tests for tamper-evident audit log hash chaining."""

import json

import pytest

from castor.audit import AuditLog, _hash_entry


@pytest.fixture
def audit_log(tmp_path):
    path = str(tmp_path / "audit.log")
    return AuditLog(log_path=path)


class TestHashChainCreation:
    def test_empty_log_verifies(self, audit_log):
        valid, idx = audit_log.verify_chain()
        assert valid is True
        assert idx is None

    def test_single_entry_genesis(self, audit_log):
        audit_log.log("test_event", source="test")
        with open(audit_log._path) as f:
            entry = json.loads(f.readline())
        assert entry["prev_hash"] == "GENESIS"
        valid, idx = audit_log.verify_chain()
        assert valid is True

    def test_chain_of_three(self, audit_log):
        audit_log.log("event1")
        audit_log.log("event2")
        audit_log.log("event3")

        with open(audit_log._path) as f:
            lines = [line.strip() for line in f if line.strip()]

        assert len(lines) == 3
        assert json.loads(lines[0])["prev_hash"] == "GENESIS"
        assert json.loads(lines[1])["prev_hash"] == _hash_entry(lines[0])
        assert json.loads(lines[2])["prev_hash"] == _hash_entry(lines[1])

        valid, idx = audit_log.verify_chain()
        assert valid is True

    def test_ten_entries(self, audit_log):
        for i in range(10):
            audit_log.log(f"event_{i}", source="test", seq=i)
        valid, idx = audit_log.verify_chain()
        assert valid is True


class TestTamperDetection:
    def test_tamper_middle_entry(self, audit_log):
        for i in range(5):
            audit_log.log(f"event_{i}")

        # Tamper with entry at index 2
        with open(audit_log._path) as f:
            lines = f.readlines()

        entry = json.loads(lines[2])
        entry["event"] = "TAMPERED"
        lines[2] = json.dumps(entry) + "\n"

        with open(audit_log._path, "w") as f:
            f.writelines(lines)

        valid, idx = audit_log.verify_chain()
        assert valid is False
        assert idx == 3  # Entry 3's prev_hash won't match tampered entry 2

    def test_tamper_first_entry(self, audit_log):
        for i in range(3):
            audit_log.log(f"event_{i}")

        with open(audit_log._path) as f:
            lines = f.readlines()

        entry = json.loads(lines[0])
        entry["event"] = "TAMPERED"
        lines[0] = json.dumps(entry) + "\n"

        with open(audit_log._path, "w") as f:
            f.writelines(lines)

        valid, idx = audit_log.verify_chain()
        assert valid is False
        assert idx == 1

    def test_tamper_genesis(self, audit_log):
        audit_log.log("event_0")

        with open(audit_log._path) as f:
            lines = f.readlines()

        entry = json.loads(lines[0])
        entry["prev_hash"] = "WRONG"
        lines[0] = json.dumps(entry) + "\n"

        with open(audit_log._path, "w") as f:
            f.writelines(lines)

        valid, idx = audit_log.verify_chain()
        assert valid is False
        assert idx == 0

    def test_tamper_last_entry(self, audit_log):
        for i in range(4):
            audit_log.log(f"event_{i}")

        with open(audit_log._path) as f:
            lines = f.readlines()

        # Tamper last entry's prev_hash
        entry = json.loads(lines[-1])
        entry["prev_hash"] = "deadbeef"
        lines[-1] = json.dumps(entry) + "\n"

        with open(audit_log._path, "w") as f:
            f.writelines(lines)

        valid, idx = audit_log.verify_chain()
        assert valid is False
        assert idx == 3

    def test_deleted_entry(self, audit_log):
        for i in range(5):
            audit_log.log(f"event_{i}")

        with open(audit_log._path) as f:
            lines = f.readlines()

        # Delete entry 2 — entry 3's hash will point to entry 2, but now sees entry 1
        del lines[2]

        with open(audit_log._path, "w") as f:
            f.writelines(lines)

        valid, idx = audit_log.verify_chain()
        assert valid is False


class TestBackwardCompatibility:
    def test_legacy_entries_no_hash(self, audit_log):
        """Old entries without prev_hash should not break verification."""
        # Write legacy entries (no prev_hash)
        with open(audit_log._path, "w") as f:
            f.write(
                json.dumps({"ts": "2024-01-01T00:00:00", "event": "old1", "source": "sys"}) + "\n"
            )
            f.write(
                json.dumps({"ts": "2024-01-01T00:01:00", "event": "old2", "source": "sys"}) + "\n"
            )

        valid, idx = audit_log.verify_chain()
        assert valid is True

    def test_legacy_then_chained(self, audit_log):
        """New entries after legacy ones should chain from the last legacy entry."""
        with open(audit_log._path, "w") as f:
            f.write(
                json.dumps({"ts": "2024-01-01T00:00:00", "event": "old1", "source": "sys"}) + "\n"
            )
            f.write(
                json.dumps({"ts": "2024-01-01T00:01:00", "event": "old2", "source": "sys"}) + "\n"
            )

        # Now log a new entry — it should hash the last legacy line
        audit_log.log("new1", source="test")

        with open(audit_log._path) as f:
            lines = [line.strip() for line in f if line.strip()]

        new_entry = json.loads(lines[2])
        assert new_entry["prev_hash"] == _hash_entry(lines[1])

        valid, idx = audit_log.verify_chain()
        assert valid is True

    def test_mixed_legacy_and_chained(self, audit_log):
        """Multiple legacy then multiple chained entries."""
        with open(audit_log._path, "w") as f:
            for i in range(3):
                f.write(
                    json.dumps({"ts": f"2024-01-0{i + 1}", "event": f"legacy_{i}", "source": "sys"})
                    + "\n"
                )

        for i in range(3):
            audit_log.log(f"new_{i}", source="test")

        valid, idx = audit_log.verify_chain()
        assert valid is True


class TestEdgeCases:
    def test_nonexistent_file(self, tmp_path):
        log = AuditLog(log_path=str(tmp_path / "nope.log"))
        valid, idx = log.verify_chain()
        assert valid is True

    def test_corrupt_json_line(self, audit_log):
        audit_log.log("good")
        with open(audit_log._path, "a") as f:
            f.write("NOT VALID JSON\n")
        valid, idx = audit_log.verify_chain()
        assert valid is False
        assert idx == 1

    def test_convenience_methods_chain(self, audit_log):
        audit_log.log_startup("/etc/config.yaml")
        audit_log.log_motor_command({"type": "drive", "linear": 0.5})
        audit_log.log_approval(1, "granted")
        audit_log.log_config_change("robot.yaml")
        audit_log.log_error("something broke")
        audit_log.log_shutdown("normal")

        valid, idx = audit_log.verify_chain()
        assert valid is True
