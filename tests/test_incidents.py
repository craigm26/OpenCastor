"""Tests for castor.incidents — serious-incident log and monitoring report."""

import hashlib
import json

from castor.incidents import (
    CHAIN_BROKEN,
    CHAIN_NO_LOG,
    CHAIN_OK,
    INCIDENT_SCHEMA_VERSION,
    ROTATION_RECORD_TYPE,
    IncidentLog,
    IncidentSeverity,
    generate_report,
    verify_incident_chain,
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


def _concurrent_writer(path: str, tag: str, count: int, max_bytes: int) -> None:
    """A second process filing incidents into the same log. Module level so it
    survives being handed to multiprocessing."""
    log = IncidentLog(path, max_bytes=max_bytes)
    for i in range(count):
        log.record(IncidentSeverity.SERIOUS_HARM, "estop", f"{tag}-{i}", {})


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

    def test_two_processes_do_not_fork_the_chain(self, tmp_path):
        """The runtime files from a stop while a submit stamps in another
        process. Without a cross-process lock both read the same last line and
        both chain onto it, which reads back as a broken chain."""
        import multiprocessing as mp

        path = tmp_path / "incidents.jsonl"
        max_bytes = 1500  # small enough that the writers also race a rotation
        ctx = mp.get_context("fork")
        procs = [
            ctx.Process(target=_concurrent_writer, args=(str(path), tag, 30, max_bytes))
            for tag in ("a", "b", "c")
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        assert [p.exitcode for p in procs] == [0, 0, 0]

        log = IncidentLog(path, max_bytes=max_bytes)
        lines = self._chain_lines(log)
        prev = ""
        for raw in lines:
            row = json.loads(raw)
            assert row.get("prev_sha256", "") == prev, f"chain forked at {row.get('id')}"
            prev = hashlib.sha256(raw.encode()).hexdigest()

        # Nothing was lost to the rotations the writers raced through.
        assert len(log.list_incidents()) == 90

    def test_cache_is_dropped_when_the_file_was_replaced_at_the_same_size(self, tmp_path):
        """A rotation puts a new file at this path, and that file grows back
        through the sizes the old one had. A cache keyed on the size alone
        would recognise one of them and chain onto a hash from a file that is
        no longer there."""
        import os

        path = tmp_path / "incidents.jsonl"
        log = IncidentLog(path)
        log.record(IncidentSeverity.SERIOUS_HARM, "estop", "first", {})
        warm = log._last_line_hash()  # cache taken at this size

        # Somebody else rolled the log away and the new file reached exactly
        # the same length with a different last line.
        body = path.read_text()
        os.replace(path, tmp_path / "incidents.2026-01-01.jsonl")
        replacement = body.replace('"first"', '"firsx"')
        assert len(replacement) == len(body)
        path.write_text(replacement)

        expected = hashlib.sha256(replacement.strip().encode()).hexdigest()
        assert log._last_line_hash() == expected
        assert log._last_line_hash() != warm

    def test_a_torn_tail_is_terminated_before_the_next_record(self, tmp_path):
        """A process killed mid-append leaves a line with no newline on it.
        Appending onto that byte would put two JSON objects on one physical
        line, and a reader drops the whole line, losing the record that was
        already written as well as the new one."""
        path = tmp_path / "incidents.jsonl"
        torn = json.dumps(
            {
                "id": "torn",
                "record_type": "incident",
                "prev_sha256": "",
                "severity": "serious_harm",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "discovered_at": "2026-01-01T00:00:00+00:00",
            }
        )
        path.write_text(torn)  # no trailing newline

        log = IncidentLog(path)
        log.record(IncidentSeverity.SERIOUS_HARM, "estop", "after the tear", {})

        lines = [x for x in path.read_text().splitlines() if x.strip()]
        assert len(lines) == 2
        assert lines[0] == torn  # the torn record is still its own line
        assert json.loads(lines[1])["prev_sha256"] == hashlib.sha256(torn.encode()).hexdigest()
        assert len(IncidentLog(path).list_incidents()) == 2

    def test_rotated_files_stay_in_write_order_past_the_tenth(self, tmp_path):
        """Two rotations in one day land as incidents.<date>.jsonl and
        incidents.<date>_1.jsonl. Sorted as text, _10 comes before _2, and
        mtimes tie on a filesystem with coarse timestamps."""
        import os

        path = tmp_path / "incidents.jsonl"
        log = IncidentLog(path, max_bytes=700)
        ids = [
            log.record(IncidentSeverity.SERIOUS_HARM, "estop", f"stop {i}", {}) for i in range(14)
        ]
        rotated = log.rotated_paths()
        assert len(rotated) >= 11, "needs enough same-day rotations to pass _9"

        # Force the mtime tie the coarse-timestamp case would give us.
        for p in rotated:
            os.utime(p, (1_700_000_000, 1_700_000_000))

        assert [i["id"] for i in IncidentLog(path, max_bytes=700).list_incidents()] == ids


class TestChainVerifier:
    """`castor incidents verify` walks every link, rotated files first.

    A pass here says the links hold, which is the writer being consistent with
    itself. It is not an outside party's check and nothing is rendered
    "verified".
    """

    @staticmethod
    def _fill(path, count=4, max_bytes=None):
        log = IncidentLog(path, max_bytes=max_bytes) if max_bytes else IncidentLog(path)
        for i in range(count):
            log.record(IncidentSeverity.SERIOUS_HARM, "estop", f"stop {i}", {})
        return log

    @staticmethod
    def _lines(path):
        return [x for x in path.read_text().splitlines() if x.strip()]

    # -- the clean cases -------------------------------------------------

    def test_a_clean_single_file_checks_out(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        self._fill(path, 4)
        check = verify_incident_chain(path)
        assert check.state == CHAIN_OK
        assert check.exit_code == 0
        assert check.records == 4
        assert [f.records for f in check.files] == [4]
        assert check.reason is None

    def test_a_clean_chain_across_two_rotations_checks_out(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        log = self._fill(path, 12, max_bytes=900)
        rotated = log.rotated_paths()
        assert len(rotated) >= 2, "needs at least two rotations"

        check = IncidentLog(path, max_bytes=900).verify_chain()
        assert check.state == CHAIN_OK
        assert check.exit_code == 0
        # Every file is reported, rotated ones first, then the active log.
        assert [f.path for f in check.files] == rotated + [path]
        assert check.records == sum(len(self._lines(p)) for p in rotated + [path])
        # The carry-over lines are counted as records and are not incidents.
        assert check.records > 12

    def test_an_existing_empty_file_is_a_log_with_no_records(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        path.write_text("")
        check = verify_incident_chain(path)
        assert check.state == CHAIN_OK
        assert check.exit_code == 0
        assert check.records == 0

    def test_no_file_at_all_is_not_a_clean_bill(self, tmp_path):
        path = tmp_path / "nothing-here" / "incidents.jsonl"
        check = verify_incident_chain(path)
        assert check.state == CHAIN_NO_LOG
        assert check.exit_code == 2
        assert check.files == []

    # -- the breaks ------------------------------------------------------

    def test_a_tampered_middle_line_breaks_the_next_link(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        self._fill(path, 4)
        lines = self._lines(path)
        row = json.loads(lines[1])
        row["description"] = "something else entirely"
        lines[1] = json.dumps(row)
        path.write_text("\n".join(lines) + "\n")

        check = verify_incident_chain(path)
        assert check.state == CHAIN_BROKEN
        assert check.exit_code == 1
        assert check.break_path == path
        assert check.break_line == 3  # the line whose prev_sha256 no longer holds
        assert "not the hash of the line before it" in check.reason

    def test_a_truncated_last_line_is_a_break(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        self._fill(path, 3)
        raw = path.read_text()
        path.write_text(raw[: len(raw) - 40])  # a process killed mid-append

        check = verify_incident_chain(path)
        assert check.state == CHAIN_BROKEN
        assert check.exit_code == 1
        assert check.break_line == 3
        assert "does not parse as JSON" in check.reason

    def test_a_line_with_no_prev_sha256_is_a_break(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        self._fill(path, 2)
        with open(path, "a") as f:
            f.write(json.dumps({"id": "x", "record_type": "incident"}) + "\n")

        check = verify_incident_chain(path)
        assert check.state == CHAIN_BROKEN
        assert check.break_line == 3
        assert "no prev_sha256" in check.reason

    def test_the_head_of_the_chain_must_carry_an_empty_prev_sha256(self, tmp_path):
        """Genesis here is an EMPTY prev_sha256, not the audit log's GENESIS word."""
        path = tmp_path / "incidents.jsonl"
        self._fill(path, 1)
        first = json.loads(self._lines(path)[0])
        assert first["prev_sha256"] == ""

        first["prev_sha256"] = "GENESIS"
        path.write_text(json.dumps(first) + "\n")
        check = verify_incident_chain(path)
        assert check.state == CHAIN_BROKEN
        assert check.break_line == 1
        assert "head of the chain" in check.reason

    # -- the forged carry-over -------------------------------------------

    def _rotated_log_with_carry_over(self, tmp_path):
        """A log that has rolled once, with the carry-over line first in the active file."""
        path = tmp_path / "incidents.jsonl"
        log = self._fill(path, 12, max_bytes=900)
        assert log.rotated_paths()
        active = self._lines(path)
        carry = json.loads(active[0])
        assert carry["record_type"] == ROTATION_RECORD_TYPE
        return path, active, carry

    def test_a_carry_over_naming_a_file_that_is_not_there_is_a_break(self, tmp_path):
        path, active, carry = self._rotated_log_with_carry_over(tmp_path)
        carry["rotated_to"] = "incidents.2019-01-01.jsonl"  # never written
        active[0] = json.dumps(carry)
        path.write_text("\n".join(active) + "\n")

        check = verify_incident_chain(path)
        assert check.state == CHAIN_BROKEN
        assert check.exit_code == 1
        assert check.break_path == path
        assert check.break_line == 1
        assert "not a file next to this log" in check.reason

    def test_a_carry_over_whose_target_ends_on_another_hash_is_a_break(self, tmp_path):
        path, active, carry = self._rotated_log_with_carry_over(tmp_path)
        # A real file, sitting next to the log, that this chain never wrote.
        decoy = tmp_path / "decoy.jsonl"
        decoy.write_text(json.dumps({"id": "decoy", "prev_sha256": ""}) + "\n")
        carry["rotated_to"] = decoy.name
        active[0] = json.dumps(carry)
        path.write_text("\n".join(active) + "\n")

        check = verify_incident_chain(path)
        assert check.state == CHAIN_BROKEN
        assert check.break_line == 1
        assert "is not that file's last line" in check.reason

    def test_a_line_that_merely_says_log_rotation_does_not_excuse_a_gap(self, tmp_path):
        """Anything that can append can write a log_rotation line."""
        path = tmp_path / "incidents.jsonl"
        self._fill(path, 2)
        with open(path, "a") as f:
            f.write(
                json.dumps(
                    {
                        "id": "forged",
                        "record_type": ROTATION_RECORD_TYPE,
                        "rotated_to": "incidents.2020-02-02.jsonl",
                        "prev_sha256": hashlib.sha256(b"whatever").hexdigest(),
                    }
                )
                + "\n"
            )
        check = verify_incident_chain(path)
        assert check.state == CHAIN_BROKEN
        assert check.break_line == 3

    # -- what a clean result is allowed to say ---------------------------

    def test_a_clean_result_does_not_call_the_log_verified(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        self._fill(path, 2)
        data = verify_incident_chain(path).to_dict()
        assert data["state"] == "ok"
        assert data["break"] is None
        assert data["note"] == "A link check is not an outside party's verification."
        assert "verified" not in json.dumps(data).replace(data["note"], "")
