"""End-to-end tests for AutoDreamBrain (castor/brain/autodream.py).

Tests mock AnthropicProvider.think() so no real API calls are made.
Validates that AutoDreamBrain.run() returns a well-formed DreamResult
and that the fields satisfy schema constraints.
"""

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(raw_text: str) -> MagicMock:
    """Return a mock provider whose think() resolves to *raw_text*."""
    thought = MagicMock()
    thought.raw_text = raw_text

    provider = MagicMock()
    provider.think.return_value = thought
    # system_prompt must be a settable str attribute (AutoDreamBrain swaps it)
    provider.system_prompt = ""
    return provider


def _canned_dream_json(
    *,
    learnings: list[str] | None = None,
    issues_detected: list[str] | None = None,
    summary: str = "Quiet night.",
    memory: str = "# Robot Memory\n## Learnings\n- CPU spikes under OAK-D load",
) -> str:
    return json.dumps(
        {
            "updated_memory": memory,
            "learnings": learnings if learnings is not None else ["CPU spikes to 72 C under load"],
            "issues_detected": issues_detected if issues_detected is not None else [],
            "summary": summary,
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> DreamSession:
    return DreamSession(
        session_logs=[
            "ERROR: OAK-D frame timeout after 2000 ms",
            "WARN: CPU temp 72 C — throttling imminent",
        ],
        robot_memory="# Robot Memory\n## Known Issues\n- none",
        health_report={"cpu_temp_c": "72.0", "disk_used_pct": 55, "gateway": "ok"},
        date="2026-04-02",
    )


# ---------------------------------------------------------------------------
# Tests: return type and field schema
# ---------------------------------------------------------------------------


def test_run_returns_dream_result(session: DreamSession) -> None:
    brain = AutoDreamBrain(provider=_make_provider(_canned_dream_json()))
    result = brain.run(session)
    assert isinstance(result, DreamResult)


def test_run_result_has_updated_memory(session: DreamSession) -> None:
    brain = AutoDreamBrain(provider=_make_provider(_canned_dream_json()))
    result = brain.run(session)
    assert isinstance(result.updated_memory, str)
    assert result.updated_memory.strip()  # non-empty


def test_run_result_learnings_is_list_of_strings(session: DreamSession) -> None:
    raw = _canned_dream_json(learnings=["Learned A", "Learned B"])
    brain = AutoDreamBrain(provider=_make_provider(raw))
    result = brain.run(session)
    assert isinstance(result.learnings, list)
    assert len(result.learnings) == 2
    assert all(isinstance(item, str) for item in result.learnings)


def test_run_result_issues_detected_is_list(session: DreamSession) -> None:
    raw = _canned_dream_json(issues_detected=["Motor stall #3 at joint 2"])
    brain = AutoDreamBrain(provider=_make_provider(raw))
    result = brain.run(session)
    assert isinstance(result.issues_detected, list)
    assert result.issues_detected[0] == "Motor stall #3 at joint 2"


def test_run_result_summary_is_str(session: DreamSession) -> None:
    raw = _canned_dream_json(summary="All systems nominal.")
    brain = AutoDreamBrain(provider=_make_provider(raw))
    result = brain.run(session)
    assert isinstance(result.summary, str)
    assert result.summary == "All systems nominal."


# ---------------------------------------------------------------------------
# Tests: provider interaction
# ---------------------------------------------------------------------------


def test_run_calls_provider_think_once(session: DreamSession) -> None:
    provider = _make_provider(_canned_dream_json())
    brain = AutoDreamBrain(provider=provider)
    brain.run(session)
    provider.think.assert_called_once()


def test_run_restores_provider_system_prompt(session: DreamSession) -> None:
    """AutoDreamBrain must restore the original system_prompt after the call."""
    provider = _make_provider(_canned_dream_json())
    original = "original system prompt"
    provider.system_prompt = original
    brain = AutoDreamBrain(provider=provider)
    brain.run(session)
    assert provider.system_prompt == original


def test_run_swaps_to_autodream_system_prompt_during_call(session: DreamSession) -> None:
    """Provider must receive the AUTODREAM_SYSTEM_PROMPT during think()."""
    captured_prompts: list[str] = []

    thought = MagicMock()
    thought.raw_text = _canned_dream_json()

    provider = MagicMock()
    provider.system_prompt = "original"

    def _capture_think(*args, **kwargs):
        captured_prompts.append(provider.system_prompt)
        return thought

    provider.think.side_effect = _capture_think

    brain = AutoDreamBrain(provider=provider)
    brain.run(session)

    assert len(captured_prompts) == 1
    assert captured_prompts[0] == AUTODREAM_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests: fallback behaviour
# ---------------------------------------------------------------------------


def test_run_fallback_on_unparseable_json(session: DreamSession) -> None:
    brain = AutoDreamBrain(provider=_make_provider("not {{ valid json"))
    result = brain.run(session)
    # Must preserve original memory — never corrupt it
    assert result.updated_memory == session.robot_memory
    assert result.learnings == []
    assert result.issues_detected == []


def test_run_fallback_on_provider_exception(session: DreamSession) -> None:
    provider = MagicMock()
    provider.system_prompt = ""
    provider.think.side_effect = RuntimeError("quota exceeded")
    brain = AutoDreamBrain(provider=provider)
    result = brain.run(session)
    assert result.updated_memory == session.robot_memory
    assert "unavailable" in result.summary.lower()


def test_run_fallback_when_updated_memory_missing(session: DreamSession) -> None:
    raw = json.dumps({"learnings": ["x"], "issues_detected": [], "summary": "ok"})
    brain = AutoDreamBrain(provider=_make_provider(raw))
    result = brain.run(session)
    assert result.updated_memory == session.robot_memory


# ---------------------------------------------------------------------------
# Tests: JSON wrapped in markdown fences
# ---------------------------------------------------------------------------


def test_run_parses_json_in_markdown_fence(session: DreamSession) -> None:
    fence_wrapped = "```json\n" + _canned_dream_json() + "\n```"
    brain = AutoDreamBrain(provider=_make_provider(fence_wrapped))
    result = brain.run(session)
    assert isinstance(result, DreamResult)
    assert result.learnings  # at least one entry
