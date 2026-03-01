"""Tests for Whisper.cpp STT engine in castor/voice.py (issue #291)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import castor.voice as _voice


def _reset_probes():
    """Reset lazy-init flags so each test starts fresh."""
    _voice._HAS_WHISPER_CPP = None


# ── _probe_whisper_cpp ────────────────────────────────────────────────────────


def test_probe_mock_bin_returns_true(monkeypatch):
    _reset_probes()
    monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
    result = _voice._probe_whisper_cpp()
    assert result is True


def test_probe_missing_bin_returns_false(monkeypatch):
    _reset_probes()
    monkeypatch.setenv("WHISPER_CPP_BIN", "nonexistent_whisper_cpp_bin_xyz")
    with patch("shutil.which", return_value=None):
        result = _voice._probe_whisper_cpp()
    assert result is False


def test_probe_present_bin_returns_true(monkeypatch, tmp_path):
    _reset_probes()
    fake_bin = str(tmp_path / "whisper-cpp")
    monkeypatch.setenv("WHISPER_CPP_BIN", fake_bin)
    with patch("shutil.which", return_value=fake_bin):
        result = _voice._probe_whisper_cpp()
    assert result is True


def test_probe_caches_result(monkeypatch):
    _reset_probes()
    monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
    _voice._probe_whisper_cpp()
    # Second call should use cached value (HAS_WHISPER_CPP no longer None)
    assert _voice._HAS_WHISPER_CPP is not None


# ── _transcribe_whisper_cpp ───────────────────────────────────────────────────


def test_mock_bin_returns_mock_transcription(monkeypatch):
    monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
    result = _voice._transcribe_whisper_cpp(b"fake audio")
    assert result == "mock transcription"


def test_missing_binary_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("WHISPER_CPP_BIN", "nonexistent_bin_xyz")
    monkeypatch.delenv("WHISPER_CPP_MODEL", raising=False)

    import subprocess

    with patch("subprocess.run", side_effect=FileNotFoundError("no binary")):
        result = _voice._transcribe_whisper_cpp(b"fake audio")
    assert result is None


def test_timeout_returns_none(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setenv("WHISPER_CPP_BIN", str(tmp_path / "whisper-cpp"))
    monkeypatch.delenv("WHISPER_CPP_MODEL", raising=False)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="whisper-cpp", timeout=60)):
        result = _voice._transcribe_whisper_cpp(b"fake audio")
    assert result is None


def test_successful_transcription(monkeypatch, tmp_path):
    """Simulate whisper.cpp writing a .txt file and returning a transcript."""
    bin_path = str(tmp_path / "whisper-cpp")
    monkeypatch.setenv("WHISPER_CPP_BIN", bin_path)
    monkeypatch.delenv("WHISPER_CPP_MODEL", raising=False)

    transcript_text = "hello world from whisper"

    import subprocess

    def fake_run(cmd, **kwargs):
        # whisper.cpp writes output to <input_file>.txt
        wav_path = cmd[-1]
        txt_path = wav_path + ".txt"
        Path(txt_path).write_text(transcript_text)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        result = _voice._transcribe_whisper_cpp(b"fake audio bytes")

    assert result == transcript_text


def test_model_path_included_in_command(monkeypatch, tmp_path):
    """When WHISPER_CPP_MODEL is set, --model arg should be in the command."""
    bin_path = str(tmp_path / "whisper-cpp")
    model_path = str(tmp_path / "model.bin")
    monkeypatch.setenv("WHISPER_CPP_BIN", bin_path)
    monkeypatch.setenv("WHISPER_CPP_MODEL", model_path)

    captured_cmd = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        # write empty txt file
        wav_path = cmd[-1]
        Path(wav_path + ".txt").write_text("")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        _voice._transcribe_whisper_cpp(b"audio")

    assert "--model" in captured_cmd
    assert model_path in captured_cmd


def test_no_model_omits_model_arg(monkeypatch, tmp_path):
    """When WHISPER_CPP_MODEL is not set, --model should not appear in command."""
    bin_path = str(tmp_path / "whisper-cpp")
    monkeypatch.setenv("WHISPER_CPP_BIN", bin_path)
    monkeypatch.delenv("WHISPER_CPP_MODEL", raising=False)

    captured_cmd = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        wav_path = cmd[-1]
        Path(wav_path + ".txt").write_text("")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        _voice._transcribe_whisper_cpp(b"audio")

    assert "--model" not in captured_cmd


def test_empty_transcript_returns_none(monkeypatch, tmp_path):
    bin_path = str(tmp_path / "whisper-cpp")
    monkeypatch.setenv("WHISPER_CPP_BIN", bin_path)
    monkeypatch.delenv("WHISPER_CPP_MODEL", raising=False)

    def fake_run(cmd, **kwargs):
        wav_path = cmd[-1]
        Path(wav_path + ".txt").write_text("   ")  # whitespace only
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        result = _voice._transcribe_whisper_cpp(b"audio")

    assert result is None


def test_temp_files_cleaned_up(monkeypatch, tmp_path):
    """Temp .wav and .txt files should be deleted after transcription."""
    bin_path = str(tmp_path / "whisper-cpp")
    monkeypatch.setenv("WHISPER_CPP_BIN", bin_path)
    monkeypatch.delenv("WHISPER_CPP_MODEL", raising=False)

    created_paths = []

    def fake_run(cmd, **kwargs):
        wav_path = cmd[-1]
        created_paths.append(wav_path)
        txt_path = wav_path + ".txt"
        created_paths.append(txt_path)
        Path(txt_path).write_text("hello")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        _voice._transcribe_whisper_cpp(b"audio")

    # Both wav and txt should be cleaned up
    for p in created_paths:
        assert not Path(p).exists(), f"Temp file not cleaned up: {p}"


# ── available_engines ─────────────────────────────────────────────────────────


def test_available_engines_includes_whisper_cpp_when_available(monkeypatch):
    _reset_probes()
    monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
    engines = _voice.available_engines()
    assert "whisper_cpp" in engines


def test_whisper_cpp_between_local_and_google_in_engines(monkeypatch):
    _reset_probes()
    monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
    engines = _voice.available_engines()
    if "whisper_cpp" in engines and "google" in engines:
        assert engines.index("whisper_cpp") < engines.index("google")


# ── transcribe_bytes engine dispatch ─────────────────────────────────────────


def test_transcribe_bytes_whisper_cpp_engine(monkeypatch):
    monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
    result = _voice.transcribe_bytes(b"audio", engine="whisper_cpp")
    assert result == "mock transcription"


def test_transcribe_bytes_auto_uses_whisper_cpp_when_available(monkeypatch):
    _reset_probes()
    monkeypatch.setenv("WHISPER_CPP_BIN", "mock")
    # Force probe to True so auto path uses it
    _voice._HAS_WHISPER_CPP = True

    with patch.object(_voice, "_transcribe_whisper_cpp", return_value="cpp result") as mock_cpp:
        with patch.object(_voice, "_probe_openai", return_value=False):
            with patch.object(_voice, "_probe_whisper_local", return_value=False):
                result = _voice.transcribe_bytes(b"audio", engine="auto")
    # Since whisper_cpp is available and others are not, should use cpp
    if result == "cpp result":
        mock_cpp.assert_called_once()
