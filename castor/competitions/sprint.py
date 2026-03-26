"""Sprint competition manager for OpenCastor (#735).

Handles create, submit, leaderboard, finalize, and list for SPRINT format competitions.
Firestore collection: competitions/{id}/ with subcollection entries/{rrn}.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from castor.competitions.models import (
    CompetitionFormat,
    CompetitionStatus,
    SprintCompetition,
    SprintEntry,
)
from castor.contribute.credits import award_credits

log = logging.getLogger("OpenCastor.Competitions")

# Submissions close this many seconds before ends_at.
_LOCK_BEFORE_END_SECONDS = 3600  # 1 hour

# Credits per "scenario" unit as defined in castor.contribute.credits.
_CREDITS_PER_SCENARIO = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_firestore_client():
    """Return a cached Firestore client, delegating to the harness_eval singleton."""
    try:
        from castor.contribute.harness_eval import _get_firestore_client as _hef

        return _hef()
    except Exception:
        return None


def _compute_status(starts_at: datetime, ends_at: datetime) -> CompetitionStatus:
    """Derive live competition status from timestamps."""
    now = _now()
    lock_at = ends_at - timedelta(seconds=_LOCK_BEFORE_END_SECONDS)
    if now < starts_at:
        return CompetitionStatus.UPCOMING
    if now >= ends_at:
        return CompetitionStatus.COMPLETED
    if now >= lock_at:
        return CompetitionStatus.LOCKED
    return CompetitionStatus.ACTIVE


class SprintManager:
    """Manage sprint competitions backed by Firestore with offline fallback."""

    _COLLECTION = "competitions"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_sprint(
        self,
        name: str,
        hardware_tiers: list[str],
        model_id: Optional[str],
        starts_at: datetime,
        ends_at: datetime,
        prize_pool: int,
    ) -> SprintCompetition:
        """Create a new sprint competition and persist to Firestore.

        Args:
            name: Human-readable display name.
            hardware_tiers: Eligible hardware tier strings (e.g. ["pi5-hailo8l"]).
            model_id: Optional model constraint (None = any model).
            starts_at: UTC datetime when the competition opens.
            ends_at: UTC datetime when the competition closes; locked 1 h before.
            prize_pool: Total credits to distribute to top-3 finishers.

        Returns:
            The persisted SprintCompetition.
        """
        now = _now()
        comp = SprintCompetition(
            id=str(uuid.uuid4()),
            name=name,
            format=CompetitionFormat.SPRINT,
            hardware_tiers=hardware_tiers,
            model_id=model_id,
            starts_at=starts_at,
            ends_at=ends_at,
            prize_pool_credits=prize_pool,
            status=_compute_status(starts_at, ends_at),
            created_at=now,
        )
        try:
            db = _get_firestore_client()
            if db is not None:
                db.collection(self._COLLECTION).document(comp.id).set(comp.to_dict())
        except Exception as exc:
            log.debug("Firestore write skipped (offline): %s", exc)
        return comp

    def submit_score(
        self,
        competition_id: str,
        rrn: str,
        score: float,
        candidate_id: str,
    ) -> SprintEntry:
        """Submit a score for a sprint competition.

        Only accepted when the competition is ACTIVE and now < ends_at - 1 h.
        Updates Firestore only if the submitted score exceeds the existing best.

        Args:
            competition_id: Firestore document ID of the competition.
            rrn: Robot Registration Number of the submitter.
            score: Evaluation score (higher is better).
            candidate_id: Identifier of the evaluated candidate harness.

        Returns:
            The current best SprintEntry for this robot.

        Raises:
            ValueError: If the competition does not exist or is not accepting submissions.
        """
        comp = self._load_competition(competition_id)
        if comp is None:
            raise ValueError(f"Competition {competition_id!r} not found")

        now = _now()
        lock_at = comp.ends_at - timedelta(seconds=_LOCK_BEFORE_END_SECONDS)
        live_status = _compute_status(comp.starts_at, comp.ends_at)

        if live_status != CompetitionStatus.ACTIVE or now >= lock_at:
            raise ValueError(
                f"Competition {competition_id!r} is not accepting submissions "
                f"(status={live_status.value})"
            )

        entry = SprintEntry(
            competition_id=competition_id,
            rrn=rrn,
            best_score=score,
            submitted_at=now,
        )

        try:
            db = _get_firestore_client()
            if db is not None:
                ref = (
                    db.collection(self._COLLECTION)
                    .document(competition_id)
                    .collection("entries")
                    .document(rrn)
                )
                doc = ref.get()
                if doc.exists:
                    existing_score = float((doc.to_dict() or {}).get("best_score", 0.0))
                    if score <= existing_score:
                        existing_data = dict(doc.to_dict() or {})
                        existing_data.setdefault("rrn", rrn)
                        return SprintEntry.from_dict(existing_data, competition_id)

                ref.set(
                    {
                        "rrn": rrn,
                        "competition_id": competition_id,
                        "best_score": score,
                        "submitted_at": now.isoformat(),
                        "candidate_id": candidate_id,
                    }
                )
        except Exception as exc:
            log.debug("Firestore submit skipped (offline): %s", exc)

        return entry

    def get_leaderboard(self, competition_id: str) -> list[SprintEntry]:
        """Return the leaderboard for a competition sorted by best_score descending.

        Rank 1 = highest score. Rank is assigned after retrieval.

        Args:
            competition_id: Firestore document ID of the competition.

        Returns:
            Ranked list of SprintEntry objects (empty on Firestore failure).
        """
        entries: list[SprintEntry] = []
        try:
            db = _get_firestore_client()
            if db is None:
                return []
            docs = (
                db.collection(self._COLLECTION)
                .document(competition_id)
                .collection("entries")
                .order_by("best_score", direction="DESCENDING")
                .stream()
            )
            for doc in docs:
                data = dict(doc.to_dict() or {})
                data.setdefault("rrn", doc.id)
                entries.append(SprintEntry.from_dict(data, competition_id))
        except Exception as exc:
            log.debug("Firestore leaderboard fetch failed (offline): %s", exc)
            return []

        for i, entry in enumerate(entries):
            entry.rank = i + 1

        return entries

    def finalize_sprint(self, competition_id: str) -> dict[str, int]:
        """Finalize a sprint and award credits to the top-3 finishers.

        Payout ratios: rank-1 = 50%, rank-2 = 30%, rank-3 = 20% of prize_pool.
        Uses castor.contribute.credits.award_credits() for each payout.
        Marks the competition COMPLETED in Firestore.

        Args:
            competition_id: Firestore document ID of the competition.

        Returns:
            Mapping of rrn → credits_awarded for every robot that received a payout.
        """
        comp = self._load_competition(competition_id)
        if comp is None:
            return {}

        leaderboard = self.get_leaderboard(competition_id)
        if not leaderboard:
            return {}

        pool = comp.prize_pool_credits
        payout_ratios = [0.5, 0.3, 0.2]
        payouts: dict[str, int] = {}

        for i, entry in enumerate(leaderboard[:3]):
            credits_target = int(pool * payout_ratios[i])
            if credits_target <= 0:
                continue
            scenarios = max(1, credits_target // _CREDITS_PER_SCENARIO)
            try:
                awarded = award_credits(
                    owner_uid=entry.rrn,
                    rrn=entry.rrn,
                    scenarios_completed=scenarios,
                    beat_champion=False,
                    rare_tier=False,
                    tier="sprint",
                )
                payouts[entry.rrn] = awarded
            except Exception as exc:
                log.debug("Credits award failed for %s: %s", entry.rrn, exc)
                payouts[entry.rrn] = 0

        try:
            db = _get_firestore_client()
            if db is not None:
                db.collection(self._COLLECTION).document(competition_id).set(
                    {"status": CompetitionStatus.COMPLETED.value}, merge=True
                )
        except Exception as exc:
            log.debug("Firestore finalize write failed (offline): %s", exc)

        return payouts

    def list_competitions(
        self,
        status: Optional[CompetitionStatus] = None,
    ) -> list[SprintCompetition]:
        """List competitions, optionally filtered by live status.

        Args:
            status: If provided, return only competitions with this live status.

        Returns:
            List of SprintCompetition objects (empty on Firestore failure).
        """
        results: list[SprintCompetition] = []
        try:
            db = _get_firestore_client()
            if db is None:
                return []
            docs = db.collection(self._COLLECTION).stream()
            for doc in docs:
                data = doc.to_dict() or {}
                try:
                    comp = SprintCompetition.from_dict(data)
                    # Recompute live status — stored value may be stale.
                    comp.status = _compute_status(comp.starts_at, comp.ends_at)
                    if status is None or comp.status == status:
                        results.append(comp)
                except Exception:
                    continue
        except Exception as exc:
            log.debug("Firestore list_competitions failed (offline): %s", exc)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_competition(self, competition_id: str) -> Optional[SprintCompetition]:
        """Fetch a single competition document from Firestore."""
        try:
            db = _get_firestore_client()
            if db is None:
                return None
            doc = db.collection(self._COLLECTION).document(competition_id).get()
            if not doc.exists:
                return None
            return SprintCompetition.from_dict(doc.to_dict() or {})
        except Exception as exc:
            log.debug("Firestore load_competition failed (offline): %s", exc)
            return None
