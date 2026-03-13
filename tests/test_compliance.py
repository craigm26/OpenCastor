"""Tests for castor.compliance module."""

from __future__ import annotations

import json
import textwrap
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from castor.compliance import (
    SPEC_VERSION,
    ComplianceReport,
    _get_opencastor_version,
    _get_rcan_py_version,
    generate_report,
    print_report_json,
    print_report_text,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = textwrap.dedent(
    """\
    agent:
      name: test-bot
      version: "1.0"
    """
)

PASSING_CHECKS = [
    {
        "check_id": "safety.estop_configured",
        "status": "pass",
        "message": "E-stop configured",
        "detail": None,
    },
    {
        "check_id": "protocol.rcan_v12",
        "status": "pass",
        "message": "RCAN v1.2 compliant",
        "detail": None,
    },
]

FAILING_CHECKS = [
    {
        "check_id": "safety.estop_configured",
        "status": "fail",
        "message": "E-stop not configured",
        "detail": "Add estop to config",
    },
    {
        "check_id": "protocol.rcan_v12",
        "status": "pass",
        "message": "RCAN v1.2 compliant",
        "detail": None,
    },
]


def _make_report(checks=None, compliant=True):
    if checks is None:
        checks = PASSING_CHECKS
    return ComplianceReport(
        spec_version=SPEC_VERSION,
        rcan_py_version="0.3.0",
        opencastor_version="2026.3.3.0",
        checks=checks,
        compliant=compliant,
        generated_at="2026-03-06T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_compliance_report_aggregates_counts():
    """ComplianceReport.__post_init__ correctly counts pass/warn/fail."""
    checks = [
        {"check_id": "a", "status": "pass", "message": "", "detail": None},
        {"check_id": "b", "status": "warn", "message": "", "detail": None},
        {"check_id": "c", "status": "fail", "message": "", "detail": None},
    ]
    report = _make_report(checks=checks, compliant=False)
    assert report.passed == 1
    assert report.warned == 1
    assert report.failed == 1
    assert report.score == 87  # 100 - 10 - 3


def test_compliance_report_to_dict():
    """to_dict() includes all expected keys."""
    report = _make_report()
    d = report.to_dict()
    assert d["spec_version"] == SPEC_VERSION
    assert "checks" in d
    assert "summary" in d
    assert d["compliant"] is True


def test_print_report_text_compliant(capsys):
    """print_report_text prints a COMPLIANT result for a passing report."""
    report = _make_report(checks=PASSING_CHECKS, compliant=True)
    buf = StringIO()
    print_report_text(report, file=buf)
    out = buf.getvalue()
    assert "COMPLIANT" in out
    assert "✅" in out


def test_print_report_text_non_compliant():
    """print_report_text shows NON-COMPLIANT for a failing report."""
    report = _make_report(checks=FAILING_CHECKS, compliant=False)
    buf = StringIO()
    print_report_text(report, file=buf)
    out = buf.getvalue()
    assert "NON-COMPLIANT" in out


def test_print_report_json_valid_json():
    """print_report_json outputs valid JSON."""
    report = _make_report()
    buf = StringIO()
    print_report_json(report, file=buf)
    data = json.loads(buf.getvalue())
    assert data["spec_version"] == SPEC_VERSION
    assert data["compliant"] is True


def test_generate_report_uses_config(tmp_path):
    """generate_report loads the config and runs checks."""
    config_file = tmp_path / "robot.rcan.yaml"
    config_file.write_text(MINIMAL_CONFIG)

    [
        MagicMock(check_id="c1", status="pass", message="ok", detail=None),
    ]

    with (
        patch(
            "castor.compliance._run_conformance_checks",
            return_value=[{"check_id": "c1", "status": "pass", "message": "ok", "detail": None}],
        ),
        patch("castor.compliance._get_rcan_py_version", return_value="0.3.0"),
        patch("castor.compliance._get_opencastor_version", return_value="2026.3.3.0"),
    ):
        report = generate_report(config_path=str(config_file))

    assert report.spec_version == SPEC_VERSION
    assert report.compliant is True
    assert len(report.checks) == 1


def test_generate_report_file_not_found():
    """generate_report raises FileNotFoundError for missing config."""
    with pytest.raises(FileNotFoundError):
        generate_report(config_path="/nonexistent/robot.rcan.yaml")


def test_get_rcan_py_version_returns_string_or_none():
    version = _get_rcan_py_version()
    assert version is None or isinstance(version, str)


def test_get_opencastor_version_returns_string():
    version = _get_opencastor_version()
    assert isinstance(version, str)
    assert len(version) > 0
