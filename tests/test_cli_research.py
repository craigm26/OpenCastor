"""
tests/test_cli_research.py — Tests for castor research and castor season CLI commands.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs):
    args = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


# ---------------------------------------------------------------------------
# castor research status
# ---------------------------------------------------------------------------


class TestCmdResearchStatus:
    def test_status_with_champion(self, capsys, tmp_path):
        from castor.commands.research import cmd_research

        harness_dir = tmp_path / "harness-research"
        harness_dir.mkdir()
        champion = harness_dir / "champion.yaml"
        champion.write_text("score: 92.5\nevaluated_at: '2026-03-20'\n")

        args = _make_args(research_action="status")

        with patch("castor.commands.research._ops_dir", return_value=tmp_path):
            cmd_research(args)

        out = capsys.readouterr().out
        assert "92.5" in out
        assert "2026-03-20" in out

    def test_status_no_champion(self, capsys, tmp_path):
        from castor.commands.research import cmd_research

        args = _make_args(research_action="status")

        with patch("castor.commands.research._ops_dir", return_value=tmp_path):
            cmd_research(args)

        out = capsys.readouterr().out
        assert "not available" in out

    def test_status_shows_queue_depth(self, capsys, tmp_path):
        from castor.commands.research import cmd_research

        harness_dir = tmp_path / "harness-research"
        candidates = harness_dir / "candidates"
        candidates.mkdir(parents=True)
        (candidates / "cand-001.yaml").write_text("score: 80\n")
        (candidates / "cand-002.yaml").write_text("score: 75\n")
        (candidates / "cand-001-winner.yaml").write_text("score: 80\n")

        args = _make_args(research_action="status")

        with patch("castor.commands.research._ops_dir", return_value=tmp_path):
            cmd_research(args)

        out = capsys.readouterr().out
        assert "Queue depth" in out
        # 3 total YAMLs (cand-001, cand-002, cand-001-winner) - 1 winner = 2 pending
        assert "2" in out


# ---------------------------------------------------------------------------
# castor research history
# ---------------------------------------------------------------------------


class TestCmdResearchHistory:
    def test_history_with_winners(self, capsys, tmp_path):
        from castor.commands.research import cmd_research

        harness_dir = tmp_path / "harness-research"
        candidates = harness_dir / "candidates"
        candidates.mkdir(parents=True)

        for i in range(3):
            (candidates / f"run-{i:03d}-winner.yaml").write_text(
                f"score: {80 + i}\nevaluated_at: '2026-03-{18 + i:02d}'\nharness: harness-{i}\n"
            )

        args = _make_args(research_action="history")

        with patch("castor.commands.research._ops_dir", return_value=tmp_path):
            cmd_research(args)

        out = capsys.readouterr().out
        assert "harness-0" in out or "run-000" in out

    def test_history_no_files(self, capsys, tmp_path):
        from castor.commands.research import cmd_research

        args = _make_args(research_action="history")

        with patch("castor.commands.research._ops_dir", return_value=tmp_path):
            cmd_research(args)

        out = capsys.readouterr().out
        assert "No history found" in out


# ---------------------------------------------------------------------------
# castor research champion
# ---------------------------------------------------------------------------


class TestCmdResearchChampion:
    def test_champion_prints_yaml(self, capsys, tmp_path):
        from castor.commands.research import cmd_research

        harness_dir = tmp_path / "harness-research"
        harness_dir.mkdir()
        champion = harness_dir / "champion.yaml"
        champion.write_text("score: 99\nmodel: gemini-2.5-flash\n")

        args = _make_args(research_action="champion")

        with patch("castor.commands.research._ops_dir", return_value=tmp_path):
            cmd_research(args)

        out = capsys.readouterr().out
        assert "score" in out
        assert "99" in out

    def test_champion_missing(self, capsys, tmp_path):
        from castor.commands.research import cmd_research

        args = _make_args(research_action="champion")

        with patch("castor.commands.research._ops_dir", return_value=tmp_path):
            cmd_research(args)

        out = capsys.readouterr().out
        assert "No champion.yaml found" in out


# ---------------------------------------------------------------------------
# castor research queue
# ---------------------------------------------------------------------------


class TestCmdResearchQueue:
    def test_queue_from_gateway(self, capsys):
        from castor.commands.research import cmd_research

        args = _make_args(research_action="queue")

        with patch(
            "castor.commands.research._api_get",
            return_value={"queue_depth": 7, "running": True, "last_eval": "2026-03-20"},
        ):
            cmd_research(args)

        out = capsys.readouterr().out
        assert "7" in out

    def test_queue_fallback_to_local(self, capsys, tmp_path):
        from castor.commands.research import cmd_research

        args = _make_args(research_action="queue")

        with patch("castor.commands.research._api_get", return_value=None):
            with patch("castor.commands.research._ops_dir", return_value=tmp_path):
                cmd_research(args)

        out = capsys.readouterr().out
        assert "Gateway not reachable" in out or "queue depth" in out.lower()

    def test_unknown_action(self, capsys):
        from castor.commands.research import cmd_research

        args = _make_args(research_action="bogus")
        cmd_research(args)

        out = capsys.readouterr().out
        assert "Unknown research action" in out


# ---------------------------------------------------------------------------
# castor season
# ---------------------------------------------------------------------------


class TestCmdSeason:
    def test_season_default_overview(self, capsys):
        from castor.commands.season import cmd_season

        data = {
            "id": "2026-spring",
            "days_remaining": 42,
            "class_id": "medium",
            "your_rank": 3,
            "your_score": 88.5,
        }
        args = _make_args(list_seasons=False, class_id=None)

        with patch("castor.commands.season._api_get", return_value=data):
            cmd_season(args)

        out = capsys.readouterr().out
        assert "2026-spring" in out
        assert "42" in out
        assert "#3" in out

    def test_season_list(self, capsys):
        from castor.commands.season import cmd_season

        seasons = [
            {"id": "2025-fall", "status": "completed", "start_date": "2025-09-01", "end_date": "2025-11-30"},
            {"id": "2026-spring", "status": "active", "start_date": "2026-03-01", "end_date": "2026-05-31"},
        ]
        args = _make_args(list_seasons=True, class_id=None)

        with patch("castor.commands.season._api_get", return_value=seasons):
            cmd_season(args)

        out = capsys.readouterr().out
        assert "2025-fall" in out
        assert "2026-spring" in out

    def test_season_class_filter(self, capsys):
        from castor.commands.season import cmd_season

        data = {
            "leaderboard": [
                {"rank": 1, "robot_name": "top-bot", "score": 99},
                {"rank": 2, "robot_name": "second-bot", "score": 87},
            ]
        }
        args = _make_args(list_seasons=False, class_id="medium")

        with patch("castor.commands.season._api_get", return_value=data):
            cmd_season(args)

        out = capsys.readouterr().out
        assert "medium" in out
        assert "top-bot" in out

    def test_season_gateway_unreachable(self, capsys):
        from castor.commands.season import cmd_season

        args = _make_args(list_seasons=False, class_id=None)

        with patch("castor.commands.season._api_get", return_value=None):
            cmd_season(args)

        out = capsys.readouterr().out
        assert "Could not reach gateway" in out
