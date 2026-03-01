"""Tests for STT confidence score in castor.voice (issue #304).

Verifies that ``transcribe_bytes()`` returns a dict with keys ``text``,
``confidence``, and ``engine`` instead of a bare string.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import castor.voice as voice_mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_AUDIO = b"\x00" * 1024  # 1 KB of silence (sufficient for mocking)


def _reset_probes() -> None:
    """Reset lazy availability probes between tests."""
    voice_mod._HAS_OPENAI = None
    voice_mod._HAS_WHISPER_LOCAL = None
    voice_mod._HAS_WHISPER_CPP = None
    voice_mod._HAS_SPEECH_RECOGNITION = None


# ---------------------------------------------------------------------------
# _ENGINE_CONFIDENCE mapping
# ---------------------------------------------------------------------------


class TestEngineConfidenceMapping:
    def test_mapping_exists(self):
        assert hasattr(voice_mod, "_ENGINE_CONFIDENCE")

    def test_mapping_is_dict(self):
        assert isinstance(voice_mod._ENGINE_CONFIDENCE, dict)

    def test_whisper_cpp_confidence(self):
        assert voice_mod._ENGINE_CONFIDENCE["whisper_cpp"] == pytest.approx(0.85)

    def test_whisper_local_confidence(self):
        assert voice_mod._ENGINE_CONFIDENCE["whisper_local"] == pytest.approx(0.90)

    def test_openai_whisper_api_confidence(self):
        # Both "openai" and "whisper_api" keys should map to 0.95
        assert voice_mod._ENGINE_CONFIDENCE["whisper_api"] == pytest.approx(0.95)

    def test_google_confidence(self):
        assert voice_mod._ENGINE_CONFIDENCE["google"] == pytest.approx(0.80)

    def test_mock_confidence(self):
        assert voice_mod._ENGINE_CONFIDENCE["mock"] == pytest.approx(0.50)

    def test_all_values_between_zero_and_one(self):
        for engine, conf in voice_mod._ENGINE_CONFIDENCE.items():
            assert 0.0 <= conf <= 1.0, f"Confidence for {engine!r} out of range: {conf}"


# ---------------------------------------------------------------------------
# Return type: dict with required keys
# ---------------------------------------------------------------------------


class TestTranscribeBytesReturnType:
    def test_returns_dict_for_whisper_cpp_engine(self):
        with patch.object(voice_mod, "_transcribe_whisper_cpp", return_value="hello robot"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert isinstance(result, dict)

    def test_dict_has_text_key(self):
        with patch.object(voice_mod, "_transcribe_whisper_cpp", return_value="hello robot"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert "text" in result

    def test_dict_has_confidence_key(self):
        with patch.object(voice_mod, "_transcribe_whisper_cpp", return_value="hello robot"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert "confidence" in result

    def test_dict_has_engine_key(self):
        with patch.object(voice_mod, "_transcribe_whisper_cpp", return_value="hello robot"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert "engine" in result

    def test_confidence_is_float(self):
        with patch.object(voice_mod, "_transcribe_whisper_cpp", return_value="hello robot"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert isinstance(result["confidence"], float)

    def test_confidence_between_zero_and_one(self):
        with patch.object(voice_mod, "_transcribe_google_sr", return_value="turn right"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="google")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_engine_field_matches_requested_engine(self):
        with patch.object(voice_mod, "_transcribe_google_sr", return_value="turn right"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="google")
        assert result["engine"] == "google"

    def test_engine_field_whisper_local(self):
        with patch.object(voice_mod, "_transcribe_whisper_local", return_value="go forward"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_local")
        assert result["engine"] == "whisper_local"

    def test_engine_field_whisper_api(self):
        with patch.object(voice_mod, "_transcribe_whisper_api", return_value="spin around"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_api")
        assert result["engine"] == "whisper_api"


# ---------------------------------------------------------------------------
# Confidence values match _ENGINE_CONFIDENCE
# ---------------------------------------------------------------------------


class TestConfidenceValues:
    def test_whisper_cpp_confidence_matches_mapping(self):
        with patch.object(voice_mod, "_transcribe_whisper_cpp", return_value="test"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert result["confidence"] == pytest.approx(voice_mod._ENGINE_CONFIDENCE["whisper_cpp"])

    def test_whisper_local_confidence_matches_mapping(self):
        with patch.object(voice_mod, "_transcribe_whisper_local", return_value="test"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_local")
        assert result["confidence"] == pytest.approx(voice_mod._ENGINE_CONFIDENCE["whisper_local"])

    def test_whisper_api_confidence_matches_mapping(self):
        with patch.object(voice_mod, "_transcribe_whisper_api", return_value="test"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_api")
        assert result["confidence"] == pytest.approx(voice_mod._ENGINE_CONFIDENCE["whisper_api"])

    def test_google_confidence_matches_mapping(self):
        with patch.object(voice_mod, "_transcribe_google_sr", return_value="test"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="google")
        assert result["confidence"] == pytest.approx(voice_mod._ENGINE_CONFIDENCE["google"])


# ---------------------------------------------------------------------------
# None return on failure
# ---------------------------------------------------------------------------


class TestTranscribeBytesNoneOnFailure:
    def test_returns_none_for_empty_audio(self):
        result = voice_mod.transcribe_bytes(b"")
        assert result is None

    def test_returns_none_when_engine_returns_none(self):
        with patch.object(voice_mod, "_transcribe_whisper_cpp", return_value=None):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert result is None

    def test_returns_none_when_all_auto_engines_fail(self, monkeypatch):
        _reset_probes()
        voice_mod._HAS_OPENAI = False
        voice_mod._HAS_WHISPER_LOCAL = False
        voice_mod._HAS_WHISPER_CPP = False
        voice_mod._HAS_SPEECH_RECOGNITION = False
        result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="auto")
        assert result is None

    def test_none_is_not_a_dict(self):
        with patch.object(voice_mod, "_transcribe_google_sr", return_value=None):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="google")
        assert not isinstance(result, dict)


# ---------------------------------------------------------------------------
# Mock engine (WHISPER_CPP_BIN=mock)
# ---------------------------------------------------------------------------


class TestMockEngine:
    def test_mock_bin_returns_dict(self, monkeypatch):
        _reset_probes()
        monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
        voice_mod._HAS_WHISPER_CPP = None  # force re-probe
        result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert isinstance(result, dict)

    def test_mock_bin_text_field(self, monkeypatch):
        _reset_probes()
        monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
        voice_mod._HAS_WHISPER_CPP = None
        result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert result is not None
        assert result["text"] == "mock transcription"

    def test_mock_bin_engine_field(self, monkeypatch):
        _reset_probes()
        monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
        voice_mod._HAS_WHISPER_CPP = None
        result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert result is not None
        assert result["engine"] == "whisper_cpp"

    def test_mock_bin_confidence_is_whisper_cpp(self, monkeypatch):
        _reset_probes()
        monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
        voice_mod._HAS_WHISPER_CPP = None
        result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_cpp")
        assert result is not None
        assert result["confidence"] == pytest.approx(voice_mod._ENGINE_CONFIDENCE["whisper_cpp"])


# ---------------------------------------------------------------------------
# Auto engine routing
# ---------------------------------------------------------------------------


class TestAutoEngineRouting:
    def test_auto_returns_dict_or_none(self, monkeypatch):
        _reset_probes()
        voice_mod._HAS_OPENAI = False
        voice_mod._HAS_WHISPER_LOCAL = False
        voice_mod._HAS_WHISPER_CPP = False
        voice_mod._HAS_SPEECH_RECOGNITION = True

        with patch.object(voice_mod, "_transcribe_google_sr", return_value="auto text"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="auto")

        assert result is None or isinstance(result, dict)

    def test_auto_result_is_dict_when_engine_succeeds(self, monkeypatch):
        _reset_probes()
        voice_mod._HAS_OPENAI = False
        voice_mod._HAS_WHISPER_LOCAL = False
        voice_mod._HAS_WHISPER_CPP = False
        voice_mod._HAS_SPEECH_RECOGNITION = True

        with patch.object(voice_mod, "_transcribe_google_sr", return_value="auto text"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="auto")

        assert isinstance(result, dict)
        assert result["text"] == "auto text"
        assert result["engine"] == "google"

    def test_auto_dict_not_bare_string(self, monkeypatch):
        _reset_probes()
        voice_mod._HAS_OPENAI = False
        voice_mod._HAS_WHISPER_LOCAL = False
        voice_mod._HAS_WHISPER_CPP = False
        voice_mod._HAS_SPEECH_RECOGNITION = True

        with patch.object(voice_mod, "_transcribe_google_sr", return_value="some text"):
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="auto")

        assert not isinstance(result, str)


# ---------------------------------------------------------------------------
# available_engines() still works
# ---------------------------------------------------------------------------


class TestAvailableEnginesStillWorks:
    def test_available_engines_returns_list(self):
        result = voice_mod.available_engines()
        assert isinstance(result, list)

    def test_available_engines_contains_strings(self):
        _reset_probes()
        voice_mod._HAS_OPENAI = False
        voice_mod._HAS_WHISPER_LOCAL = False
        voice_mod._HAS_WHISPER_CPP = False
        voice_mod._HAS_SPEECH_RECOGNITION = False
        result = voice_mod.available_engines()
        assert all(isinstance(e, str) for e in result)

    def test_available_engines_not_dicts(self):
        _reset_probes()
        result = voice_mod.available_engines()
        assert all(not isinstance(e, dict) for e in result)
