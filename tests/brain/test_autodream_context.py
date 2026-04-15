"""Tests for autoDream enriched session context (issue #846).

Covers:
  - DreamSession accepts new fields (recent_commits, bridge_log_tail, cron_outcomes)
  - DreamSession fields default to empty lists (backward compat)
  - _build_session_prompt() includes new sections in output
  - _load_recent_commits() returns lines from git log stdout
  - _load_bridge_log_tail() reads last 20 lines of bridge log
  - _load_cron_outcomes() extracts 'summary' from last 3 dream-log.jsonl entries
  - main() passes new fields into DreamSession
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── DreamSession defaults ─────────────────────────────────────────────────────


def test_dream_session_new_fields_default_to_empty_lists():
    """New fields must default to empty list — keeps existing callers working."""
    from castor.brain.autodream import DreamSession

    s = DreamSession(session_logs=[], robot_memory="", health_report={}, date="2026-04-01")
    assert s.recent_commits == []
    assert s.bridge_log_tail == []
    assert s.cron_outcomes == []


def test_dream_session_new_fields_accept_values():
    from castor.brain.autodream import DreamSession

    s = DreamSession(
        session_logs=[],
        robot_memory="",
        health_report={},
        date="2026-04-01",
        recent_commits=["abc1234 fix: something"],
        bridge_log_tail=["INFO bridge connected"],
        cron_outcomes=["all clear"],
    )
    assert s.recent_commits == ["abc1234 fix: something"]
    assert s.bridge_log_tail == ["INFO bridge connected"]
    assert s.cron_outcomes == ["all clear"]


# ── _build_session_prompt includes new sections ───────────────────────────────


def _make_brain():
    from unittest.mock import MagicMock

    from castor.brain.autodream import AutoDreamBrain

    return AutoDreamBrain(provider=MagicMock())


def test_build_session_prompt_includes_recent_code_changes():
    from castor.brain.autodream import DreamSession

    brain = _make_brain()
    s = DreamSession(
        session_logs=[],
        robot_memory="",
        health_report={},
        date="2026-04-01",
        recent_commits=["abc1234 feat: add widget"],
    )
    prompt = brain._build_session_prompt(s)
    assert "<recent-code-changes>" in prompt
    assert "abc1234 feat: add widget" in prompt


def test_build_session_prompt_includes_bridge_activity():
    from castor.brain.autodream import DreamSession

    brain = _make_brain()
    s = DreamSession(
        session_logs=[],
        robot_memory="",
        health_report={},
        date="2026-04-01",
        bridge_log_tail=["INFO heartbeat ok", "WARN slow response"],
    )
    prompt = brain._build_session_prompt(s)
    assert "<bridge-activity>" in prompt
    assert "INFO heartbeat ok" in prompt
    assert "WARN slow response" in prompt


def test_build_session_prompt_includes_recent_dream_outcomes():
    from castor.brain.autodream import DreamSession

    brain = _make_brain()
    s = DreamSession(
        session_logs=[],
        robot_memory="",
        health_report={},
        date="2026-04-01",
        cron_outcomes=["no new learnings", "motor jitter noted"],
    )
    prompt = brain._build_session_prompt(s)
    assert "<recent-dream-outcomes>" in prompt
    assert "no new learnings" in prompt
    assert "motor jitter noted" in prompt


def test_build_session_prompt_empty_fields_show_none_placeholder():
    """When new fields are empty, prompt should show '(none)' placeholders."""
    from castor.brain.autodream import DreamSession

    brain = _make_brain()
    s = DreamSession(session_logs=[], robot_memory="", health_report={}, date="2026-04-01")
    prompt = brain._build_session_prompt(s)
    # All three sections should still appear but with (none)
    assert "<recent-code-changes>" in prompt
    assert "<bridge-activity>" in prompt
    assert "<recent-dream-outcomes>" in prompt
    assert prompt.count("(none)") >= 3


# ── _load_recent_commits ──────────────────────────────────────────────────────


def test_load_recent_commits_parses_git_output(monkeypatch):
    import castor.brain.autodream_runner as runner_mod

    fake_stdout = "abc1234 feat: add thing\ndef5678 fix: repair bug\n"
    mock_result = MagicMock(stdout=fake_stdout)

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        commits = runner_mod._load_recent_commits()

    assert commits == ["abc1234 feat: add thing", "def5678 fix: repair bug"]
    cmd = mock_run.call_args.args[0]
    assert "log" in cmd
    assert "--oneline" in cmd
    assert "-5" in cmd


def test_load_recent_commits_returns_empty_on_error(monkeypatch):
    import castor.brain.autodream_runner as runner_mod

    with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
        commits = runner_mod._load_recent_commits()

    assert commits == []


# ── _load_bridge_log_tail ─────────────────────────────────────────────────────


def test_load_bridge_log_tail_reads_last_20_lines(tmp_path):
    import castor.brain.autodream_runner as runner_mod

    bridge_log = tmp_path / "castor-bridge.log"
    all_lines = [f"line {i}" for i in range(30)]
    bridge_log.write_text("\n".join(all_lines) + "\n")

    with patch("castor.brain.autodream_runner.Path") as MockPath:
        # Only intercept the bridge log path; let other Path calls pass through
        real_path = Path

        def path_side_effect(arg):
            if arg == "/tmp/castor-bridge.log":
                return bridge_log
            return real_path(arg)

        MockPath.side_effect = path_side_effect
        tail = runner_mod._load_bridge_log_tail()

    assert len(tail) == 20
    assert tail[0] == "line 10"
    assert tail[-1] == "line 29"


def test_load_bridge_log_tail_returns_empty_when_missing():
    import castor.brain.autodream_runner as runner_mod

    with patch("castor.brain.autodream_runner.Path") as MockPath:
        real_path = Path

        def path_side_effect(arg):
            if arg == "/tmp/castor-bridge.log":
                missing = MagicMock(spec=Path)
                missing.read_text.side_effect = FileNotFoundError
                return missing
            return real_path(arg)

        MockPath.side_effect = path_side_effect
        tail = runner_mod._load_bridge_log_tail()

    assert tail == []


# ── _load_cron_outcomes ───────────────────────────────────────────────────────


def test_load_cron_outcomes_extracts_last_3_summaries(tmp_path, monkeypatch):
    import castor.brain.autodream_runner as runner_mod

    dream_log = tmp_path / "dream-log.jsonl"
    entries = [{"date": f"2026-04-0{i}", "summary": f"summary {i}"} for i in range(1, 6)]
    dream_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    monkeypatch.setattr(runner_mod, "DREAM_LOG_FILE", dream_log)

    outcomes = runner_mod._load_cron_outcomes()

    assert outcomes == ["summary 3", "summary 4", "summary 5"]


def test_load_cron_outcomes_skips_entries_without_summary(tmp_path, monkeypatch):
    import castor.brain.autodream_runner as runner_mod

    dream_log = tmp_path / "dream-log.jsonl"
    entries = [
        {"date": "2026-04-01", "summary": ""},
        {"date": "2026-04-02"},
        {"date": "2026-04-03", "summary": "good run"},
    ]
    dream_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    monkeypatch.setattr(runner_mod, "DREAM_LOG_FILE", dream_log)

    outcomes = runner_mod._load_cron_outcomes()

    assert outcomes == ["good run"]


def test_load_cron_outcomes_returns_empty_when_missing(tmp_path, monkeypatch):
    import castor.brain.autodream_runner as runner_mod

    monkeypatch.setattr(runner_mod, "DREAM_LOG_FILE", tmp_path / "nonexistent.jsonl")
    assert runner_mod._load_cron_outcomes() == []


# ── main() passes new fields into DreamSession ───────────────────────────────


def test_main_passes_enriched_context_to_session(tmp_path, monkeypatch):
    """main() must pass recent_commits, bridge_log_tail, cron_outcomes to DreamSession."""
    import castor.brain.autodream_runner as runner_mod

    monkeypatch.setattr(runner_mod, "DRY_RUN", False)
    monkeypatch.setattr(runner_mod, "OPENCASTOR_DIR", tmp_path)
    monkeypatch.setattr(runner_mod, "MEMORY_FILE", tmp_path / "robot-memory.md")
    monkeypatch.setattr(runner_mod, "DREAM_LOG_FILE", tmp_path / "dream-log.jsonl")
    monkeypatch.setattr(runner_mod, "GATEWAY_LOG", tmp_path / "gateway.log")

    captured_session: list = []

    def fake_run(session):
        captured_session.append(session)
        from castor.brain.autodream import DreamResult

        return DreamResult(updated_memory="ok", summary="done")

    mock_brain = MagicMock()
    mock_brain.run.side_effect = fake_run

    with (
        patch("castor.providers.anthropic_provider.AnthropicProvider", return_value=MagicMock()),
        patch("castor.brain.autodream_runner.AutoDreamBrain", return_value=mock_brain),
        patch("castor.brain.autodream_runner._load_recent_commits", return_value=["abc fix"]),
        patch("castor.brain.autodream_runner._load_bridge_log_tail", return_value=["INFO ok"]),
        patch("castor.brain.autodream_runner._load_cron_outcomes", return_value=["prev summary"]),
    ):
        runner_mod.main()

    assert len(captured_session) == 1
    s = captured_session[0]
    assert s.recent_commits == ["abc fix"]
    assert s.bridge_log_tail == ["INFO ok"]
    assert s.cron_outcomes == ["prev summary"]
