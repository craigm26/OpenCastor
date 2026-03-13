"""Hot-word wake detection for OpenCastor (issue #137).

Always-on microphone listens for the wake phrase ('Hey Castor') and
triggers the STT pipeline via /api/voice/listen.

Supported backends:
    openwakeword  — lightweight on-device wake detection
    mock          — always-off mock for testing/no-hardware

Usage::

    from castor.hotword import get_detector

    det = get_detector()
    det.start(on_wake=lambda: print("Wake word detected!"))
    det.stop()

REST API:
    POST /api/hotword/start   — start wake detection
    POST /api/hotword/stop    — stop wake detection
    GET  /api/hotword/status  — {active, engine, detections}

Install::

    pip install opencastor[hotword]
    # pip install openwakeword pyaudio
"""

import logging
import os
import threading
import time
from typing import Any, Optional
from collections.abc import Callable

logger = logging.getLogger("OpenCastor.Hotword")

CASTOR_HOTWORD = os.getenv("CASTOR_HOTWORD", "hey castor")
CASTOR_HOTWORD_ENGINE = os.getenv("CASTOR_HOTWORD_ENGINE", "auto")
# Optional: pin the PyAudio input device index to avoid hangs on systems where
# the default device probe blocks (e.g. RPi without a configured ALSA default).
# Set to the integer index shown by `python3 -c "import pyaudio; pa=pyaudio.PyAudio();
# [print(i, pa.get_device_info_by_index(i)['name']) for i in range(pa.get_device_count())]"`.
_MIC_DEVICE_INDEX_ENV = os.getenv("CASTOR_MIC_DEVICE_INDEX", "").strip()
CASTOR_MIC_DEVICE_INDEX: int | None = (
    int(_MIC_DEVICE_INDEX_ENV) if _MIC_DEVICE_INDEX_ENV.isdigit() else None
)

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------

try:
    import openwakeword  # noqa: F401

    HAS_OPENWAKEWORD = True
except ImportError:
    HAS_OPENWAKEWORD = False

try:
    import pyaudio  # noqa: F401

    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

try:
    import speech_recognition  # noqa: F401

    HAS_SR = True
except ImportError:
    HAS_SR = False


class WakeWordDetector:
    """Always-on wake word detector.

    Args:
        wake_phrase: Target wake phrase (default: ``CASTOR_HOTWORD``).
        engine: Detection backend (``openwakeword`` or ``mock``/``auto``).
        on_wake: Optional callback fired on each detection.
    """

    def __init__(
        self,
        wake_phrase: str = CASTOR_HOTWORD,
        engine: str = CASTOR_HOTWORD_ENGINE,
        on_wake: Optional[Callable[[], None]] = None,
    ):
        self._wake_phrase = wake_phrase
        self._on_wake = on_wake
        self._active = False
        self._detections = 0
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Engine selection: prefer "sr" (speech_recognition) — it can match
        # any arbitrary phrase including the robot's name.  Fall back to
        # openwakeword for its lightweight always-on model, then mock.
        if engine == "auto":
            if HAS_SR and HAS_PYAUDIO:
                self._engine = "sr"
            elif HAS_OPENWAKEWORD and HAS_PYAUDIO:
                self._engine = "openwakeword"
            else:
                self._engine = "mock"
        else:
            self._engine = engine

        logger.info(
            "WakeWordDetector initialized (engine=%s, phrase=%r)", self._engine, wake_phrase
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, on_wake: Optional[Callable[[], None]] = None) -> None:
        """Start the wake detection loop in a background thread.

        Args:
            on_wake: Optional callback to fire on detection (overrides __init__ arg).
        """
        if self._active:
            logger.debug("WakeWordDetector already active")
            return
        if on_wake:
            self._on_wake = on_wake
        self._stop_event.clear()
        self._active = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hotword-loop")
        self._thread.start()
        logger.info("WakeWordDetector started (engine=%s)", self._engine)

    def stop(self) -> None:
        """Stop the wake detection loop."""
        self._active = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("WakeWordDetector stopped")

    @property
    def status(self) -> dict[str, Any]:
        return {
            "active": self._active,
            "engine": self._engine,
            "wake_phrase": self._wake_phrase,
            "detections": self._detections,
        }

    # ------------------------------------------------------------------
    # Detection loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        if self._engine == "sr":
            self._loop_sr()
        elif self._engine == "openwakeword":
            self._loop_openwakeword()
        else:
            self._loop_mock()

    def _loop_openwakeword(self) -> None:
        """Real detection using openwakeword + pyaudio."""
        try:
            import numpy as np
            import pyaudio
            from openwakeword.model import Model

            # openwakeword 0.4.x compat: Model() without inference_framework
            try:
                model = Model(inference_framework="onnx")
            except TypeError:
                model = Model()
            import audioop

            pa = pyaudio.PyAudio()
            # Use device's native rate; downsample to 16 kHz for openwakeword
            if CASTOR_MIC_DEVICE_INDEX is not None:
                dev_info = pa.get_device_info_by_index(CASTOR_MIC_DEVICE_INDEX)
            else:
                dev_info = pa.get_default_input_device_info()
            native_rate = int(dev_info.get("defaultSampleRate", 44100))
            target_rate = 16000
            chunk_native = int(1280 * native_rate / target_rate)
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=native_rate,
                input=True,
                frames_per_buffer=chunk_native,
            )
            _resample_state = None

            logger.info(
                "Hotword: microphone open at %dHz → %dHz, listening for %r",
                native_rate,
                target_rate,
                self._wake_phrase,
            )
            while not self._stop_event.is_set():
                try:
                    audio_chunk = stream.read(chunk_native, exception_on_overflow=False)
                    # Downsample to 16 kHz
                    if native_rate != target_rate:
                        audio_chunk, _resample_state = audioop.ratecv(
                            audio_chunk, 2, 1, native_rate, target_rate, _resample_state
                        )
                    audio_data = np.frombuffer(audio_chunk, dtype=np.int16)
                    prediction = model.predict(audio_data)
                    # Check any model score above threshold
                    for _, score in prediction.items():
                        if score > 0.5:
                            self._fire_detection()
                            time.sleep(1.0)  # debounce
                            break
                except Exception as exc:
                    logger.warning("Hotword stream error: %s", exc)
                    time.sleep(0.1)

            stream.stop_stream()
            stream.close()
            pa.terminate()
        except Exception as exc:
            logger.warning("Hotword openwakeword error: %s; falling back to mock", exc)
            self._loop_mock()

    def _loop_sr(self) -> None:
        """Detection via speech_recognition — works for any arbitrary phrase.

        Listens in short bursts, transcribes each chunk, and fires when the
        robot's name (or any word from the wake phrase) appears in the text.
        This is the preferred engine because openwakeword only has fixed
        built-in models and can't detect custom names like "alex" or "bob".
        """
        try:
            import speech_recognition as sr

            recognizer = sr.Recognizer()
            recognizer.energy_threshold = 300
            recognizer.dynamic_energy_threshold = True
            recognizer.pause_threshold = 0.6

            # Build a set of trigger words from the wake phrase (e.g. "alex")
            trigger_words = {w.lower() for w in self._wake_phrase.split()}

            mic = sr.Microphone(device_index=CASTOR_MIC_DEVICE_INDEX)
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=1.0)

            logger.info(
                "Hotword: SR engine listening for %r (trigger words: %s)",
                self._wake_phrase,
                trigger_words,
            )

            while not self._stop_event.is_set():
                try:
                    with mic as source:
                        audio = recognizer.listen(source, timeout=3.0, phrase_time_limit=3.0)
                    try:
                        text = recognizer.recognize_google(audio).lower()
                        logger.debug("SR heard: %r", text)
                        heard_words = set(text.split())
                        if trigger_words & heard_words:
                            logger.info("Wake phrase detected in %r", text)
                            self._fire_detection()
                            time.sleep(1.0)  # debounce
                    except sr.UnknownValueError:
                        pass  # silence or unintelligible — normal
                    except sr.RequestError as exc:
                        logger.warning("SR request error: %s", exc)
                        time.sleep(2.0)
                except sr.WaitTimeoutError:
                    pass  # no speech in window — normal
                except Exception as exc:
                    logger.warning("Hotword SR loop error: %s", exc)
                    time.sleep(0.5)
        except Exception as exc:
            logger.warning("Hotword SR init error: %s; falling back to mock", exc)
            self._loop_mock()

    def _loop_mock(self) -> None:
        """Mock detection loop — never fires unless manually triggered."""
        logger.info("Hotword: mock mode (no audio hardware detected)")
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=1.0)

    def _fire_detection(self) -> None:
        """Record and dispatch a wake word detection."""
        self._detections += 1
        logger.info("Wake word detected! (total=%d)", self._detections)
        if self._on_wake:
            try:
                self._on_wake()
            except Exception as exc:
                logger.warning("Wake callback error: %s", exc)

    def trigger(self) -> None:
        """Manually trigger a detection (for testing)."""
        self._fire_detection()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_detector: Optional[WakeWordDetector] = None


def get_detector(wake_phrase: Optional[str] = None) -> WakeWordDetector:
    """Return the process-wide WakeWordDetector.

    If *wake_phrase* is provided and differs from the current detector's
    phrase, the singleton is recreated with the new phrase.
    """
    global _detector
    effective = wake_phrase or CASTOR_HOTWORD
    if _detector is None:
        _detector = WakeWordDetector(wake_phrase=effective)
    elif wake_phrase and _detector._wake_phrase != effective:
        # Phrase changed (e.g. robot name from RCAN config) — recreate.
        _detector = WakeWordDetector(wake_phrase=effective)
    return _detector
