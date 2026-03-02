"""Tests for castor doctor --auto-fix — issue #362."""

from __future__ import annotations

import os
import sqlite3
import time

import pytest


# ── _fix_env_file ─────────────────────────────────────────────────────────────


def test_fix_env_file_creates_from_example(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("GOOGLE_API_KEY=\nANTHROPIC_API_KEY=\n")
    from castor.doctor import _fix_env_file

    result = _fix_env_file()
    assert result is True
    assert (tmp_path / ".env").exists()
    out = capsys.readouterr().out
    assert "FIXED" in out


def test_fix_env_file_skips_when_env_exists(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("already exists\n")
    (tmp_path / ".env.example").write_text("template\n")
    from castor.doctor import _fix_env_file

    result = _fix_env_file()
    assert result is False
    out = capsys.readouterr().out
    assert "SKIP" in out


def test_fix_env_file_skips_when_no_example(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    # No .env and no .env.example
    from castor.doctor import _fix_env_file

    result = _fix_env_file()
    assert result is False
    out = capsys.readouterr().out
    assert "SKIP" in out


def test_fix_env_file_copies_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("KEY=value\n")
    from castor.doctor import _fix_env_file

    _fix_env_file()
    assert (tmp_path / ".env").read_text() == "KEY=value\n"


# ── _fix_memory_db ────────────────────────────────────────────────────────────


def _make_db(tmp_path, n_old=5, n_new=3):
    db = str(tmp_path / "test_mem.db")
    now = int(time.time())
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE episodes (
            id TEXT PRIMARY KEY, timestamp INTEGER, instruction TEXT,
            raw_thought TEXT, action_json TEXT, latency_ms REAL,
            outcome TEXT, source TEXT, image_blob BLOB, tags TEXT
        )"""
    )
    # Old episodes (>30 days)
    old_ts = now - 40 * 86400
    for i in range(n_old):
        con.execute(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"old-{i}", old_ts, f"old {i}", "t", "{}", 0, "", "", None, ""),
        )
    # New episodes (<30 days)
    for i in range(n_new):
        con.execute(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"new-{i}", now - 1, f"new {i}", "t", "{}", 0, "", "", None, ""),
        )
    con.commit()
    con.close()
    return db


def test_fix_memory_db_deletes_old_episodes(tmp_path, monkeypatch, capsys):
    db = _make_db(tmp_path, n_old=4, n_new=2)
    monkeypatch.setenv("CASTOR_MEMORY_DB", db)
    from castor.doctor import _fix_memory_db

    result = _fix_memory_db()
    assert result is True
    con = sqlite3.connect(db)
    remaining = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    con.close()
    assert remaining == 2  # only the 2 new ones remain
    out = capsys.readouterr().out
    assert "FIXED" in out


def test_fix_memory_db_skips_when_no_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CASTOR_MEMORY_DB", str(tmp_path / "nonexistent.db"))
    from castor.doctor import _fix_memory_db

    result = _fix_memory_db()
    assert result is False
    out = capsys.readouterr().out
    assert "SKIP" in out


def test_fix_memory_db_shows_deleted_count(tmp_path, monkeypatch, capsys):
    db = _make_db(tmp_path, n_old=3, n_new=0)
    monkeypatch.setenv("CASTOR_MEMORY_DB", db)
    from castor.doctor import _fix_memory_db

    _fix_memory_db()
    out = capsys.readouterr().out
    assert "3" in out or "deleted" in out.lower()


# ── run_auto_fix ──────────────────────────────────────────────────────────────


def test_run_auto_fix_skips_passed_checks(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from castor.doctor import run_auto_fix

    # All checks passing — nothing to fix
    results = [(True, ".env file", "found"), (True, "Memory DB", "ok (1.2 MB)")]
    run_auto_fix(results)
    out = capsys.readouterr().out
    assert "No automatic fixes" in out


def test_run_auto_fix_fixes_env_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("KEY=\n")
    from castor.doctor import run_auto_fix

    results = [(False, ".env file", "missing")]
    run_auto_fix(results)
    assert (tmp_path / ".env").exists()
    out = capsys.readouterr().out
    assert "FIXED" in out


def test_run_auto_fix_fixes_large_memory_db(tmp_path, monkeypatch, capsys):
    db = _make_db(tmp_path, n_old=5, n_new=1)
    monkeypatch.setenv("CASTOR_MEMORY_DB", db)
    from castor.doctor import run_auto_fix

    results = [(False, "Memory DB", "large (150.3 MB) — consider running")]
    run_auto_fix(results)
    out = capsys.readouterr().out
    assert "FIXED" in out


def test_run_auto_fix_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from castor.doctor import run_auto_fix

    result = run_auto_fix([])
    assert result is None


def test_run_auto_fix_unfixable_check_does_not_raise(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from castor.doctor import run_auto_fix

    # Check with a name that has no auto-fix handler
    results = [(False, "SDK: OpenCV", "not installed"), (False, "Camera", "not accessible")]
    run_auto_fix(results)  # Must not raise


# ── CLI --auto-fix flag ───────────────────────────────────────────────────────


def test_cli_doctor_help_has_auto_fix():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "castor.cli", "doctor", "--help"],
        capture_output=True,
        text=True,
    )
    assert "--auto-fix" in result.stdout
