"""Tests for castor.voice_loop."""

from unittest.mock import MagicMock, patch

import pytest

import castor.voice_loop as vl_mod
from castor.voice_loop import VoiceAssistantLoop, get_voice_loop


@pytest.fixture(autouse=True)
def reset_singleton():
    vl_mod._singleton = None
    yield
    vl_mod._singleton = None


class TestVoiceAssistantLoopInit:
    def test_init_defaults(self):
        loop = VoiceAssistantLoop()
        assert loop.state == "idle"
        assert loop.running is False
        assert loop._hotword == "hey castor"

    def test_init_custom_hotword(self):
        loop = VoiceAssistantLoop(hotword="hey robot")
        assert loop._hotword == "hey robot"

    def test_stats_initial(self):
        loop = VoiceAssistantLoop()
        s = loop.stats
        assert s["sessions"] == 0
        assert s["avg_stt_ms"] == 0.0


class TestVoiceAssistantLoopLifecycle:
    def test_start_sets_running(self):
        loop = VoiceAssistantLoop()
        mock_detector = MagicMock()
        mock_detector.start.side_effect = lambda on_wake: None

        with patch("castor.voice_loop.VoiceAssistantLoop._loop"):
            loop.start()
            assert loop.running is True
            loop.stop()

    def test_stop_clears_running(self):
        loop = VoiceAssistantLoop()
        loop._running = True
        loop.stop()
        assert loop.running is False

    def test_double_start_noop(self):
        loop = VoiceAssistantLoop()
        with patch.object(loop, "_loop"):
            loop.start()
            loop.start()  # second call should be noop
            assert loop._thread is not None
            loop.stop()


class TestVoiceAssistantLoopLLM:
    def test_llm_uses_on_command(self):
        cb = MagicMock(return_value="turning left")
        loop = VoiceAssistantLoop(on_command=cb)
        result = loop._llm("turn left")
        assert result == "turning left"
        cb.assert_called_once_with("turn left")

    def test_llm_uses_brain_fallback(self):
        brain = MagicMock()
        brain.think.return_value = MagicMock(raw_text="moving forward")
        loop = VoiceAssistantLoop(brain=brain)
        result = loop._llm("go forward")
        assert result == "moving forward"

    def test_llm_returns_empty_on_exception(self):
        cb = MagicMock(side_effect=Exception("fail"))
        loop = VoiceAssistantLoop(on_command=cb)
        result = loop._llm("test")
        assert result == ""

    def test_llm_no_brain_no_command(self):
        loop = VoiceAssistantLoop()
        result = loop._llm("test")
        assert result == ""


class TestVoiceAssistantLoopTTS:
    def test_tts_calls_say(self):
        loop = VoiceAssistantLoop()
        mock_tts = MagicMock()
        with patch("castor.tts_local.LocalTTS", return_value=mock_tts):
            loop._tts("hello world")
        mock_tts.say.assert_called_once_with("hello world")

    def test_tts_exception_suppressed(self):
        loop = VoiceAssistantLoop()
        with patch("castor.tts_local.LocalTTS", side_effect=Exception("no tts")):
            loop._tts("test")  # should not raise


class TestGetVoiceLoopSingleton:
    def test_returns_singleton(self):
        a = get_voice_loop()
        b = get_voice_loop()
        assert a is b

    def test_singleton_with_brain(self):
        brain = MagicMock()
        loop = get_voice_loop(brain=brain)
        assert loop._brain is brain
