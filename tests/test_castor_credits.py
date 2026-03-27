"""Tests for castor/contribute/credits.py — Castor Credits system."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(*, credits=0, credits_redeemable=0, badge="none", robots=None, exists=True):
    """Build a minimal Firestore mock with a contributors/{uid} doc."""
    doc_data = {
        "credits": credits,
        "credits_redeemable": credits_redeemable,
        "badge": badge,
        "robots": robots or [],
    }
    doc_mock = MagicMock()
    doc_mock.exists = exists
    doc_mock.to_dict.return_value = doc_data if exists else {}

    log_doc = MagicMock()
    log_doc.to_dict.return_value = {"amount": 10, "reason": "test", "ts": 1, "rrn": "RRN-X"}

    log_query = MagicMock()
    log_query.order_by.return_value = log_query
    log_query.limit.return_value = log_query
    log_query.stream.return_value = [log_doc]

    ref = MagicMock()
    ref.get.return_value = doc_mock
    ref.collection.return_value = log_query

    db = MagicMock()
    db.collection.return_value.document.return_value = ref
    return db, ref


# ---------------------------------------------------------------------------
# _compute_badge
# ---------------------------------------------------------------------------


def test_compute_badge_none():
    from castor.contribute.credits import _compute_badge

    assert _compute_badge(0) == "none"
    assert _compute_badge(99) == "none"


def test_compute_badge_bronze():
    from castor.contribute.credits import _compute_badge

    assert _compute_badge(100) == "bronze"
    assert _compute_badge(499) == "bronze"


def test_compute_badge_silver():
    from castor.contribute.credits import _compute_badge

    assert _compute_badge(500) == "silver"


def test_compute_badge_gold():
    from castor.contribute.credits import _compute_badge

    assert _compute_badge(2000) == "gold"


def test_compute_badge_diamond():
    from castor.contribute.credits import _compute_badge

    assert _compute_badge(10000) == "diamond"
    assert _compute_badge(99999) == "diamond"


# ---------------------------------------------------------------------------
# award_credits — base calculation
# ---------------------------------------------------------------------------


def test_award_credits_base():
    from castor.contribute.credits import award_credits

    db, ref = _make_db(credits=0, credits_redeemable=0)
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        awarded = award_credits(
            owner_uid="user-1",
            rrn="RRN-000000000001",
            scenarios_completed=10,
            beat_champion=False,
            rare_tier=False,
            tier="pi5-hailo8l",
        )
    # base = 10 * 10 = 100, no multipliers
    assert awarded == 100
    ref.set.assert_called_once()
    call_kwargs = ref.set.call_args[0][0]
    assert call_kwargs["credits"] == 100
    assert call_kwargs["badge"] == "bronze"


def test_award_credits_beat_champion_multiplier():
    from castor.contribute.credits import award_credits

    db, ref = _make_db()
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        awarded = award_credits(
            owner_uid="user-1",
            rrn="RRN-1",
            scenarios_completed=10,
            beat_champion=True,
            rare_tier=False,
            tier="server",
        )
    # 100 * 2.0 = 200
    assert awarded == 200


def test_award_credits_rare_tier_multiplier():
    from castor.contribute.credits import award_credits

    db, ref = _make_db()
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        awarded = award_credits(
            owner_uid="user-1",
            rrn="RRN-1",
            scenarios_completed=10,
            beat_champion=False,
            rare_tier=True,
            tier="server",
        )
    # 100 * 5.0 = 500
    assert awarded == 500


def test_award_credits_both_multipliers():
    from castor.contribute.credits import award_credits

    db, ref = _make_db()
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        awarded = award_credits(
            owner_uid="user-1",
            rrn="RRN-1",
            scenarios_completed=10,
            beat_champion=True,
            rare_tier=True,
            tier="server",
        )
    # 100 * 2.0 * 5.0 = 1000
    assert awarded == 1000


def test_award_credits_accumulates():
    from castor.contribute.credits import award_credits

    db, ref = _make_db(credits=400, credits_redeemable=400)
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        awarded = award_credits(
            owner_uid="user-1",
            rrn="RRN-1",
            scenarios_completed=10,
            beat_champion=False,
            rare_tier=False,
            tier="pi5-8gb",
        )
    assert awarded == 100
    call_data = ref.set.call_args[0][0]
    assert call_data["credits"] == 500  # 400 + 100
    assert call_data["badge"] == "silver"


def test_award_credits_adds_rrn_to_robots():
    from castor.contribute.credits import award_credits

    db, ref = _make_db(robots=["RRN-OTHER"])
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        award_credits("u1", "RRN-NEW", 10, False, False, "pi5-8gb")
    robots = ref.set.call_args[0][0]["robots"]
    assert "RRN-NEW" in robots


def test_award_credits_no_duplicate_rrn():
    from castor.contribute.credits import award_credits

    db, ref = _make_db(robots=["RRN-1"])
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        award_credits("u1", "RRN-1", 10, False, False, "pi5-8gb")
    robots = ref.set.call_args[0][0]["robots"]
    assert robots.count("RRN-1") == 1


def test_award_credits_firestore_error_returns_zero():
    from castor.contribute.credits import award_credits

    with patch(
        "castor.contribute.credits._get_firestore_client",
        side_effect=Exception("no firestore"),
    ):
        awarded = award_credits("u1", "RRN-1", 10, False, False, "pi5-8gb")
    assert awarded == 0


# ---------------------------------------------------------------------------
# redeem_credits
# ---------------------------------------------------------------------------


def test_redeem_credits_success():
    from castor.contribute.credits import redeem_credits

    db, ref = _make_db(credits=1000, credits_redeemable=1000)
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        result = redeem_credits("user-1", "pro_month")
    assert result["success"] is True
    assert result["credits_spent"] == 500
    assert result["credits_remaining"] == 500
    assert result["error"] is None


def test_redeem_credits_insufficient():
    from castor.contribute.credits import redeem_credits

    db, ref = _make_db(credits=100, credits_redeemable=100)
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        result = redeem_credits("user-1", "pro_month")
    assert result["success"] is False
    assert result["credits_spent"] == 0
    assert "insufficient" in result["error"]


def test_redeem_credits_unknown_type():
    from castor.contribute.credits import redeem_credits

    result = redeem_credits("user-1", "free_beer")
    assert result["success"] is False
    assert "unknown" in result["error"]


def test_redeem_credits_harness_run():
    from castor.contribute.credits import redeem_credits

    db, ref = _make_db(credits_redeemable=300)
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        result = redeem_credits("user-1", "harness_run")
    assert result["success"] is True
    assert result["credits_spent"] == 200


def test_redeem_credits_api_boost():
    from castor.contribute.credits import redeem_credits

    db, ref = _make_db(credits_redeemable=200)
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        result = redeem_credits("user-1", "api_boost")
    assert result["success"] is True
    assert result["credits_spent"] == 150


def test_redeem_credits_champion_badge():
    from castor.contribute.credits import redeem_credits

    db, ref = _make_db(credits_redeemable=300)
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        result = redeem_credits("user-1", "champion_badge")
    assert result["success"] is True
    assert result["credits_spent"] == 250


def test_redeem_credits_firestore_error():
    from castor.contribute.credits import redeem_credits

    with patch(
        "castor.contribute.credits._get_firestore_client",
        side_effect=Exception("boom"),
    ):
        result = redeem_credits("user-1", "api_boost")
    assert result["success"] is False
    assert result["credits_spent"] == 0


# ---------------------------------------------------------------------------
# get_credits
# ---------------------------------------------------------------------------


def test_get_credits_returns_summary():
    from castor.contribute.credits import get_credits

    db, ref = _make_db(credits=2500, credits_redeemable=2000, badge="gold")
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        result = get_credits("user-1")
    assert result["credits"] == 2500
    assert result["credits_redeemable"] == 2000
    assert result["badge"] == "gold"
    assert isinstance(result["credit_log"], list)


def test_get_credits_firestore_error_returns_zeros():
    from castor.contribute.credits import get_credits

    with patch(
        "castor.contribute.credits._get_firestore_client",
        side_effect=Exception("offline"),
    ):
        result = get_credits("user-1")
    assert result["credits"] == 0
    assert result["badge"] == "none"
    assert result["credit_log"] == []


def test_get_credits_nonexistent_user():
    from castor.contribute.credits import get_credits

    db, ref = _make_db(exists=False)
    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        result = get_credits("new-user")
    assert result["credits"] == 0


# ---------------------------------------------------------------------------
# get_credits_leaderboard
# ---------------------------------------------------------------------------


def test_get_credits_leaderboard_returns_list():
    from castor.contribute.credits import get_credits_leaderboard

    doc1 = MagicMock()
    doc1.id = "user-1"
    doc1.to_dict.return_value = {"credits": 5000, "credits_redeemable": 4000, "badge": "gold"}

    doc2 = MagicMock()
    doc2.id = "user-2"
    doc2.to_dict.return_value = {"credits": 1000, "credits_redeemable": 800, "badge": "silver"}

    query = MagicMock()
    query.order_by.return_value = query
    query.limit.return_value = query
    query.stream.return_value = [doc1, doc2]

    db = MagicMock()
    db.collection.return_value = query

    with patch("castor.contribute.credits._get_firestore_client", return_value=db):
        result = get_credits_leaderboard()

    assert len(result) == 2
    assert result[0]["owner_uid"] == "user-1"
    assert result[0]["credits"] == 5000
    assert result[1]["badge"] == "silver"


def test_get_credits_leaderboard_firestore_error_returns_empty():
    from castor.contribute.credits import get_credits_leaderboard

    with patch(
        "castor.contribute.credits._get_firestore_client",
        side_effect=Exception("offline"),
    ):
        result = get_credits_leaderboard()
    assert result == []


# ---------------------------------------------------------------------------
# BADGE_THRESHOLDS and MULTIPLIERS constants
# ---------------------------------------------------------------------------


def test_constants_exist():
    from castor.contribute.credits import BADGE_THRESHOLDS, MULTIPLIERS, REDEMPTION_COSTS

    assert BADGE_THRESHOLDS["bronze"] == 100
    assert BADGE_THRESHOLDS["silver"] == 500
    assert BADGE_THRESHOLDS["gold"] == 2000
    assert BADGE_THRESHOLDS["diamond"] == 10000

    assert MULTIPLIERS["champion_beat"] == 2.0
    assert MULTIPLIERS["rare_tier"] == 5.0

    assert REDEMPTION_COSTS["pro_month"] == 500
    assert REDEMPTION_COSTS["harness_run"] == 200
    assert REDEMPTION_COSTS["api_boost"] == 150
    assert REDEMPTION_COSTS["champion_badge"] == 250
