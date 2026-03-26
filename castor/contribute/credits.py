"""Castor Credits — contributor reward tracking layer.

Credits are awarded when robots contribute harness eval work units.
They can be redeemed for pro features (API boosts, harness runs, badges).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

log = logging.getLogger("OpenCastor.Credits")

BADGE_THRESHOLDS = {
    "bronze": 100,
    "silver": 500,
    "gold": 2000,
    "diamond": 10000,
}

MULTIPLIERS = {
    "champion_beat": 2.0,
    "rare_tier": 5.0,
}

REDEMPTION_COSTS = {
    "pro_month": 500,
    "harness_run": 200,
    "api_boost": 150,
    "champion_badge": 250,
}

_BASE_CREDITS_PER_SCENARIO = 10


def _get_firestore_client():
    """Create Firestore client using service account or ADC."""
    from google.cloud import firestore as _firestore  # type: ignore[import-untyped]

    creds_path = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        str(Path.home() / ".config" / "opencastor" / "firebase-sa-key.json"),
    )
    try:
        from google.oauth2 import service_account  # type: ignore[import-untyped]

        creds = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=[
                "https://www.googleapis.com/auth/datastore",
                "https://www.googleapis.com/auth/cloud-platform",
            ],
        )
        return _firestore.Client(project="opencastor", credentials=creds)
    except Exception:
        import google.auth  # type: ignore[import-untyped]

        creds, project = google.auth.default()
        return _firestore.Client(project=project or "opencastor", credentials=creds)


def _compute_badge(total_credits: int) -> str:
    """Return the highest badge tier the contributor has earned."""
    badge = "none"
    for tier, threshold in sorted(BADGE_THRESHOLDS.items(), key=lambda x: x[1]):
        if total_credits >= threshold:
            badge = tier
    return badge


def award_credits(
    owner_uid: str,
    rrn: str,
    scenarios_completed: int,
    beat_champion: bool,
    rare_tier: bool,
    tier: str,
) -> int:
    """Compute and award credits for a completed harness eval work unit.

    Args:
        owner_uid: Firestore contributor ID (falls back to rrn if no owner doc).
        rrn: Robot Registration Number that submitted the work.
        scenarios_completed: Number of scenarios evaluated (typically 10).
        beat_champion: True if submitted score exceeded current champion score.
        rare_tier: True if fewer than 3 contributors exist for this hardware tier.
        tier: Hardware tier string (e.g. "pi5-hailo8l", "server").

    Returns:
        Number of credits awarded (0 on Firestore failure, still logs).
    """
    base = scenarios_completed * _BASE_CREDITS_PER_SCENARIO
    multiplier = 1.0
    reasons = [f"{scenarios_completed} scenarios completed in tier={tier}"]

    if beat_champion:
        multiplier *= MULTIPLIERS["champion_beat"]
        reasons.append("beat champion")
    if rare_tier:
        multiplier *= MULTIPLIERS["rare_tier"]
        reasons.append("rare tier (<3 contributors)")

    credits_awarded = int(base * multiplier)
    reason = "; ".join(reasons)

    try:
        db = _get_firestore_client()
        ref = db.collection("contributors").document(owner_uid)

        doc = ref.get()
        existing = doc.to_dict() or {} if doc.exists else {}

        current_total = int(existing.get("credits", 0))
        current_redeemable = int(existing.get("credits_redeemable", 0))
        robots: list = existing.get("robots", [])

        new_total = current_total + credits_awarded
        new_redeemable = current_redeemable + credits_awarded
        badge = _compute_badge(new_total)

        if rrn not in robots:
            robots.append(rrn)

        ref.set(
            {
                "credits": new_total,
                "credits_redeemable": new_redeemable,
                "badge": badge,
                "robots": robots,
            },
            merge=True,
        )

        ref.collection("credit_log").add(
            {
                "amount": credits_awarded,
                "reason": reason,
                "ts": int(time.time()),
                "rrn": rrn,
            }
        )

        log.info(
            "Credits awarded: owner=%s rrn=%s amount=%d badge=%s",
            owner_uid,
            rrn,
            credits_awarded,
            badge,
        )
    except Exception as exc:
        log.debug("Credits award skipped (Firestore unavailable): %s", exc)
        return 0

    return credits_awarded


def redeem_credits(owner_uid: str, redemption_type: str) -> dict:
    """Redeem credits for a feature or badge.

    Args:
        owner_uid: Firestore contributor ID.
        redemption_type: One of "pro_month", "harness_run", "api_boost", "champion_badge".

    Returns:
        {success: bool, credits_spent: int, credits_remaining: int, error: str|None}
    """
    cost = REDEMPTION_COSTS.get(redemption_type)
    if cost is None:
        return {
            "success": False,
            "credits_spent": 0,
            "credits_remaining": 0,
            "error": f"unknown redemption type: {redemption_type!r}",
        }

    try:
        db = _get_firestore_client()
        ref = db.collection("contributors").document(owner_uid)

        doc = ref.get()
        existing = doc.to_dict() or {} if doc.exists else {}
        redeemable = int(existing.get("credits_redeemable", 0))

        if redeemable < cost:
            return {
                "success": False,
                "credits_spent": 0,
                "credits_remaining": redeemable,
                "error": f"insufficient credits: need {cost}, have {redeemable}",
            }

        new_redeemable = redeemable - cost
        ref.set({"credits_redeemable": new_redeemable}, merge=True)

        ref.collection("credit_log").add(
            {
                "amount": -cost,
                "reason": f"redeemed: {redemption_type}",
                "ts": int(time.time()),
                "rrn": "",
            }
        )

        return {
            "success": True,
            "credits_spent": cost,
            "credits_remaining": new_redeemable,
            "error": None,
        }
    except Exception as exc:
        log.debug("Credits redeem failed (Firestore unavailable): %s", exc)
        return {
            "success": False,
            "credits_spent": 0,
            "credits_remaining": 0,
            "error": str(exc),
        }


def get_credits(owner_uid: str) -> dict:
    """Return credits summary for a contributor.

    Returns:
        {credits, credits_redeemable, badge, credit_log (last 10 entries)}
    """
    try:
        db = _get_firestore_client()
        ref = db.collection("contributors").document(owner_uid)

        doc = ref.get()
        existing = doc.to_dict() or {} if doc.exists else {}

        log_docs = (
            ref.collection("credit_log").order_by("ts", direction="DESCENDING").limit(10).stream()
        )
        credit_log = [d.to_dict() for d in log_docs]

        return {
            "credits": int(existing.get("credits", 0)),
            "credits_redeemable": int(existing.get("credits_redeemable", 0)),
            "badge": existing.get("badge", "none"),
            "credit_log": credit_log,
        }
    except Exception as exc:
        log.debug("Credits fetch failed (Firestore unavailable): %s", exc)
        return {
            "credits": 0,
            "credits_redeemable": 0,
            "badge": "none",
            "credit_log": [],
        }


def get_credits_leaderboard() -> list:
    """Return top 20 contributors by lifetime credits.

    Returns:
        List of {owner_uid, credits, credits_redeemable, badge} dicts.
    """
    try:
        db = _get_firestore_client()
        docs = (
            db.collection("contributors")
            .order_by("credits", direction="DESCENDING")
            .limit(20)
            .stream()
        )
        results = []
        for doc in docs:
            data = doc.to_dict() or {}
            results.append(
                {
                    "owner_uid": doc.id,
                    "credits": int(data.get("credits", 0)),
                    "credits_redeemable": int(data.get("credits_redeemable", 0)),
                    "badge": data.get("badge", "none"),
                }
            )
        return results
    except Exception as exc:
        log.debug("Credits leaderboard fetch failed (Firestore unavailable): %s", exc)
        return []
