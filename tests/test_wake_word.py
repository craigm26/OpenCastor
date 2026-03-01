"""Tests for wake-word detection additions in castor/voice.py (issue #317)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import castor.voice as _voice

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_probes():
    """Reset the lazy _HAS_WAKE_WORD flag between tests."""
    _voice._HAS_WAKE_WORD = None


# ---------------------------------------------------------------------------
# _probe_wake_word()
# ---------------------------------------------------------------------------


class TestProbeWakeWord:
    def setup_method(self):
        _reset_probes()

    def test_returns_true_when_mock_env_set(self, monkeypatch):
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        assert _voice._probe_wake_word() is True

    def test_returns_false_when_no_library_installed(self, monkeypatch):
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        # Ensure neither openwakeword nor pvporcupine is importable
        with patch.dict(sys.modules, {"openwakeword": None, "pvporcupine": None}):
            result = _voice._probe_wake_word()
        assert result is False

    def test_returns_true_when_openwakeword_importable(self, monkeypatch):
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        fake_oww = MagicMock()
        with patch.dict(sys.modules, {"openwakeword": fake_oww}):
            result = _voice._probe_wake_word()
        assert result is True

    def test_returns_true_when_pvporcupine_importable(self, monkeypatch):
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        fake_pv = MagicMock()
        # openwakeword not available, pvporcupine is
        with patch.dict(sys.modules, {"openwakeword": None, "pvporcupine": fake_pv}):
            result = _voice._probe_wake_word()
        assert result is True

    def test_caches_result_in_has_wake_word_flag(self, monkeypatch):
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        assert _voice._HAS_WAKE_WORD is None
        _voice._probe_wake_word()
        assert _voice._HAS_WAKE_WORD is True

    def test_caches_false_when_no_library(self, monkeypatch):
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        with patch.dict(sys.modules, {"openwakeword": None, "pvporcupine": None}):
            _voice._probe_wake_word()
        assert _voice._HAS_WAKE_WORD is False

    def test_does_not_re_probe_when_already_cached(self, monkeypatch):
        """If _HAS_WAKE_WORD is already set, probe returns it without checking env."""
        _voice._HAS_WAKE_WORD = True
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        # Even without mock env, cached True is returned
        assert _voice._probe_wake_word() is True


# ---------------------------------------------------------------------------
# WakeWordDetector — construction and properties
# ---------------------------------------------------------------------------


class TestWakeWordDetectorConstruction:
    def test_can_be_constructed_without_error(self):
        det = _voice.WakeWordDetector()
        assert det is not None

    def test_running_is_false_initially(self):
        det = _voice.WakeWordDetector()
        assert det.running is False

    def test_custom_sensitivity_and_model_stored(self):
        det = _voice.WakeWordDetector(sensitivity=0.8, model="alexa")
        assert det._sensitivity == 0.8
        assert det._model == "alexa"


# ---------------------------------------------------------------------------
# WakeWordDetector.start() / stop() in mock mode
# ---------------------------------------------------------------------------


class TestWakeWordDetectorMockMode:
    def setup_method(self):
        _reset_probes()

    def test_start_in_mock_mode_sets_running_true(self, monkeypatch):
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        det = _voice.WakeWordDetector()
        det.start(callback=None)
        try:
            assert det.running is True
        finally:
            det.stop()

    def test_stop_sets_running_false(self, monkeypatch):
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        det = _voice.WakeWordDetector()
        det.start(callback=None)
        det.stop()
        assert det.running is False

    def test_double_stop_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        det = _voice.WakeWordDetector()
        det.start(callback=None)
        det.stop()
        det.stop()  # second stop must not raise

    def test_start_without_callback_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        det = _voice.WakeWordDetector()
        try:
            det.start()  # no callback argument
        except TypeError:
            pytest.fail("start() raised TypeError when called without callback")
        finally:
            det.stop()


# ---------------------------------------------------------------------------
# WakeWordDetector — no library available (non-mock)
# ---------------------------------------------------------------------------


class TestWakeWordDetectorNoLibrary:
    def setup_method(self):
        _reset_probes()

    def test_start_without_library_does_not_raise(self, monkeypatch):
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        with patch.dict(sys.modules, {"openwakeword": None, "pvporcupine": None}):
            det = _voice.WakeWordDetector()
            # start() should log a warning but not raise
            det.start(callback=lambda t: None)
        # Thread was never started → running is False
        assert det.running is False


# ---------------------------------------------------------------------------
# get_wake_word_detector() factory
# ---------------------------------------------------------------------------


class TestGetWakeWordDetector:
    def setup_method(self):
        _reset_probes()

    def test_returns_wake_word_detector_instance(self, monkeypatch):
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        detector = _voice.get_wake_word_detector()
        assert isinstance(detector, _voice.WakeWordDetector)

    def test_reads_sensitivity_env_var(self, monkeypatch):
        monkeypatch.setenv("WAKE_WORD_SENSITIVITY", "0.75")
        detector = _voice.get_wake_word_detector()
        assert detector._sensitivity == pytest.approx(0.75)

    def test_reads_model_env_var(self, monkeypatch):
        monkeypatch.setenv("WAKE_WORD_MODEL", "alexa")
        detector = _voice.get_wake_word_detector()
        assert detector._model == "alexa"

    def test_explicit_args_override_env_vars(self, monkeypatch):
        monkeypatch.setenv("WAKE_WORD_SENSITIVITY", "0.9")
        monkeypatch.setenv("WAKE_WORD_MODEL", "alexa")
        detector = _voice.get_wake_word_detector(sensitivity=0.3, model="picovoice")
        assert detector._sensitivity == pytest.approx(0.3)
        assert detector._model == "picovoice"

    def test_default_sensitivity_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("WAKE_WORD_SENSITIVITY", raising=False)
        detector = _voice.get_wake_word_detector()
        assert detector._sensitivity == pytest.approx(0.5)

    def test_default_model_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("WAKE_WORD_MODEL", raising=False)
        detector = _voice.get_wake_word_detector()
        assert detector._model == "hey_jarvis"


# ---------------------------------------------------------------------------
# _VALID_ENGINES contains "wake_word"
# ---------------------------------------------------------------------------


class TestValidEngines:
    def test_wake_word_in_valid_engines(self):
        assert "wake_word" in _voice._VALID_ENGINES
