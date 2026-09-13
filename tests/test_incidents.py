"""Tests for castor.incidents — serious-incident log and monitoring report."""

import json

from castor.incidents import (
    INCIDENT_SCHEMA_VERSION,
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
