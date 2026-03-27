"""Tests for Sprint competition format — Issue #735.

Covers: create, submit, reject late submission, leaderboard order, finalize payout math.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from castor.competitions.models import (
    CompetitionFormat,
    CompetitionStatus,
    SprintCompetition,
    SprintEntry,
)
from castor.competitions.sprint import SprintManager, _compute_status, _now

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(*args, **kwargs) -> datetime:
    return datetime(*args, tzinfo=timezone.utc, **kwargs)


def _active_comp(prize_pool: int = 1000) -> SprintCompetition:
    """Return an ACTIVE competition: started 1 h ago, ends in 3 h."""
    now = _now()
    return SprintCompetition(
        id=str(uuid.uuid4()),
        name="Test Sprint",
        format=CompetitionFormat.SPRINT,
        hardware_tiers=["pi5"],
        model_id=None,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=3),
        prize_pool_credits=prize_pool,
        status=CompetitionStatus.ACTIVE,
        created_at=now,
    )


def _mock_db() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# Test: create_sprint
# ---------------------------------------------------------------------------


class TestCreateSprint:
    def test_create_returns_sprint_competition(self):
        mgr = SprintManager()
        now = _now()

        with patch("castor.competitions.sprint._get_firestore_client", return_value=_mock_db()):
            comp = mgr.create_sprint(
                name="Weekly Sprint",
                hardware_tiers=["pi5", "server"],
                model_id="gemini-2.5-flash",
                starts_at=now + timedelta(hours=1),
                ends_at=now + timedelta(hours=25),
                prize_pool=500,
            )

        assert isinstance(comp, SprintCompetition)
        assert comp.name == "Weekly Sprint"
        assert comp.format == CompetitionFormat.SPRINT
        assert comp.hardware_tiers == ["pi5", "server"]
        assert comp.model_id == "gemini-2.5-flash"
        assert comp.prize_pool_credits == 500
        assert comp.status == CompetitionStatus.UPCOMING
        assert len(comp.id) > 0

    def test_create_offline_does_not_raise(self):
        """Firestore failure must not propagate — graceful offline fallback."""
        mgr = SprintManager()
        now = _now()

        with patch("castor.competitions.sprint._get_firestore_client", return_value=None):
            comp = mgr.create_sprint(
                name="Offline Sprint",
                hardware_tiers=["server"],
                model_id=None,
                starts_at=now + timedelta(hours=1),
                ends_at=now + timedelta(hours=2),
                prize_pool=100,
            )

        assert comp.name == "Offline Sprint"


# ---------------------------------------------------------------------------
# Test: submit_score
# ---------------------------------------------------------------------------


class TestSubmitScore:
    def _entry_ref_mock(self, db: MagicMock, exists: bool, existing_score: float = 0.0):
        """Wire the mock db so entries/{rrn}.get() returns exists/score."""
        doc_mock = MagicMock()
        doc_mock.exists = exists
        doc_mock.to_dict.return_value = {
            "rrn": "RRN-000000000001",
            "best_score": existing_score,
            "submitted_at": _now().isoformat(),
            "competition_id": "comp-001",
        }
        (
            db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value
        ) = doc_mock
        return doc_mock

    def test_submit_score_accepted_new_entry(self):
        mgr = SprintManager()
        comp = _active_comp()
        db = _mock_db()
        self._entry_ref_mock(db, exists=False)

        with patch("castor.competitions.sprint._get_firestore_client", return_value=db):
            with patch.object(mgr, "_load_competition", return_value=comp):
                entry = mgr.submit_score(comp.id, "RRN-000000000001", 95.5, "cand-001")

        assert entry.rrn == "RRN-000000000001"
        assert entry.best_score == 95.5
        assert entry.competition_id == comp.id

    def test_submit_score_rejected_when_locked(self):
        """Competition ends in 30 min — already inside the 1 h lock window."""
        mgr = SprintManager()
        now = _now()
        comp = SprintCompetition(
            id=str(uuid.uuid4()),
            name="Locking Soon",
            format=CompetitionFormat.SPRINT,
            hardware_tiers=["pi5"],
            model_id=None,
            starts_at=now - timedelta(hours=2),
            ends_at=now + timedelta(minutes=30),
            prize_pool_credits=200,
            status=CompetitionStatus.LOCKED,
            created_at=now,
        )

        with patch.object(mgr, "_load_competition", return_value=comp):
            with pytest.raises(ValueError, match="not accepting submissions"):
                mgr.submit_score(comp.id, "RRN-000000000002", 80.0, "cand-002")

    def test_submit_score_rejected_upcoming(self):
        """Competition hasn't started yet."""
        mgr = SprintManager()
        now = _now()
        comp = SprintCompetition(
            id=str(uuid.uuid4()),
            name="Future Sprint",
            format=CompetitionFormat.SPRINT,
            hardware_tiers=["pi5"],
            model_id=None,
            starts_at=now + timedelta(hours=2),
            ends_at=now + timedelta(hours=26),
            prize_pool_credits=100,
            status=CompetitionStatus.UPCOMING,
            created_at=now,
        )

        with patch.object(mgr, "_load_competition", return_value=comp):
            with pytest.raises(ValueError, match="not accepting submissions"):
                mgr.submit_score(comp.id, "RRN-000000000003", 70.0, "cand-003")

    def test_submit_lower_score_keeps_existing_best(self):
        """Score <= existing best must not overwrite Firestore."""
        mgr = SprintManager()
        comp = _active_comp()
        db = _mock_db()
        self._entry_ref_mock(db, exists=True, existing_score=90.0)

        with patch("castor.competitions.sprint._get_firestore_client", return_value=db):
            with patch.object(mgr, "_load_competition", return_value=comp):
                entry = mgr.submit_score(comp.id, "RRN-000000000001", 70.0, "cand-004")

        # Should return the existing best, not the lower submitted score
        assert entry.best_score == 90.0
        # ref.set should NOT have been called
        ref = db.collection.return_value.document.return_value.collection.return_value.document.return_value
        ref.set.assert_not_called()


# ---------------------------------------------------------------------------
# Test: get_leaderboard
# ---------------------------------------------------------------------------


class TestLeaderboard:
    def test_leaderboard_rank_assignment(self):
        """Entries returned from Firestore get sequential ranks starting at 1."""
        mgr = SprintManager()

        def _doc(rrn: str, score: float) -> MagicMock:
            d = MagicMock()
            d.id = rrn
            d.to_dict.return_value = {
                "rrn": rrn,
                "best_score": score,
                "submitted_at": _now().isoformat(),
                "competition_id": "comp-lb",
            }
            return d

        db = _mock_db()
        # Firestore order_by DESCENDING assumed: highest score first
        (
            db.collection.return_value.document.return_value.collection.return_value.order_by.return_value.stream.return_value
        ) = [
            _doc("RRN-A", 95.0),
            _doc("RRN-B", 85.0),
            _doc("RRN-C", 70.0),
        ]

        with patch("castor.competitions.sprint._get_firestore_client", return_value=db):
            entries = mgr.get_leaderboard("comp-lb")

        assert len(entries) == 3
        assert entries[0].rrn == "RRN-A"
        assert entries[0].rank == 1
        assert entries[1].rank == 2
        assert entries[2].rank == 3


# ---------------------------------------------------------------------------
# Test: finalize_sprint — payout math
# ---------------------------------------------------------------------------


class TestFinalizeSprint:
    def test_payout_math_top3(self):
        """rank-1=50%, rank-2=30%, rank-3=20% of prize_pool distributed via award_credits."""
        mgr = SprintManager()
        now = _now()
        comp = _active_comp(prize_pool=1000)

        entries = [
            SprintEntry("c", "RRN-A", 95.0, now, rank=1),
            SprintEntry("c", "RRN-B", 85.0, now, rank=2),
            SprintEntry("c", "RRN-C", 75.0, now, rank=3),
        ]

        def _mock_award(owner_uid, rrn, scenarios_completed, beat_champion, rare_tier, tier):
            return scenarios_completed * 10  # simulate exact payout

        with patch.object(mgr, "_load_competition", return_value=comp):
            with patch.object(mgr, "get_leaderboard", return_value=entries):
                with patch(
                    "castor.competitions.sprint._get_firestore_client", return_value=_mock_db()
                ):
                    with patch("castor.competitions.sprint.award_credits", side_effect=_mock_award):
                        payouts = mgr.finalize_sprint(comp.id)

        assert payouts["RRN-A"] == 500  # 50% of 1000
        assert payouts["RRN-B"] == 300  # 30% of 1000
        assert payouts["RRN-C"] == 200  # 20% of 1000

    def test_finalize_missing_competition_returns_empty(self):
        mgr = SprintManager()
        with patch.object(mgr, "_load_competition", return_value=None):
            payouts = mgr.finalize_sprint("nonexistent-id")
        assert payouts == {}


# ---------------------------------------------------------------------------
# Test: _compute_status helper
# ---------------------------------------------------------------------------


class TestComputeStatus:
    def test_upcoming(self):
        now = _now()
        assert (
            _compute_status(now + timedelta(hours=1), now + timedelta(hours=5))
            == CompetitionStatus.UPCOMING
        )

    def test_active(self):
        now = _now()
        assert (
            _compute_status(now - timedelta(hours=1), now + timedelta(hours=3))
            == CompetitionStatus.ACTIVE
        )

    def test_locked(self):
        now = _now()
        assert (
            _compute_status(now - timedelta(hours=2), now + timedelta(minutes=30))
            == CompetitionStatus.LOCKED
        )

    def test_completed(self):
        now = _now()
        assert (
            _compute_status(now - timedelta(hours=3), now - timedelta(hours=1))
            == CompetitionStatus.COMPLETED
        )
