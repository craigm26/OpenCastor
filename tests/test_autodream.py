"""Tests for autoDream LLM brain (castor/brain/autodream.py)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from castor.brain.autodream import (
    AUTODREAM_SYSTEM_PROMPT,
    AutoDreamBrain,
    DreamResult,
    DreamSession,
)


def _mock_provider(raw_text: str) -> MagicMock:
    """Return a mock provider whose think() returns an object with .raw_text."""
    thought = MagicMock()
    thought.raw_text = raw_text
    provider = MagicMock()
    provider.think.return_value = thought
    return provider


@pytest.fixture
def sample_session():
    return DreamSession(
        session_logs=["ERROR: connection refused", "WARN: high CPU temp 72C"],
        robot_memory="# Robot Memory\n## Known Issues\n- none\n## Learnings\n- none",
        health_report={"cpu_temp_c": "72.0", "disk_used_pct": 69, "gateway": "ok"},
        date="2026-04-01",
    )


@pytest.fixture
def valid_dream_json():
    return json.dumps(
        {
            "updated_memory": "# Robot Memory\n## Learnings\n- High CPU under load",
            "learnings": ["CPU spikes to 72C under heavy OAK-D load"],
            "issues_detected": [],
            "summary": "Quiet night. One CPU spike noted.",
        }
    )


def test_run_returns_dream_result(sample_session, valid_dream_json):
    brain = AutoDreamBrain(provider=_mock_provider(valid_dream_json))
    result = brain.run(sample_session)
    assert isinstance(result, DreamResult)
    assert "Learnings" in result.updated_memory
    assert len(result.learnings) == 1
    assert result.summary == "Quiet night. One CPU spike noted."


def test_run_fallback_on_bad_json(sample_session):
    brain = AutoDreamBrain(provider=_mock_provider("not valid json {{ garbage"))
    result = brain.run(sample_session)
    # Must not corrupt memory — falls back to original
    assert result.updated_memory == sample_session.robot_memory
    assert isinstance(result.learnings, list)
    assert isinstance(result.issues_detected, list)


def test_build_session_prompt_includes_all_sections(sample_session):
    brain = AutoDreamBrain(provider=MagicMock())
    prompt = brain._build_session_prompt(sample_session)
    assert "<dream-session>" in prompt
    assert "<date>2026-04-01</date>" in prompt
    assert "<health>" in prompt
    assert "<recent-errors>" in prompt
    assert "<existing-memory>" in prompt
    assert "connection refused" in prompt


def test_dream_result_learnings_always_list(sample_session):
    # Provider returns JSON with learnings/issues as None
    raw = json.dumps(
        {
            "updated_memory": sample_session.robot_memory,
            "learnings": None,
            "issues_detected": None,
            "summary": "ok",
        }
    )
    brain = AutoDreamBrain(provider=_mock_provider(raw))
    result = brain.run(sample_session)
    assert isinstance(result.learnings, list)
    assert isinstance(result.issues_detected, list)


def test_system_prompt_is_stable():
    """AUTODREAM_SYSTEM_PROMPT must not contain dynamic content — must be cache-safe."""
    dynamic_markers = ["{date}", "{today}", "datetime.now", "time.time"]
    for marker in dynamic_markers:
        assert marker not in AUTODREAM_SYSTEM_PROMPT, (
            f"System prompt contains dynamic marker '{marker}' — breaks prompt cache"
        )
