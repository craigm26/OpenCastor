"""Tests for castor.voice — tiered audio transcription module (#85)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

import castor.voice as voice_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_AUDIO = b"\x00" * 1024  # 1 KB of silence (valid enough for mocking)


def _reset_probes():
    """Reset lazy availability probes between tests."""
    voice_mod._HAS_OPENAI = None
    voice_mod._HAS_WHISPER_LOCAL = None
    voice_mod._HAS_SPEECH_RECOGNITION = None


# ---------------------------------------------------------------------------
# available_engines()
# ---------------------------------------------------------------------------


class TestAvailableEngines:
    def test_returns_list(self):
        _reset_probes()
        result = voice_mod.available_engines()
        assert isinstance(result, list)

    def test_no_key_excludes_whisper_api(self, monkeypatch):
        _reset_probes()
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        engines = voice_mod.available_engines()
        assert "whisper_api" not in engines

    def test_key_present_includes_whisper_api_when_openai_importable(self, monkeypatch):
        _reset_probes()
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        # Patch the import so openai appears importable
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            voice_mod._HAS_OPENAI = None  # force re-probe
            engines = voice_mod.available_engines()
        assert "whisper_api" in engines


# ---------------------------------------------------------------------------
# transcribe_bytes() — engine routing
# ---------------------------------------------------------------------------


class TestTranscribeBytes:
    def test_returns_none_for_empty_bytes(self):
        assert voice_mod.transcribe_bytes(b"") is None

    def test_whisper_api_engine_selected(self):
        with patch.object(voice_mod, "_transcribe_whisper_api", return_value="hello") as mock_fn:
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_api")
        mock_fn.assert_called_once()
        assert result == "hello"

    def test_whisper_local_engine_selected(self):
        with patch.object(voice_mod, "_transcribe_whisper_local", return_value="world") as mock_fn:
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="whisper_local")
        mock_fn.assert_called_once()
        assert result == "world"

    def test_google_engine_selected(self):
        with patch.object(voice_mod, "_transcribe_google_sr", return_value="go left") as mock_fn:
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="google")
        mock_fn.assert_called_once()
        assert result == "go left"

    def test_auto_tries_whisper_api_first(self, monkeypatch):
        _reset_probes()
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with (
            patch.dict("sys.modules", {"openai": MagicMock()}),
            patch.object(voice_mod, "_transcribe_whisper_api", return_value="auto result") as api_mock,
        ):
            voice_mod._HAS_OPENAI = None
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="auto")
        api_mock.assert_called_once()
        assert result == "auto result"

    def test_auto_falls_back_to_google_when_whisper_unavailable(self, monkeypatch):
        _reset_probes()
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        voice_mod._HAS_OPENAI = False
        voice_mod._HAS_WHISPER_LOCAL = False
        voice_mod._HAS_SPEECH_RECOGNITION = True

        with patch.object(voice_mod, "_transcribe_google_sr", return_value="fallback") as gsr_mock:
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="auto")
        gsr_mock.assert_called_once()
        assert result == "fallback"

    def test_auto_returns_none_when_all_engines_fail(self, monkeypatch):
        _reset_probes()
        voice_mod._HAS_OPENAI = False
        voice_mod._HAS_WHISPER_LOCAL = False
        voice_mod._HAS_SPEECH_RECOGNITION = False

        result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="auto")
        assert result is None

    def test_env_var_overrides_engine(self, monkeypatch):
        _reset_probes()
        monkeypatch.setenv("CASTOR_VOICE_ENGINE", "google")
        with patch.object(voice_mod, "_transcribe_google_sr", return_value="env override") as gsr_mock:
            result = voice_mod.transcribe_bytes(_DUMMY_AUDIO, engine="auto")
        gsr_mock.assert_called_once()
        assert result == "env override"

    def test_hint_format_passed_through(self):
        with patch.object(voice_mod, "_transcribe_google_sr", return_value="ok") as gsr_mock:
            voice_mod.transcribe_bytes(_DUMMY_AUDIO, hint_format="mp3", engine="google")
        _, kwargs = gsr_mock.call_args
        assert kwargs.get("hint_format") == "mp3" or gsr_mock.call_args[0][1] == "mp3"


# ---------------------------------------------------------------------------
# _split_sentences (via Speaker) integration smoke test
# ---------------------------------------------------------------------------


class TestSpeakerSentenceChunking:
    """Verify the Speaker._split_sentences helper (imported from main)."""

    def test_short_text_returned_as_one_chunk(self):
        from castor.main import Speaker

        chunks = Speaker._split_sentences("Hello world.")
        assert chunks == ["Hello world."]

    def test_long_text_split_at_sentence_boundary(self):
        from castor.main import Speaker

        text = "Go forward. Turn left. Stop and wait."
        chunks = Speaker._split_sentences(text)
        assert len(chunks) == 3
        assert "Go forward." in chunks[0]

    def test_very_long_sentence_split_by_whitespace(self):
        from castor.main import Speaker

        long_word_seq = " ".join(["word"] * 200)  # 900+ chars
        chunks = Speaker._split_sentences(long_word_seq, max_chunk=100)
        assert all(len(c) <= 100 for c in chunks)

    def test_empty_string_returns_single_chunk(self):
        from castor.main import Speaker

        chunks = Speaker._split_sentences("", max_chunk=500)
        # Should return a list; may be empty or contain empty string
        assert isinstance(chunks, list)

    def test_no_200_char_truncation(self):
        from castor.main import Speaker

        text = "Hello world. " * 20  # 260 chars, should NOT be truncated
        chunks = Speaker._split_sentences(text)
        total_chars = sum(len(c) for c in chunks)
        # All characters must be preserved (minus whitespace trimming)
        assert total_chars >= len(text.strip()) - len(chunks)  # allow for join whitespace
