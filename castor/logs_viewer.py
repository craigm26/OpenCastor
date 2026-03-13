"""castor.logs_viewer — tail and search commitment chain + event logs."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional
from collections.abc import Iterator

try:
    from rich.console import Console

    HAS_RICH = True
except ImportError:
    HAS_RICH = False

DEFAULT_LOG_PATHS = [
    Path(".opencastor-commitments.jsonl"),
    Path.home() / ".opencastor" / "commitments.jsonl",
    Path.home() / ".opencastor" / "events.jsonl",
]

LEVEL_COLOR = {
    "safety_block": "red",
    "action": "green",
    "commitment": "cyan",
    "streaming_action": "blue",
    "failover": "yellow",
}


def _find_log(path: Optional[Path] = None) -> Optional[Path]:
    if path and path.exists():
        return path
    for p in DEFAULT_LOG_PATHS:
        if p.exists():
            return p
    return None


def _read_jsonl(path: Path) -> Iterator[dict]:
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield {"raw": line, "_parse_error": True}
    except OSError as e:
        yield {"error": str(e)}


def _format_record(rec: dict, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(rec)
    ts = rec.get("timestamp", rec.get("ts", ""))[:19] if rec.get("timestamp") else ""
    event_type = rec.get("type", rec.get("action", "?"))
    confidence = rec.get(
        "confidence",
        rec.get("payload", {}).get("confidence") if isinstance(rec.get("payload"), dict) else None,
    )
    conf_str = f" [{confidence:.2f}]" if isinstance(confidence, float) else ""
    hmac = rec.get("hmac", "")[:8] if rec.get("hmac") else ""
    hmac_str = f" #{hmac}" if hmac else ""
    return f"{ts}  {event_type:<24}{conf_str}{hmac_str}"


def tail_logs(
    path: Optional[Path] = None,
    last: int = 50,
    grep: Optional[str] = None,
    follow: bool = False,
    as_json: bool = False,
) -> None:
    log_path = _find_log(path)
    if not log_path:
        print("No log file found. Run castor with RCAN config to generate logs.", file=sys.stderr)
        sys.exit(1)

    con = Console() if HAS_RICH and not as_json else None

    def _print(rec: dict) -> None:
        if grep and grep.lower() not in json.dumps(rec).lower():
            return
        line = _format_record(rec, as_json)
        if con:
            event_type = rec.get("type", rec.get("action", ""))
            color = LEVEL_COLOR.get(event_type, "white")
            con.print(f"[{color}]{line}[/{color}]")
        else:
            print(line)

    # Read existing records
    records = list(_read_jsonl(log_path))
    for rec in records[-last:]:
        _print(rec)

    if not follow:
        return

    # Follow mode — poll for new lines
    offset = log_path.stat().st_size
    if con:
        con.print(f"[dim]Following {log_path} …  (Ctrl-C to stop)[/dim]")
    try:
        while True:
            time.sleep(0.5)
            size = log_path.stat().st_size
            if size > offset:
                with open(log_path) as f:
                    f.seek(offset)
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                _print(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                offset = size
    except KeyboardInterrupt:
        pass
