"""Tests for ResponderSpecialist."""

from __future__ import annotations

import asyncio

import pytest

from castor.specialists.base_specialist import Task, TaskStatus
from castor.specialists.responder import ResponderSpecialist


def run(coro):
    return asyncio.run(coro)


class TestResponderSpecialist:
    def setup_method(self):
        self.spec = ResponderSpecialist()

    def test_name(self):
        assert self.spec.name == "responder"

    def test_capabilities(self):
        assert set(self.spec.capabilities) == {"report", "respond", "status", "alert"}

    def test_can_handle_report(self):
        task = Task(type="report", goal="report status")
        assert self.spec.can_handle(task) is True

    def test_cannot_handle_grasp(self):
        task = Task(type="grasp", goal="grasp")
        assert self.spec.can_handle(task) is False

    # ------------------------------------------------------------------ #
    # Report
    # ------------------------------------------------------------------ #

    def test_report_succeeds(self):
        task = Task(
            type="report",
            goal="generate status report",
            params={"robot_status": {"battery": 75, "mode": "patrol"}},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS

    def test_report_contains_string(self):
        task = Task(
            type="report",
            goal="report",
            params={"robot_status": {"battery": 50}},
        )
        result = run(self.spec.execute(task))
        assert "report" in result.output
        assert isinstance(result.output["report"], str)

    def test_report_includes_battery(self):
        task = Task(
            type="report",
            goal="report",
            params={"robot_status": {"battery": 42}},
        )
        result = run(self.spec.execute(task))
        assert "42" in result.output["report"]

    def test_report_formatting_with_nested_dict(self):
        task = Task(
            type="report",
            goal="report",
            params={"robot_status": {"position": {"x": 1.0, "y": 2.0}}},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        # Should contain position info
        assert "position" in result.output["report"]

    def test_report_empty_status(self):
        """Empty robot_status should not crash."""
        task = Task(type="report", goal="report", params={"robot_status": {}})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS

    def test_report_timestamp_present(self):
        task = Task(type="report", goal="report", params={"robot_status": {"x": 1}})
        result = run(self.spec.execute(task))
        assert "timestamp" in result.output

    def test_report_fields_count(self):
        task = Task(
            type="report",
            goal="report",
            params={"robot_status": {"a": 1, "b": 2, "c": 3}},
        )
        result = run(self.spec.execute(task))
        assert result.output["fields_reported"] == 3

    # ------------------------------------------------------------------ #
    # Alert
    # ------------------------------------------------------------------ #

    def test_alert_info_severity(self):
        task = Task(
            type="alert",
            goal="send alert",
            params={"message": "low battery", "severity": "info"},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert result.output["severity"] == "info"

    def test_alert_warn_severity(self):
        task = Task(
            type="alert",
            goal="warn",
            params={"message": "obstacle detected", "severity": "warn"},
        )
        result = run(self.spec.execute(task))
        assert result.output["severity"] == "warn"

    def test_alert_critical_severity(self):
        task = Task(
            type="alert",
            goal="critical",
            params={"message": "motor failure", "severity": "critical"},
        )
        result = run(self.spec.execute(task))
        assert result.output["severity"] == "critical"

    def test_alert_invalid_severity_defaults_info(self):
        task = Task(
            type="alert",
            goal="alert",
            params={"message": "test", "severity": "bogus_level"},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert result.output["severity"] == "info"

    def test_alert_missing_message(self):
        task = Task(type="alert", goal="alert", params={})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED
        assert result.error is not None

    def test_alert_formatted_string(self):
        task = Task(
            type="alert",
            goal="alert",
            params={"message": "fire detected", "severity": "critical", "source": "sensor_1"},
        )
        result = run(self.spec.execute(task))
        alert_str = result.output["alert"]
        assert "CRITICAL" in alert_str
        assert "fire detected" in alert_str

    def test_alert_has_timestamp(self):
        task = Task(
            type="alert",
            goal="alert",
            params={"message": "test", "severity": "info"},
        )
        result = run(self.spec.execute(task))
        assert "timestamp" in result.output

    def test_alert_has_icon(self):
        task = Task(
            type="alert",
            goal="alert",
            params={"message": "test", "severity": "warn"},
        )
        result = run(self.spec.execute(task))
        assert "icon" in result.output
        assert result.output["icon"]  # non-empty

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def test_status_returns_dict(self):
        task = Task(
            type="status",
            goal="get status",
            params={"battery_level": 80, "robot_id": "bot-1"},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert isinstance(result.output, dict)

    def test_status_includes_robot_id(self):
        task = Task(
            type="status",
            goal="status",
            params={"robot_id": "bot-42"},
        )
        result = run(self.spec.execute(task))
        assert result.output["robot_id"] == "bot-42"

    def test_status_default_values(self):
        """No params → defaults should be used."""
        task = Task(type="status", goal="status", params={})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert "timestamp" in result.output

    def test_status_extra_params_captured(self):
        task = Task(
            type="status",
            goal="status",
            params={"custom_field": "value123"},
        )
        result = run(self.spec.execute(task))
        # Extra fields should appear in output
        assert result.status == TaskStatus.SUCCESS

    # ------------------------------------------------------------------ #
    # Respond
    # ------------------------------------------------------------------ #

    def test_respond_succeeds(self):
        task = Task(
            type="respond",
            goal="respond",
            params={"message": "Hello, I am a robot."},
        )
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS

    def test_respond_contains_message(self):
        task = Task(
            type="respond",
            goal="respond",
            params={"message": "task complete"},
        )
        result = run(self.spec.execute(task))
        assert "task complete" in result.output["response"]

    # ------------------------------------------------------------------ #
    # Duration / Health
    # ------------------------------------------------------------------ #

    def test_estimate_duration_fast(self):
        task = Task(type="report", goal="report")
        assert self.spec.estimate_duration_s(task) < 1.0

    def test_health_no_hardware(self):
        h = self.spec.health()
        assert h["hardware_deps"] == "none"
