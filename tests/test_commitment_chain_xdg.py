"""Tests for XDG-compliant commitment chain log path resolution."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


def _reload_path(monkeypatch, env: dict[str, str | None]) -> Path:
    """Reload commitment_chain with the given env vars set and return DEFAULT_LOG_PATH."""
    for key, val in env.items():
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)

    import castor.rcan.commitment_chain as mod

    importlib.reload(mod)
    return mod._resolve_default_log_path()


def test_env_var_takes_priority(monkeypatch, tmp_path):
    """OPENCASTOR_COMMITMENT_LOG env var has highest priority."""
    custom = str(tmp_path / "custom.jsonl")
    path = _reload_path(
        monkeypatch,
        {
            "OPENCASTOR_COMMITMENT_LOG": custom,
            "XDG_DATA_HOME": str(tmp_path / "xdg"),
        },
    )
    assert path == Path(custom)


def test_xdg_data_home(monkeypatch, tmp_path):
    """XDG_DATA_HOME is used when OPENCASTOR_COMMITMENT_LOG is not set."""
    xdg = tmp_path / "xdg"
    path = _reload_path(
        monkeypatch,
        {
            "OPENCASTOR_COMMITMENT_LOG": None,
            "XDG_DATA_HOME": str(xdg),
        },
    )
    assert path == xdg / "opencastor" / "commitments.jsonl"


def test_default_fallback_no_xdg(monkeypatch, tmp_path):
    """Falls back to ~/.local/share/opencastor/commitments.jsonl when XDG_DATA_HOME not set."""
    monkeypatch.delenv("OPENCASTOR_COMMITMENT_LOG", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    # Patch Path.home() to our tmp dir so we don't write to the real homedir
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    path = _reload_path(monkeypatch, {})
    # Should be inside the tmp_path home
    assert "opencastor" in str(path)
    assert path.name == "commitments.jsonl"


def test_cwd_fallback_when_home_inaccessible(monkeypatch, tmp_path):
    """Falls back to CWD .opencastor-commitments.jsonl if home is inaccessible."""
    monkeypatch.delenv("OPENCASTOR_COMMITMENT_LOG", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    # Make Path.home() point to a read-only location that fails mkdir
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/nonexistent_readonly_path")))

    path = _reload_path(monkeypatch, {})
    assert path == Path(".opencastor-commitments.jsonl")
