"""Local TTS with Piper/Coqui for OpenCastor (issue #138).

Low-latency on-device speech synthesis — no cloud required.
Drop-in replacement for gTTS in castor/main.py Speaker class.

Supported engines (in priority order):
    piper    — Piper TTS (fast, low-memory, ONNX-based)
    coqui    — Coqui TTS (higher quality, more models)
    gtts     — Google TTS (cloud, original fallback)
    none     — Silence (disabled)

Selection: CASTOR_TTS_ENGINE env var (default: auto)

Usage::

    from castor.tts_local import LocalTTS

    tts = LocalTTS(engine="piper", model="en_US-lessac-medium")
    audio_bytes = tts.synthesize("Hello robot world")

Install::

    pip install opencastor[tts]
    # Piper:  pip install piper-tts
    # Coqui:  pip install TTS
"""

import io
import logging
import os
from typing import Optional

logger = logging.getLogger("OpenCastor.TTSLocal")

CASTOR_TTS_ENGINE = os.getenv("CASTOR_TTS_ENGINE", "auto")

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------

try:
    import piper  # noqa: F401

    HAS_PIPER = True
except ImportError:
    HAS_PIPER = False

try:
    from TTS.api import TTS as _CoquiTTS  # noqa: F401

    HAS_COQUI = True
except ImportError:
    HAS_COQUI = False

try:
    from gtts import gTTS  # noqa: F401

    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False


def available_engines() -> list:
    """Return list of available TTS engines on this system."""
    engines = []
    if HAS_PIPER:
        engines.append("piper")
    if HAS_COQUI:
        engines.append("coqui")
    if HAS_GTTS:
        engines.append("gtts")
    return engines


def _select_engine(requested: str) -> str:
    """Select the best available engine.

    Args:
        requested: ``auto``, ``piper``, ``coqui``, or ``gtts``.

    Returns:
        Resolved engine name, or ``none`` if nothing is available.
    """
    if requested == "auto":
        avail = available_engines()
        return avail[0] if avail else "none"
    if requested == "piper" and HAS_PIPER:
        return "piper"
    if requested == "coqui" and HAS_COQUI:
        return "coqui"
    if requested == "gtts" and HAS_GTTS:
        return "gtts"
    logger.warning("TTS engine '%s' not available; falling back to auto", requested)
    return _select_engine("auto")


class LocalTTS:
    """On-device TTS synthesizer.

    Args:
        engine: TTS engine to use (piper/coqui/gtts/auto).
        model: Model name/path (engine-specific).
        language: BCP-47 language code (default: "en").
    """

    def __init__(
        self,
        engine: str = CASTOR_TTS_ENGINE,
        model: Optional[str] = None,
        language: str = "en",
    ):
        self._engine = _select_engine(engine)
        self._model = model
        self._language = language
        self._piper_voice = None
        self._coqui_tts = None

        logger.info("LocalTTS initialized (engine=%s)", self._engine)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def engine(self) -> str:
        return self._engine

    def synthesize(self, text: str) -> bytes:
        """Synthesize *text* to WAV/MP3 bytes.

        Args:
            text: Text to synthesize.

        Returns:
            Audio bytes (WAV for piper/coqui, MP3 for gtts).
            Empty bytes if engine is ``none``.
        """
        if not text or self._engine == "none":
            return b""

        if self._engine == "piper":
            return self._synth_piper(text)
        elif self._engine == "coqui":
            return self._synth_coqui(text)
        elif self._engine == "gtts":
            return self._synth_gtts(text)
        return b""

    def say(self, text: str) -> None:
        """Synthesize and play audio using pygame (blocks until done).

        Falls back to silence if pygame is unavailable.
        """
        audio = self.synthesize(text)
        if not audio:
            return

        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init()

            buf = io.BytesIO(audio)
            sound = pygame.mixer.Sound(buf)
            sound.play()
            while pygame.mixer.get_busy():
                pygame.time.wait(50)
        except Exception as exc:
            logger.warning("LocalTTS playback error: %s", exc)

    # ------------------------------------------------------------------
    # Engine implementations
    # ------------------------------------------------------------------

    def _synth_piper(self, text: str) -> bytes:
        """Synthesize via Piper TTS."""
        try:
            import wave

            import piper

            if self._piper_voice is None:
                model_name = self._model or "en_US-lessac-medium"
                self._piper_voice = piper.PiperVoice.load(model_name)

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav_file:
                audio = self._piper_voice.synthesize(text, wav_file=wav_file)
            return buf.getvalue()
        except Exception as exc:
            logger.warning("Piper TTS error: %s", exc)
            return b""

    def _synth_coqui(self, text: str) -> bytes:
        """Synthesize via Coqui TTS."""
        try:
            import tempfile

            from TTS.api import TTS as _CoquiTTS

            if self._coqui_tts is None:
                model_name = self._model or "tts_models/en/ljspeech/vits"
                self._coqui_tts = _CoquiTTS(model_name=model_name, progress_bar=False)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_path = f.name

            self._coqui_tts.tts_to_file(text=text, file_path=tmp_path)
            with open(tmp_path, "rb") as f:
                data = f.read()
            os.unlink(tmp_path)
            return data
        except Exception as exc:
            logger.warning("Coqui TTS error: %s", exc)
            return b""

    def _synth_gtts(self, text: str) -> bytes:
        """Synthesize via gTTS (cloud)."""
        try:
            from gtts import gTTS

            buf = io.BytesIO()
            tts = gTTS(text=text, lang=self._language)
            tts.write_to_fp(buf)
            return buf.getvalue()
        except Exception as exc:
            logger.warning("gTTS error: %s", exc)
            return b""
