"""Tests for ThresholdRace competition format (#736)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from castor.competitions.models import RaceStatus, ThresholdEntry, ThresholdRace, VerificationStatus
from castor.competitions.threshold_race import ThresholdRaceManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)
_PAST = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _make_manager() -> ThresholdRaceManager:
    """Return a ThresholdRaceManager with Firestore patched out."""
    mgr = ThresholdRaceManager()
    return mgr


def _make_race(mgr: ThresholdRaceManager, target: float = 0.80) -> ThresholdRace:
    with patch("castor.competitions.threshold_race._get_firestore_client", return_value=None):
        return mgr.create_race(
            name="Test Race",
            hardware_tier="pi5-hailo8l",
            model_id=None,
            target_score=target,
            prize_pool=1000,
            soft_deadline=_FUTURE,
        )


# ---------------------------------------------------------------------------
# Test 1: create_race persists to in-memory store
# ---------------------------------------------------------------------------


def test_create_race():
    mgr = _make_manager()
    race = _make_race(mgr)

    assert race.id in mgr._races
    assert race.name == "Test Race"
    assert race.hardware_tier == "pi5-hailo8l"
    assert race.target_score == 0.80
    assert race.prize_pool_credits == 1000
    assert race.status == RaceStatus.OPEN
    assert race.winner_rrn is None


# ---------------------------------------------------------------------------
# Test 2: submit_claim below threshold records entry without verification
# ---------------------------------------------------------------------------


def test_submit_below_threshold():
    mgr = _make_manager()
    race = _make_race(mgr, target=0.80)

    with patch("castor.competitions.threshold_race._get_firestore_client", return_value=None):
        entry = mgr.submit_claim(race.id, "rrn://org/robot/test/bot-1", score=0.70, candidate_id="cand-1")

    assert entry.best_score == 0.70
    assert entry.verification_status == VerificationStatus.PENDING
    # Race still open — no winner
    assert mgr._races[race.id].status == RaceStatus.OPEN


# ---------------------------------------------------------------------------
# Test 3: submit_claim at or above threshold triggers verification
# ---------------------------------------------------------------------------


def test_submit_claim_triggers_verification():
    mgr = _make_manager()
    race = _make_race(mgr, target=0.80)

    verify_called = []

    def _fake_verify(race_id, rrn, candidate_id):
        verify_called.append((race_id, rrn, candidate_id))
        return False  # verification fails — race stays open

    mgr.verify_claim = _fake_verify  # type: ignore[method-assign]

    with patch("castor.competitions.threshold_race._get_firestore_client", return_value=None):
        entry = mgr.submit_claim(race.id, "rrn://org/robot/test/bot-2", score=0.85, candidate_id="cand-2")

    assert entry.verification_status == VerificationStatus.VERIFYING
    assert len(verify_called) == 1
    assert verify_called[0] == (race.id, "rrn://org/robot/test/bot-2", "cand-2")


# ---------------------------------------------------------------------------
# Test 4: verification pass awards winner and closes race
# ---------------------------------------------------------------------------


def test_verification_pass_awards_winner():
    mgr = _make_manager()
    race = _make_race(mgr, target=0.80)

    # Inject an entry already in VERIFYING state
    entry = ThresholdEntry(
        race_id=race.id,
        rrn="rrn://org/robot/test/bot-3",
        best_score=0.85,
        verification_status=VerificationStatus.VERIFYING,
    )
    mgr._entries[race.id] = {"rrn://org/robot/test/bot-3": entry}

    # Patch the eval to always return a high score
    with (
        patch(
            "castor.competitions.threshold_race._run_verification_eval",
            return_value=0.85,
        ),
        patch("castor.competitions.threshold_race._get_firestore_client", return_value=None),
        patch("castor.competitions.threshold_race.ThresholdRaceManager._award_credits"),
    ):
        result = mgr.verify_claim(race.id, "rrn://org/robot/test/bot-3", "cand-3")

    assert result is True
    updated_race = mgr._races[race.id]
    assert updated_race.status == RaceStatus.COMPLETED
    assert updated_race.winner_rrn == "rrn://org/robot/test/bot-3"


# ---------------------------------------------------------------------------
# Test 5: soft deadline partial payout
# ---------------------------------------------------------------------------


def test_soft_deadline_partial_payout():
    mgr = _make_manager()

    # Create race with a past soft deadline
    with patch("castor.competitions.threshold_race._get_firestore_client", return_value=None):
        race = mgr.create_race(
            name="Expiring Race",
            hardware_tier="pi5-8gb",
            model_id=None,
            target_score=0.90,
            prize_pool=2000,
            soft_deadline=_PAST,  # already expired
        )

    # Add a leading entry
    mgr._entries[race.id] = {
        "rrn://org/robot/test/leader": ThresholdEntry(
            race_id=race.id,
            rrn="rrn://org/robot/test/leader",
            best_score=0.75,
        )
    }

    with (
        patch("castor.competitions.threshold_race._get_firestore_client", return_value=None),
        patch("castor.competitions.threshold_race.ThresholdRaceManager._award_credits") as mock_award,
    ):
        result = mgr.check_soft_deadline(race.id)

    assert result["action"] == "partial_payout"
    assert result["rrn"] == "rrn://org/robot/test/leader"
    assert result["credits_awarded"] == 1000  # 50% of 2000
    mock_award.assert_called_once()
    assert mgr._races[race.id].status == RaceStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test 6: get_standings returns entries sorted by best_score desc
# ---------------------------------------------------------------------------


def test_get_standings_sorted():
    mgr = _make_manager()
    race = _make_race(mgr)

    mgr._entries[race.id] = {
        "bot-a": ThresholdEntry(race_id=race.id, rrn="bot-a", best_score=0.60),
        "bot-b": ThresholdEntry(race_id=race.id, rrn="bot-b", best_score=0.85),
        "bot-c": ThresholdEntry(race_id=race.id, rrn="bot-c", best_score=0.72),
    }

    with patch("castor.competitions.threshold_race._get_firestore_client", return_value=None):
        standings = mgr.get_standings(race.id)

    assert [e.rrn for e in standings] == ["bot-b", "bot-c", "bot-a"]


# ---------------------------------------------------------------------------
# Test 7: submit_claim updates best_score only when new score is higher
# ---------------------------------------------------------------------------


def test_submit_claim_does_not_downgrade_best_score():
    mgr = _make_manager()
    race = _make_race(mgr, target=0.95)

    with patch("castor.competitions.threshold_race._get_firestore_client", return_value=None):
        mgr.submit_claim(race.id, "bot-x", score=0.70, candidate_id="c1")
        entry_after_second = mgr.submit_claim(race.id, "bot-x", score=0.55, candidate_id="c2")

    assert entry_after_second.best_score == 0.70


# ---------------------------------------------------------------------------
# Test 8: verification failure keeps race open
# ---------------------------------------------------------------------------


def test_verification_failure_keeps_race_open():
    mgr = _make_manager()
    race = _make_race(mgr, target=0.90)

    entry = ThresholdEntry(
        race_id=race.id,
        rrn="bot-fail",
        best_score=0.92,
        verification_status=VerificationStatus.VERIFYING,
    )
    mgr._entries[race.id] = {"bot-fail": entry}

    # Verification eval returns score below tolerance (0.90 * 0.98 = 0.882)
    with (
        patch("castor.competitions.threshold_race._run_verification_eval", return_value=0.70),
        patch("castor.competitions.threshold_race._get_firestore_client", return_value=None),
    ):
        result = mgr.verify_claim(race.id, "bot-fail", "cand-fail")

    assert result is False
    assert mgr._races[race.id].status == RaceStatus.OPEN
    assert mgr._entries[race.id]["bot-fail"].verification_status == VerificationStatus.FAILED
