"""Tests for castor/competitions/bracket_season.py — BracketSeasonManager (#737)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call

from castor.competitions.models import BracketEntry, INITIAL_CLASSES
from castor.competitions.bracket_season import BracketSeasonManager


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_doc(data: dict | None, doc_id: str = "") -> MagicMock:
    """Build a minimal Firestore document mock."""
    m = MagicMock()
    m.exists = data is not None
    m.to_dict.return_value = dict(data) if data else {}
    m.id = doc_id
    return m


def _make_submit_db(
    *,
    season_status: str = "ACTIVE",
    class_hw_tier: str = "pi5-hailo8l",
    robot_hw_tier: str | None = "pi5-hailo8l",
    existing_score: float | None = None,
) -> MagicMock:
    """Build a Firestore mock for submit_score tests.

    Routes db.collection("seasons") and db.collection("robots") to separate mocks
    so each can return different data.
    """
    db = MagicMock()

    seasons_coll = MagicMock()
    robots_coll = MagicMock()

    def _coll(name: str):
        if name == "seasons":
            return seasons_coll
        if name == "robots":
            return robots_coll
        return MagicMock()

    db.collection.side_effect = _coll

    # ── season document ────────────────────────────────────────────────
    season_doc = _mock_doc({"status": season_status})
    # ── class document ────────────────────────────────────────────────
    class_doc = _mock_doc(
        {"hardware_tier": class_hw_tier, "model_id": "gemini-2.5-flash",
         "season_id": "2026-04", "scenario_pack_id": "default"}
    )
    # ── entry document ────────────────────────────────────────────────
    entry_data = {"best_score": existing_score, "submitted_at": 1000,
                  "season_id": "2026-04", "class_id": "pi5-hailo8l__gemini-2.5-flash",
                  "rrn": "RRN-001"} if existing_score is not None else None
    entry_doc = _mock_doc(entry_data, doc_id="RRN-001")
    # ── robot document ────────────────────────────────────────────────
    robot_data = {"hardware_tier": robot_hw_tier} if robot_hw_tier else None
    robot_doc = _mock_doc(robot_data, doc_id="RRN-001")

    # Wire seasons chain:
    #   seasons_coll.document(sid).get() → season_doc
    #   seasons_coll.document(sid).collection("classes").document(cid).get() → class_doc
    #   ... .collection("entries").document(rrn).get() → entry_doc
    entry_ref = MagicMock()
    entry_ref.get.return_value = entry_doc

    entries_coll = MagicMock()
    entries_coll.document.return_value = entry_ref

    class_ref = MagicMock()
    class_ref.get.return_value = class_doc
    class_ref.collection.return_value = entries_coll

    classes_coll = MagicMock()
    classes_coll.document.return_value = class_ref

    season_ref = MagicMock()
    season_ref.get.return_value = season_doc
    season_ref.collection.return_value = classes_coll

    seasons_coll.document.return_value = season_ref

    # Wire robots chain
    robots_coll.document.return_value.get.return_value = robot_doc

    return db


def _make_finalize_db(class_ids: list[str]) -> MagicMock:
    """Build a Firestore mock for finalize_season tests.

    seasons/{sid}.get() → ACTIVE season doc
    seasons/{sid}/classes.stream() → class docs for each class_id
    """
    db = MagicMock()

    season_doc = _mock_doc({"status": "ACTIVE"})
    class_stream_docs = [_mock_doc({"hardware_tier": cid.split("__")[0],
                                    "model_id": cid.split("__")[1]}, doc_id=cid)
                         for cid in class_ids]

    season_ref = MagicMock()
    season_ref.get.return_value = season_doc
    # .collection("classes").stream() → class_stream_docs
    # All other .collection() calls (champions, contributors) are MagicMock (no-op)
    classes_coll = MagicMock()
    classes_coll.stream.return_value = class_stream_docs
    season_ref.collection.return_value = classes_coll

    db.collection.return_value.document.return_value = season_ref
    return db


# ---------------------------------------------------------------------------
# Test 1 — create_season generates correct classes
# ---------------------------------------------------------------------------


def test_create_season_generates_correct_classes():
    """create_season(2026, 4) produces exactly the INITIAL_CLASSES entries."""
    with patch(
        "castor.competitions.bracket_season._get_firestore_client",
        return_value=MagicMock(),
    ):
        mgr = BracketSeasonManager()
        season = mgr.create_season(2026, 4)

    assert season.season_id == "2026-04"
    assert season.starts_at.year == 2026
    assert season.starts_at.month == 4
    assert season.starts_at.day == 1
    assert season.ends_at.day == 30  # April has 30 days
    assert season.status == "UPCOMING"
    assert len(season.classes) == len(INITIAL_CLASSES)

    expected_ids = {f"{c['hardware_tier']}__{c['model_id']}" for c in INITIAL_CLASSES}
    actual_ids = {cls.class_id for cls in season.classes}
    assert actual_ids == expected_ids

    for cls in season.classes:
        assert cls.season_id == "2026-04"
        assert cls.scenario_pack_id == "default"
        assert cls.hardware_tier
        assert cls.model_id


# ---------------------------------------------------------------------------
# Test 2 — submit_score rejects wrong hardware tier
# ---------------------------------------------------------------------------


def test_submit_score_wrong_hardware_tier_rejected():
    """Submitting with mismatched hardware tier raises ValueError."""
    db = _make_submit_db(
        season_status="ACTIVE",
        class_hw_tier="pi5-hailo8l",
        robot_hw_tier="pi5-8gb",  # ← wrong tier
    )

    with patch("castor.competitions.bracket_season._get_firestore_client", return_value=db):
        mgr = BracketSeasonManager()
        with pytest.raises(ValueError, match="tier mismatch"):
            mgr.submit_score("2026-04", "pi5-hailo8l__gemini-2.5-flash", "RRN-001", 0.8)


# ---------------------------------------------------------------------------
# Test 3 — submit_score updates best score
# ---------------------------------------------------------------------------


def test_submit_score_updates_best():
    """A higher score replaces the existing entry; a lower score is ignored."""
    # --- higher score should update ---
    db = _make_submit_db(existing_score=0.7)
    with patch("castor.competitions.bracket_season._get_firestore_client", return_value=db):
        mgr = BracketSeasonManager()
        entry = mgr.submit_score("2026-04", "pi5-hailo8l__gemini-2.5-flash", "RRN-001", 0.9)
    assert entry.best_score == 0.9

    # --- lower score should NOT update (returns existing best) ---
    db2 = _make_submit_db(existing_score=0.9)
    with patch("castor.competitions.bracket_season._get_firestore_client", return_value=db2):
        mgr2 = BracketSeasonManager()
        entry2 = mgr2.submit_score("2026-04", "pi5-hailo8l__gemini-2.5-flash", "RRN-001", 0.5)
    assert entry2.best_score == 0.9  # unchanged


# ---------------------------------------------------------------------------
# Test 4 — leaderboard sorted correctly
# ---------------------------------------------------------------------------


def test_leaderboard_sorted_correctly():
    """get_class_leaderboard returns entries sorted descending with correct ranks."""
    # Three entry docs in unsorted order
    raw_scores = [("RRN-A", 0.7), ("RRN-B", 0.9), ("RRN-C", 0.5)]
    docs = [_mock_doc({"best_score": score, "submitted_at": 1000,
                       "season_id": "2026-04", "class_id": "pi5-hailo8l__gemini-2.5-flash",
                       "rrn": rrn}, doc_id=rrn)
            for rrn, score in raw_scores]

    db = MagicMock()
    # Chain: db.collection().document().collection().document().collection().stream() → docs
    entries_coll = MagicMock()
    entries_coll.stream.return_value = docs
    db.collection.return_value.document.return_value.collection.return_value \
        .document.return_value.collection.return_value = entries_coll

    with patch("castor.competitions.bracket_season._get_firestore_client", return_value=db):
        mgr = BracketSeasonManager()
        lb = mgr.get_class_leaderboard("2026-04", "pi5-hailo8l__gemini-2.5-flash")

    assert len(lb) == 3
    assert lb[0].rrn == "RRN-B" and lb[0].rank == 1 and lb[0].best_score == 0.9
    assert lb[1].rrn == "RRN-A" and lb[1].rank == 2 and lb[1].best_score == 0.7
    assert lb[2].rrn == "RRN-C" and lb[2].rank == 3 and lb[2].best_score == 0.5


# ---------------------------------------------------------------------------
# Test 5 — finalize awards class champions
# ---------------------------------------------------------------------------


def test_finalize_awards_class_champions():
    """finalize_season crowns the rank-1 entry per class and calls award_credits."""
    class_id = "pi5-hailo8l__gemini-2.5-flash"
    db = _make_finalize_db([class_id])

    rank1 = BracketEntry(season_id="2026-04", class_id=class_id, rrn="RRN-001",
                         best_score=0.9, submitted_at=1000, rank=1)
    rank2 = BracketEntry(season_id="2026-04", class_id=class_id, rrn="RRN-002",
                         best_score=0.7, submitted_at=900, rank=2)

    with (
        patch("castor.competitions.bracket_season._get_firestore_client", return_value=db),
        patch.object(BracketSeasonManager, "get_class_leaderboard",
                     return_value=[rank1, rank2]),
        patch.object(BracketSeasonManager, "_get_owner_uid",
                     side_effect=lambda _db, rrn: f"owner-{rrn}"),
        patch("castor.contribute.credits.award_credits",
              side_effect=[2000, 1000, 5000]) as mock_award,
    ):
        mgr = BracketSeasonManager()
        champions = mgr.finalize_season("2026-04")

    # rank-1 call: scenarios_completed=200
    rank1_call = call("owner-RRN-001", "RRN-001", scenarios_completed=200,
                      beat_champion=False, rare_tier=False, tier="2026-04")
    # rank-2 call: scenarios_completed=100
    rank2_call = call("owner-RRN-002", "RRN-002", scenarios_completed=100,
                      beat_champion=False, rare_tier=False, tier="2026-04")
    mock_award.assert_any_call(*rank1_call.args, **rank1_call.kwargs)
    mock_award.assert_any_call(*rank2_call.args, **rank2_call.kwargs)

    assert len(champions) == 1
    champ = champions[0]
    assert champ.rrn == "RRN-001"
    assert champ.class_id == class_id
    assert champ.score == 0.9
    # Only class, so also grand champion — credits_awarded = 2000 + 5000
    assert champ.is_grand_champion is True
    assert champ.credits_awarded == 7000


# ---------------------------------------------------------------------------
# Test 6 — grand champion gets bonus
# ---------------------------------------------------------------------------


def test_grand_champion_gets_bonus():
    """The robot with the highest score across all class winners earns grand champion."""
    class_a = "pi5-hailo8l__gemini-2.5-flash"
    class_b = "server__gemini-2.5-pro"
    db = _make_finalize_db([class_a, class_b])

    winner_a = BracketEntry(season_id="2026-04", class_id=class_a, rrn="RRN-A",
                            best_score=0.95, submitted_at=1000, rank=1)
    winner_b = BracketEntry(season_id="2026-04", class_id=class_b, rrn="RRN-B",
                            best_score=0.80, submitted_at=900, rank=1)

    def _lb(season_id: str, class_id: str) -> list[BracketEntry]:
        return [winner_a] if class_id == class_a else [winner_b]

    with (
        patch("castor.competitions.bracket_season._get_firestore_client", return_value=db),
        patch.object(BracketSeasonManager, "get_class_leaderboard", side_effect=_lb),
        patch.object(BracketSeasonManager, "_get_owner_uid",
                     side_effect=lambda _db, rrn: f"owner-{rrn}"),
        patch("castor.contribute.credits.award_credits",
              side_effect=[2000, 2000, 5000]) as mock_award,
    ):
        mgr = BracketSeasonManager()
        champions = mgr.finalize_season("2026-04")

    assert len(champions) == 2

    # grand champion is RRN-A (score 0.95 > 0.80)
    gc = next(c for c in champions if c.is_grand_champion)
    assert gc.rrn == "RRN-A"
    assert gc.class_id == class_a
    assert gc.credits_awarded == 7000  # 2000 class + 5000 bonus

    # RRN-B is class champion but not grand champion
    not_gc = next(c for c in champions if not c.is_grand_champion)
    assert not_gc.rrn == "RRN-B"
    assert not_gc.credits_awarded == 2000

    # grand champion bonus call: scenarios_completed=500
    gc_call = call("owner-RRN-A", "RRN-A", scenarios_completed=500,
                   beat_champion=False, rare_tier=False, tier="2026-04_grand_champion")
    mock_award.assert_any_call(*gc_call.args, **gc_call.kwargs)


# ---------------------------------------------------------------------------
# Test 7 — submit_score rejects inactive season
# ---------------------------------------------------------------------------


def test_submit_score_rejects_inactive_season():
    """submit_score raises ValueError when the season status is not ACTIVE."""
    db = _make_submit_db(season_status="UPCOMING")
    with patch("castor.competitions.bracket_season._get_firestore_client", return_value=db):
        mgr = BracketSeasonManager()
        with pytest.raises(ValueError, match="not ACTIVE"):
            mgr.submit_score("2026-04", "pi5-hailo8l__gemini-2.5-flash", "RRN-001", 0.8)
