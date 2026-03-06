"""Tests for check_rcan_compliance_version() in castor/doctor.py — issue #473."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from castor.doctor import check_rcan_compliance_version, run_all_checks

# Minimal compatibility JSON
COMPAT = {
    "updated": "2026-03-06",
    "spec_versions": [
        {"version": "1.0", "status": "archived", "notes": "old"},
        {"version": "1.1", "status": "archived", "notes": "older"},
        {"version": "1.2", "status": "current", "notes": "latest"},
    ],
}


def _write_rcan_yaml(tmp_path: Path, version: str) -> Path:
    p = tmp_path / "bob.rcan.yaml"
    p.write_text(f"rcan_version: '{version}'\nname: test-bot\n")
    return p


# ── return shape ───────────────────────────────────────────────────────────────


def test_returns_tuple_of_length_3():
    result = check_rcan_compliance_version()
    assert isinstance(result, tuple) and len(result) == 3


def test_first_element_is_bool():
    ok, _, _ = check_rcan_compliance_version()
    assert isinstance(ok, bool)


def test_second_element_is_rcan_compliance():
    _, name, _ = check_rcan_compliance_version()
    assert name == "RCAN compliance"


def test_third_element_is_string():
    _, _, detail = check_rcan_compliance_version()
    assert isinstance(detail, str)


# ── no config → graceful skip ──────────────────────────────────────────────────


def test_no_config_returns_ok_skip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CASTOR_CONFIG", raising=False)
    with patch("castor.doctor.Path.home", return_value=tmp_path):
        ok, name, detail = check_rcan_compliance_version()
    assert ok is True
    assert "skip" in detail.lower() or "no rcan_version" in detail.lower()


# ── version found and current ──────────────────────────────────────────────────


def test_current_version_returns_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_rcan_yaml(tmp_path, "1.2")
    monkeypatch.delenv("CASTOR_CONFIG", raising=False)

    # Cache the compat data so no network call
    cache_dir = tmp_path / ".opencastor"
    cache_dir.mkdir()
    cached = dict(COMPAT)
    cached["_cached_at"] = time.time()
    (cache_dir / "compat-cache.json").write_text(json.dumps(cached))

    with patch("castor.doctor.Path.home", return_value=tmp_path):
        ok, name, detail = check_rcan_compliance_version()

    assert ok is True
    assert "1.2" in detail
    assert "current" in detail


# ── archived version returns not-ok ───────────────────────────────────────────


def test_archived_version_returns_warn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_rcan_yaml(tmp_path, "1.0")
    monkeypatch.delenv("CASTOR_CONFIG", raising=False)

    cache_dir = tmp_path / ".opencastor"
    cache_dir.mkdir()
    cached = dict(COMPAT)
    cached["_cached_at"] = time.time()
    (cache_dir / "compat-cache.json").write_text(json.dumps(cached))

    with patch("castor.doctor.Path.home", return_value=tmp_path):
        ok, name, detail = check_rcan_compliance_version()

    assert ok is False
    assert "upgrade" in detail.lower() or "archived" in detail.lower()


# ── unknown version not in matrix ─────────────────────────────────────────────


def test_unknown_version_returns_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_rcan_yaml(tmp_path, "9.9")
    monkeypatch.delenv("CASTOR_CONFIG", raising=False)

    cache_dir = tmp_path / ".opencastor"
    cache_dir.mkdir()
    cached = dict(COMPAT)
    cached["_cached_at"] = time.time()
    (cache_dir / "compat-cache.json").write_text(json.dumps(cached))

    with patch("castor.doctor.Path.home", return_value=tmp_path):
        ok, name, detail = check_rcan_compliance_version()

    assert ok is False
    assert "9.9" in detail


# ── network failure → graceful fallback ───────────────────────────────────────


def test_network_failure_graceful(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_rcan_yaml(tmp_path, "1.2")
    monkeypatch.delenv("CASTOR_CONFIG", raising=False)

    # No cache dir → will try network
    cache_dir = tmp_path / ".opencastor"
    cache_dir.mkdir()

    with patch("castor.doctor.Path.home", return_value=tmp_path):
        with patch("urllib.request.urlopen", side_effect=OSError("no network")):
            ok, name, detail = check_rcan_compliance_version()

    assert ok is True  # graceful fallback
    assert "could not fetch" in detail.lower()


# ── stale cache is refreshed ──────────────────────────────────────────────────


def test_stale_cache_triggers_fetch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_rcan_yaml(tmp_path, "1.2")
    monkeypatch.delenv("CASTOR_CONFIG", raising=False)

    cache_dir = tmp_path / ".opencastor"
    cache_dir.mkdir()
    stale = dict(COMPAT)
    stale["_cached_at"] = time.time() - 90000  # >24h ago
    (cache_dir / "compat-cache.json").write_text(json.dumps(stale))

    fresh = dict(COMPAT)
    fresh["_cached_at"] = time.time()
    fresh_bytes = json.dumps(fresh).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = fresh_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("castor.doctor.Path.home", return_value=tmp_path):
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_url:
            ok, name, detail = check_rcan_compliance_version()

    mock_url.assert_called_once()
    assert ok is True


# ── CASTOR_CONFIG env var is respected ────────────────────────────────────────


def test_castor_config_env_var(tmp_path, monkeypatch):
    cfg = tmp_path / "custom.rcan.yaml"
    cfg.write_text("rcan_version: '1.2'\n")
    monkeypatch.setenv("CASTOR_CONFIG", str(cfg))

    cache_dir = tmp_path / ".opencastor"
    cache_dir.mkdir()
    cached = dict(COMPAT)
    cached["_cached_at"] = time.time()
    (cache_dir / "compat-cache.json").write_text(json.dumps(cached))

    with patch("castor.doctor.Path.home", return_value=tmp_path):
        ok, name, detail = check_rcan_compliance_version()

    assert ok is True
    assert "1.2" in detail


# ── run_all_checks includes the new check ─────────────────────────────────────


def test_run_all_checks_includes_rcan_compliance():
    results = run_all_checks()
    names = [r[1] for r in results]
    assert "RCAN compliance" in names
