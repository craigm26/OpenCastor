"""
OpenCastor Virtual Filesystem -- Context & Compound Pipelines.

Context Window (``/tmp/context/``)
    A sliding window of recent interactions that the brain can use
    for multi-turn reasoning.  The window has a configurable depth
    and automatically summarises older entries when it fills up.

Compound Pipelines
    Unix-inspired pipe operator for chaining operations.  Just as
    ``cat file | grep pattern | wc -l`` chains Unix tools, pipelines
    chain robot operations::

        result = pipeline.run([
            Read("/dev/camera"),        # Capture frame
            Exec("/mnt/providers"),     # Send to brain
            Write("/dev/motor"),        # Execute action
            Append("/var/log/actions"), # Audit
        ])

    Each stage receives the output of the previous stage.  Stages
    can be filesystem operations or callables.
"""

import time
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Union

from castor.fs.namespace import Namespace

logger = logging.getLogger("OpenCastor.FS.Context")

# Default context window settings
DEFAULT_WINDOW_DEPTH = 20      # Max entries in the sliding window
DEFAULT_SUMMARY_THRESHOLD = 15  # Summarise when this many entries exist


class ContextWindow:
    """Sliding context window stored at ``/tmp/context/``.

    Maintains a bounded list of interaction records.  When the window
    fills up, the oldest entries are compressed into a running summary,
    keeping the total context size manageable.

    Args:
        ns:                The underlying namespace.
        max_depth:         Maximum entries before eviction.
        summary_threshold: Trigger summarisation at this count.
        summariser:        Optional callable ``(entries) -> str`` for
                           custom summarisation.  Defaults to a simple
                           concatenation of observations.
    """

    def __init__(self, ns: Namespace, max_depth: int = DEFAULT_WINDOW_DEPTH,
                 summary_threshold: int = DEFAULT_SUMMARY_THRESHOLD,
                 summariser: Optional[Callable] = None):
        self.ns = ns
        self.max_depth = max_depth
        self.summary_threshold = summary_threshold
        self._summariser = summariser or self._default_summariser
        self._lock = threading.Lock()
        self._bootstrap()

    def _bootstrap(self):
        self.ns.mkdir("/tmp/context")
        self.ns.write("/tmp/context/window", [])
        self.ns.write("/tmp/context/summary", "")
        self.ns.write("/tmp/context/turn_count", 0)

    def push(self, role: str, content: str, metadata: Optional[Dict] = None):
        """Add an entry to the context window.

        Args:
            role:     ``"user"``, ``"brain"``, ``"system"``, or ``"action"``.
            content:  The text content.
            metadata: Optional extra data (action dict, sensor readings, etc.).
        """
        entry = {
            "t": time.time(),
            "role": role,
            "content": content,
        }
        if metadata:
            entry["meta"] = metadata
        with self._lock:
            self.ns.append("/tmp/context/window", entry)
            count = (self.ns.read("/tmp/context/turn_count") or 0) + 1
            self.ns.write("/tmp/context/turn_count", count)
            self._maybe_summarise()

    def get_window(self) -> List[Dict]:
        """Return the current context window entries."""
        return self.ns.read("/tmp/context/window") or []

    def get_summary(self) -> str:
        """Return the running summary of evicted entries."""
        return self.ns.read("/tmp/context/summary") or ""

    def get_turn_count(self) -> int:
        return self.ns.read("/tmp/context/turn_count") or 0

    def clear(self):
        """Reset the context window and summary."""
        with self._lock:
            self.ns.write("/tmp/context/window", [])
            self.ns.write("/tmp/context/summary", "")
            self.ns.write("/tmp/context/turn_count", 0)

    def build_prompt_context(self) -> str:
        """Build a text block suitable for injection into a system prompt.

        Combines the running summary with the current window entries.
        """
        parts = []
        summary = self.get_summary()
        if summary:
            parts.append(f"[Context Summary]\n{summary}")
        window = self.get_window()
        if window:
            parts.append("[Recent Interactions]")
            for entry in window:
                role = entry.get("role", "?")
                content = entry.get("content", "")
                parts.append(f"{role}: {content}")
        return "\n".join(parts)

    def _maybe_summarise(self):
        """Compress older entries into the summary when the window grows too large."""
        window = self.ns.read("/tmp/context/window") or []
        if len(window) <= self.summary_threshold:
            return
        # Split: keep the most recent entries, summarise the rest
        keep = self.max_depth // 2
        to_summarise = window[:-keep]
        to_keep = window[-keep:]

        new_summary_part = self._summariser(to_summarise)
        existing_summary = self.ns.read("/tmp/context/summary") or ""
        if existing_summary:
            combined = f"{existing_summary}\n{new_summary_part}"
        else:
            combined = new_summary_part

        # Truncate the running summary if it gets too long
        max_summary_chars = 2000
        if len(combined) > max_summary_chars:
            combined = combined[-max_summary_chars:]

        self.ns.write("/tmp/context/summary", combined)
        self.ns.write("/tmp/context/window", to_keep)
        logger.debug("Context summarised: %d entries -> summary, keeping %d",
                     len(to_summarise), len(to_keep))

    @staticmethod
    def _default_summariser(entries: List[Dict]) -> str:
        """Simple summariser: concatenate observations."""
        lines = []
        for e in entries:
            role = e.get("role", "?")
            content = e.get("content", "")[:100]
            lines.append(f"[{role}] {content}")
        return "; ".join(lines)


# -----------------------------------------------------------------------
# Compound Pipelines (Unix pipes for robot operations)
# -----------------------------------------------------------------------
class PipelineStage:
    """A single stage in a compound pipeline.

    A stage is either:
    - A filesystem path (read or write depending on position).
    - A callable ``(input_data) -> output_data``.
    - A named operation with parameters.
    """

    def __init__(self, operation: Union[str, Callable], **kwargs):
        self.operation = operation
        self.kwargs = kwargs

    def __repr__(self):
        if callable(self.operation):
            name = getattr(self.operation, "__name__", "callable")
            return f"Stage({name})"
        return f"Stage({self.operation})"


class Pipeline:
    """Chain operations like Unix pipes.

    Each stage receives the output of the previous stage and produces
    input for the next.  Stages can be filesystem reads, writes,
    transforms, or arbitrary callables.

    Usage::

        pipe = Pipeline("observe-think-act", fs)
        pipe.add_stage(PipelineStage("/dev/camera"))       # read frame
        pipe.add_stage(PipelineStage(brain_think))         # process with LLM
        pipe.add_stage(PipelineStage("/dev/motor"))        # write action
        pipe.add_stage(PipelineStage("/var/log/actions"))  # audit

        result = pipe.run()

    Or using the builder pattern::

        result = (Pipeline("ooda", fs)
                  .read("/dev/camera")
                  .transform(brain_think)
                  .write("/dev/motor")
                  .append("/var/log/actions")
                  .run())
    """

    def __init__(self, name: str, ns: Namespace, principal: str = "brain"):
        self.name = name
        self.ns = ns
        self.principal = principal
        self._stages: List[PipelineStage] = []
        self._results: List[Any] = []

    def add_stage(self, stage: PipelineStage) -> "Pipeline":
        self._stages.append(stage)
        return self

    # Builder methods
    def read(self, path: str) -> "Pipeline":
        """Add a read stage that reads data from a filesystem path."""
        def _read(_input):
            return self.ns.read(path)
        _read.__name__ = f"read({path})"
        return self.add_stage(PipelineStage(_read))

    def write(self, path: str) -> "Pipeline":
        """Add a write stage that writes the pipeline data to a path."""
        def _write(data):
            self.ns.write(path, data)
            return data
        _write.__name__ = f"write({path})"
        return self.add_stage(PipelineStage(_write))

    def append(self, path: str) -> "Pipeline":
        """Add an append stage that appends pipeline data to a list at path."""
        def _append(data):
            self.ns.append(path, data)
            return data
        _append.__name__ = f"append({path})"
        return self.add_stage(PipelineStage(_append))

    def transform(self, fn: Callable) -> "Pipeline":
        """Add a transform stage using an arbitrary callable."""
        return self.add_stage(PipelineStage(fn))

    def run(self, initial: Any = None) -> Any:
        """Execute the pipeline, passing data through each stage.

        Returns the final output and stores intermediate results in
        ``self._results`` for inspection.
        """
        data = initial
        self._results = []
        logger.debug("Pipeline '%s' starting with %d stages", self.name,
                     len(self._stages))

        for i, stage in enumerate(self._stages):
            try:
                if callable(stage.operation):
                    data = stage.operation(data)
                else:
                    # String path -- auto-detect read vs write
                    if i == 0:
                        data = self.ns.read(stage.operation)
                    else:
                        self.ns.write(stage.operation, data)
                self._results.append({"stage": repr(stage), "ok": True})
            except Exception as exc:
                logger.error("Pipeline '%s' failed at stage %d (%s): %s",
                             self.name, i, stage, exc)
                self._results.append({"stage": repr(stage), "ok": False,
                                      "error": str(exc)})
                break

        logger.debug("Pipeline '%s' complete: %d/%d stages succeeded",
                     self.name, sum(1 for r in self._results if r["ok"]),
                     len(self._stages))
        return data

    @property
    def results(self) -> List[Dict]:
        """Inspection: results from the last run."""
        return list(self._results)
