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
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("OpenCastor.Hotword")

CASTOR_HOTWORD = os.getenv("CASTOR_HOTWORD", "hey castor")
CASTOR_HOTWORD_ENGINE = os.getenv("CASTOR_HOTWORD_ENGINE", "auto")

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

        # Engine selection
        if engine == "auto":
            self._engine = "openwakeword" if (HAS_OPENWAKEWORD and HAS_PYAUDIO) else "mock"
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
    def status(self) -> Dict[str, Any]:
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
        if self._engine == "openwakeword":
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
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1280,
            )

            logger.info("Hotword: microphone open, listening for %r", self._wake_phrase)
            while not self._stop_event.is_set():
                try:
                    audio_chunk = stream.read(1280, exception_on_overflow=False)
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


def get_detector() -> WakeWordDetector:
    """Return the process-wide WakeWordDetector."""
    global _detector
    if _detector is None:
        _detector = WakeWordDetector()
    return _detector
