"""
tests/test_cli_compete.py — Tests for castor compete and castor leaderboard CLI commands.
"""

from __future__ import annotations

import argparse
import json
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
# castor leaderboard
# ---------------------------------------------------------------------------


class TestCmdLeaderboard:
    def test_prints_table_on_success(self, capsys):
        from castor.commands.leaderboard import cmd_leaderboard

        rows = [
            {
                "rank": 1,
                "robot_name": "alpha",
                "score": 95.0,
                "eval_count": 10,
                "last_eval": "2026-03-20",
            },
            {
                "rank": 2,
                "robot_name": "beta",
                "score": 88.0,
                "eval_count": 8,
                "last_eval": "2026-03-19",
            },
            {
                "rank": 3,
                "robot_name": "gamma",
                "score": 72.0,
                "eval_count": 5,
                "last_eval": "2026-03-18",
            },
        ]
        args = _make_args(tier="community", season=None, top=10, output_json=False, config=None)

        with patch("castor.commands.leaderboard._fetch_leaderboard_http", return_value=rows):
            cmd_leaderboard(args)

        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out

    def test_json_flag_outputs_json(self, capsys):
        from castor.commands.leaderboard import cmd_leaderboard

        rows = [{"rank": 1, "robot_name": "alpha", "score": 90}]
        args = _make_args(tier="community", season=None, top=10, output_json=True, config=None)

        with patch("castor.commands.leaderboard._fetch_leaderboard_http", return_value=rows):
            cmd_leaderboard(args)

        out = capsys.readouterr().out
        parsed = json.loads(out.strip())
        assert parsed[0]["robot_name"] == "alpha"

    def test_offline_message_when_gateway_unreachable(self, capsys):
        from castor.commands.leaderboard import cmd_leaderboard

        args = _make_args(tier="community", season=None, top=10, output_json=False, config=None)

        with patch("castor.commands.leaderboard._fetch_leaderboard_http", return_value=None):
            with patch.dict("os.environ", {}, clear=True):
                cmd_leaderboard(args)

        out = capsys.readouterr().out
        assert "Could not reach gateway" in out or "No leaderboard data" in out

    def test_firestore_fallback_used_when_credentials_set(self, capsys):
        from castor.commands.leaderboard import cmd_leaderboard

        rows = [{"rank": 1, "robot_name": "firestore-bot", "score": 80}]
        args = _make_args(tier="community", season=None, top=10, output_json=False, config=None)

        with patch("castor.commands.leaderboard._fetch_leaderboard_http", return_value=None):
            with patch(
                "castor.commands.leaderboard._fetch_leaderboard_firestore", return_value=rows
            ):
                with patch.dict("os.environ", {"GOOGLE_APPLICATION_CREDENTIALS": "/creds.json"}):
                    cmd_leaderboard(args)

        out = capsys.readouterr().out
        assert "firestore-bot" in out

    def test_empty_leaderboard_shows_no_data_message(self, capsys):
        from castor.commands.leaderboard import cmd_leaderboard

        args = _make_args(tier="community", season=None, top=10, output_json=False, config=None)

        with patch("castor.commands.leaderboard._fetch_leaderboard_http", return_value=[]):
            cmd_leaderboard(args)

        out = capsys.readouterr().out
        assert "No leaderboard data" in out


# ---------------------------------------------------------------------------
# castor compete list
# ---------------------------------------------------------------------------


class TestCmdCompeteList:
    def test_list_prints_competitions(self, capsys):
        from castor.commands.compete import cmd_compete

        competitions = [
            {
                "name": "Sprint Q1",
                "type": "sprint",
                "seconds_remaining": 7200,
                "credit_pool": 100,
                "robot_count": 5,
            }
        ]
        args = _make_args(compete_action="list", competition_id=None)

        with patch("castor.commands.compete._api_get", return_value={"competitions": competitions}):
            cmd_compete(args)

        out = capsys.readouterr().out
        assert "Sprint Q1" in out
        assert "2h" in out

    def test_list_no_competitions(self, capsys):
        from castor.commands.compete import cmd_compete

        args = _make_args(compete_action="list", competition_id=None)

        with patch("castor.commands.compete._api_get", return_value={"competitions": []}):
            cmd_compete(args)

        out = capsys.readouterr().out
        assert "No active competitions" in out

    def test_list_gateway_unreachable(self, capsys):
        from castor.commands.compete import cmd_compete

        args = _make_args(compete_action="list", competition_id=None)

        with patch("castor.commands.compete._api_get", return_value=None):
            cmd_compete(args)

        out = capsys.readouterr().out
        assert "Could not reach gateway" in out


# ---------------------------------------------------------------------------
# castor compete enter
# ---------------------------------------------------------------------------


class TestCmdCompeteEnter:
    def test_enter_success(self, capsys):
        from castor.commands.compete import cmd_compete

        args = _make_args(compete_action="enter", competition_id="sprint-q1")

        with patch("castor.commands.compete._api_post", return_value={"ok": True}):
            with patch("castor.commands.compete._api_post") as mock_post:
                mock_post.side_effect = [{"ok": True}, None]  # enter OK, bridge fails silently
                cmd_compete(args)

        out = capsys.readouterr().out
        assert "sprint-q1" in out

    def test_enter_missing_id_shows_usage(self, capsys):
        from castor.commands.compete import cmd_compete

        args = _make_args(compete_action="enter", competition_id=None)
        cmd_compete(args)

        out = capsys.readouterr().out
        assert "Usage" in out

    def test_enter_gateway_unreachable(self, capsys):
        from castor.commands.compete import cmd_compete

        args = _make_args(compete_action="enter", competition_id="sprint-q1")

        with patch("castor.commands.compete._api_post", return_value=None):
            cmd_compete(args)

        out = capsys.readouterr().out
        assert "Could not reach gateway" in out

    def test_enter_api_error_message(self, capsys):
        from castor.commands.compete import cmd_compete

        args = _make_args(compete_action="enter", competition_id="sprint-q1")

        with patch("castor.commands.compete._api_post", return_value={"error": "already entered"}):
            cmd_compete(args)

        out = capsys.readouterr().out
        assert "already entered" in out


# ---------------------------------------------------------------------------
# castor compete status
# ---------------------------------------------------------------------------


class TestCmdCompeteStatus:
    def test_status_shows_rank(self, capsys):
        from castor.commands.compete import cmd_compete

        rows = [
            {"rank": 1, "robot_name": "my-robot", "score": 95},
            {"rank": 2, "robot_name": "other-bot", "score": 80},
        ]
        args = _make_args(compete_action="status", competition_id="sprint-q1")

        with patch("castor.commands.compete._api_get", return_value=rows):
            with patch.dict("os.environ", {"OPENCASTOR_ROBOT_NAME": "my-robot"}):
                cmd_compete(args)

        out = capsys.readouterr().out
        assert "#1" in out
        assert "95" in out

    def test_status_gateway_unreachable(self, capsys):
        from castor.commands.compete import cmd_compete

        args = _make_args(compete_action="status", competition_id="sprint-q1")

        with patch("castor.commands.compete._api_get", return_value=None):
            cmd_compete(args)

        out = capsys.readouterr().out
        assert "Could not reach gateway" in out
