"""
OpenCastor Audit Log -- append-only record of all significant events.

Records motor commands, approval decisions, config changes, errors,
and who/what triggered each event. The log is append-only and cannot
be truncated by normal operations.

Log format (one JSON object per line)::

    {"ts": "...", "event": "motor_command", "action": {...}, "source": "brain"}
    {"ts": "...", "event": "approval_granted", "id": 1, "source": "cli"}
    {"ts": "...", "event": "config_changed", "file": "robot.rcan.yaml", "source": "wizard"}
    {"ts": "...", "event": "error", "message": "...", "source": "runtime"}

Usage:
    castor audit                          # View recent audit entries
    castor audit --since 24h             # Filter by time
    castor audit --event motor_command   # Filter by event type
"""

import json
import logging
import os
import threading
from datetime import datetime

logger = logging.getLogger("OpenCastor.Audit")

_AUDIT_FILE = ".opencastor-audit.log"


class AuditLog:
    """Append-only audit logger for significant robot events."""

    def __init__(self, log_path: str = None):
        self._path = log_path or _AUDIT_FILE
        self._lock = threading.Lock()

    def log(self, event: str, source: str = "system", **kwargs):
        """Append an audit entry.

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
            try:
                with open(self._path, "a") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
            except Exception as exc:
                logger.debug(f"Audit write failed: {exc}")

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
            skip_keys = {"ts", "event", "source"}
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
            skip_keys = {"ts", "event", "source"}
            details = ", ".join(
                f"{k}={v}" for k, v in entry.items() if k not in skip_keys and v is not None
            )
            print(f"  {ts}  {event:20s} [{source}] {details[:50]}")
        print()
