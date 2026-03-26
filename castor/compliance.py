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

SPEC_VERSION = "2.1"

# All RCAN spec versions that this runtime can accept (for inbound message validation).
# RCAN v2.1 is a clean break — v1.x messages are rejected (no version negotiation).
# Both MAJOR.MINOR ("2.1") and MAJOR.MINOR.PATCH ("2.1.0") formats are accepted.
ACCEPTED_RCAN_VERSIONS: tuple[str, ...] = (
    "2.1",
    "2.1.0",
