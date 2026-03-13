"""
Privacy Mode — zero cloud egress enforcement.

When enabled, all AI inference is restricted to local-only providers
(Ollama, llama.cpp, MLX, ONNX). Outbound webhooks and cloud TTS engines
are blocked. All violations are logged and counted.

This complements the existing castor/privacy.py sensor-level policies.

Env:
  CASTOR_PRIVACY_MODE=1   — enable fully-local mode on startup

API:
  POST /api/privacy/mode/enable
  POST /api/privacy/mode/disable
  GET  /api/privacy/mode/status
"""

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger("OpenCastor.PrivacyMode")

_LOCAL_PROVIDERS = frozenset(
    {
        "ollama",
        "llamacpp",
        "llama.cpp",
        "llama-cpp",
        "mlx",
        "mlx-lm",
        "onnx",
        "onnxruntime",
    }
)

_LOCAL_TTS_ENGINES = frozenset({"piper", "coqui", "espeak", "mock"})

_singleton: Optional["PrivacyModeManager"] = None
_lock = threading.Lock()


class PrivacyModeManager:
    """Enforces zero-cloud-egress constraints across the runtime."""

    def __init__(self):
        env = os.getenv("CASTOR_PRIVACY_MODE", "0").strip().lower()
        self._enabled: bool = env in ("1", "true", "yes")
        self._violations: list[str] = []
        if self._enabled:
            logger.warning("PRIVACY MODE: fully-local operation enforced on startup")

    # ── Control ───────────────────────────────────────────────────────

    def enable(self) -> None:
        self._enabled = True
        self._violations.clear()
        logger.warning("PRIVACY MODE ENABLED — all cloud egress blocked")

    def disable(self) -> None:
        self._enabled = False
        logger.info("Privacy mode disabled — cloud providers re-enabled")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Gate checks ───────────────────────────────────────────────────

    def check_provider(self, provider_name: str) -> bool:
        """Return True if provider is allowed. Logs + records violation if blocked."""
        if not self._enabled:
            return True
        ok = provider_name.lower() in _LOCAL_PROVIDERS
        if not ok:
            self._record(f"Blocked cloud provider '{provider_name}'")
        return ok

    def check_webhook(self, url: str) -> bool:
        """Return True if outbound webhook is allowed."""
        if not self._enabled:
            return True
        self._record(f"Blocked outbound webhook → {url}")
        return False

    def check_tts_engine(self, engine: str) -> bool:
        """Return True if TTS engine operates locally."""
        if not self._enabled:
            return True
        ok = engine.lower() in _LOCAL_TTS_ENGINES
        if not ok:
            self._record(f"Blocked cloud TTS engine '{engine}'")
        return ok

    def check_camera_upload(self, destination: str) -> bool:
        """Return True if uploading camera frames is allowed."""
        if not self._enabled:
            return True
        self._record(f"Blocked camera frame upload to '{destination}'")
        return False

    # ── Status ────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "enabled": self._enabled,
            "local_providers": sorted(_LOCAL_PROVIDERS),
            "local_tts_engines": sorted(_LOCAL_TTS_ENGINES),
            "violation_count": len(self._violations),
            "recent_violations": self._violations[-10:],
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _record(self, msg: str):
        full = f"{msg} (privacy mode)"
        logger.warning(full)
        self._violations.append(full)


def get_privacy_mode() -> PrivacyModeManager:
    """Return the process-wide PrivacyModeManager singleton."""
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = PrivacyModeManager()
    return _singleton
