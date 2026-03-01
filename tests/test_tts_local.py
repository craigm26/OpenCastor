"""Tests for castor/tts_local.py (issue #138)."""

from unittest.mock import MagicMock, patch

from castor.tts_local import LocalTTS, _select_engine, available_engines

# ---------------------------------------------------------------------------
# available_engines / _select_engine
# ---------------------------------------------------------------------------


def test_available_engines_empty():
    """When no TTS library is installed, available_engines() returns []."""
    with (
        patch("castor.tts_local.HAS_PIPER", False),
        patch("castor.tts_local.HAS_COQUI", False),
        patch("castor.tts_local.HAS_GTTS", False),
    ):
        assert available_engines() == []


def test_available_engines_piper_only():
    with (
        patch("castor.tts_local.HAS_PIPER", True),
        patch("castor.tts_local.HAS_COQUI", False),
        patch("castor.tts_local.HAS_GTTS", False),
    ):
        assert available_engines() == ["piper"]


def test_available_engines_all():
    with (
        patch("castor.tts_local.HAS_PIPER", True),
        patch("castor.tts_local.HAS_COQUI", True),
        patch("castor.tts_local.HAS_GTTS", True),
    ):
        engines = available_engines()
        assert "piper" in engines
        assert "coqui" in engines
        assert "gtts" in engines


def test_select_engine_auto_none_available():
    with (
        patch("castor.tts_local.HAS_PIPER", False),
        patch("castor.tts_local.HAS_COQUI", False),
        patch("castor.tts_local.HAS_GTTS", False),
    ):
        assert _select_engine("auto") == "none"


def test_select_engine_auto_picks_first():
    with (
        patch("castor.tts_local.HAS_PIPER", False),
        patch("castor.tts_local.HAS_COQUI", False),
        patch("castor.tts_local.HAS_GTTS", True),
    ):
        assert _select_engine("auto") == "gtts"


def test_select_engine_explicit_unavailable_falls_back():
    with (
        patch("castor.tts_local.HAS_PIPER", False),
        patch("castor.tts_local.HAS_COQUI", False),
        patch("castor.tts_local.HAS_GTTS", False),
    ):
        # Requesting piper when unavailable should fall back to "none"
        assert _select_engine("piper") == "none"


def test_select_engine_explicit_available():
    with (
        patch("castor.tts_local.HAS_PIPER", True),
        patch("castor.tts_local.HAS_COQUI", False),
        patch("castor.tts_local.HAS_GTTS", False),
    ):
        assert _select_engine("piper") == "piper"


# ---------------------------------------------------------------------------
# LocalTTS engine property + synthesize edge cases
# ---------------------------------------------------------------------------


def test_local_tts_none_engine():
    with (
        patch("castor.tts_local.HAS_PIPER", False),
        patch("castor.tts_local.HAS_COQUI", False),
        patch("castor.tts_local.HAS_GTTS", False),
    ):
        tts = LocalTTS(engine="auto")
        assert tts.engine == "none"


def test_synthesize_returns_empty_for_none_engine():
    with (
        patch("castor.tts_local.HAS_PIPER", False),
        patch("castor.tts_local.HAS_COQUI", False),
        patch("castor.tts_local.HAS_GTTS", False),
    ):
        tts = LocalTTS(engine="auto")
        assert tts.synthesize("hello") == b""


def test_synthesize_returns_empty_for_empty_text():
    """synthesize() with empty string returns b'' regardless of engine."""
    with patch("castor.tts_local.HAS_GTTS", True):
        tts = LocalTTS(engine="gtts")
        assert tts.synthesize("") == b""


# ---------------------------------------------------------------------------
# _synth_gtts
# ---------------------------------------------------------------------------


def test_synth_gtts_success():
    """_synth_gtts() returns bytes from gTTS write_to_fp."""
    fake_audio = b"FAKE_MP3_DATA"

    tts = LocalTTS.__new__(LocalTTS)
    tts._engine = "gtts"
    tts._language = "en"

    import sys

    class FakeGTTS:
        def __init__(self, **kwargs):
            pass

        def write_to_fp(self, fp):
            fp.write(fake_audio)

    gtts_mod = MagicMock()
    gtts_mod.gTTS = FakeGTTS
    with patch.dict(sys.modules, {"gtts": gtts_mod}):
        result = tts._synth_gtts("hello world")
    assert result == fake_audio


def test_synth_gtts_error_returns_empty():
    """_synth_gtts() returns b'' if gTTS raises."""
    tts = LocalTTS.__new__(LocalTTS)
    tts._engine = "gtts"
    tts._language = "en"

    import sys

    gtts_mod = MagicMock()
    gtts_mod.gTTS.side_effect = RuntimeError("network error")
    with patch.dict(sys.modules, {"gtts": gtts_mod}):
        result = tts._synth_gtts("hello")
        assert result == b""


# ---------------------------------------------------------------------------
# _synth_piper / _synth_coqui errors → b""
# ---------------------------------------------------------------------------


def test_synth_piper_error_returns_empty():
    tts = LocalTTS.__new__(LocalTTS)
    tts._engine = "piper"
    tts._model = None
    tts._piper_voice = None

    import sys

    piper_mod = MagicMock()
    piper_mod.PiperVoice.load.side_effect = RuntimeError("no model")
    with patch.dict(sys.modules, {"piper": piper_mod}):
        assert tts._synth_piper("hello") == b""


def test_synth_coqui_error_returns_empty():
    tts = LocalTTS.__new__(LocalTTS)
    tts._engine = "coqui"
    tts._model = None
    tts._coqui_tts = None

    import sys

    tts_mod = MagicMock()
    tts_mod.api.TTS.side_effect = RuntimeError("no model")
    with patch.dict(sys.modules, {"TTS": tts_mod, "TTS.api": tts_mod.api}):
        assert tts._synth_coqui("hello") == b""


# ---------------------------------------------------------------------------
# say() — no audio hardware
# ---------------------------------------------------------------------------


def test_say_no_crash_without_pygame():
    """say() should not raise even when pygame is unavailable."""
    tts = LocalTTS.__new__(LocalTTS)
    tts._engine = "none"

    # synthesize returns b"", say() should return immediately
    tts.say("anything")  # should not raise


def test_say_pygame_error_is_swallowed():
    """say() catches pygame errors and returns silently."""
    fake_audio = b"FAKE_AUDIO"

    tts = LocalTTS.__new__(LocalTTS)
    tts._engine = "gtts"
    tts._language = "en"

    import sys

    pygame_mod = MagicMock()
    pygame_mod.mixer.get_init.return_value = True
    pygame_mod.mixer.Sound.side_effect = RuntimeError("no audio device")
    pygame_mod.mixer.get_busy.return_value = False

    with (
        patch.object(tts, "synthesize", return_value=fake_audio),
        patch.dict(sys.modules, {"pygame": pygame_mod}),
    ):
        tts.say("hello")  # should not raise
