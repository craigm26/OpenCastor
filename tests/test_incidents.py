"""Tests for castor.incidents — serious-incident log and monitoring report."""

import hashlib
import json

from castor.incidents import (
    INCIDENT_SCHEMA_VERSION,
    ROTATION_RECORD_TYPE,
    IncidentLog,
    IncidentSeverity,
    generate_report,
)


class TestIncidentLog:
    def test_record_creates_entry(self, tmp_path):
        log = IncidentLog(tmp_path / "incidents.jsonl")
        log.record(
            severity=IncidentSeverity.SERIOUS_HARM,
            category="test_category",
            description="Test incident",
            system_state={"driver": "simulation"},
        )
        entries = log.list_incidents()
        assert len(entries) == 1
        assert entries[0]["severity"] == "serious_harm"
        assert entries[0]["category"] == "test_category"
        assert entries[0]["description"] == "Test incident"

    def test_record_assigns_uuid_id(self, tmp_path):
        log = IncidentLog(tmp_path / "incidents.jsonl")
        log.record(IncidentSeverity.SERIOUS_HARM, "cat", "desc", {})
        entries = log.list_incidents()
        assert len(entries[0]["id"]) == 36  # UUID4 format

    def test_record_assigns_timestamp(self, tmp_path):
        log = IncidentLog(tmp_path / "incidents.jsonl")
        log.record(IncidentSeverity.SERIOUS_HARM, "cat", "desc", {})
        entries = log.list_incidents()
        assert "T" in entries[0]["timestamp"]  # ISO 8601

    def test_life_health_severity(self, tmp_path):
        log = IncidentLog(tmp_path / "incidents.jsonl")
        log.record(IncidentSeverity.DEATH, "estop", "ESTOP triggered", {})
        entries = log.list_incidents()
        assert entries[0]["severity"] == "death"

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        IncidentLog(path).record(IncidentSeverity.SERIOUS_HARM, "cat", "desc", {})
        IncidentLog(path).record(IncidentSeverity.SERIOUS_HARM, "cat2", "desc2", {})
        entries = IncidentLog(path).list_incidents()
        assert len(entries) == 2

    def test_empty_log_returns_empty_list(self, tmp_path):
        log = IncidentLog(tmp_path / "incidents.jsonl")
        assert log.list_incidents() == []


class TestGenerateReport:
    def test_report_schema_and_fields(self, tmp_path):
        log = IncidentLog(tmp_path / "incidents.jsonl")
        log.record(IncidentSeverity.SERIOUS_HARM, "cat", "Test incident", {"rrn": "RRN-1"})
        report = generate_report(log)
        assert report["schema"] == INCIDENT_SCHEMA_VERSION
        assert "generated_at" in report
        assert "total_incidents" in report
        assert "incidents_by_severity" in report
        assert "incidents" in report
        assert "post_market_monitoring_note" in report
        assert "serious_incident_reporting" in report

    def test_report_counts_by_severity(self, tmp_path):
        log = IncidentLog(tmp_path / "incidents.jsonl")
        log.record(IncidentSeverity.DEATH, "estop", "Critical", {})
        log.record(IncidentSeverity.SERIOUS_HARM, "config", "Minor", {})
        report = generate_report(log)
        assert report["total_incidents"] == 2
        assert report["incidents_by_severity"]["death"] == 1
        assert report["incidents_by_severity"]["serious_harm"] == 1

    def test_report_json_serializable(self, tmp_path):
        log = IncidentLog(tmp_path / "incidents.jsonl")
        log.record(IncidentSeverity.SERIOUS_HARM, "cat", "desc", {})
        report = generate_report(log)
        serialized = json.dumps(report)
        assert len(serialized) > 0

    def test_empty_log_report(self, tmp_path):
        log = IncidentLog(tmp_path / "incidents.jsonl")
        report = generate_report(log)
        assert report["total_incidents"] == 0


class TestRotationAndHashCache:
    """OC-13 follow-up: the chain is cached in memory and bounded on disk."""

    @staticmethod
    def _chain_lines(log):
        """Every raw line of the log, rotated files first, in write order."""
        lines = []
        for path in log.rotated_paths() + [log._path]:
            if path.exists():
                lines.extend([x for x in path.read_text().splitlines() if x.strip()])
        return lines

    def test_chain_is_continuous_across_a_rotation(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        log = IncidentLog(path, max_bytes=900)
        for i in range(12):
            log.record(IncidentSeverity.SERIOUS_HARM, "estop", f"stop {i}", {})

        assert log.rotated_paths(), "the log should have rolled at least once"

        lines = self._chain_lines(log)
        prev = ""
        for raw in lines:
            row = json.loads(raw)
            assert row.get("prev_sha256", "") == prev, row.get("record_type")
            prev = hashlib.sha256(raw.encode()).hexdigest()

        # The carry-over line names the file it rolled away from, and the
        # incidents themselves survive the rotation.
        carries = [
            json.loads(x)
            for x in self._chain_lines(log)
            if json.loads(x).get("record_type") == ROTATION_RECORD_TYPE
        ]
        assert carries and carries[0]["rotated_to"].startswith("incidents.")
        assert len(IncidentLog(path, max_bytes=900).list_incidents()) == 12

    def test_reader_sees_incidents_from_rotated_files(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        log = IncidentLog(path, max_bytes=700)
        first = log.record(IncidentSeverity.SERIOUS_HARM, "estop", "the oldest one", {})
        for i in range(10):
            log.record(IncidentSeverity.SERIOUS_HARM, "estop", f"stop {i}", {})
        assert log.rotated_paths()

        # A fresh reader (what `castor incidents list` and the submitter use).
        fresh = IncidentLog(path, max_bytes=700)
        ids = [i["id"] for i in fresh.list_incidents()]
        assert first in ids
        assert ids[0] == first  # oldest first, rotated file read first
        assert first in [i["id"] for i in fresh.unreported_incidents()]

    def test_cached_hash_matches_a_full_re_read(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        log = IncidentLog(path)
        for i in range(5):
            log.record(IncidentSeverity.SERIOUS_HARM, "estop", f"stop {i}", {})

        cached = log._last_line_hash()
        assert log._cached_hash == cached

        raw = [x for x in path.read_text().splitlines() if x.strip()]
        assert cached == hashlib.sha256(raw[-1].encode()).hexdigest()

        # And a reader that has never cached anything agrees.
        assert IncidentLog(path)._last_line_hash() == cached

    def test_an_external_append_is_detected(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        log = IncidentLog(path)
        log.record(IncidentSeverity.SERIOUS_HARM, "estop", "first", {})
        stale = log._last_line_hash()  # warms the cache

        # A second writer appends underneath us.
        other = IncidentLog(path)
        other.record(IncidentSeverity.SERIOUS_HARM, "estop", "from the other writer", {})

        # The first log must not chain onto its stale hash.
        assert log._last_line_hash() != stale
        log.record(IncidentSeverity.SERIOUS_HARM, "estop", "third", {})

        raw = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        prev = ""
        for line, row in zip(
            [x for x in path.read_text().splitlines() if x.strip()], raw, strict=True
        ):
            assert row["prev_sha256"] == prev
            prev = hashlib.sha256(line.encode()).hexdigest()
        assert len(raw) == 3
