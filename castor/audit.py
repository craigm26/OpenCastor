"""
OpenCastor Audit Log -- append-only, tamper-evident record of all significant events.

Records motor commands, approval decisions, config changes, errors,
and who/what triggered each event. The log is append-only and cannot
be truncated by normal operations.

Each entry is hash-chained: the ``prev_hash`` field contains the SHA-256
digest of the previous entry's JSON line, forming a tamper-evident chain.
The first entry uses ``prev_hash: "GENESIS"``.

Log format (one JSON object per line)::

    {"ts": "...", "event": "motor_command", "action": {...}, "source": "brain", "prev_hash": "..."}

Usage:
    castor audit                          # View recent audit entries
    castor audit --since 24h             # Filter by time
    castor audit --event motor_command   # Filter by event type
    castor audit --verify                # Verify hash chain integrity
"""

import hashlib
import json
import logging
import os
import threading
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger("OpenCastor.Audit")

_AUDIT_FILE = ".opencastor-audit.log"


def _hash_entry(line: str) -> str:
    """Return the SHA-256 hex digest of *line* (stripped)."""
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only, hash-chained audit logger for significant robot events."""

    def __init__(self, log_path: str = None):
        self._path = log_path or _AUDIT_FILE
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _last_line(self) -> Optional[str]:
        """Return the last non-empty line of the log file, or *None*."""
        if not os.path.exists(self._path):
            return None
        last = None
        with open(self._path) as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    last = stripped
        return last

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, event: str, source: str = "system", **kwargs):
        """Append a hash-chained audit entry.

        Args:
            event: Event type (e.g. ``"motor_command"``, ``"approval_granted"``).
            source: What triggered this (e.g. ``"brain"``, ``"cli"``, ``"api"``).
            **kwargs: Additional event-specific data.
        """
        entry = {
            "ts": datetime.now().isoformat(),
            "event": event,
            "source": source,
        }
        entry.update(kwargs)

        with self._lock:
            # Compute prev_hash from the last line in the log
            last = self._last_line()
            if last is None:
                entry["prev_hash"] = "GENESIS"
            else:
                entry["prev_hash"] = _hash_entry(last)

            try:
                with open(self._path, "a") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
            except Exception as exc:
                logger.debug(f"Audit write failed: {exc}")

    # ------------------------------------------------------------------
    # Convenience loggers
    # ------------------------------------------------------------------

    def log_motor_command(self, action: dict, source: str = "brain"):
        """Log a motor command."""
        self.log(
            "motor_command",
            source=source,
            action_type=action.get("type", "?"),
            linear=action.get("linear"),
            angular=action.get("angular"),
        )

    def log_approval(self, approval_id: int, decision: str, source: str = "cli"):
        """Log an approval decision."""
        self.log("approval", source=source, id=approval_id, decision=decision)

    def log_config_change(self, file: str, source: str = "wizard"):
        """Log a config file change."""
        self.log("config_changed", source=source, file=file)

    def log_error(self, message: str, source: str = "runtime"):
        """Log an error."""
        self.log("error", source=source, message=str(message)[:500])

    def log_startup(self, config_path: str):
        """Log a runtime startup."""
        self.log("startup", source="runtime", config=config_path)

    def log_shutdown(self, reason: str = "normal"):
        """Log a runtime shutdown."""
        self.log("shutdown", source="runtime", reason=reason)

    # ------------------------------------------------------------------
    # Chain verification
    # ------------------------------------------------------------------

    def verify_chain(self) -> Tuple[bool, Optional[int]]:
        """Walk the log and verify every hash link.

        Returns:
            ``(True, None)`` if the chain is intact (or empty).
            ``(False, index)`` where *index* is the first broken link.
        """
        if not os.path.exists(self._path):
            return (True, None)

        lines: list[str] = []
        with open(self._path) as f:
            for raw in f:
                stripped = raw.strip()
                if stripped:
                    lines.append(stripped)

        if not lines:
            return (True, None)

        prev_line: Optional[str] = None
        chaining_started = False

        for idx, line in enumerate(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return (False, idx)

            if "prev_hash" not in entry:
                # Legacy entry without hash — skip, but track for next
                prev_line = line
                continue

            # This entry has prev_hash — chaining is active
            expected_prev = entry["prev_hash"]

            if not chaining_started and prev_line is None:
                # First entry overall — must be GENESIS
                if expected_prev != "GENESIS":
                    return (False, idx)
                chaining_started = True
                prev_line = line
                continue

            if not chaining_started and prev_line is not None:
                # First chained entry after legacy entries
                computed = _hash_entry(prev_line)
                if expected_prev != computed:
                    return (False, idx)
                chaining_started = True
                prev_line = line
                continue

            # Normal chained entry
            if prev_line is None:
                # Should not happen, but guard
                if expected_prev != "GENESIS":
                    return (False, idx)
            else:
                computed = _hash_entry(prev_line)
                if expected_prev != computed:
                    return (False, idx)

            prev_line = line

        return (True, None)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read(self, since: str = None, event: str = None, limit: int = 50) -> list:
        """Read audit entries with optional filters.

        Args:
            since: Time window (e.g. ``"24h"``, ``"7d"``).
            event: Filter by event type.
            limit: Max entries to return.
        """
        if not os.path.exists(self._path):
            return []

        cutoff = None
        if since:
            from castor.memory_search import _parse_since

            cutoff = _parse_since(since)

        entries = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Time filter
                if cutoff:
                    try:
                        entry_time = datetime.fromisoformat(entry["ts"])
                        if entry_time < cutoff:
                            continue
                    except Exception:
                        continue

                # Event filter
                if event and entry.get("event") != event:
                    continue

                entries.append(entry)

        # Return most recent entries
        return entries[-limit:]


# Global audit instance
_audit = AuditLog()


def get_audit() -> AuditLog:
    """Get the global audit log instance."""
    return _audit


def print_audit(entries: list):
    """Print audit entries."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if not entries:
        msg = "  No audit entries found."
        if has_rich:
            console.print(f"\n[dim]{msg}[/]\n")
        else:
            print(f"\n{msg}\n")
        return

    if has_rich:
        table = Table(title=f"Audit Log ({len(entries)} entries)", show_header=True)
        table.add_column("Time", style="dim", width=19)
        table.add_column("Event", style="bold")
        table.add_column("Source")
        table.add_column("Details")

        event_colors = {
            "motor_command": "cyan",
            "approval": "yellow",
            "config_changed": "blue",
            "error": "red",
            "startup": "green",
            "shutdown": "magenta",
        }

        for entry in entries:
            ts = entry.get("ts", "?")[:19]
            event = entry.get("event", "?")
            source = entry.get("source", "?")

            # Build details from remaining keys
            skip_keys = {"ts", "event", "source", "prev_hash"}
            details = ", ".join(
                f"{k}={v}" for k, v in entry.items() if k not in skip_keys and v is not None
            )

            color = event_colors.get(event, "white")
            table.add_row(ts, f"[{color}]{event}[/]", source, details[:60])

        console.print()
        console.print(table)
        console.print()
    else:
        print(f"\n  Audit Log ({len(entries)} entries):\n")
        for entry in entries:
            ts = entry.get("ts", "?")[:19]
            event = entry.get("event", "?")
            source = entry.get("source", "?")
            skip_keys = {"ts", "event", "source", "prev_hash"}
            details = ", ".join(
                f"{k}={v}" for k, v in entry.items() if k not in skip_keys and v is not None
            )
            print(f"  {ts}  {event:20s} [{source}] {details[:50]}")
        print()
