"""
castor/voice.py — Shared audio transcription module.

Provides a tiered transcription pipeline:
    1. OpenAI Whisper API (if OPENAI_API_KEY set)
    2. Local openai-whisper package (if installed)
    3. whisper.cpp CLI binary (if WHISPER_CPP_BIN is set or whisper-cpp is on PATH)
    4. Google SpeechRecognition (always available as fallback)
    5. Returns None if all engines fail or none are available

Usage::

    from castor.voice import transcribe_bytes

    with open("audio.ogg", "rb") as f:
        text = transcribe_bytes(f.read(), hint_format="ogg")
    # → "turn left and go forward"

The preferred engine can be forced via the ``engine`` parameter or the
``CASTOR_VOICE_ENGINE`` environment variable ("whisper_api", "whisper_local",
"whisper_cpp", "google", "auto").
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

logger = logging.getLogger("OpenCastor.Voice")

# ---------------------------------------------------------------------------
# Engine availability probes (lazy — checked once per process)
# ---------------------------------------------------------------------------

_HAS_OPENAI: Optional[bool] = None
_HAS_WHISPER_LOCAL: Optional[bool] = None
_HAS_WHISPER_CPP: Optional[bool] = None
_HAS_SPEECH_RECOGNITION: Optional[bool] = None


def _probe_openai() -> bool:
    global _HAS_OPENAI
    if _HAS_OPENAI is None:
        try:
            import openai  # noqa: F401

            _HAS_OPENAI = bool(os.getenv("OPENAI_API_KEY"))
        except ImportError:
            _HAS_OPENAI = False
    return _HAS_OPENAI


def _probe_whisper_local() -> bool:
    global _HAS_WHISPER_LOCAL
    if _HAS_WHISPER_LOCAL is None:
        try:
            import whisper  # noqa: F401

            _HAS_WHISPER_LOCAL = True
        except ImportError:
            _HAS_WHISPER_LOCAL = False
    return _HAS_WHISPER_LOCAL


def _probe_whisper_cpp() -> bool:
    """Check whether the whisper.cpp CLI binary is available."""
    global _HAS_WHISPER_CPP
    if _HAS_WHISPER_CPP is None:
        bin_path = os.getenv("WHISPER_CPP_BIN", "whisper-cpp")
        if bin_path == "mock":
            _HAS_WHISPER_CPP = True
            return _HAS_WHISPER_CPP
        # shutil.which covers both absolute paths and PATH lookup
        if shutil.which(bin_path) is not None:
            _HAS_WHISPER_CPP = True
        else:
            # Fallback: try --version to confirm binary runs
            try:
                subprocess.run(
                    [bin_path, "--version"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                _HAS_WHISPER_CPP = True
            except (FileNotFoundError, OSError):
                _HAS_WHISPER_CPP = False
        logger.debug("whisper.cpp probe: available=%s (bin=%s)", _HAS_WHISPER_CPP, bin_path)
    return _HAS_WHISPER_CPP


def _probe_speech_recognition() -> bool:
    global _HAS_SPEECH_RECOGNITION
    if _HAS_SPEECH_RECOGNITION is None:
        try:
            import speech_recognition  # noqa: F401

            _HAS_SPEECH_RECOGNITION = True
        except ImportError:
            _HAS_SPEECH_RECOGNITION = False
    return _HAS_SPEECH_RECOGNITION


# ---------------------------------------------------------------------------
# Individual engine implementations
# ---------------------------------------------------------------------------


def _transcribe_whisper_api(audio_bytes: bytes, hint_format: str = "ogg") -> Optional[str]:
    """Transcribe via OpenAI Whisper API."""
    try:
        from openai import OpenAI

        client = OpenAI()
        ext = hint_format.lstrip(".")
        # Whisper API accepts: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            with open(tmp_path, "rb") as audio_file:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                )
            text = result.text.strip()
            logger.debug("Whisper API transcription: %d chars", len(text))
            return text or None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("Whisper API transcription failed: %s", exc)
        return None


def _transcribe_whisper_local(audio_bytes: bytes, hint_format: str = "ogg") -> Optional[str]:
    """Transcribe using local openai-whisper package."""
    try:
        import whisper

        ext = hint_format.lstrip(".")
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            model = whisper.load_model("base")
            result = model.transcribe(tmp_path)
            text = result.get("text", "").strip()
            logger.debug("Local Whisper transcription: %d chars", len(text))
            return text or None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("Local Whisper transcription failed: %s", exc)
        return None


def _transcribe_whisper_cpp(audio_bytes: bytes) -> Optional[str]:
    """Transcribe via the whisper.cpp CLI binary.

    Writes audio to a temporary WAV file, invokes the binary with
    ``--output-txt``, reads the resulting ``<tmp>.txt`` file, then
    cleans up both temp files.

    Environment variables:
        WHISPER_CPP_BIN   — path to the whisper.cpp binary (default: "whisper-cpp").
                            Set to "mock" to return a fixed string without running anything.
        WHISPER_CPP_MODEL — optional model file path, passed as ``--model <path>``.
    """
    bin_path = os.getenv("WHISPER_CPP_BIN", "whisper-cpp")
    model_path = os.getenv("WHISPER_CPP_MODEL", "")

    # Mock mode — useful for testing without an actual binary installed
    if bin_path == "mock":
        logger.debug("whisper.cpp: mock mode — returning fixed transcription")
        return "mock transcription"

    tmp_wav_path: Optional[str] = None
    txt_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_wav_path = tmp.name

        txt_path = tmp_wav_path + ".txt"

        cmd = [bin_path]
        if model_path:
            cmd += ["--model", model_path]
        cmd += ["--output-txt", tmp_wav_path]

        logger.debug("whisper.cpp: running %s", cmd)
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "whisper.cpp exited with code %d: %s",
                result.returncode,
                result.stderr.decode(errors="replace").strip(),
            )
            return None

        with open(txt_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip()

        logger.debug("whisper.cpp transcription: %d chars", len(text))
        return text or None

    except FileNotFoundError:
        logger.warning("whisper.cpp binary not found: %s — skipping engine", bin_path)
        return None
    except subprocess.TimeoutExpired:
        logger.warning("whisper.cpp timed out after 60s")
        return None
    except Exception as exc:
        logger.warning("whisper.cpp transcription failed: %s", exc)
        return None
    finally:
        for path in (tmp_wav_path, txt_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _transcribe_google_sr(audio_bytes: bytes, hint_format: str = "ogg") -> Optional[str]:
    """Transcribe via Google SpeechRecognition (free, no API key required)."""
    try:
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        ext = hint_format.lstrip(".").lower()

        # speech_recognition works best with WAV; convert OGG/OGG-Opus/MP3 if possible
        audio_data = audio_bytes
        if ext in ("ogg", "oga", "mp3", "m4a", "aac", "webm", "opus"):
            audio_data = _convert_to_wav(audio_bytes, ext)
            if audio_data is None:
                logger.debug("Audio format conversion failed; trying raw bytes with Google SR")
                audio_data = audio_bytes

        audio_file = io.BytesIO(audio_data)
        with sr.AudioFile(audio_file) as source:
            audio = recognizer.record(source)

        text = recognizer.recognize_google(audio).strip()
        logger.debug("Google SR transcription: %d chars", len(text))
        return text or None
    except Exception as exc:
        logger.warning("Google SR transcription failed: %s", exc)
        return None


def _convert_to_wav(audio_bytes: bytes, src_format: str) -> Optional[bytes]:
    """Convert audio to WAV using pydub (optional dependency)."""
    try:
        from pydub import AudioSegment  # noqa

        seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format=src_format)
        buf = io.BytesIO()
        seg.export(buf, format="wav")
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        logger.debug("pydub conversion failed (%s): %s", src_format, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_VALID_ENGINES = ("auto", "whisper_api", "whisper_local", "whisper_cpp", "google")


def transcribe_bytes(
    audio_bytes: bytes,
    hint_format: str = "ogg",
    engine: str = "auto",
    language: str = "en",
) -> Optional[str]:
    """Transcribe audio bytes to text.

    Args:
        audio_bytes: Raw audio file bytes (any common format).
        hint_format: File extension hint for the audio format, e.g. "ogg", "mp3", "wav".
        engine: Transcription engine override. One of "auto", "whisper_api",
                "whisper_local", "whisper_cpp", "google". Defaults to the
                ``CASTOR_VOICE_ENGINE`` env var, then "auto" (tries in order:
                whisper_api → whisper_local → whisper_cpp → google).
        language: Language code hint (currently used by Google SR; Whisper auto-detects).

    Returns:
        Transcribed text string, or None if transcription failed.
    """
    if not audio_bytes:
        return None

    # Resolve engine preference
    resolved_engine = engine
    if resolved_engine == "auto":
        resolved_engine = os.getenv("CASTOR_VOICE_ENGINE", "auto")

    t0 = time.time()
    text: Optional[str] = None

    if resolved_engine == "whisper_api":
        text = _transcribe_whisper_api(audio_bytes, hint_format)
    elif resolved_engine == "whisper_local":
        text = _transcribe_whisper_local(audio_bytes, hint_format)
    elif resolved_engine == "whisper_cpp":
        text = _transcribe_whisper_cpp(audio_bytes)
    elif resolved_engine == "google":
        text = _transcribe_google_sr(audio_bytes, hint_format)
    else:
        # auto: try engines in priority order
        if _probe_openai():
            logger.debug("voice: trying Whisper API")
            text = _transcribe_whisper_api(audio_bytes, hint_format)
        if text is None and _probe_whisper_local():
            logger.debug("voice: trying local Whisper")
            text = _transcribe_whisper_local(audio_bytes, hint_format)
        if text is None and _probe_whisper_cpp():
            logger.debug("voice: trying whisper.cpp")
            text = _transcribe_whisper_cpp(audio_bytes)
        if text is None and _probe_speech_recognition():
            logger.debug("voice: trying Google SR")
            text = _transcribe_google_sr(audio_bytes, hint_format)

    elapsed_ms = round((time.time() - t0) * 1000, 1)
    if text:
        logger.info(
            "Transcribed %d audio bytes → %d chars (engine=%s, %.0fms)",
            len(audio_bytes),
            len(text),
            resolved_engine,
            elapsed_ms,
        )
    else:
        logger.warning(
            "Transcription returned empty result (engine=%s, %.0fms, format=%s)",
            resolved_engine,
            elapsed_ms,
            hint_format,
        )

    return text


def available_engines() -> list[str]:
    """Return list of transcription engines available in this environment."""
    engines = []
    if _probe_openai():
        engines.append("whisper_api")
    if _probe_whisper_local():
        engines.append("whisper_local")
    if _probe_whisper_cpp():
        engines.append("whisper_cpp")
    if _probe_speech_recognition():
        engines.append("google")
    return engines
