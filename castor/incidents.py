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

The active log is bounded. When it passes ``CASTOR_INCIDENT_LOG_MAX_BYTES``
(default a few MB) it rolls to ``incidents.<date>.jsonl`` and the new file
opens with a carry-over line whose ``prev_sha256`` is the rotated file's last
line, so the hash chain crosses the boundary. Every reader here, and so both
``castor incidents list`` and the submitter, reads rotated files before the
active one.

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

import contextlib
import hashlib
import json
import logging
import os
import time
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

#: Rotation bound, read from the same place the module already reads its log
#: location: the environment. A robot that stops often files often, so the
#: active file is capped and rolled to ``incidents.<date>.jsonl`` rather than
#: growing without bound on a device with a small card. The default is a
#: generated default in the sense that matters here: nothing has to be written
#: anywhere for the bound to apply, and an operator who wants a different one
#: sets CASTOR_INCIDENT_LOG_MAX_BYTES next to CASTOR_INCIDENT_LOG.
INCIDENT_LOG_MAX_BYTES_ENV = "CASTOR_INCIDENT_LOG_MAX_BYTES"
DEFAULT_INCIDENT_LOG_MAX_BYTES = 4 * 1024 * 1024

#: The first line of every file written by a rotation. It carries the rotated
#: file's last-line hash as its ``prev_sha256``, so the chain crosses the
#: rotation and a reader can follow it from the oldest rotated file to the
#: active one.
#:
#: A note for whoever writes the chain verifier this module does not yet have:
#: this line is the one place where a hash points at something outside the file
#: it sits in, so a line that merely SAYS ``log_rotation`` must never be enough
#: to excuse a discontinuity. Anything that can append to the log can write one.
#: A carry-over line is only a carry-over line when ``rotated_to`` names a file
#: that is actually there and that file's last line hashes to this line's
#: ``prev_sha256``; anything else is a break, and should be reported as one.
ROTATION_RECORD_TYPE = "log_rotation"

#: How long a writer waits for the chain lock before appending without it. A
#: record write must never be able to keep a robot from stopping, so the wait
#: is bounded; past it the append goes ahead unlocked, which is exactly the
#: behaviour this module had before the lock existed.
CHAIN_LOCK_TIMEOUT_S = 5.0


@contextlib.contextmanager
def _chain_lock(path: Path, timeout: float = CHAIN_LOCK_TIMEOUT_S):
    """Hold an advisory lock on ``<path>.lock`` while the chain tail is extended.

    Reading the last line and appending the next one are two operations, and
    another writer's append can land between them: both lines then carry the
    same ``prev_sha256`` and the chain forks. That is the same break the audit
    log takes an flock to avoid (``castor/audit.py``), and the incident log has
    the same two writers, the runtime filing from a stop and a
    ``castor incidents report --submit`` stamping in another process, so it
    takes the same kind of lock. Rotation runs under it too, so a rename cannot
    land between another writer's tail read and its append.

    Best effort and bounded by design: on a platform without ``fcntl``, on a
    filesystem that refuses the lock, or when a holder does not let go inside
    ``timeout``, the block still runs.
    """
    lock_file = None
    try:
        import fcntl

        lock_file = open(str(path) + ".lock", "a")
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    logger.warning(
                        "Incident chain lock held elsewhere for %.1fs; appending without it",
                        timeout,
                    )
                    lock_file.close()
                    lock_file = None
                    break
                time.sleep(0.005)
    except Exception as exc:  # pragma: no cover - platform dependent
        logger.debug("Incident chain lock unavailable (%s); continuing", exc)
        if lock_file is not None:
            lock_file.close()
            lock_file = None
    try:
        yield
    finally:
        if lock_file is not None:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:  # pragma: no cover - platform dependent
                pass
            lock_file.close()


def _configured_max_bytes() -> int:
    """Rotation bound in bytes. A bad value falls back to the default."""
    raw = os.environ.get(INCIDENT_LOG_MAX_BYTES_ENV)
    if raw is None:
        return DEFAULT_INCIDENT_LOG_MAX_BYTES
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not a byte count; using the default bound", INCIDENT_LOG_MAX_BYTES_ENV, raw
        )
        return DEFAULT_INCIDENT_LOG_MAX_BYTES
    if value <= 0:
        return DEFAULT_INCIDENT_LOG_MAX_BYTES
    return value

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

    def __init__(self, path: Path | str | None = None, max_bytes: int | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_INCIDENT_LOG_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_bytes = int(max_bytes) if max_bytes else _configured_max_bytes()
        # Cached tail of the chain, so an append does not re-read the whole
        # file. ``_cached_stat`` is the file's identity and length as the cache
        # was taken; when it no longer matches, somebody else wrote and the
        # cache is thrown away rather than used to fork the chain.
        self._cached_hash: str | None = None
        self._cached_stat: tuple[int, int, int, int] | None = None

    # -- writing ---------------------------------------------------------

    def _size(self) -> int | None:
        try:
            return self._path.stat().st_size
        except OSError:
            return None

    def _stat_token(self) -> tuple[int, int, int, int] | None:
        """What the tail cache is keyed on: which file, and how much of it.

        Size alone is not enough. A rotation puts a NEW file at this path, and
        that file grows back through the same sizes the old one had, so a
        writer holding a cache from before the rotation can find the size it
        remembers and chain onto a hash from the rotated-away file. The inode
        changes on rotation, and mtime moves on any write, so the two of them
        together with the size say "the same file, still exactly as I left it".
        """
        try:
            st = self._path.stat()
        except OSError:
            return None
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)

    def _read_last_line(self) -> str:
        """The last non-empty raw line of the active file, reading from the tail."""
        try:
            size = self._path.stat().st_size
        except OSError:
            return ""
        if size == 0:
            return ""
        window = 65536
        with open(self._path, "rb") as f:
            while True:
                start = max(0, size - window)
                f.seek(start)
                chunk = f.read(size - start)
                lines = [ln for ln in chunk.split(b"\n") if ln.strip()]
                if lines and (start == 0 or len(lines) > 1):
                    return lines[-1].decode("utf-8", "replace").strip()
                if start == 0:
                    return ""
                window *= 4

    def _last_line_hash(self) -> str:
        """sha256 of the last raw line, or the empty-chain marker.

        Cached in memory after the first read and kept current across appends.
        The cache is only trusted while the file is the same file, the same
        length and untouched since the cache was taken (see ``_stat_token``);
        anything else sends this back to the file's tail rather than chaining
        onto a stale hash.
        """
        token = self._stat_token()
        if self._cached_hash is not None and self._cached_stat == token:
            return self._cached_hash
        last = self._read_last_line()
        digest = hashlib.sha256(last.encode()).hexdigest() if last else ""
        self._cached_hash = digest
        self._cached_stat = token
        return digest

    # -- rotation --------------------------------------------------------

    def _rotated_path(self) -> Path:
        """Name for the file the active log is about to become."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stem = self._path.name[: -len(self._path.suffix)] if self._path.suffix else self._path.name
        suffix = self._path.suffix or ".jsonl"
        candidate = self._path.with_name(f"{stem}.{stamp}{suffix}")
        n = 1
        while candidate.exists():
            candidate = self._path.with_name(f"{stem}.{stamp}_{n}{suffix}")
            n += 1
        return candidate

    def rotated_paths(self) -> list[Path]:
        """Every rotated file for this log, oldest first."""
        stem = self._path.name[: -len(self._path.suffix)] if self._path.suffix else self._path.name
        suffix = self._path.suffix or ".jsonl"
        found = [
            p
            for p in self._path.parent.glob(f"{stem}.*{suffix}")
            if p != self._path and p.is_file()
        ]

        def _key(p: Path) -> tuple[float, str, int]:
            # Second and third keys are the date stamp and the within-the-day
            # counter, read as a NUMBER. Sorting those names as text puts _10
            # before _2, and mtimes tie on a filesystem with coarse timestamps,
            # which would hand a reader the rotated files out of write order.
            middle = p.name[len(stem) + 1 : -len(suffix)]
            stamp, _, tail = middle.partition("_")
            index = int(tail) if tail.isdigit() else 0
            try:
                return (p.stat().st_mtime, stamp, index)
            except OSError:
                return (0.0, stamp, index)

        return sorted(found, key=_key)

    def _rotate_if_needed(self) -> None:
        """Roll the active file when it has passed the bound. Never raises."""
        size = self._size()
        if size is None or size < self._max_bytes:
            return
        carried = self._last_line_hash()
        target = self._rotated_path()
        try:
            os.replace(self._path, target)
        except OSError as exc:  # a rotation failure must not lose the record
            logger.error("Could not rotate the incident log: %s", exc)
            return
        self._cached_hash = None
        self._cached_stat = None
        # The new file opens with a carry-over line whose prev_sha256 is the
        # rotated file's last line, so the chain crosses the rotation.
        carry = {
            "id": str(uuid.uuid4()),
            "record_type": ROTATION_RECORD_TYPE,
            "schema": INCIDENT_SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rotated_to": target.name,
            "rotated_bytes": size,
            "note": (
                "The incident log passed its size bound and was rotated. "
                "prev_sha256 is the last line of the rotated file, so the hash "
                "chain continues across this boundary."
            ),
        }
        carry["prev_sha256"] = carried
        carry_line = json.dumps(carry, default=str)
        with open(self._path, "a") as f:
            f.write(carry_line + "\n")
        self._cached_hash = hashlib.sha256(carry_line.encode()).hexdigest()
        self._cached_stat = self._stat_token()
        logger.info("Incident log rotated to %s (%d bytes)", target.name, size)

    def _ends_with_newline(self) -> bool:
        """True when the file is empty, absent, or already terminated.

        A process killed mid-append leaves a last line with no newline on it.
        Appending straight onto that byte would put two JSON objects on one
        physical line, and a reader drops the whole line, which loses the
        record that WAS written as well as the one being written now.
        """
        try:
            size = self._path.stat().st_size
        except OSError:
            return True
        if size == 0:
            return True
        try:
            with open(self._path, "rb") as f:
                f.seek(size - 1)
                return f.read(1) == b"\n"
        except OSError:
            return True

    def _append(self, entry: dict[str, Any]) -> None:
        # Rotate, read the tail and append under one cross-process lock. The
        # in-memory cache is still stat-checked inside it, which is what keeps
        # a second writer in THIS process honest; the lock is what keeps a
        # second PROCESS from chaining onto the same line.
        with _chain_lock(self._path):
            self._rotate_if_needed()
            entry["prev_sha256"] = self._last_line_hash()
            line = json.dumps(entry, default=str)
            # A torn tail gets its terminator back before anything follows it.
            prefix = "" if self._ends_with_newline() else "\n"
            with open(self._path, "a") as f:
                f.write(prefix + line + "\n")
            self._cached_hash = hashlib.sha256(line.encode()).hexdigest()
            self._cached_stat = self._stat_token()

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

    @staticmethod
    def _read_rows(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            return []
        return rows

    def list_raw(self) -> list[dict[str, Any]]:
        """Every line of the log, oldest first, ACROSS rotations.

        Rotated files are read before the active one, so `castor incidents
        list` and the submitter both see an incident that was filed before the
        log rolled.
        """
        rows: list[dict[str, Any]] = []
        for rotated in self.rotated_paths():
            rows.extend(self._read_rows(rotated))
        if self._path.exists():
            rows.extend(self._read_rows(self._path))
        return rows

    def list_incidents(self) -> list[dict[str, Any]]:
        """Return incidents oldest first, with submission lines folded in."""
        rows = self.list_raw()
        incidents: list[dict[str, Any]] = []
        submissions: list[dict[str, Any]] = []
        for row in rows:
            if row.get("record_type") == "report_submission":
                submissions.append(row)
            elif row.get("record_type") == ROTATION_RECORD_TYPE:
                # Chain bookkeeping, not an incident.
                continue
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
