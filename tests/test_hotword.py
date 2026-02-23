"""Tests for castor/hotword.py (issue #137)."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_detector(**kwargs):
    """Import with fresh module state."""
    from castor.hotword import WakeWordDetector

    return WakeWordDetector(**kwargs)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def test_default_engine_is_mock_without_hardware():
    """Without openwakeword + pyaudio, engine falls back to mock."""
    with (
        patch("castor.hotword.HAS_OPENWAKEWORD", False),
        patch("castor.hotword.HAS_PYAUDIO", False),
    ):
        det = _fresh_detector(engine="auto")
        assert det._engine == "mock"


def test_explicit_engine_preserved():
    det = _fresh_detector(engine="mock")
    assert det._engine == "mock"


def test_status_initial():
    det = _fresh_detector(engine="mock")
    s = det.status
    assert s["active"] is False
    assert s["detections"] == 0
    assert s["engine"] == "mock"
    assert "wake_phrase" in s


# ---------------------------------------------------------------------------
# Start / Stop lifecycle
# ---------------------------------------------------------------------------


def test_start_activates_flag():
    det = _fresh_detector(engine="mock")
    det.start()
    try:
        assert det._active is True
    finally:
        det.stop()


def test_stop_deactivates_flag():
    det = _fresh_detector(engine="mock")
    det.start()
    det.stop()
    assert det._active is False


def test_start_idempotent():
    """Calling start() twice should not start a second thread."""
    det = _fresh_detector(engine="mock")
    det.start()
    original_thread = det._thread
    det.start()  # second call should no-op
    assert det._thread is original_thread
    det.stop()


def test_mock_loop_exits_on_stop():
    """The mock loop should terminate promptly after stop()."""
    det = _fresh_detector(engine="mock")
    det.start()
    time.sleep(0.05)
    det.stop()
    assert not det._active
    # Thread should have joined within 5s
    assert det._thread is None or not det._thread.is_alive()


# ---------------------------------------------------------------------------
# Trigger / detection callback
# ---------------------------------------------------------------------------


def test_trigger_increments_detections():
    det = _fresh_detector(engine="mock")
    assert det._detections == 0
    det.trigger()
    assert det._detections == 1
    det.trigger()
    assert det._detections == 2


def test_trigger_fires_callback():
    fired = []
    det = _fresh_detector(engine="mock", on_wake=lambda: fired.append(1))
    det.trigger()
    assert fired == [1]


def test_start_with_on_wake_overrides():
    """on_wake passed to start() overrides the one from __init__."""
    original_fired = []
    new_fired = []
    det = _fresh_detector(engine="mock", on_wake=lambda: original_fired.append(1))
    det.start(on_wake=lambda: new_fired.append(1))
    det.trigger()
    det.stop()
    assert new_fired == [1]
    assert original_fired == []


def test_trigger_callback_error_is_swallowed():
    """Exceptions in the on_wake callback must not propagate."""

    def bad_callback():
        raise RuntimeError("boom")

    det = _fresh_detector(engine="mock", on_wake=bad_callback)
    det.trigger()  # should not raise
    assert det._detections == 1


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_detector_singleton():
    import castor.hotword as hw_mod

    # Reset singleton
    hw_mod._detector = None
    det1 = hw_mod.get_detector()
    det2 = hw_mod.get_detector()
    assert det1 is det2
    # Cleanup
    hw_mod._detector = None
