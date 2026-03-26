"""
castor/harness/span_tracer.py — OpenTelemetry-style span tracer.

Implements trace_id / span_id / parent_span_id nesting and exports JSONL to
``~/.config/opencastor/traces/{date}/{trace_id}.jsonl``.

No OTel SDK dependency — spans are structured dicts / dataclasses.

RCAN config::

    span_tracer:
      enabled: true
      max_trace_age_days: 7
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import AsyncGenerator, Generator, Optional

__all__ = ["SpanTracer", "Span"]

_DEFAULT_EXPORT_PATH = Path.home() / ".config" / "opencastor" / "traces"


@dataclass
class Span:
    """A single trace span."""

    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    name: str
    start_ns: int
    end_ns: Optional[int] = None
    status: str = "in_progress"  # "ok" | "error" | "in_progress"
    attributes: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)


class SpanTracer:
    """Manages spans for a single harness run or across runs.

    Args:
        config: ``span_tracer`` section from RCAN config.
    """

    def __init__(self, config: dict) -> None:
        export_path = config.get("export_path")
        self._export_path: Path = Path(export_path) if export_path else _DEFAULT_EXPORT_PATH
        self._max_age_days: int = int(config.get("max_trace_age_days", 7))
        # In-memory store: trace_id → list[Span]
        self._traces: dict[str, list[Span]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def start_trace(self, name: str, attributes: Optional[dict] = None) -> Span:
        """Start a new root span (new trace_id)."""
        trace_id = str(uuid.uuid4())
        return self.start_span(
            name=name, parent=None, attributes=attributes or {}, trace_id=trace_id
        )

    def start_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        attributes: Optional[dict] = None,
        trace_id: Optional[str] = None,
    ) -> Span:
        """Start a child span under ``parent``, or a new root span."""
        tid = trace_id or (parent.trace_id if parent else str(uuid.uuid4()))
        span = Span(
            trace_id=tid,
            span_id=str(uuid.uuid4()),
            parent_span_id=parent.span_id if parent else None,
            name=name,
            start_ns=time.time_ns(),
            attributes=dict(attributes or {}),
        )
        self._traces.setdefault(tid, []).append(span)
        return span

    def end_span(
        self,
        span: Span,
        status: str = "ok",
        error: Optional[str] = None,
    ) -> None:
        """Finalise a span."""
        span.end_ns = time.time_ns()
        span.status = "error" if error else status
        if error:
            span.attributes["error"] = error

    def add_event(self, span: Span, name: str, attributes: Optional[dict] = None) -> None:
        """Attach a timestamped event to a span."""
        span.events.append(
            {"name": name, "timestamp_ns": time.time_ns(), "attrs": dict(attributes or {})}
        )

    def export_trace(self, trace_id: str) -> None:
        """Flush a trace to JSONL on disk."""
        spans = self._traces.get(trace_id)
        if not spans:
            return
        date_str = time.strftime("%Y-%m-%d")
        out_dir = self._export_path / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{trace_id}.jsonl"
        with out_file.open("w") as fh:
            for span in spans:
                fh.write(json.dumps(asdict(span)) + "\n")

    def get_trace(self, trace_id: str) -> list[Span]:
        """Return all spans for a trace (from memory)."""
        return list(self._traces.get(trace_id, []))

    def list_traces(self, limit: int = 50) -> list[str]:
        """Return recent trace IDs from disk (newest first)."""
        ids: list[tuple[float, str]] = []
        if not self._export_path.exists():
            return []
        for jsonl_file in self._export_path.rglob("*.jsonl"):
            ids.append((jsonl_file.stat().st_mtime, jsonl_file.stem))
        ids.sort(reverse=True)
        return [tid for _, tid in ids[:limit]]

    def get_trace_from_disk(self, trace_id: str) -> list[dict]:
        """Load a trace from JSONL on disk."""
        spans_data: list[dict] = []
        if not self._export_path.exists():
            return spans_data
        for jsonl_file in self._export_path.rglob(f"{trace_id}.jsonl"):
            with jsonl_file.open() as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        spans_data.append(json.loads(line))
            break
        return spans_data

    def purge_old(self) -> int:
        """Delete JSONL files older than ``max_trace_age_days``.

        Returns:
            Number of files deleted.
        """
        cutoff = time.time() - self._max_age_days * 86400
        deleted = 0
        if not self._export_path.exists():
            return 0
        for jsonl_file in list(self._export_path.rglob("*.jsonl")):
            if jsonl_file.stat().st_mtime < cutoff:
                jsonl_file.unlink(missing_ok=True)
                deleted += 1
        return deleted

    # ── Context managers ──────────────────────────────────────────────────────

    @contextmanager
    def span(
        self,
        name: str,
        parent: Optional[Span] = None,
        attributes: Optional[dict] = None,
    ) -> Generator[Span, None, None]:
        """Sync context manager that starts and ends a span."""
        s = self.start_span(name=name, parent=parent, attributes=attributes)
        try:
            yield s
            self.end_span(s, status="ok")
        except Exception as exc:
            self.end_span(s, status="error", error=str(exc))
            raise

    @asynccontextmanager
    async def async_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        attributes: Optional[dict] = None,
    ) -> AsyncGenerator[Span, None]:
        """Async context manager that starts and ends a span."""
        s = self.start_span(name=name, parent=parent, attributes=attributes)
        try:
            yield s
            self.end_span(s, status="ok")
        except Exception as exc:
            self.end_span(s, status="error", error=str(exc))
            raise
