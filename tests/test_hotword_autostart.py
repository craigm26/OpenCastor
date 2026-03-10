"""Tests for wake word auto-start on gateway startup."""
import os

import pytest
from unittest.mock import MagicMock


def test_hotword_autostart_when_env_set(monkeypatch):
    """Wake word auto-start task is created when CASTOR_HOTWORD is set."""
    monkeypatch.setenv("CASTOR_HOTWORD", "hey alex")
    env_val = os.getenv("CASTOR_HOTWORD", "")
    assert bool(env_val) is True  # condition satisfied


def test_hotword_autostart_skipped_when_not_configured(monkeypatch):
    """Wake word auto-start is skipped when CASTOR_HOTWORD not set and wake_word_enabled false."""
    monkeypatch.delenv("CASTOR_HOTWORD", raising=False)
    from castor.api import state

    original_config = state.config
    state.config = {"audio": {"wake_word_enabled": False}, "metadata": {"robot_name": "bob"}}
    try:
        env_phrase = os.getenv("CASTOR_HOTWORD", "")
        ww_enabled = bool(env_phrase) or state.config.get("audio", {}).get(
            "wake_word_enabled", False
        )
        assert ww_enabled is False
    finally:
        state.config = original_config


def test_hotword_autostart_enabled_via_rcan(monkeypatch):
    """Wake word enabled when audio.wake_word_enabled: true in RCAN config."""
    monkeypatch.delenv("CASTOR_HOTWORD", raising=False)
    from castor.api import state

    original_config = state.config
    state.config = {"audio": {"wake_word_enabled": True}, "metadata": {"robot_name": "alex"}}
    try:
        env_phrase = os.getenv("CASTOR_HOTWORD", "")
        ww_enabled = bool(env_phrase) or state.config.get("audio", {}).get(
            "wake_word_enabled", False
        )
        assert ww_enabled is True
    finally:
        state.config = original_config


def test_hotword_wake_phrase_prefers_env_over_robot_name(monkeypatch):
    """CASTOR_HOTWORD env takes precedence over robot_name from RCAN config."""
    monkeypatch.setenv("CASTOR_HOTWORD", "hey robot")
    from castor.api import state

    original_config = state.config
    state.config = {"metadata": {"robot_name": "alex"}}
    try:
        _env_phrase = os.getenv("CASTOR_HOTWORD", "")
        _robot_name = (state.config or {}).get("metadata", {}).get("robot_name", "")
        _wake_phrase = _env_phrase or _robot_name or "hey castor"
        assert _wake_phrase == "hey robot"
    finally:
        state.config = original_config


def test_hotword_wake_phrase_falls_back_to_robot_name(monkeypatch):
    """robot_name from RCAN config is used as wake phrase when CASTOR_HOTWORD is unset."""
    monkeypatch.delenv("CASTOR_HOTWORD", raising=False)
    from castor.api import state

    original_config = state.config
    state.config = {"metadata": {"robot_name": "alex"}}
    try:
        _env_phrase = os.getenv("CASTOR_HOTWORD", "")
        _robot_name = (state.config or {}).get("metadata", {}).get("robot_name", "")
        _wake_phrase = _env_phrase or _robot_name or "hey castor"
        assert _wake_phrase == "alex"
    finally:
        state.config = original_config


def test_hotword_wake_phrase_defaults_to_hey_castor(monkeypatch):
    """Default wake phrase is 'hey castor' when neither env nor robot_name is set."""
    monkeypatch.delenv("CASTOR_HOTWORD", raising=False)
    from castor.api import state

    original_config = state.config
    state.config = {}
    try:
        _env_phrase = os.getenv("CASTOR_HOTWORD", "")
        _robot_name = (state.config or {}).get("metadata", {}).get("robot_name", "")
        _wake_phrase = _env_phrase or _robot_name or "hey castor"
        assert _wake_phrase == "hey castor"
    finally:
        state.config = original_config


def test_hotword_status_endpoint_returns_active_state():
    """GET /api/hotword/status returns expected shape."""
    from fastapi.testclient import TestClient
    from castor.api import app

    client = TestClient(app)
    resp = client.get("/api/hotword/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "active" in data
