"""
castor/brain/memory_schema.py — Structured robot-memory.md schema with confidence decay.

Implements the schema proposed in continuonai/rcan-spec#191:
- Typed entries (hardware_observation, environment_note, behavior_pattern, resolved)
- Confidence scoring: 0.0–1.0, decays by DECAY_RATE per day without reinforcement
- Context injection: only entries with confidence >= CONFIDENCE_INJECT_MIN
- Pruning: entries below CONFIDENCE_PRUNE_MIN are removed from the file

This module is a runtime primitive — no hardcoded operator paths.
Callers supply the file path explicitly.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import yaml

SCHEMA_VERSION = "1.0"
DECAY_RATE = 0.05  # confidence lost per day without reinforcement
CONFIDENCE_INJECT_MIN = 0.30  # below this → excluded from context injection
CONFIDENCE_PRUNE_MIN = 0.10  # below this → pruned from file on next save


class EntryType(str, Enum):
    HARDWARE_OBSERVATION = "hardware_observation"
    ENVIRONMENT_NOTE = "environment_note"
    BEHAVIOR_PATTERN = "behavior_pattern"
    RESOLVED = "resolved"  # kept for audit trail, excluded from context


@dataclass
class MemoryEntry:
    id: str
    type: EntryType
    text: str
    confidence: float  # 0.0–1.0
    first_seen: datetime
    last_reinforced: datetime
    observation_count: int = 1
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))

    def decay(self, as_of: datetime) -> MemoryEntry:
        """Return a copy with confidence decayed by elapsed days since last_reinforced."""
        delta = (as_of - self.last_reinforced).total_seconds() / 86400.0
        decayed = max(0.0, self.confidence - DECAY_RATE * delta)
        return MemoryEntry(
            id=self.id,
            type=self.type,
            text=self.text,
            confidence=decayed,
            first_seen=self.first_seen,
            last_reinforced=self.last_reinforced,
            observation_count=self.observation_count,
            tags=list(self.tags),
        )

    def reinforce(self, nudge: float = 0.1) -> MemoryEntry:
        """Return a copy with incremented observation_count and nudged confidence."""
        return MemoryEntry(
            id=self.id,
            type=self.type,
            text=self.text,
            confidence=min(1.0, self.confidence + nudge),
            first_seen=self.first_seen,
            last_reinforced=datetime.now(timezone.utc),
            observation_count=self.observation_count + 1,
            tags=list(self.tags),
        )


@dataclass
class RobotMemory:
    schema_version: str
    rrn: str
    last_updated: datetime
    entries: list[MemoryEntry] = field(default_factory=list)


# ── Serialisation helpers ─────────────────────────────────────────────────────


def _entry_to_dict(e: MemoryEntry) -> dict:
    return {
        "id": e.id,
        "type": e.type.value,
        "text": e.text,
        "confidence": round(e.confidence, 4),
        "first_seen": e.first_seen.isoformat(),
        "last_reinforced": e.last_reinforced.isoformat(),
        "observation_count": e.observation_count,
        "tags": list(e.tags),
    }


def _entry_from_dict(d: dict) -> MemoryEntry:
    def _parse_dt(v: str) -> datetime:
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    return MemoryEntry(
        id=d["id"],
        type=EntryType(d["type"]),
        text=d["text"],
        confidence=float(d.get("confidence", 0.5)),
        first_seen=_parse_dt(d["first_seen"]),
        last_reinforced=_parse_dt(d["last_reinforced"]),
        observation_count=int(d.get("observation_count", 1)),
        tags=list(d.get("tags", [])),
    )


# ── Public API ────────────────────────────────────────────────────────────────


def load_memory(path: str) -> RobotMemory:
    """
    Load a structured robot-memory.md from *path*.

    Returns an empty RobotMemory if the file does not exist or cannot be parsed.
    The YAML front-matter block (between --- delimiters) is parsed; any markdown
    body is ignored.
    """
    if not os.path.exists(path):
        return RobotMemory(
            schema_version=SCHEMA_VERSION,
            rrn="UNKNOWN",
            last_updated=datetime.now(timezone.utc),
        )

    try:
        with open(path) as f:
            raw = f.read()

        # Support YAML-only files or markdown with YAML front-matter
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            yaml_block = parts[1] if len(parts) >= 2 else ""
        else:
            yaml_block = raw

        data = yaml.safe_load(yaml_block) or {}

        entries = [_entry_from_dict(e) for e in data.get("entries", [])]
        last_updated_raw = data.get("last_updated", datetime.now(timezone.utc).isoformat())
        if isinstance(last_updated_raw, str):
            last_updated = datetime.fromisoformat(last_updated_raw)
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
        else:
            last_updated = datetime.now(timezone.utc)

        return RobotMemory(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            rrn=str(data.get("rrn", "UNKNOWN")),
            last_updated=last_updated,
            entries=entries,
        )
    except Exception:
        return RobotMemory(
            schema_version=SCHEMA_VERSION,
            rrn="UNKNOWN",
            last_updated=datetime.now(timezone.utc),
        )


def save_memory(memory: RobotMemory, path: str) -> None:
    """
    Atomically write *memory* to *path* as a YAML front-matter markdown file.

    Uses a temp file + rename for crash safety.
    """
    memory.last_updated = datetime.now(timezone.utc)
    data: dict = {
        "schema_version": memory.schema_version,
        "rrn": memory.rrn,
        "last_updated": memory.last_updated.isoformat(),
        "entries": [_entry_to_dict(e) for e in memory.entries],
    }
    yaml_block = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content = f"---\n{yaml_block}---\n"

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    dir_ = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".robot-memory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_confidence_decay(
    memory: RobotMemory,
    as_of: Optional[datetime] = None,
) -> RobotMemory:
    """Return a new RobotMemory with all entry confidences decayed to *as_of* (default: now)."""
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    return RobotMemory(
        schema_version=memory.schema_version,
        rrn=memory.rrn,
        last_updated=memory.last_updated,
        entries=[e.decay(as_of) for e in memory.entries],
    )


def filter_for_context(
    memory: RobotMemory,
    min_confidence: float = CONFIDENCE_INJECT_MIN,
) -> list[MemoryEntry]:
    """
    Return entries eligible for brain context injection.

    Excludes:
    - Entries with confidence < min_confidence
    - Entries of type RESOLVED (kept for audit, not injected)
    Sorted by confidence descending (most reliable first).
    """
    eligible = [
        e for e in memory.entries if e.confidence >= min_confidence and e.type != EntryType.RESOLVED
    ]
    return sorted(eligible, key=lambda e: e.confidence, reverse=True)


def prune_entries(
    memory: RobotMemory,
    min_confidence: float = CONFIDENCE_PRUNE_MIN,
) -> tuple[RobotMemory, int]:
    """
    Remove entries whose confidence has fallen below *min_confidence*.

    Returns (pruned_memory, count_removed).
    """
    kept = [e for e in memory.entries if e.confidence >= min_confidence]
    removed = len(memory.entries) - len(kept)
    return (
        RobotMemory(
            schema_version=memory.schema_version,
            rrn=memory.rrn,
            last_updated=memory.last_updated,
            entries=kept,
        ),
        removed,
    )


def make_entry_id(text: str, entry_type: EntryType) -> str:
    """Generate a deterministic short ID from text + type."""
    h = hashlib.sha256(f"{entry_type.value}:{text}".encode()).hexdigest()[:8]
    return f"mem-{h}"


def format_entries_for_context(entries: list[MemoryEntry]) -> str:
    """
    Format eligible entries as a compact text block for brain context injection.

    High confidence (≥0.8) → 🔴 (important, recent)
    Medium (0.5–0.8)       → 🟡
    Lower (≥INJECT_MIN)    → 🟢
    """
    if not entries:
        return "(no stored observations above confidence threshold)"

    lines = []
    for e in entries:
        if e.confidence >= 0.8:
            prefix = "🔴"
        elif e.confidence >= 0.5:
            prefix = "🟡"
        else:
            prefix = "🟢"
        conf_pct = int(e.confidence * 100)
        lines.append(f"{prefix} [{conf_pct}%] {e.text}")
    return "\n".join(lines)
