"""castor.incidents — serious-incident log and post-market monitoring report.

Provides an append-only, hash-chained JSONL incident log, a report generator,
and the bookkeeping that says which incidents have been filed and which are
overdue.

Two different clocks are involved and the module keeps them apart:

* The serious-incident reporting clock, which the OpenCastor crosswalk maps to
  EU AI Act Art. 73 and which runs from the moment the provider became aware
  of the incident, not from the moment it happened. That is why every record
  carries ``discovered_at`` as a field distinct from ``timestamp``.
* Post-market monitoring in general, which the crosswalk maps to a separate
  article cited once, in the report's post_market_monitoring_note. That is a
  standing obligation to run a monitoring system, not a deadline.

The day figures below are this project's own summary of the commonly cited
windows. They are a crosswalk, not statutory text, and no statutory text is
quoted anywhere in this module. Nothing here is "verified" by software: a
submission is filed, and a record is signed.

Usage:
    from castor.incidents import IncidentLog, IncidentSeverity, generate_report

    log = IncidentLog()  # default: ~/.opencastor/incidents.jsonl
    log.record(IncidentSeverity.SERIOUS_HARM, "estop", "ESTOP triggered", state)
    report = generate_report(log)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("OpenCastor.Incidents")

INCIDENT_SCHEMA_VERSION = "rcan-incidents-v2"

#: Env override for the incident log location. Since incidents are filed by the
#: system rather than by hand, anything that trips a stop now writes to this
#: path. A test run, a fixture robot or a second instance on one machine sets
#: CASTOR_INCIDENT_LOG so it does not append to the operator's own log.
INCIDENT_LOG_ENV = "CASTOR_INCIDENT_LOG"
DEFAULT_INCIDENT_LOG_PATH = Path(
    os.environ.get(INCIDENT_LOG_ENV) or (Path.home() / ".opencastor" / "incidents.jsonl")
)

# OpenCastor crosswalk of the serious-incident reporting windows. Every clock
# runs from discovery (``discovered_at``), never from the event timestamp.
# These are the project's own figures, summarised, not quoted.
REPORTING_DEADLINES_DAYS = {
    "critical_infrastructure": 2,
    "death": 10,
    "serious_harm": 15,
}

# Where the figures come from, carried in every generated report so a reader
# never has to guess whether software is asserting law.
CROSSWALK_ATTRIBUTION = (
    "Day figures are the OpenCastor crosswalk's own summary of the commonly "
    "cited serious-incident reporting windows (EU AI Act Art. 73). They are "
    "not statutory text and are not a legal determination. Conformance is "
    "self-asserted; conformance is not certification."
)


class IncidentSeverity(str, Enum):
    """Serious-incident categories the short reporting windows attach to."""

    CRITICAL_INFRASTRUCTURE = "critical_infrastructure"  # 2 days
    DEATH = "death"  # 10 days
    SERIOUS_HARM = "serious_harm"  # 15 days


# Records written before the categories above existed, and the category they
# now read as. Both legacy names carried the general serious-incident window.
_LEGACY_SEVERITY_ALIASES = {
    "life_health": IncidentSeverity.SERIOUS_HARM.value,
    "other": IncidentSeverity.SERIOUS_HARM.value,
}


def normalize_severity(value: Any) -> str:
    """Coerce a severity value (enum, new name, or legacy name) to a category."""
    raw = value.value if isinstance(value, IncidentSeverity) else str(value)
    raw = _LEGACY_SEVERITY_ALIASES.get(raw, raw)
    try:
        return IncidentSeverity(raw).value
    except ValueError:
        return IncidentSeverity.SERIOUS_HARM.value


def deadline_days_for(severity: Any) -> int:
    """Days from discovery allowed for this category, per the project crosswalk."""
    return REPORTING_DEADLINES_DAYS[normalize_severity(severity)]


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def days_to_deadline(incident: dict[str, Any], now: datetime | None = None) -> float | None:
    """Days remaining before this incident's filing deadline. Negative = overdue.

    Returns None when the record carries no usable discovery time.
    """
    now = now or datetime.now(timezone.utc)
    discovered = _parse_iso(incident.get("discovered_at")) or _parse_iso(
        incident.get("timestamp")
    )
    if discovered is None:
        return None
    days = incident.get("reporting_deadline_days")
    if not isinstance(days, (int, float)):
        days = deadline_days_for(incident.get("severity"))
    return ((discovered + timedelta(days=float(days))) - now).total_seconds() / 86400.0


def is_overdue(incident: dict[str, Any], now: datetime | None = None) -> bool:
    """True when an unfiled incident has passed its deadline."""
    if incident.get("reported"):
        return False
    remaining = days_to_deadline(incident, now=now)
    return remaining is not None and remaining < 0


class IncidentLog:
    """Append-only, hash-chained JSONL incident log.

    Nothing is ever edited in place. Filing an incident appends a new
    ``report_submission`` line that references the incident id; reading folds
    those lines back onto the incident records.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_INCIDENT_LOG_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # -- writing ---------------------------------------------------------

    def _last_line_hash(self) -> str:
        """sha256 of the last raw line, or the empty-chain marker."""
        if not self._path.exists():
            return ""
        last = ""
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return ""
        return hashlib.sha256(last.encode()).hexdigest()

    def _append(self, entry: dict[str, Any]) -> None:
        entry["prev_sha256"] = self._last_line_hash()
        with open(self._path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def record(
        self,
        severity: IncidentSeverity | str,
        category: str,
        description: str,
        system_state: dict[str, Any],
        discovered_at: str | datetime | None = None,
        source: str = "manual",
    ) -> str:
        """Record a new incident. Returns the incident ID (UUID4).

        Args:
            discovered_at: When the provider became aware. The reporting clock
                runs from here. When omitted it defaults to the event time and
                the record is stamped ``unknown_discovery: true`` so a reader
                can see the clock is an assumption rather than a fact.
            source: What filed it — "estop", "controlled_stop", "guardian_veto",
                "cli", and so on.
        """
        incident_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        sev = normalize_severity(severity)

        if isinstance(discovered_at, datetime):
            discovered_iso: str = discovered_at.isoformat()
            unknown_discovery = False
        elif discovered_at:
            discovered_iso = str(discovered_at)
            unknown_discovery = False
        else:
            discovered_iso = now_iso
            unknown_discovery = True

        deadline_days = REPORTING_DEADLINES_DAYS[sev]
        discovered_dt = _parse_iso(discovered_iso) or datetime.now(timezone.utc)

        entry = {
            "id": incident_id,
            "record_type": "incident",
            "schema": INCIDENT_SCHEMA_VERSION,
            "timestamp": now_iso,
            "discovered_at": discovered_iso,
            "unknown_discovery": unknown_discovery,
            "unknown_discovery_note": (
                "discovered_at was not supplied; the reporting clock is being run "
                "from the event time instead"
            )
            if unknown_discovery
            else "",
            "severity": sev,
            "category": category,
            "description": description,
            "system_state": system_state,
            "source": source,
            "reported": False,
            "reporting_deadline_days": deadline_days,
            "reporting_deadline": (discovered_dt + timedelta(days=deadline_days)).isoformat(),
        }
        self._append(entry)
        return incident_id

    def mark_reported(
        self,
        incident_ids: list[str],
        receipt: dict[str, Any] | None = None,
        submitted_at: str | None = None,
    ) -> str:
        """Append a chained submission line stamping these incidents as filed.

        Never edits an existing line. Returns the submission record id.
        """
        submission_id = str(uuid.uuid4())
        self._append(
            {
                "id": submission_id,
                "record_type": "report_submission",
                "schema": INCIDENT_SCHEMA_VERSION,
                "incident_ids": list(incident_ids),
                "reported": True,
                "reported_at": submitted_at or datetime.now(timezone.utc).isoformat(),
                "receipt": receipt or {},
            }
        )
        return submission_id

    # -- reading ---------------------------------------------------------

    def list_raw(self) -> list[dict[str, Any]]:
        """Return every line in the log, incidents and submissions alike."""
        if not self._path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return rows

    def list_incidents(self) -> list[dict[str, Any]]:
        """Return incidents oldest first, with submission lines folded in."""
        rows = self.list_raw()
        incidents: list[dict[str, Any]] = []
        submissions: list[dict[str, Any]] = []
        for row in rows:
            if row.get("record_type") == "report_submission":
                submissions.append(row)
            else:
                # Legacy lines have no record_type; they are incidents.
                inc = dict(row)
                inc.setdefault("record_type", "incident")
                inc.setdefault("discovered_at", inc.get("timestamp"))
                inc.setdefault("unknown_discovery", True)
                inc["severity"] = normalize_severity(inc.get("severity"))
                inc.setdefault("source", "manual")
                incidents.append(inc)

        filed: dict[str, dict[str, Any]] = {}
        for sub in submissions:
            for inc_id in sub.get("incident_ids") or []:
                filed[inc_id] = sub
        for inc in incidents:
            sub = filed.get(inc.get("id"))
            if sub:
                inc["reported"] = True
                inc["reported_at"] = sub.get("reported_at")
                inc["report_receipt"] = sub.get("receipt", {})
        return incidents

    def unreported_incidents(self) -> list[dict[str, Any]]:
        return [i for i in self.list_incidents() if not i.get("reported")]

    def overdue_incidents(self, now: datetime | None = None) -> list[dict[str, Any]]:
        return [i for i in self.list_incidents() if is_overdue(i, now=now)]


def generate_report(log: IncidentLog, now: datetime | None = None) -> dict[str, Any]:
    """Generate a post-market monitoring report with serious-incident bookkeeping."""
    now = now or datetime.now(timezone.utc)
    incidents = log.list_incidents()
    by_severity: dict[str, int] = {}
    for inc in incidents:
        sev = normalize_severity(inc.get("severity"))
        by_severity[sev] = by_severity.get(sev, 0) + 1

    unreported = [i for i in incidents if not i.get("reported")]
    overdue = [i for i in incidents if is_overdue(i, now=now)]

    return {
        "schema": INCIDENT_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "total_incidents": len(incidents),
        "incidents_by_severity": by_severity,
        "reported_count": len(incidents) - len(unreported),
        "unreported_count": len(unreported),
        "overdue_count": len(overdue),
        "overdue_incident_ids": [i.get("id") for i in overdue],
        # The serious-incident clock. Every value under this key cites Art. 73.
        "serious_incident_reporting": {
            "basis": "EU AI Act Art. 73 (OpenCastor crosswalk)",
            "clock_starts": "discovered_at (provider awareness), not the event timestamp",
            "deadlines_days": dict(REPORTING_DEADLINES_DAYS),
            "note": (
                "Art. 73 serious-incident reporting windows, as summarised by the "
                "OpenCastor crosswalk: critical infrastructure 2 days, death 10 days, "
                "other serious harm 15 days, each running from the provider becoming "
                "aware. Filing status below is bookkeeping only; nothing here is a "
                "legal determination."
            ),
            "attribution": CROSSWALK_ATTRIBUTION,
        },
        "reporting_deadlines": dict(REPORTING_DEADLINES_DAYS),
        # The standing post-market monitoring obligation, which is a different
        # thing from the serious-incident clock above.
        "post_market_monitoring_note": (
            "Post-market monitoring (EU AI Act Art. 72, OpenCastor crosswalk) is the "
            "standing obligation to operate a monitoring system. It carries no "
            "per-incident deadline; the deadlines live under "
            "serious_incident_reporting."
        ),
        "incidents": incidents,
    }


def file_incident(
    severity: IncidentSeverity | str,
    category: str,
    description: str,
    system_state: dict[str, Any] | None = None,
    discovered_at: str | datetime | None = None,
    source: str = "runtime",
    path: Path | str | None = None,
) -> str | None:
    """File an incident from a safety event, best effort.

    A record write must never be able to stop a stop from happening, so every
    failure is swallowed and logged. Returns the incident id, or None when the
    write failed.
    """
    try:
        return IncidentLog(path).record(
            severity=severity,
            category=category,
            description=description,
            system_state=system_state or {},
            discovered_at=discovered_at,
            source=source,
        )
    except Exception as exc:  # never in the path of a stop
        logger.error("Could not file incident for %s (%s): %s", category, source, exc)
        return None
