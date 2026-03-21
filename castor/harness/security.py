"""OPA guardrail and telemetry exporter for AgentHarness (#744)."""

from __future__ import annotations

import dataclasses
import datetime
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


@dataclasses.dataclass
class TelemetryEvent:
    session_id: str
    event_type: str
    data: dict[str, Any]
    timestamp: str = dataclasses.field(
        default_factory=lambda: datetime.datetime.utcnow().isoformat()
    )


class OPAGuardrail:
    """Calls OPA policy endpoint; falls back to allow if unreachable."""

    def __init__(
        self,
        url: str = "http://localhost:8181/v1/data/castor/allow",
        mode: str = "audit",
        timeout: float = 1.0,
    ) -> None:
        self.url = url
        self.mode = mode
        self.timeout = timeout

    def check(self, action: str, context: dict[str, Any]) -> bool:
        """Returns True if allowed. Raises PermissionError in enforce mode if denied."""
        payload = json.dumps({"input": {"action": action, **context}}).encode()
        try:
            req = urllib.request.Request(
                self.url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read())
                allowed: bool = result.get("result", True)
        except (urllib.error.URLError, OSError):
            allowed = True  # fallback: allow when OPA unreachable
        if not allowed and self.mode == "enforce":
            raise PermissionError(f"OPA denied action: {action}")
        return allowed


class TelemetryExporter:
    """Exports telemetry events to stdout and/or SQLite."""

    def __init__(
        self,
        backends: list[str] | None = None,
        db_path: str | None = None,
    ) -> None:
        self.backends = backends or ["stdout"]
        self.db_path = Path(db_path or Path.home() / ".castor" / "telemetry.db")
        if "sqlite" in self.backends:
            self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    session_id TEXT,
                    event_type TEXT,
                    data_json TEXT
                )"""
            )

    def export(self, event: TelemetryEvent) -> None:
        if "stdout" in self.backends:
            print(
                f"[telemetry] {event.timestamp} {event.session_id} {event.event_type} {event.data}"
            )
        if "sqlite" in self.backends:
            try:
                with sqlite3.connect(self.db_path) as con:
                    con.execute(
                        "INSERT INTO events(timestamp,session_id,event_type,data_json)"
                        " VALUES(?,?,?,?)",
                        (
                            event.timestamp,
                            event.session_id,
                            event.event_type,
                            json.dumps(event.data),
                        ),
                    )
            except Exception:
                pass


@dataclasses.dataclass
class SecurityContext:
    guardrail: OPAGuardrail = dataclasses.field(default_factory=OPAGuardrail)
    exporter: TelemetryExporter = dataclasses.field(default_factory=TelemetryExporter)

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> SecurityContext:
        g_cfg = cfg.get("guardrail", {})
        t_cfg = cfg.get("telemetry", {})
        return cls(
            guardrail=OPAGuardrail(
                url=g_cfg.get("url", "http://localhost:8181/v1/data/castor/allow"),
                mode=g_cfg.get("mode", "audit"),
            ),
            exporter=TelemetryExporter(
                backends=t_cfg.get("backends", ["stdout"]),
            ),
        )
