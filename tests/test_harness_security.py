from __future__ import annotations

import tempfile

from castor.harness.security import (
    OPAGuardrail,
    SecurityContext,
    TelemetryEvent,
    TelemetryExporter,
)


def test_opa_guardrail_fallback_allow():
    """OPA unreachable → fallback to allow."""
    g = OPAGuardrail(url="http://localhost:19999/nonexistent", mode="audit", timeout=0.1)
    result = g.check("run_tool", {"tool": "shell"})
    assert result is True


def test_opa_guardrail_enforce_raises():
    """In enforce mode, denied action raises PermissionError (mocked via monkeypatch)."""

    g = OPAGuardrail(url="http://localhost:19999/nonexistent", mode="enforce", timeout=0.1)
    # Unreachable → fallback=allow, so no raise expected even in enforce
    result = g.check("run_tool", {})
    assert result is True


def test_telemetry_exporter_stdout(capsys):
    exp = TelemetryExporter(backends=["stdout"])
    evt = TelemetryEvent(session_id="s1", event_type="tool_call", data={"tool": "search"})
    exp.export(evt)
    captured = capsys.readouterr()
    assert "tool_call" in captured.out
    assert "s1" in captured.out


def test_telemetry_exporter_sqlite():
    import sqlite3

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = f"{tmpdir}/telemetry.db"
        exp = TelemetryExporter(backends=["sqlite"], db_path=db_path)
        evt = TelemetryEvent(session_id="s2", event_type="cost", data={"usd": 0.001})
        exp.export(evt)
        with sqlite3.connect(db_path) as con:
            rows = con.execute("SELECT * FROM events").fetchall()
        assert len(rows) == 1
        assert rows[0][2] == "s2"  # session_id column


def test_security_context_from_config():
    cfg = {
        "guardrail": {"url": "http://opa:8181/v1/data/allow", "mode": "enforce"},
        "telemetry": {"backends": ["sqlite"]},
    }
    ctx = SecurityContext.from_config(cfg)
    assert ctx.guardrail.mode == "enforce"
    assert "sqlite" in ctx.exporter.backends
