"""End-to-end tests for the autoDream runner (castor/brain/autodream_runner.py).

Uses tmp_path to isolate filesystem writes; patches module-level globals so no
real ~/.opencastor directory is touched and no real API calls are made.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import castor.brain.autodream_runner as runner_mod
from castor.brain.autodream_runner import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider_mock(dream_json: str) -> MagicMock:
    """Return a mock AnthropicProvider instance that returns *dream_json* from think()."""
    thought = MagicMock()
    thought.raw_text = dream_json

    provider = MagicMock()
    provider.think.return_value = thought
    provider.system_prompt = ""
    return provider


def _canned_dream_json(
    *,
    memory: str = "# Robot Memory\n## Learnings\n- motor latency stable at 12 ms",
    learnings: list[str] | None = None,
    issues_detected: list[str] | None = None,
    summary: str = "All systems nominal.",
) -> str:
    return json.dumps(
        {
            "updated_memory": memory,
            "learnings": learnings if learnings is not None else ["motor latency stable at 12 ms"],
            "issues_detected": issues_detected if issues_detected is not None else [],
            "summary": summary,
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all runner file I/O to *tmp_path*."""
    monkeypatch.setattr(runner_mod, "OPENCASTOR_DIR", tmp_path)
    monkeypatch.setattr(runner_mod, "MEMORY_FILE", tmp_path / "robot-memory.md")
    monkeypatch.setattr(runner_mod, "DREAM_LOG_FILE", tmp_path / "dream-log.jsonl")
    # Point gateway log at a non-existent path so _load_session_logs returns []
    monkeypatch.setattr(runner_mod, "GATEWAY_LOG", tmp_path / "gateway.log")
    monkeypatch.setattr(runner_mod, "DRY_RUN", False)
    monkeypatch.setattr(runner_mod, "FILE_ISSUES", False)
    monkeypatch.setattr(runner_mod, "RRN", "RRN-TEST-001")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: dream-log.jsonl schema
# ---------------------------------------------------------------------------


def test_main_writes_dream_log(isolated_dir: Path) -> None:
    provider_mock = _make_provider_mock(_canned_dream_json())
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    log_path = isolated_dir / "dream-log.jsonl"
    assert log_path.exists(), "dream-log.jsonl was not created"


def test_dream_log_contains_valid_json_line(isolated_dir: Path) -> None:
    provider_mock = _make_provider_mock(_canned_dream_json())
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    raw = (isolated_dir / "dream-log.jsonl").read_text().strip()
    entry = json.loads(raw)  # raises if not valid JSON
    assert isinstance(entry, dict)


def test_dream_log_has_required_fields(isolated_dir: Path) -> None:
    provider_mock = _make_provider_mock(_canned_dream_json())
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    entry = json.loads((isolated_dir / "dream-log.jsonl").read_text().strip())
    for field in ("date", "model", "rrn", "learnings", "summary"):
        assert field in entry, f"dream-log entry missing field '{field}'"


def test_dream_log_rrn_matches_env(isolated_dir: Path) -> None:
    provider_mock = _make_provider_mock(_canned_dream_json())
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    entry = json.loads((isolated_dir / "dream-log.jsonl").read_text().strip())
    assert entry["rrn"] == "RRN-TEST-001"


def test_dream_log_learnings_is_list(isolated_dir: Path) -> None:
    raw = _canned_dream_json(learnings=["latency stable", "OAK-D healthy"])
    provider_mock = _make_provider_mock(raw)
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    entry = json.loads((isolated_dir / "dream-log.jsonl").read_text().strip())
    assert isinstance(entry["learnings"], list)
    assert len(entry["learnings"]) == 2


def test_dream_log_summary_is_str(isolated_dir: Path) -> None:
    raw = _canned_dream_json(summary="Motor latency nominal.")
    provider_mock = _make_provider_mock(raw)
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    entry = json.loads((isolated_dir / "dream-log.jsonl").read_text().strip())
    assert isinstance(entry["summary"], str)
    assert entry["summary"] == "Motor latency nominal."


def test_dream_log_model_field_present(isolated_dir: Path) -> None:
    provider_mock = _make_provider_mock(_canned_dream_json())
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    entry = json.loads((isolated_dir / "dream-log.jsonl").read_text().strip())
    assert isinstance(entry["model"], str)
    assert entry["model"]  # non-empty


# ---------------------------------------------------------------------------
# Tests: robot-memory.md written / updated
# ---------------------------------------------------------------------------


def test_main_writes_robot_memory(isolated_dir: Path) -> None:
    provider_mock = _make_provider_mock(_canned_dream_json())
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    mem_path = isolated_dir / "robot-memory.md"
    assert mem_path.exists(), "robot-memory.md was not created"


def test_main_robot_memory_contains_updated_content(isolated_dir: Path) -> None:
    updated = "# Robot Memory\n## Learnings\n- motor latency stable at 12 ms\n- OAK-D healthy"
    provider_mock = _make_provider_mock(_canned_dream_json(memory=updated))
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    content = (isolated_dir / "robot-memory.md").read_text(encoding="utf-8")
    assert "OAK-D healthy" in content


def test_main_memory_preserved_on_bad_llm_response(isolated_dir: Path) -> None:
    """If the LLM returns garbage, the original memory must not be overwritten."""
    original_memory = "# Original Robot Memory\n- do not lose this"
    (isolated_dir / "robot-memory.md").write_text(original_memory, encoding="utf-8")

    provider_mock = _make_provider_mock("totally not json }{")
    mock_cls = MagicMock(return_value=provider_mock)

    with patch("castor.providers.anthropic_provider.AnthropicProvider", mock_cls):
        main()

    content = (isolated_dir / "robot-memory.md").read_text(encoding="utf-8")
    assert content == original_memory


# ---------------------------------------------------------------------------
# Tests: dry-run mode (sanity check)
# ---------------------------------------------------------------------------


def test_dry_run_skips_log_and_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "OPENCASTOR_DIR", tmp_path)
    monkeypatch.setattr(runner_mod, "MEMORY_FILE", tmp_path / "robot-memory.md")
    monkeypatch.setattr(runner_mod, "DREAM_LOG_FILE", tmp_path / "dream-log.jsonl")
    monkeypatch.setattr(runner_mod, "GATEWAY_LOG", tmp_path / "gateway.log")
    monkeypatch.setattr(runner_mod, "DRY_RUN", True)
    monkeypatch.setattr(runner_mod, "RRN", "RRN-TEST-001")

    main()  # must return without writing anything

    assert not (tmp_path / "dream-log.jsonl").exists()
    assert not (tmp_path / "robot-memory.md").exists()
