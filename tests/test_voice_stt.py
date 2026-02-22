"""Tests for the Listener STT class (issue #119) and /api/voice/listen endpoint."""

from __future__ import annotations

import collections
import time
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_listener_config(enabled: bool = True, **kwargs) -> dict:
    audio = {"stt_enabled": enabled}
    audio.update(kwargs)
    return {"audio": audio}


# ---------------------------------------------------------------------------
# Listener unit tests
# ---------------------------------------------------------------------------


class TestListenerClass:
    def test_listener_disabled_by_default(self):
        """No stt_enabled key -> enabled=False."""
        from castor.main import Listener

        listener = Listener({})
        assert listener.enabled is False

    def test_listener_enabled_config(self):
        """stt_enabled: true -> enabled=True (when SR is importable)."""
        from castor import main as main_mod
        from castor.main import Listener

        original = main_mod.HAS_SR
        main_mod.HAS_SR = True
        try:
            listener = Listener(_make_listener_config(enabled=True))
            assert listener.enabled is True
        finally:
            main_mod.HAS_SR = original

    def test_listener_disabled_when_stt_false(self):
        """stt_enabled: false -> enabled=False regardless of HAS_SR."""
        from castor import main as main_mod
        from castor.main import Listener

        original = main_mod.HAS_SR
        main_mod.HAS_SR = True
        try:
            listener = Listener(_make_listener_config(enabled=False))
            assert listener.enabled is False
        finally:
            main_mod.HAS_SR = original

    def test_listen_once_no_sr(self):
        """HAS_SR=False -> listen_once returns None immediately."""
        from castor import main as main_mod
        from castor.main import Listener

        original = main_mod.HAS_SR
        main_mod.HAS_SR = False
        try:
            listener = Listener(_make_listener_config(enabled=True))
            # enabled will be False because HAS_SR is False
            result = listener.listen_once()
            assert result is None
        finally:
            main_mod.HAS_SR = original

    def test_listen_once_success(self):
        """Mock recognizer returns transcript -> listen_once returns transcript."""
        from castor import main as main_mod
        from castor.main import Listener

        original = main_mod.HAS_SR = True
        try:
            mock_sr = MagicMock()
            mock_recognizer = MagicMock()
            mock_audio = MagicMock()
            mock_source = MagicMock()
            mock_source.__enter__ = MagicMock(return_value=mock_source)
            mock_source.__exit__ = MagicMock(return_value=False)

            mock_recognizer.listen.return_value = mock_audio
            mock_recognizer.recognize_google.return_value = "go forward"
            mock_recognizer.adjust_for_ambient_noise = MagicMock()
            mock_sr.Recognizer.return_value = mock_recognizer
            mock_sr.Microphone.return_value = mock_source

            with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
                listener = Listener(_make_listener_config(enabled=True))
                listener.enabled = True  # force enabled since HAS_SR patched at module level
                result = listener.listen_once()

            assert result == "go forward"
        finally:
            main_mod.HAS_SR = original

    def test_listen_once_unknown_value(self):
        """UnknownValueError from recognizer -> returns None."""
        from castor import main as main_mod
        from castor.main import Listener

        main_mod.HAS_SR = True
        try:
            mock_sr = MagicMock()
            mock_sr.UnknownValueError = Exception  # make it an actual exception class

            mock_recognizer = MagicMock()
            mock_audio = MagicMock()
            mock_source = MagicMock()
            mock_source.__enter__ = MagicMock(return_value=mock_source)
            mock_source.__exit__ = MagicMock(return_value=False)

            mock_recognizer.listen.return_value = mock_audio
            mock_recognizer.adjust_for_ambient_noise = MagicMock()
            mock_recognizer.recognize_google.side_effect = mock_sr.UnknownValueError("unrecognised")
            mock_sr.Recognizer.return_value = mock_recognizer
            mock_sr.Microphone.return_value = mock_source

            with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
                listener = Listener(_make_listener_config(enabled=True))
                listener.enabled = True
                result = listener.listen_once()

            assert result is None
        finally:
            main_mod.HAS_SR = True  # restore to a known truthy state

    def test_listener_energy_threshold_config(self):
        """energy_threshold is read from config."""
        from castor import main as main_mod
        from castor.main import Listener

        main_mod.HAS_SR = True
        try:
            listener = Listener(_make_listener_config(energy_threshold=500))
            assert listener.energy_threshold == 500
        finally:
            pass

    def test_listener_pause_threshold_config(self):
        """pause_threshold is read from config."""
        from castor import main as main_mod
        from castor.main import Listener

        main_mod.HAS_SR = True
        try:
            listener = Listener(_make_listener_config(pause_threshold=1.2))
            assert listener.pause_threshold == 1.2
        finally:
            pass


# ---------------------------------------------------------------------------
# API endpoint tests  /api/voice/listen
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset AppState before each test."""
    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)
    monkeypatch.delenv("OPENCASTOR_CONFIG", raising=False)

    import castor.api as api_mod

    api_mod.state.config = None
    api_mod.state.brain = None
    api_mod.state.driver = None
    api_mod.state.channels = {}
    api_mod.state.last_thought = None
    api_mod.state.boot_time = time.time()
    api_mod.state.fs = None
    api_mod.state.ruri = None
    api_mod.state.mdns_broadcaster = None
    api_mod.state.mdns_browser = None
    api_mod.state.rcan_router = None
    api_mod.state.capability_registry = None
    api_mod.state.offline_fallback = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.state.learner = None
    api_mod.state.listener = None
    api_mod.state.nav_job = None
    api_mod.API_TOKEN = None
    api_mod._command_history.clear()
    api_mod._webhook_history.clear()
    yield


@pytest.fixture()
def client():
    from castor.api import app

    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.router.on_startup[:] = original_startup
        app.router.on_shutdown[:] = original_shutdown


@pytest.fixture()
def api_mod():
    import castor.api as mod
    return mod


class TestVoiceListenEndpoint:
    def test_api_voice_listen_no_listener(self, client):
        """state.listener=None -> 503."""
        resp = client.post("/api/voice/listen")
        assert resp.status_code == 503
        body = resp.json()
        # The custom error handler maps HTTPException to {"error": ..., "status": ..., "code": ...}
        msg = body.get("detail") or body.get("error") or ""
        assert "Listener" in msg or "listener" in msg.lower()

    def test_api_voice_listen_disabled(self, client, api_mod):
        """listener.enabled=False -> 503."""
        mock_listener = MagicMock()
        mock_listener.enabled = False
        api_mod.state.listener = mock_listener
        resp = client.post("/api/voice/listen")
        assert resp.status_code == 503

    def test_api_voice_listen_returns_transcript(self, client, api_mod):
        """Mock listener returns transcript -> 200 with transcript field."""
        mock_listener = MagicMock()
        mock_listener.enabled = True
        mock_listener.listen_once.return_value = "move left"
        api_mod.state.listener = mock_listener

        resp = client.post("/api/voice/listen")
        assert resp.status_code == 200
        body = resp.json()
        assert body["transcript"] == "move left"

    def test_api_voice_listen_no_audio(self, client, api_mod):
        """listener.listen_once returns None -> 503."""
        mock_listener = MagicMock()
        mock_listener.enabled = True
        mock_listener.listen_once.return_value = None
        api_mod.state.listener = mock_listener

        resp = client.post("/api/voice/listen")
        assert resp.status_code == 503

    def test_api_voice_listen_thought_included(self, client, api_mod):
        """When brain is available, thought is included in response."""
        from castor.providers.base import Thought

        mock_listener = MagicMock()
        mock_listener.enabled = True
        mock_listener.listen_once.return_value = "go forward"
        api_mod.state.listener = mock_listener

        mock_brain = MagicMock()
        mock_brain.think.return_value = Thought("Moving forward!", {"type": "move", "linear": 0.5})
        api_mod.state.brain = mock_brain

        resp = client.post("/api/voice/listen")
        assert resp.status_code == 200
        body = resp.json()
        assert body["transcript"] == "go forward"
        assert body["thought"] is not None
        assert body["thought"]["raw_text"] == "Moving forward!"
