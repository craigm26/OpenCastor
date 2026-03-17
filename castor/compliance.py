"""
OpenCastor Compliance Report — structured RCAN conformance reporting.

Provides a :class:`ComplianceReport` dataclass and helpers to generate,
display, and serialise compliance reports for a robot config.

Usage::

    from castor.compliance import generate_report, print_report_text

    report = generate_report(config_path="robot.rcan.yaml")
    print_report_text(report)
"""

from __future__ import annotations

import datetime
import json
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPEC_VERSION = "1.6"

# All RCAN spec versions that this runtime can accept (for inbound message validation).
# Robots running older spec versions are still interoperable; newer versions are warned.
# Both MAJOR.MINOR ("1.4") and MAJOR.MINOR.PATCH ("1.4.0") formats are accepted.
ACCEPTED_RCAN_VERSIONS: tuple[str, ...] = (
    "1.0",
    "1.0.0",
    "1.1",
    "1.1.0",
    "1.2",
    "1.2.0",
    "1.3",
    "1.3.0",
    "1.4",
    "1.4.0",
    "1.5",
    "1.5.0",
    "1.6",
    "1.6.0",
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ComplianceReport:
    """Structured RCAN conformance report."""

    spec_version: str
    rcan_py_version: str | None
    opencastor_version: str
    checks: list[dict]  # serialised ConformanceResult dicts from run_all_checks()
    compliant: bool
    generated_at: str  # ISO 8601

    # Convenience aggregates (computed post-init if not provided)
    passed: int = field(default=0)
    warned: int = field(default=0)
    failed: int = field(default=0)
    score: int = field(default=0)

    def __post_init__(self) -> None:
        if not self.passed and not self.warned and not self.failed:
            self.passed = sum(1 for c in self.checks if c.get("status") == "pass")
            self.warned = sum(1 for c in self.checks if c.get("status") == "warn")
            self.failed = sum(1 for c in self.checks if c.get("status") == "fail")
            self.score = max(0, 100 - self.failed * 10 - self.warned * 3)

    def to_dict(self) -> dict:
        return {
            "spec_version": self.spec_version,
            "rcan_py_version": self.rcan_py_version,
            "opencastor_version": self.opencastor_version,
            "checks": self.checks,
            "compliant": self.compliant,
            "generated_at": self.generated_at,
            "summary": {
                "passed": self.passed,
                "warned": self.warned,
                "failed": self.failed,
                "score": self.score,
            },
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_rcan_py_version() -> str | None:
    try:
        import rcan  # type: ignore[import]

        return getattr(rcan, "__version__", None)
    except ImportError:
        return None


def _get_opencastor_version() -> str:
    try:
        from castor import __version__

        return __version__
    except Exception:
        return "unknown"


def _load_config(config_path: str) -> dict:
    try:
        import yaml  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required: pip install pyyaml") from exc

    with open(config_path) as fh:
        return yaml.safe_load(fh) or {}


def _run_conformance_checks(config: dict, config_path: str) -> list[dict]:
    """Run OpenCastor conformance checks and return serialised results."""
    from castor.conformance import ConformanceChecker

    checker = ConformanceChecker(config, config_path=config_path)
    results = checker.run_all()
    return [
        {
            "check_id": r.check_id,
            "status": r.status,
            # ConformanceResult uses 'detail' as the human-readable text
            "message": getattr(r, "detail", getattr(r, "message", r.check_id)),
            "detail": getattr(r, "fix", None),
            "category": getattr(r, "category", ""),
        }
        for r in results
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_report(config_path: str | None = None) -> ComplianceReport:
    """
    Generate a :class:`ComplianceReport` for a robot config.

    Args:
        config_path: Path to the robot RCAN YAML config. Defaults to
                     ``robot.rcan.yaml`` in the current directory.

    Returns:
        A populated :class:`ComplianceReport`.
    """
    if config_path is None:
        config_path = "robot.rcan.yaml"

    config = _load_config(config_path)
    checks = _run_conformance_checks(config, config_path)
    failed_count = sum(1 for c in checks if c["status"] == "fail")
    compliant = failed_count == 0

    return ComplianceReport(
        spec_version=SPEC_VERSION,
        rcan_py_version=_get_rcan_py_version(),
        opencastor_version=_get_opencastor_version(),
        checks=checks,
        compliant=compliant,
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


def print_report_text(report: ComplianceReport, file=None) -> None:
    """Print a human-readable compliance report."""
    if file is None:
        file = sys.stdout

    status_icon = "✅" if report.compliant else "❌"
    print(f"\n{status_icon} RCAN Compliance Report", file=file)
    print(f"   Spec version     : {report.spec_version}", file=file)
    print(f"   OpenCastor       : {report.opencastor_version}", file=file)
    rcan_ver = report.rcan_py_version or "not installed"
    print(f"   rcan-py          : {rcan_ver}", file=file)
    print(f"   Generated at     : {report.generated_at}", file=file)
    print(f"   Score            : {report.score}/100", file=file)
    print(
        f"   Checks           : {report.passed} passed  "
        f"{report.warned} warnings  {report.failed} failures",
        file=file,
    )
    print("", file=file)

    _STATUS_ICON = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}

    for check in report.checks:
        icon = _STATUS_ICON.get(check["status"], "❓")
        print(f"  {icon}  [{check['check_id']}] {check['message']}", file=file)
        if check.get("detail") and check["status"] != "pass":
            print(f"        {check['detail']}", file=file)

    print("", file=file)
    if report.compliant:
        print("  Result: COMPLIANT ✅", file=file)
    else:
        print(f"  Result: NON-COMPLIANT ❌  ({report.failed} failure(s))", file=file)
    print("", file=file)


def print_report_json(report: ComplianceReport, file=None) -> None:
    """Print the compliance report as JSON."""
    if file is None:
        file = sys.stdout
    print(json.dumps(report.to_dict(), indent=2), file=file)
