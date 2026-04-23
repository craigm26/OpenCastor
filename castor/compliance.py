"""castor.compliance — RCAN 3.0 compliance constants, ComplianceReport, and report generation.

SPEC_VERSION: the RCAN spec version this OpenCastor build targets.
ACCEPTED_RCAN_VERSIONS: versions accepted in inbound messages (hard-cut: 3.x only — 2.x no longer accepted per ecosystem policy).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from typing import IO, Any

# ─────────────────────────────────────────────────────────────────────────────
# Version constants
# ─────────────────────────────────────────────────────────────────────────────

SPEC_VERSION: str = "3.0"

ACCEPTED_RCAN_VERSIONS: tuple[str, ...] = ("3.0",)


def is_accepted_version(version: str) -> bool:
    """Return True if *version* is RCAN 3.x (hard-cut — 2.x rejected).

    Accepts any version whose major component is exactly 3 (forward-compatible
    within RCAN 3.x). Future majors (4.x+) require an explicit opencastor bump
    and are NOT granted a free pass here.
    """
    try:
        parts = str(version).split(".")
        if len(parts) < 2:
            return False
        major = int(parts[0])
        int(parts[1])  # ensure minor is numeric
        return major == 3
    except (ValueError, AttributeError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# ComplianceReport dataclass
# ─────────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class ComplianceReport:
    """Structured compliance report for a robot config."""

    spec_version: str
    rcan_py_version: str | None
    opencastor_version: str
    checks: list[dict[str, Any]]
    compliant: bool
    generated_at: str

    # Computed in __post_init__
    passed: int = dataclasses.field(init=False)
    warned: int = dataclasses.field(init=False)
    failed: int = dataclasses.field(init=False)
    score: int = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self.passed = sum(1 for c in self.checks if c.get("status") == "pass")
        self.warned = sum(1 for c in self.checks if c.get("status") == "warn")
        self.failed = sum(1 for c in self.checks if c.get("status") == "fail")
        # Score: start at 100, -10 per fail, -3 per warn
        self.score = max(0, 100 - self.failed * 10 - self.warned * 3)

    def to_dict(self) -> dict[str, Any]:
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


# ─────────────────────────────────────────────────────────────────────────────
# Printing helpers
# ─────────────────────────────────────────────────────────────────────────────


def print_report_text(report: ComplianceReport, file: IO[str] = sys.stdout) -> None:
    status_line = "✅ COMPLIANT" if report.compliant else "❌ NON-COMPLIANT"
    print(f"\nRCAN v{report.spec_version} Compliance Report", file=file)
    print("=" * 40, file=file)
    print(f"Status: {status_line}", file=file)
    print(
        f"Score:  {report.score}/100  (pass={report.passed} warn={report.warned} fail={report.failed})",
        file=file,
    )
    print(file=file)
    for check in report.checks:
        icon = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}.get(check.get("status", ""), "❓")
        print(f"  {icon} [{check.get('check_id', '?')}] {check.get('message', '')}", file=file)
        if check.get("detail"):
            print(f"       {check['detail']}", file=file)
    print(file=file)


def print_report_json(report: ComplianceReport, file: IO[str] = sys.stdout) -> None:
    print(json.dumps(report.to_dict(), indent=2), file=file)


# ─────────────────────────────────────────────────────────────────────────────
# Report generation
# ─────────────────────────────────────────────────────────────────────────────


def _get_rcan_py_version() -> str | None:
    try:
        from rcan.version import SDK_VERSION

        return SDK_VERSION
    except Exception:
        try:
            import importlib.metadata

            return importlib.metadata.version("rcan")
        except Exception:
            return None


def _get_opencastor_version() -> str:
    try:
        import castor

        return getattr(castor, "__version__", "unknown")
    except Exception:
        return "unknown"


def _load_config(config_path: str) -> dict[str, Any]:
    import yaml

    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def _run_conformance_checks(config: dict[str, Any], config_path: str) -> list[dict[str, Any]]:
    """Run all conformance checks via ConformanceChecker."""
    try:
        from castor.conformance import ConformanceChecker

        checker = ConformanceChecker(config, config_path=config_path)
        results = checker.run_all()
        return [
            {
                "check_id": r.check_id,
                "status": r.status,
                "message": r.detail,
                "detail": r.fix if hasattr(r, "fix") else None,
            }
            for r in results
        ]
    except Exception as exc:
        return [
            {"check_id": "conformance.error", "status": "fail", "message": str(exc), "detail": None}
        ]


def generate_report(config_path: str) -> ComplianceReport:
    """Load config and return a ComplianceReport."""
    import datetime

    if not __import__("os").path.exists(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")

    config = _load_config(config_path)
    checks = _run_conformance_checks(config, config_path)
    compliant = all(c.get("status") != "fail" for c in checks)

    return ComplianceReport(
        spec_version=SPEC_VERSION,
        rcan_py_version=_get_rcan_py_version(),
        opencastor_version=_get_opencastor_version(),
        checks=checks,
        compliant=compliant,
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


__all__ = [
    "SPEC_VERSION",
    "ACCEPTED_RCAN_VERSIONS",
    "is_accepted_version",
    "ComplianceReport",
    "print_report_text",
    "print_report_json",
    "generate_report",
    "_get_rcan_py_version",
    "_get_opencastor_version",
    "_run_conformance_checks",
    "_load_config",
]
