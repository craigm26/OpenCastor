"""Tests for wake-word audio streaming additions in castor/voice.py (issue #323)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import castor.voice as _voice
from castor.voice import MockAudioStream, get_audio_config, stream_audio_source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_probes():
    """Reset lazy probe caches between tests."""
    _voice._HAS_WAKE_WORD = None


# ---------------------------------------------------------------------------
# MockAudioStream
# ---------------------------------------------------------------------------


class TestMockAudioStream:
    def test_mock_audio_stream_read_returns_bytes(self):
        """read() must return a bytes object."""
        stream = MockAudioStream()
        result = stream.read(512)
        assert isinstance(result, bytes)

    def test_mock_audio_stream_read_size_correct(self):
        """read(n) must return exactly n * 2 bytes (int16 = 2 bytes/sample)."""
        stream = MockAudioStream()
        for chunk_size in (64, 512, 1024, 1280):
            result = stream.read(chunk_size)
            assert len(result) == chunk_size * 2, (
                f"Expected {chunk_size * 2} bytes for chunk_size={chunk_size}, got {len(result)}"
            )

    def test_mock_audio_stream_stop_no_crash(self):
        """stop_stream() must not raise any exception."""
        stream = MockAudioStream()
        stream.stop_stream()  # should be a no-op

    def test_mock_audio_stream_close_no_crash(self):
        """close() must not raise any exception."""
        stream = MockAudioStream()
        stream.close()  # should be a no-op

    def test_mock_audio_chunk_content_is_zeros(self):
        """read() must return zero-padded bytes."""
        stream = MockAudioStream()
        result = stream.read(256)
        assert result == bytes(256 * 2), "MockAudioStream.read() should return all-zero bytes"


# ---------------------------------------------------------------------------
# stream_audio_source context manager
# ---------------------------------------------------------------------------


class TestStreamAudioSource:
    def setup_method(self):
        _reset_probes()

    def test_stream_audio_source_mock_mode(self, monkeypatch):
        """When WAKE_WORD_BIN=mock, context manager must yield a MockAudioStream."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        with stream_audio_source() as stream:
            assert isinstance(stream, MockAudioStream)

    def test_stream_audio_source_mock_mode_read_works(self, monkeypatch):
        """In mock mode, the yielded stream's read() must return the right-sized bytes."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        with stream_audio_source(chunk_size=128) as stream:
            data = stream.read(128)
            assert isinstance(data, bytes)
            assert len(data) == 128 * 2

    def test_stream_audio_source_no_library_yields_mock(self, monkeypatch):
        """When neither pyaudio nor sounddevice is importable, must yield MockAudioStream."""
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        with patch.dict(sys.modules, {"pyaudio": None, "sounddevice": None}):
            with stream_audio_source() as stream:
                assert isinstance(stream, MockAudioStream)

    def test_stream_audio_source_no_library_read_returns_bytes(self, monkeypatch):
        """Fallback MockAudioStream read() must still return bytes when no library present."""
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        with patch.dict(sys.modules, {"pyaudio": None, "sounddevice": None}):
            with stream_audio_source(chunk_size=64) as stream:
                data = stream.read(64)
                assert isinstance(data, bytes)
                assert len(data) == 64 * 2


# ---------------------------------------------------------------------------
# get_audio_config()
# ---------------------------------------------------------------------------


class TestGetAudioConfig:
    def setup_method(self):
        _reset_probes()

    def test_get_audio_config_returns_dict(self, monkeypatch):
        """get_audio_config() must return a dict."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        result = get_audio_config()
        assert isinstance(result, dict)

    def test_get_audio_config_has_required_keys(self, monkeypatch):
        """Returned dict must contain device, sample_rate, chunk_size, library."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        result = get_audio_config()
        for key in ("device", "sample_rate", "chunk_size", "library"):
            assert key in result, f"Missing key: {key}"

    def test_get_audio_config_sample_rate_from_env(self, monkeypatch):
        """WAKE_WORD_SAMPLE_RATE env var must control the sample_rate value."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        monkeypatch.setenv("WAKE_WORD_SAMPLE_RATE", "8000")
        result = get_audio_config()
        assert result["sample_rate"] == 8000

    def test_get_audio_config_chunk_size_from_env(self, monkeypatch):
        """WAKE_WORD_CHUNK_SIZE env var must control the chunk_size value."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        monkeypatch.setenv("WAKE_WORD_CHUNK_SIZE", "1024")
        result = get_audio_config()
        assert result["chunk_size"] == 1024

    def test_get_audio_config_device_from_env(self, monkeypatch):
        """WAKE_WORD_AUDIO_DEVICE env var must be parsed as an int for the device key."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        monkeypatch.setenv("WAKE_WORD_AUDIO_DEVICE", "2")
        result = get_audio_config()
        assert result["device"] == 2

    def test_get_audio_config_device_default_is_none(self, monkeypatch):
        """When WAKE_WORD_AUDIO_DEVICE is not set, device must be None."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        monkeypatch.delenv("WAKE_WORD_AUDIO_DEVICE", raising=False)
        result = get_audio_config()
        assert result["device"] is None

    def test_get_audio_config_device_default_keyword(self, monkeypatch):
        """When WAKE_WORD_AUDIO_DEVICE='default', device must be None."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        monkeypatch.setenv("WAKE_WORD_AUDIO_DEVICE", "default")
        result = get_audio_config()
        assert result["device"] is None

    def test_get_audio_config_library_mock_when_bin_is_mock(self, monkeypatch):
        """library key must be 'mock' when WAKE_WORD_BIN=mock."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        result = get_audio_config()
        assert result["library"] == "mock"

    def test_get_audio_config_library_mock_when_no_audio_lib(self, monkeypatch):
        """library key must be 'mock' when neither pyaudio nor sounddevice is installed."""
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        with patch.dict(sys.modules, {"pyaudio": None, "sounddevice": None}):
            result = get_audio_config()
        assert result["library"] == "mock"

    def test_get_audio_config_default_sample_rate(self, monkeypatch):
        """Default sample_rate must be 16000."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        monkeypatch.delenv("WAKE_WORD_SAMPLE_RATE", raising=False)
        result = get_audio_config()
        assert result["sample_rate"] == 16000

    def test_get_audio_config_default_chunk_size(self, monkeypatch):
        """Default chunk_size must be 512."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        monkeypatch.delenv("WAKE_WORD_CHUNK_SIZE", raising=False)
        result = get_audio_config()
        assert result["chunk_size"] == 512


# ---------------------------------------------------------------------------
# WakeWordDetector.start() + audio stream integration
# ---------------------------------------------------------------------------


class TestDetectorStartUsesAudioStream:
    def setup_method(self):
        _reset_probes()

    def test_detector_start_uses_audio_stream(self, monkeypatch):
        """start() in mock mode must complete without raising."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        det = _voice.WakeWordDetector()
        try:
            det.start(callback=None)
        except Exception as exc:
            pytest.fail(f"start() raised an unexpected exception: {exc}")
        finally:
            det.stop()

    def test_detector_running_true_after_start(self, monkeypatch):
        """running property must be True immediately after start() in mock mode."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        det = _voice.WakeWordDetector()
        det.start(callback=None)
        try:
            assert det.running is True
        finally:
            det.stop()

    def test_detector_stop_sets_running_false(self, monkeypatch):
        """running must be False after stop()."""
        monkeypatch.setenv("WAKE_WORD_BIN", "mock")
        det = _voice.WakeWordDetector()
        det.start(callback=None)
        det.stop()
        assert det.running is False

    def test_detector_start_no_library_no_crash(self, monkeypatch):
        """start() must not raise when no library is available."""
        monkeypatch.delenv("WAKE_WORD_BIN", raising=False)
        with patch.dict(sys.modules, {"openwakeword": None, "pvporcupine": None}):
            det = _voice.WakeWordDetector()
            det.start(callback=lambda t: None)
        assert det.running is False
