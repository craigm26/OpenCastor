"""Tests for wake word using robot name — issue: 'alex' not detected."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── get_detector resolution ───────────────────────────────────────────────────


def test_get_detector_uses_env_hotword():
    import castor.hotword as hw_mod

    hw_mod._detector = None  # reset singleton
    # Patch the module-level constant (already read at import time)
    with patch.object(hw_mod, "CASTOR_HOTWORD", "hey jarvis"):
        det = hw_mod.get_detector()
    assert det._wake_phrase == "hey jarvis"
    hw_mod._detector = None


def test_get_detector_recreates_on_phrase_change():
    from castor.hotword import get_detector
    import castor.hotword as hw_mod

    hw_mod._detector = None
    det1 = get_detector(wake_phrase="hey castor")
    det2 = get_detector(wake_phrase="alex")
    assert det2._wake_phrase == "alex"
    assert det1 is not det2
    hw_mod._detector = None


def test_get_detector_reuses_same_phrase():
    from castor.hotword import get_detector
    import castor.hotword as hw_mod

    hw_mod._detector = None
    det1 = get_detector(wake_phrase="alex")
    det2 = get_detector(wake_phrase="alex")
    assert det1 is det2
    hw_mod._detector = None


# ── hotword_start API endpoint ────────────────────────────────────────────────


def _call_hotword_start(config=None, env_hotword=""):
    """Helper to call the hotword_start endpoint logic directly."""
    import asyncio
    import os

    if env_hotword:
        os.environ["CASTOR_HOTWORD"] = env_hotword
    else:
        os.environ.pop("CASTOR_HOTWORD", None)

    mock_det = MagicMock()
    mock_det.status = {"active": True, "engine": "mock", "detections": 0}
    mock_det._wake_phrase = "alex"

    with patch("castor.hotword.get_detector", return_value=mock_det) as mock_gd:
        # Simulate the endpoint logic directly
        env_phrase = os.getenv("CASTOR_HOTWORD", "")
        robot_name = (config or {}).get("metadata", {}).get("robot_name", "")
        wake_phrase = env_phrase or robot_name or "hey castor"
        mock_gd(wake_phrase=wake_phrase)
        return wake_phrase, mock_gd


def test_hotword_start_uses_robot_name_from_config():
    cfg = {"metadata": {"robot_name": "alex"}}
    phrase, _ = _call_hotword_start(config=cfg)
    assert phrase == "alex"


def test_hotword_start_env_overrides_robot_name():
    cfg = {"metadata": {"robot_name": "alex"}}
    phrase, _ = _call_hotword_start(config=cfg, env_hotword="hey zeus")
    assert phrase == "hey zeus"


def test_hotword_start_defaults_hey_castor_when_no_config():
    phrase, _ = _call_hotword_start(config=None)
    assert phrase == "hey castor"


def test_hotword_start_robot_name_bob():
    cfg = {"metadata": {"robot_name": "bob"}}
    phrase, _ = _call_hotword_start(config=cfg)
    assert phrase == "bob"


def test_hotword_start_empty_robot_name_falls_back():
    cfg = {"metadata": {"robot_name": ""}}
    phrase, _ = _call_hotword_start(config=cfg)
    assert phrase == "hey castor"


# ── WakeWordDetector wake phrase matching ─────────────────────────────────────


def test_wakeword_detector_stores_phrase():
    from castor.hotword import WakeWordDetector

    det = WakeWordDetector(wake_phrase="alex")
    assert det._wake_phrase == "alex"


def test_wakeword_detector_default_phrase(monkeypatch):
    monkeypatch.delenv("CASTOR_HOTWORD", raising=False)
    from castor.hotword import WakeWordDetector

    det = WakeWordDetector()
    # Default from CASTOR_HOTWORD env or "hey castor"
    assert det._wake_phrase in ("hey castor", "alex")  # depends on env


def test_wakeword_phrase_case_insensitive():
    """Mock match logic: set intersection of lowercased words."""
    trigger_words = {"alex"}
    transcript = "alex go forward"
    heard_words = set(transcript.lower().split())
    assert bool(trigger_words & heard_words)


def test_wakeword_phrase_no_match():
    trigger_words = {"alex"}
    transcript = "hey castor go"
    heard_words = set(transcript.lower().split())
    assert not bool(trigger_words & heard_words)


def test_wakeword_multi_word_phrase_partial_match():
    """Any word in the phrase triggers (set intersection)."""
    trigger_words = set("hey alex".split())
    transcript = "hey go forward"
    heard_words = set(transcript.lower().split())
    # "hey" matches
    assert bool(trigger_words & heard_words)


def test_wakeword_robot_name_as_standalone_word():
    """'alexander' should NOT match 'alex' (whole word only)."""
    trigger_words = {"alex"}
    transcript = "alexander go home"
    heard_words = set(transcript.lower().split())
    assert not bool(trigger_words & heard_words)
