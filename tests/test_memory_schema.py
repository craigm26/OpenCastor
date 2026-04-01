"""Tests for castor/brain/memory_schema.py"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

from castor.brain.memory_schema import (
    CONFIDENCE_INJECT_MIN,
    CONFIDENCE_PRUNE_MIN,
    EntryType,
    MemoryEntry,
    RobotMemory,
    apply_confidence_decay,
    filter_for_context,
    format_entries_for_context,
    load_memory,
    make_entry_id,
    prune_entries,
    save_memory,
)

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_entry(
    text: str = "left wheel encoder intermittent",
    confidence: float = 0.9,
    entry_type: EntryType = EntryType.HARDWARE_OBSERVATION,
    days_ago: int = 0,
) -> MemoryEntry:
    ts = _NOW - timedelta(days=days_ago)
    return MemoryEntry(
        id=make_entry_id(text, entry_type),
        type=entry_type,
        text=text,
        confidence=confidence,
        first_seen=ts,
        last_reinforced=ts,
    )


def _make_memory(entries: list[MemoryEntry] | None = None) -> RobotMemory:
    return RobotMemory(
        schema_version="1.0",
        rrn="RRN-000000000001",
        last_updated=_NOW,
        entries=entries or [],
    )


# ── load / save roundtrip ─────────────────────────────────────────────────────


def test_save_and_load_roundtrip():
    mem = _make_memory([_make_entry(confidence=0.85)])
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        path = f.name
    try:
        save_memory(mem, path)
        loaded = load_memory(path)
        assert loaded.rrn == "RRN-000000000001"
        assert len(loaded.entries) == 1
        assert abs(loaded.entries[0].confidence - 0.85) < 0.001
        assert loaded.entries[0].type == EntryType.HARDWARE_OBSERVATION
    finally:
        os.unlink(path)


def test_load_missing_file_returns_empty():
    mem = load_memory("/tmp/definitely_does_not_exist_robot_memory.md")
    assert mem.rrn == "UNKNOWN"
    assert mem.entries == []


def test_save_is_atomic(tmp_path):
    path = str(tmp_path / "robot-memory.md")
    mem = _make_memory([_make_entry()])
    save_memory(mem, path)
    assert os.path.exists(path)
    # Second save overwrites cleanly
    mem2 = _make_memory([_make_entry("new observation")])
    save_memory(mem2, path)
    loaded = load_memory(path)
    assert loaded.entries[0].text == "new observation"


def test_entry_tags_roundtrip(tmp_path):
    entry = _make_entry()
    entry.tags = ["wheel", "encoder", "navigation"]
    mem = _make_memory([entry])
    path = str(tmp_path / "memory.md")
    save_memory(mem, path)
    loaded = load_memory(path)
    assert loaded.entries[0].tags == ["wheel", "encoder", "navigation"]


# ── confidence decay ──────────────────────────────────────────────────────────


def test_decay_one_day():
    entry = _make_entry(confidence=0.9, days_ago=1)
    mem = _make_memory([entry])
    decayed = apply_confidence_decay(mem, as_of=_NOW)
    # 1 day * 0.05/day = 0.05 decay → 0.85
    assert abs(decayed.entries[0].confidence - 0.85) < 0.01


def test_decay_seven_days():
    entry = _make_entry(confidence=0.9, days_ago=7)
    mem = _make_memory([entry])
    decayed = apply_confidence_decay(mem, as_of=_NOW)
    # 7 * 0.05 = 0.35 decay → 0.55
    assert abs(decayed.entries[0].confidence - 0.55) < 0.01


def test_decay_thirty_days_floors_at_zero():
    entry = _make_entry(confidence=0.5, days_ago=30)
    mem = _make_memory([entry])
    decayed = apply_confidence_decay(mem, as_of=_NOW)
    # 30 * 0.05 = 1.5 decay → floored at 0.0
    assert decayed.entries[0].confidence == 0.0


def test_decay_does_not_mutate_original():
    entry = _make_entry(confidence=0.9, days_ago=7)
    mem = _make_memory([entry])
    apply_confidence_decay(mem, as_of=_NOW)
    assert mem.entries[0].confidence == 0.9  # original unchanged


# ── filter_for_context ────────────────────────────────────────────────────────


def test_filter_excludes_below_threshold():
    entries = [
        _make_entry("above", confidence=0.8),
        _make_entry("below", confidence=CONFIDENCE_INJECT_MIN - 0.01),
    ]
    result = filter_for_context(_make_memory(entries))
    assert len(result) == 1
    assert result[0].text == "above"


def test_filter_excludes_resolved_entries():
    entries = [
        _make_entry("resolved fix", confidence=0.95, entry_type=EntryType.RESOLVED),
        _make_entry("active issue", confidence=0.8),
    ]
    result = filter_for_context(_make_memory(entries))
    assert len(result) == 1
    assert result[0].text == "active issue"


def test_filter_sorts_by_confidence_descending():
    entries = [
        _make_entry("low", confidence=0.4),
        _make_entry("high", confidence=0.9),
        _make_entry("mid", confidence=0.6),
    ]
    result = filter_for_context(_make_memory(entries))
    assert [e.text for e in result] == ["high", "mid", "low"]


# ── prune_entries ─────────────────────────────────────────────────────────────


def test_prune_removes_low_confidence():
    entries = [
        _make_entry("keep", confidence=0.5),
        _make_entry("prune", confidence=CONFIDENCE_PRUNE_MIN - 0.01),
    ]
    pruned, count = prune_entries(_make_memory(entries))
    assert count == 1
    assert len(pruned.entries) == 1
    assert pruned.entries[0].text == "keep"


def test_prune_keeps_all_above_threshold():
    entries = [_make_entry(confidence=0.5), _make_entry(confidence=0.9)]
    pruned, count = prune_entries(_make_memory(entries))
    assert count == 0
    assert len(pruned.entries) == 2


# ── format_entries_for_context ────────────────────────────────────────────────


def test_format_uses_emoji_prefixes():
    entries = [
        _make_entry("critical issue", confidence=0.9),
        _make_entry("medium concern", confidence=0.6),
        _make_entry("low concern", confidence=0.35),
    ]
    text = format_entries_for_context(entries)
    assert "🔴" in text
    assert "🟡" in text
    assert "🟢" in text


def test_format_empty_returns_placeholder():
    text = format_entries_for_context([])
    assert "no stored observations" in text


# ── reinforce ─────────────────────────────────────────────────────────────────


def test_reinforce_bumps_count_and_confidence():
    entry = _make_entry(confidence=0.7)
    reinforced = entry.reinforce(nudge=0.1)
    assert reinforced.observation_count == 2
    assert abs(reinforced.confidence - 0.8) < 0.001
    assert reinforced.last_reinforced > entry.last_reinforced


def test_reinforce_caps_at_one():
    entry = _make_entry(confidence=0.95)
    reinforced = entry.reinforce(nudge=0.2)
    assert reinforced.confidence == 1.0
