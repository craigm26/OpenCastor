"""
Voice Assistant Continuous Loop.

Implements the full pipeline:
  wake word → STT → LLM → TTS → (repeat)

Integrates:
  - castor.hotword  (OpenWakeWord / mock)
  - castor.voice    (whisper / google-sr / vosk)
  - brain.think()   (any BaseProvider)
  - castor.tts_local (piper / gTTS / espeak)

Env:
  CASTOR_VOICE_LOOP      — "1" to auto-start at gateway launch
  CASTOR_HOTWORD         — wake word phrase (default "hey castor")
  CASTOR_VOICE_ENGINE    — STT engine (whisper / google / vosk)
  CASTOR_TTS_ENGINE      — TTS engine (piper / gtts / espeak)

API:
  POST /api/voice/loop/start
  POST /api/voice/loop/stop
  GET  /api/voice/loop/status
"""

import logging
import threading
import time
from typing import Callable, Optional

from castor.command_interpreter import get_command_interpreter

logger = logging.getLogger("OpenCastor.VoiceLoop")

_singleton: Optional["VoiceAssistantLoop"] = None
_lock = threading.Lock()


class VoiceAssistantLoop:
    """Continuous wake→STT→LLM→TTS voice assistant pipeline."""

    def __init__(
        self,
        brain=None,
        on_command: Optional[Callable[[str], str]] = None,
        hotword: str = "hey castor",
        dry_run_mode: bool = False,
    ):
        self._brain = brain
        self._on_command = on_command
        self._hotword = hotword
        self._running = False
        self._dry_run_mode = dry_run_mode
        self._pending_confirmation: Optional[str] = None
        self._interpreter = get_command_interpreter()
        self._thread: Optional[threading.Thread] = None
        self._state = "idle"  # idle | waiting | listening | processing | speaking
        self._stats = {
            "sessions": 0,
            "avg_stt_ms": 0.0,
            "avg_llm_ms": 0.0,
            "avg_tts_ms": 0.0,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("VoiceAssistantLoop started (hotword=%r)", self._hotword)

    def stop(self):
        self._running = False
        self._pending_confirmation = None
        self._state = "idle"
        logger.info("VoiceAssistantLoop stopped")

    # ── Properties ────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ── Main pipeline ─────────────────────────────────────────────────

    def _loop(self):
        from castor.hotword import get_detector

        detector = get_detector()

        while self._running:
            try:
                self._state = "waiting"
                woke = threading.Event()
                detector.start(on_wake=lambda e=woke: e.set())
                woke.wait(timeout=30)

                if not self._running:
                    break
                if not woke.is_set():
                    continue

                self._stats["sessions"] += 1
                self._state = "listening"
                logger.info("Wake word detected — recording command")

                # 1. STT
                t0 = time.monotonic()
                text = self._stt()
                stt_ms = (time.monotonic() - t0) * 1000
                self._stats["avg_stt_ms"] = 0.9 * self._stats["avg_stt_ms"] + 0.1 * stt_ms

                if not text:
                    logger.debug("STT returned empty — skipping")
                    continue

                logger.info("STT: %r (%.0f ms)", text, stt_ms)
                self._state = "processing"

                # 2. Command interpreter + LLM
                t1 = time.monotonic()
                reply = self._handle_command(text)
                llm_ms = (time.monotonic() - t1) * 1000
                self._stats["avg_llm_ms"] = 0.9 * self._stats["avg_llm_ms"] + 0.1 * llm_ms
                logger.info("LLM: %r (%.0f ms)", (reply or "")[:80], llm_ms)

                # 3. TTS
                self._state = "speaking"
                t2 = time.monotonic()
                self._tts(reply or "I didn't understand that.")
                tts_ms = (time.monotonic() - t2) * 1000
                self._stats["avg_tts_ms"] = 0.9 * self._stats["avg_tts_ms"] + 0.1 * tts_ms

            except Exception as exc:
                logger.error("VoiceLoop iteration error: %s", exc)

        self._state = "idle"

    # ── Pipeline stages ───────────────────────────────────────────────

    def _stt(self) -> str:
        try:
            audio_bytes = self._record_audio(seconds=4)
            if not audio_bytes:
                return ""
            from castor.voice import transcribe_bytes

            return transcribe_bytes(audio_bytes, hint_format="wav") or ""
        except Exception as exc:
            logger.error("STT error: %s", exc)
            return ""

    def _record_audio(self, seconds: float = 4.0) -> bytes:
        """Record microphone input using PyAudio. Returns WAV bytes."""
        try:
            import io
            import wave

            import pyaudio

            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1024,
            )
            frames = [
                stream.read(1024, exception_on_overflow=False)
                for _ in range(int(16000 / 1024 * seconds))
            ]
            stream.stop_stream()
            stream.close()
            pa.terminate()

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
                wf.setframerate(16000)
                wf.writeframes(b"".join(frames))
            return buf.getvalue()
        except Exception as exc:
            logger.error("Audio record error: %s", exc)
            return b""

    def _handle_command(self, text: str) -> str:
        incoming = (text or "").strip()
        if incoming.lower() == "cancel" and self._pending_confirmation:
            self._pending_confirmation = None
            return "Cancelled pending dry-run plan."

        if incoming.lower() == "confirm" and self._pending_confirmation:
            text = self._pending_confirmation
            self._pending_confirmation = None
            interpreted = self._interpreter.interpret(text, dry_run=False)
        else:
            dry_run = self._dry_run_mode or incoming.lower().startswith("--dry-run")
            text = (
                incoming[len("--dry-run") :].strip()
                if incoming.lower().startswith("--dry-run")
                else incoming
            )
            interpreted = self._interpreter.interpret(text, dry_run=dry_run)

        safety = interpreted["safety"]
        logger.info(
            "Voice command explanation_id=%s policy=%s decision=%s",
            safety["explanation_id"],
            safety["policy_id"],
            "allow" if interpreted["execution_allowed"] else "deny",
        )

        if not interpreted["execution_allowed"]:
            alts = "; ".join(safety.get("alternatives") or [])
            return (
                f"[{safety['explanation_id']}] I cannot do that. {safety['rationale']} "
                f"Safe alternatives: {alts}"
            )

        if interpreted.get("dry_run"):
            self._pending_confirmation = text
            steps = " ".join(
                [f"Step {i}: {step}" for i, step in enumerate(interpreted.get("plan", []), start=1)]
            )
            return f"[{safety['explanation_id']}] Dry-run plan. {steps} Say confirm to execute or cancel."

        return self._llm(text)

    def _llm(self, text: str) -> str:
        if self._on_command:
            try:
                return self._on_command(text) or ""
            except Exception as exc:
                logger.error("LLM on_command error: %s", exc)
                return ""
        if self._brain is not None:
            try:
                thought = self._brain.think(b"", text)
                return thought.raw_text or ""
            except Exception as exc:
                logger.error("LLM think error: %s", exc)
        return ""

    def _tts(self, text: str):
        try:
            from castor.tts_local import LocalTTS

            LocalTTS().say(text)
        except Exception as exc:
            logger.warning("TTS error: %s", exc)


def get_voice_loop(
    brain=None,
    on_command: Optional[Callable[[str], str]] = None,
    hotword: str = "hey castor",
    dry_run_mode: bool = False,
) -> VoiceAssistantLoop:
    """Return the process-wide VoiceAssistantLoop singleton."""
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = VoiceAssistantLoop(
                brain=brain,
                on_command=on_command,
                hotword=hotword,
                dry_run_mode=dry_run_mode,
            )
    return _singleton
