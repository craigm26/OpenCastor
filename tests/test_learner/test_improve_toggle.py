"""Tests for castor improve --enable / --disable CLI shortcuts."""

import os
import types

import yaml

from castor.cli import _improve_toggle


def _make_args(**kwargs):
    defaults = {"enable": False, "disable": False, "config": None}
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def test_noop_when_neither_flag(tmp_path):
    assert _improve_toggle(_make_args()) is False


def test_enable_creates_learner_section(tmp_path):
    cfg = tmp_path / "bot.rcan.yaml"
    cfg.write_text(yaml.dump({"agent": {"provider": "ollama"}}))
    args = _make_args(enable=True, config=str(cfg))
    assert _improve_toggle(args) is True

    data = yaml.safe_load(cfg.read_text())
    assert data["learner"]["enabled"] is True
    assert data["learner"]["provider"] == "huggingface"
    assert data["learner"]["auto_apply_code"] is False


def test_enable_preserves_existing_learner(tmp_path):
    cfg = tmp_path / "bot.rcan.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "agent": {"provider": "ollama"},
                "learner": {
                    "enabled": False,
                    "provider": "google",
                    "model": "gemini-2.5-flash-lite",
                    "cadence_n": 1,
                },
            }
        )
    )
    args = _make_args(enable=True, config=str(cfg))
    _improve_toggle(args)

    data = yaml.safe_load(cfg.read_text())
    assert data["learner"]["enabled"] is True
    # Should preserve existing provider, not overwrite with default
    assert data["learner"]["provider"] == "google"
    assert data["learner"]["model"] == "gemini-2.5-flash-lite"


def test_disable(tmp_path):
    cfg = tmp_path / "bot.rcan.yaml"
    cfg.write_text(
        yaml.dump({"learner": {"enabled": True, "provider": "huggingface"}})
    )
    args = _make_args(disable=True, config=str(cfg))
    assert _improve_toggle(args) is True

    data = yaml.safe_load(cfg.read_text())
    assert data["learner"]["enabled"] is False
    # Provider config preserved
    assert data["learner"]["provider"] == "huggingface"


def test_disable_no_learner_section(tmp_path):
    cfg = tmp_path / "bot.rcan.yaml"
    cfg.write_text(yaml.dump({"agent": {"provider": "ollama"}}))
    args = _make_args(disable=True, config=str(cfg))
    _improve_toggle(args)

    data = yaml.safe_load(cfg.read_text())
    assert data["learner"]["enabled"] is False


def test_missing_config():
    args = _make_args(enable=True, config="/nonexistent/bot.rcan.yaml")
    assert _improve_toggle(args) is True  # Handled (printed error)


def test_auto_detect_single_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "bob.rcan.yaml"
    cfg.write_text(yaml.dump({"agent": {}}))
    args = _make_args(enable=True)  # No --config
    _improve_toggle(args)

    data = yaml.safe_load(cfg.read_text())
    assert data["learner"]["enabled"] is True
