"""Tests for castor.harness.dlq."""

import time

import pytest

from castor.harness.dlq import DeadLetterQueue


@pytest.fixture
def dlq(tmp_path):
    return DeadLetterQueue(db_path=str(tmp_path / "test.db"))


def test_push_and_list_pending(dlq):
    dlq.push("cmd-1", "do something", "chat", "timeout")
    items = dlq.list_pending()
    assert len(items) == 1
    assert items[0]["command_id"] == "cmd-1"
    assert items[0]["error"] == "timeout"


def test_count_pending(dlq):
    assert dlq.count_pending() == 0
    dlq.push("cmd-1", "inst1", "chat", "err1")
    dlq.push("cmd-2", "inst2", "control", "err2")
    assert dlq.count_pending() == 2


def test_mark_reviewed(dlq):
    dlq.push("cmd-1", "inst1", "chat", "err1")
    items = dlq.list_pending()
    dlq_id = items[0]["id"]
    dlq.mark_reviewed(dlq_id)
    assert dlq.count_pending() == 0
    assert len(dlq.list_pending()) == 0


def test_list_pending_excludes_reviewed(dlq):
    dlq.push("cmd-1", "inst1", "chat", "err1")
    dlq.push("cmd-2", "inst2", "chat", "err2")
    items = dlq.list_pending()
    dlq.mark_reviewed(items[0]["id"])
    pending = dlq.list_pending()
    assert len(pending) == 1
    assert pending[0]["command_id"] == "cmd-2"


def test_purge_old(dlq):
    dlq.push("cmd-old", "inst", "chat", "err")
    # Artificially age by directly manipulating (via another DLQ instance)
    import sqlite3
    old_time = time.time() - 8 * 86400  # 8 days ago
    conn = sqlite3.connect(dlq._db_path)
    conn.execute("UPDATE dead_letters SET created_at = ?", (old_time,))
    conn.commit()
    conn.close()

    deleted = dlq.purge_old(older_than_days=7)
    assert deleted == 1
    assert dlq.count_pending() == 0


def test_purge_old_keeps_recent(dlq):
    dlq.push("cmd-new", "inst", "chat", "err")
    deleted = dlq.purge_old(older_than_days=7)
    assert deleted == 0
    assert dlq.count_pending() == 1


def test_metadata_stored(dlq):
    dlq.push("cmd-1", "inst", "chat", "err", metadata={"run_id": "abc123"})
    items = dlq.list_pending()
    assert items[0]["metadata"]["run_id"] == "abc123"


def test_list_pending_limit(dlq):
    for i in range(25):
        dlq.push(f"cmd-{i}", f"inst-{i}", "chat", "err")
    items = dlq.list_pending(limit=10)
    assert len(items) == 10


def test_multiple_push_and_review_cycle(dlq):
    for i in range(5):
        dlq.push(f"cmd-{i}", f"inst-{i}", "chat", "err")
    assert dlq.count_pending() == 5
    for item in dlq.list_pending():
        dlq.mark_reviewed(item["id"])
    assert dlq.count_pending() == 0
