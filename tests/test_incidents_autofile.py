"""OC-13: incidents are filed by the system, not by hand.

Covers the four behaviours the remediation item names:
  1. an estop files an incident, with discovered_at and the right category;
  2. the stop still happens when the incident write fails;
  3. a submission stamps reported and a second run submits nothing;
  4. a submit against an empty log is refused.

Plus the article relabel: the generated report's serious-incident block cites
Art. 73, and no value under the serious-incident keys cites Art. 72.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from castor.incidents import (
    REPORTING_DEADLINES_DAYS,
    IncidentLog,
    IncidentSeverity,
    days_to_deadline,
    file_incident,
    generate_report,
    is_overdue,
)


# ---------------------------------------------------------------------------
# 1. an estop files an incident
# ---------------------------------------------------------------------------


def _safety_layer(tmp_path):
    """Build a real SafetyLayer over a fresh CastorFS namespace."""
    from castor.fs import Namespace, PermissionTable, SafetyLayer

    return SafetyLayer(Namespace(), PermissionTable(), limits={"motor_rate_hz": 100.0})


def test_estop_files_an_incident(tmp_path, monkeypatch):
    log_path = tmp_path / "incidents.jsonl"
    monkeypatch.setattr("castor.incidents.DEFAULT_INCIDENT_LOG_PATH", log_path)

    fs = _safety_layer(tmp_path)
    assert fs.estop(principal="root", source="api", reason="obstacle at 0.1 m") is True

    entries = IncidentLog(log_path).list_incidents()
    assert len(entries) == 1
    inc = entries[0]
    assert inc["category"] == "estop"
    assert inc["source"] == "estop"
    assert inc["severity"] == IncidentSeverity.SERIOUS_HARM.value
    # discovered_at exists and is distinct from the event timestamp as a field
    assert "discovered_at" in inc
    assert inc["unknown_discovery"] is True
    assert inc["reporting_deadline_days"] == REPORTING_DEADLINES_DAYS["serious_harm"]
    assert inc["reported"] is False
    assert "obstacle at 0.1 m" in inc["description"]


def test_controlled_stop_files_an_incident(tmp_path, monkeypatch):
    log_path = tmp_path / "incidents.jsonl"
    monkeypatch.setattr("castor.incidents.DEFAULT_INCIDENT_LOG_PATH", log_path)

    fs = _safety_layer(tmp_path)
    assert fs.controlled_stop(principal="root", source="rcan", reason="operator asked") is True

    entries = IncidentLog(log_path).list_incidents()
    assert [e["category"] for e in entries] == ["controlled_stop"]


def test_guardian_veto_files_an_incident(tmp_path, monkeypatch):
    log_path = tmp_path / "incidents.jsonl"
    monkeypatch.setattr("castor.incidents.DEFAULT_INCIDENT_LOG_PATH", log_path)

    from castor.agents.guardian import GuardianAgent

    g = GuardianAgent()
    g.trigger_estop(reason="speed over limit")
    assert g.estop_active is True

    entries = IncidentLog(log_path).list_incidents()
    assert [e["category"] for e in entries] == ["guardian_estop"]
    assert entries[0]["source"] == "guardian_veto"


# ---------------------------------------------------------------------------
# 2. a record write must never be able to stop a stop
# ---------------------------------------------------------------------------


def test_stop_still_happens_when_incident_write_fails(tmp_path, monkeypatch, caplog):
    def _boom(*_a, **_kw):
        raise OSError("incident log is on a read-only filesystem")

    monkeypatch.setattr("castor.incidents.IncidentLog.record", _boom)

    fs = _safety_layer(tmp_path)
    assert fs.estop(principal="root", source="api", reason="blocked write") is True
    # The stop is in effect regardless of the log.
    assert fs.is_estopped is True

    assert fs.controlled_stop(principal="root", source="api", reason="blocked write") is True


def test_file_incident_returns_none_instead_of_raising(tmp_path, monkeypatch):
    def _boom(*_a, **_kw):
        raise OSError("no space left on device")

    monkeypatch.setattr("castor.incidents.IncidentLog.record", _boom)
    assert file_incident(IncidentSeverity.SERIOUS_HARM, "estop", "x") is None


def test_guardian_estop_survives_a_failed_write(tmp_path, monkeypatch):
    def _boom(*_a, **_kw):
        raise OSError("nope")

    monkeypatch.setattr("castor.incidents.IncidentLog.record", _boom)
    from castor.agents.guardian import GuardianAgent

    g = GuardianAgent()
    g.trigger_estop(reason="veto")
    assert g.estop_active is True


# ---------------------------------------------------------------------------
# 3. submitting stamps reported; a second run submits nothing
# ---------------------------------------------------------------------------


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _patch_submission_path(monkeypatch, submit_fn):
    """Point the submit path at a fake registry.

    Patch MODULE OBJECTS, not dotted strings. ``castor.rcan3.__init__`` has a
    module ``__getattr__`` that resolves symbols lazily but not submodules, so
    ``monkeypatch.setattr("castor.rcan3.reader.read_robot_md", ...)`` raises
    AttributeError whenever something earlier in the session left
    ``castor.rcan3.reader`` in sys.modules without the attribute bound on the
    package. importlib.import_module does not repair that (the module is
    already in sys.modules, so nothing rebinds the parent). Resolving the
    module object once and patching it is order independent.
    """
    import importlib

    mods = {
        name: importlib.import_module(f"castor.rcan3.{name}")
        for name in ("compliance", "identity", "signer", "reader", "rrf_client")
    }

    monkeypatch.setattr(
        mods["compliance"], "submit_incident_report", submit_fn, raising=False
    )
    monkeypatch.setattr(mods["identity"], "load_or_generate_identity", lambda: object())
    monkeypatch.setattr(mods["signer"], "CastorSigner", lambda ident: object())
    monkeypatch.setattr(
        mods["reader"],
        "read_robot_md",
        lambda p: _Args(rrn="RRN-000000000001", endpoint="https://example.invalid"),
    )

    class _FakeRrf:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(mods["rrf_client"], "RrfClient", _FakeRrf)


def test_submit_marks_reported_and_second_run_submits_nothing(tmp_path, monkeypatch, capsys):
    from castor import cli as cli_mod

    log_path = tmp_path / "incidents.jsonl"
    log = IncidentLog(log_path)
    inc_id = log.record(IncidentSeverity.SERIOUS_HARM, "estop", "ESTOP", {})

    calls: list[list[dict]] = []

    async def _fake_submit(*, rrf, signer, rrn, incidents):
        calls.append(list(incidents))
        return {"status": "accepted", "receipt_id": "rcpt-1"}

    _patch_submission_path(monkeypatch, _fake_submit)

    args = _Args(manifest="ROBOT.md")

    rc = cli_mod._submit_incident_report(args, log)
    assert rc == 0
    assert len(calls) == 1
    # The body came from the log, not from a flag.
    assert [i["id"] for i in calls[0]] == [inc_id]

    entries = log.list_incidents()
    assert len(entries) == 1
    assert entries[0]["reported"] is True
    assert entries[0]["reported_at"]
    assert entries[0]["report_receipt"]["receipt_id"] == "rcpt-1"

    # Never edited in place: the original incident line still says reported false.
    raw_lines = [json.loads(x) for x in log_path.read_text().splitlines() if x.strip()]
    assert len(raw_lines) == 2
    assert raw_lines[0]["record_type"] == "incident"
    assert raw_lines[0]["reported"] is False
    assert raw_lines[1]["record_type"] == "report_submission"
    assert raw_lines[1]["reported"] is True
    # And the appended line is chained to the one before it.
    assert raw_lines[1]["prev_sha256"]

    # Second run: nothing left unfiled.
    rc2 = cli_mod._submit_incident_report(args, log)
    assert rc2 == 1
    assert len(calls) == 1


def test_failed_submission_never_marks_reported(tmp_path, monkeypatch, capsys):
    """OC-13 verifier: reported is a fact about a submission that succeeded.

    RrfClient.submit_compliance raises on any status >= 400, so a refused or
    errored submission must leave the log exactly as it was.
    """
    from castor import cli as cli_mod

    log_path = tmp_path / "incidents.jsonl"
    log = IncidentLog(log_path)
    log.record(IncidentSeverity.SERIOUS_HARM, "estop", "ESTOP", {})
    before = log_path.read_text()

    async def _boom(*, rrf, signer, rrn, incidents):
        raise RuntimeError("503: registry is down")

    _patch_submission_path(monkeypatch, _boom)

    rc = cli_mod._submit_incident_report(_Args(manifest="ROBOT.md"), log)
    assert rc == 1
    assert "submission failed" in capsys.readouterr().err
    # Nothing appended, nothing stamped.
    assert log_path.read_text() == before
    assert log.list_incidents()[0]["reported"] is False
    assert log.unreported_incidents()


def test_submit_with_empty_log_is_refused(tmp_path, capsys):
    from castor import cli as cli_mod

    log = IncidentLog(tmp_path / "incidents.jsonl")
    rc = cli_mod._submit_incident_report(_Args(manifest="ROBOT.md"), log)
    assert rc == 1
    assert "nothing to submit" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# deadlines run from discovery
# ---------------------------------------------------------------------------


def test_deadline_runs_from_discovered_at_not_timestamp(tmp_path):
    log = IncidentLog(tmp_path / "incidents.jsonl")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    log.record(
        IncidentSeverity.SERIOUS_HARM, "estop", "found late", {}, discovered_at=long_ago
    )
    inc = log.list_incidents()[0]
    assert inc["unknown_discovery"] is False
    # 20 days since discovery against a 15-day window.
    assert days_to_deadline(inc) < 0
    assert is_overdue(inc) is True


def test_categories_carry_the_short_windows(tmp_path):
    assert REPORTING_DEADLINES_DAYS == {
        "critical_infrastructure": 2,
        "death": 10,
        "serious_harm": 15,
    }
    # The 90-day bucket is gone.
    assert 90 not in REPORTING_DEADLINES_DAYS.values()
    assert "other" not in REPORTING_DEADLINES_DAYS


def test_filed_incident_is_never_overdue(tmp_path):
    log = IncidentLog(tmp_path / "incidents.jsonl")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    inc_id = log.record(
        IncidentSeverity.SERIOUS_HARM, "estop", "x", {}, discovered_at=long_ago
    )
    assert log.overdue_incidents()
    log.mark_reported([inc_id], receipt={"status": "accepted"})
    assert log.overdue_incidents() == []


# ---------------------------------------------------------------------------
# the article relabel
# ---------------------------------------------------------------------------


def test_serious_incident_clock_cites_73(tmp_path):
    log = IncidentLog(tmp_path / "incidents.jsonl")
    log.record(IncidentSeverity.DEATH, "collision", "x", {})
    report = generate_report(log)

    block = report["serious_incident_reporting"]
    blob = json.dumps(block)
    assert "Art. 73" in blob
    # Nothing under the serious-incident keys attributes the clock to Art. 72.
    assert "Art. 72" not in blob
    assert block["clock_starts"].startswith("discovered_at")
    assert block["deadlines_days"]["death"] == 10
    # Figures are the project's own crosswalk, not statutory text.
    assert "crosswalk" in block["attribution"].lower()
    # Software never renders anything "verified".
    assert "verified" not in json.dumps(report).lower()


# ---------------------------------------------------------------------------
# signed submissions are derived from a record, not from --data
# ---------------------------------------------------------------------------


def test_safety_benchmark_without_record_is_refused():
    """OC-13: the verdict is the record's, so there must be a record."""
    import pytest

    from castor.cli import _load_benchmark_record

    with pytest.raises(ValueError, match="--record"):
        _load_benchmark_record(None)
    with pytest.raises(ValueError, match="no such file"):
        _load_benchmark_record("/nonexistent/record.json")


def test_safety_benchmark_passed_comes_from_the_record_verdict(tmp_path):
    import hashlib

    from castor.cli import _load_benchmark_record

    rec = {
        "benchmark": "ten-minutes",
        "verdict": "pass",
        "verdict_reason": "all checkpoints in order in 412.0 s of 600 s",
        "elapsed_s": 412.0,
    }
    p = tmp_path / "record.json"
    p.write_text(json.dumps(rec))

    facts = _load_benchmark_record(str(p))
    assert facts["passed"] is True
    assert facts["details"]["record_sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert facts["details"]["record_verdict"] == "pass"
    # A ten-minutes record has no reference or scoring ink, so neither is invented.
    assert "reference_sha256" not in facts["details"]
    assert "ink_sha256" not in facts["details"]


def test_safety_benchmark_ci_pass_is_not_a_pass(tmp_path):
    from castor.cli import _load_benchmark_record

    p = tmp_path / "record.json"
    p.write_text(json.dumps({"verdict": "ci-pass"}))
    assert _load_benchmark_record(str(p))["passed"] is False

    p.write_text(json.dumps({"verdict": "fail"}))
    assert _load_benchmark_record(str(p))["passed"] is False


def test_sacpaint_record_carries_reference_and_ink_sha(tmp_path):
    from castor.cli import _load_benchmark_record

    p = tmp_path / "record.json"
    p.write_text(
        json.dumps(
            {
                "benchmark": "sacpaint",
                "verdict": "pass",
                "reference_sha256": "a" * 64,
                "ink_sha256": "b" * 64,
            }
        )
    )
    details = _load_benchmark_record(str(p))["details"]
    assert details["reference_sha256"] == "a" * 64
    assert details["ink_sha256"] == "b" * 64


# ---------------------------------------------------------------------------
# legacy rows keep their own clock
# ---------------------------------------------------------------------------


def test_legacy_rows_read_back_without_being_re_dated(tmp_path):
    """OC-13 verifier: old rows relabel, they do not get a shorter deadline.

    `life_health` and `other` both read as `serious_harm` now, but the old code
    always wrote `reporting_deadline_days` and `days_to_deadline` prefers the
    figure the record carries. A three-month `other` row keeps its three
    months; it is not silently re-dated to fifteen days.
    """
    path = tmp_path / "incidents.jsonl"
    discovered = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "id": "legacy-other",
                    "timestamp": discovered,
                    "severity": "other",
                    "category": "config",
                    "description": "old row",
                    "system_state": {},
                    "reported": False,
                    "reporting_deadline_days": 90,
                },
                {
                    "id": "legacy-life-health",
                    "timestamp": discovered,
                    "severity": "life_health",
                    "category": "estop",
                    "description": "old row",
                    "system_state": {},
                    "reported": False,
                    "reporting_deadline_days": 15,
                },
            )
        )
        + "\n"
    )

    by_id = {i["id"]: i for i in IncidentLog(path).list_incidents()}
    assert len(by_id) == 2
    # Both legacy names now read as the general serious-harm category.
    assert by_id["legacy-other"]["severity"] == "serious_harm"
    assert by_id["legacy-life-health"]["severity"] == "serious_harm"
    # A legacy row with no discovered_at falls back to its timestamp and says so.
    assert by_id["legacy-other"]["discovered_at"] == discovered
    assert by_id["legacy-other"]["unknown_discovery"] is True
    # The 90-day row keeps 90 days: 30 days in, it has about 60 left and is not
    # overdue. Relabelling must not shorten a clock that was already running.
    remaining = days_to_deadline(by_id["legacy-other"])
    assert 59 < remaining < 61
    assert is_overdue(by_id["legacy-other"]) is False
    # The 15-day row is 30 days past its own deadline.
    assert is_overdue(by_id["legacy-life-health"]) is True
