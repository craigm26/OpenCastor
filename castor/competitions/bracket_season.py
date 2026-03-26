"""BracketSeasonManager — Model×Hardware bracket season format (#737).

Monthly seasons where each hardware_tier × model_id combination is a 'class'.
Champions are crowned per class; the highest-scoring class winner earns the
grand champion title and a bonus credit payout.

Firestore layout::

    seasons/{season_id}/                        — BracketSeason document
    seasons/{season_id}/classes/{class_id}/     — BracketClass document
    seasons/{season_id}/classes/{class_id}/entries/{rrn}  — BracketEntry document
    seasons/{season_id}/champions/{class_id}/   — SeasonChampion document
"""

from __future__ import annotations

import calendar
import logging
import time
from datetime import datetime, timezone

try:
    from google.cloud.firestore_v1.base_query import FieldFilter  # type: ignore[import-untyped]
except ImportError:
    FieldFilter = None  # type: ignore[assignment,misc]

from castor.competitions.models import (
    INITIAL_CLASSES,
    BracketClass,
    BracketEntry,
    BracketSeason,
    SeasonChampion,
)

log = logging.getLogger("OpenCastor.Competitions")

# Credits mapped via scenarios_completed × _CREDITS_PER_SCENARIO (= 10 each).
_RANK1_SCENARIOS = 200  # → 2 000 credits
_RANK2_SCENARIOS = 100  # → 1 000 credits
_GRAND_CHAMPION_SCENARIOS = 500  # → 5 000 bonus credits


def _get_firestore_client():
    """Return a cached Firestore client; None if unavailable."""
    try:
        from castor.contribute.harness_eval import _get_firestore_client as _hef

        return _hef()
    except Exception:
        return None


class BracketSeasonManager:
    """Manage Model×Hardware bracket seasons backed by Firestore."""

    _COL_SEASONS = "seasons"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_season(self, year: int, month: int) -> BracketSeason:
        """Create a new bracket season for *year*/*month*.

        Writes the season document and one class document per INITIAL_CLASSES
        entry to Firestore.  Offline Firestore writes are silently skipped.

        Args:
            year:  4-digit year (e.g. 2026).
            month: 1-based month (e.g. 4 for April).

        Returns:
            The newly created BracketSeason (populated in-memory even offline).
        """
        season_id = f"{year:04d}-{month:02d}"
        starts_at = datetime(year, month, 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(year, month)[1]
        ends_at = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)

        classes = [
            BracketClass(
                class_id=f"{c['hardware_tier']}__{c['model_id']}",
                hardware_tier=c["hardware_tier"],
                model_id=c["model_id"],
                season_id=season_id,
                scenario_pack_id="default",
            )
            for c in INITIAL_CLASSES
        ]

        season = BracketSeason(
            season_id=season_id,
            starts_at=starts_at,
            ends_at=ends_at,
            classes=classes,
            status="UPCOMING",
        )

        try:
            db = _get_firestore_client()
            if db is not None:
                season_ref = db.collection(self._COL_SEASONS).document(season_id)
                season_ref.set(
                    {
                        "season_id": season_id,
                        "starts_at": starts_at.isoformat(),
                        "ends_at": ends_at.isoformat(),
                        "status": "UPCOMING",
                    }
                )
                for cls in classes:
                    season_ref.collection("classes").document(cls.class_id).set(cls.to_dict())
        except Exception as exc:
            log.debug("create_season Firestore write skipped (offline): %s", exc)

        return season

    def submit_score(self, season_id: str, class_id: str, rrn: str, score: float) -> BracketEntry:
        """Submit a score for a robot in a bracket class.

        Validates that:
          - The season exists and is ACTIVE.
          - The robot's ``hardware_tier`` (from the ``robots`` collection) matches
            the class's ``hardware_tier`` when available.

        Only updates Firestore when the new score exceeds the existing best.

        Args:
            season_id: e.g. '2026-04'.
            class_id:  e.g. 'pi5-hailo8l__gemini-2.5-flash'.
            rrn:       Robot Registration Number.
            score:     Composite evaluation score (higher is better).

        Returns:
            BracketEntry reflecting the robot's current best score.

        Raises:
            ValueError: Season not found, not ACTIVE, class not found, or hardware
                tier mismatch.
        """
        db = _get_firestore_client()
        if db is None:
            raise ValueError("Firestore unavailable — cannot validate submission")

        # Validate season is ACTIVE
        season_doc = db.collection(self._COL_SEASONS).document(season_id).get()
        if not season_doc.exists:
            raise ValueError(f"Season {season_id!r} not found")
        season_data = season_doc.to_dict() or {}
        if season_data.get("status") != "ACTIVE":
            raise ValueError(
                f"Season {season_id!r} is not ACTIVE (status={season_data.get('status')!r})"
            )

        # Validate hardware_tier matches the class
        class_doc = (
            db.collection(self._COL_SEASONS)
            .document(season_id)
            .collection("classes")
            .document(class_id)
            .get()
        )
        if not class_doc.exists:
            raise ValueError(f"Class {class_id!r} not found in season {season_id!r}")
        class_data = class_doc.to_dict() or {}
        expected_tier = class_data.get("hardware_tier", "")

        robot_doc = db.collection("robots").document(rrn).get()
        if robot_doc.exists:
            robot_tier = (robot_doc.to_dict() or {}).get("hardware_tier", "")
            if robot_tier and robot_tier != expected_tier:
                raise ValueError(
                    f"Hardware tier mismatch: robot {rrn!r} is {robot_tier!r}, "
                    f"class {class_id!r} requires {expected_tier!r}"
                )

        # Read existing entry; update only when score improves
        entry_ref = (
            db.collection(self._COL_SEASONS)
            .document(season_id)
            .collection("classes")
            .document(class_id)
            .collection("entries")
            .document(rrn)
        )
        existing_doc = entry_ref.get()
        now = int(time.time())

        if existing_doc.exists:
            existing_data = existing_doc.to_dict() or {}
            existing_best = float(existing_data.get("best_score", 0.0))
            if score <= existing_best:
                return BracketEntry.from_dict(existing_data, doc_id=rrn)

        try:
            entry_ref.set(
                {
                    "season_id": season_id,
                    "class_id": class_id,
                    "rrn": rrn,
                    "best_score": score,
                    "submitted_at": now,
                }
            )
        except Exception as exc:
            log.debug("submit_score Firestore write skipped (offline): %s", exc)

        return BracketEntry(
            season_id=season_id,
            class_id=class_id,
            rrn=rrn,
            best_score=score,
            submitted_at=now,
        )

    def get_class_leaderboard(self, season_id: str, class_id: str) -> list[BracketEntry]:
        """Return ranked leaderboard for a bracket class (best score → rank 1).

        Args:
            season_id: e.g. '2026-04'.
            class_id:  e.g. 'pi5-hailo8l__gemini-2.5-flash'.

        Returns:
            Sorted list of BracketEntry with ``rank`` populated; empty on failure.
        """
        try:
            db = _get_firestore_client()
            if db is None:
                return []
            docs = list(
                db.collection(self._COL_SEASONS)
                .document(season_id)
                .collection("classes")
                .document(class_id)
                .collection("entries")
                .stream()
            )
            entries = [BracketEntry.from_dict(doc.to_dict() or {}, doc_id=doc.id) for doc in docs]
            entries.sort(key=lambda e: e.best_score, reverse=True)
            for i, entry in enumerate(entries, 1):
                entry.rank = i
            return entries
        except Exception as exc:
            log.debug("get_class_leaderboard failed (offline): %s", exc)
            return []

    def finalize_season(self, season_id: str) -> list[SeasonChampion]:
        """Crown champions for every class and award credits.

        Payout rules:
          - Rank 1 per class  → 2 000 credits (200 × 10).
          - Rank 2 per class  → 1 000 credits (100 × 10).
          - Grand champion (highest score across all class winners)
            → 5 000 bonus credits (500 × 10) + ``grand_champion`` badge.

        Writes SeasonChampion records to
        ``seasons/{season_id}/champions/{class_id}`` and marks the season
        COMPLETED.

        Args:
            season_id: e.g. '2026-04'.

        Returns:
            List of SeasonChampion records (empty on Firestore failure).
        """
        from castor.contribute.credits import award_credits

        champions: list[SeasonChampion] = []

        try:
            db = _get_firestore_client()
            if db is None:
                return []

            season_ref = db.collection(self._COL_SEASONS).document(season_id)
            season_doc = season_ref.get()
            if not season_doc.exists:
                return []

            class_docs = list(season_ref.collection("classes").stream())

            # (class_id, rrn, score) for grand champion selection
            class_winners: list[tuple[str, str, float]] = []

            for class_doc in class_docs:
                class_id = class_doc.id
                lb = self.get_class_leaderboard(season_id, class_id)
                if not lb:
                    continue

                for entry in lb:
                    owner_uid = self._get_owner_uid(db, entry.rrn)
                    if entry.rank == 1:
                        credits = award_credits(
                            owner_uid,
                            entry.rrn,
                            scenarios_completed=_RANK1_SCENARIOS,
                            beat_champion=False,
                            rare_tier=False,
                            tier=season_id,
                        )
                        champions.append(
                            SeasonChampion(
                                season_id=season_id,
                                class_id=class_id,
                                rrn=entry.rrn,
                                score=entry.best_score,
                                credits_awarded=credits,
                                is_grand_champion=False,
                            )
                        )
                        class_winners.append((class_id, entry.rrn, entry.best_score))
                    elif entry.rank == 2:
                        award_credits(
                            owner_uid,
                            entry.rrn,
                            scenarios_completed=_RANK2_SCENARIOS,
                            beat_champion=False,
                            rare_tier=False,
                            tier=season_id,
                        )

            # Identify grand champion — highest score across all class rank-1 winners
            if class_winners:
                gc_class_id, gc_rrn, _gc_score = max(class_winners, key=lambda x: x[2])
                gc_owner_uid = self._get_owner_uid(db, gc_rrn)
                gc_credits = award_credits(
                    gc_owner_uid,
                    gc_rrn,
                    scenarios_completed=_GRAND_CHAMPION_SCENARIOS,
                    beat_champion=False,
                    rare_tier=False,
                    tier=f"{season_id}_grand_champion",
                )
                # Write grand_champion badge to contributors doc
                try:
                    db.collection("contributors").document(gc_owner_uid).set(
                        {"grand_champion": True, "grand_champion_season": season_id},
                        merge=True,
                    )
                except Exception as exc:
                    log.debug("grand_champion badge write failed: %s", exc)

                # Update the corresponding SeasonChampion record
                for champ in champions:
                    if champ.class_id == gc_class_id and champ.rrn == gc_rrn:
                        champ.is_grand_champion = True
                        champ.credits_awarded += gc_credits

            # Persist champion records and mark season COMPLETED
            for champ in champions:
                try:
                    season_ref.collection("champions").document(champ.class_id).set(champ.to_dict())
                except Exception as exc:
                    log.debug("champion write failed for %s: %s", champ.class_id, exc)

            try:
                season_ref.set({"status": "COMPLETED"}, merge=True)
            except Exception as exc:
                log.debug("season COMPLETED write failed: %s", exc)

        except Exception as exc:
            log.debug("finalize_season failed (offline): %s", exc)

        return champions

    def get_current_season(self) -> BracketSeason | None:
        """Return the current ACTIVE season, or the most recent UPCOMING one.

        Returns:
            BracketSeason, or None if no seasons exist or Firestore is offline.
        """
        try:
            db = _get_firestore_client()
            if db is None:
                return None
            seasons_ref = db.collection(self._COL_SEASONS)

            # Prefer ACTIVE
            if FieldFilter is not None:
                active_q = seasons_ref.where(filter=FieldFilter("status", "==", "ACTIVE")).limit(1)
            else:
                active_q = seasons_ref.where("status", "==", "ACTIVE").limit(1)  # type: ignore[arg-type]

            for doc in active_q.stream():
                return self._doc_to_season(doc, db)

            # Fall back to most recent UPCOMING
            if FieldFilter is not None:
                upcoming_q = (
                    seasons_ref.where(filter=FieldFilter("status", "==", "UPCOMING"))
                    .order_by("season_id", direction="DESCENDING")
                    .limit(1)
                )
            else:
                upcoming_q = (  # type: ignore[assignment]
                    seasons_ref.where("status", "==", "UPCOMING")
                    .order_by("season_id", direction="DESCENDING")
                    .limit(1)
                )

            for doc in upcoming_q.stream():
                return self._doc_to_season(doc, db)

        except Exception as exc:
            log.debug("get_current_season failed (offline): %s", exc)

        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_owner_uid(self, db, rrn: str) -> str:
        """Look up owner_uid from the robots collection; fall back to rrn."""
        try:
            doc = db.collection("robots").document(rrn).get()
            if doc.exists:
                return (doc.to_dict() or {}).get("owner_uid", rrn)
        except Exception:
            pass
        return rrn

    def _doc_to_season(self, doc, db) -> BracketSeason:
        """Convert a Firestore season document into a BracketSeason."""
        data = doc.to_dict() or {}
        season_id = doc.id

        try:
            class_docs = list(
                db.collection(self._COL_SEASONS).document(season_id).collection("classes").stream()
            )
            classes = [
                BracketClass.from_dict(
                    cd.to_dict()
                    or {
                        "class_id": cd.id,
                        "hardware_tier": "",
                        "model_id": "",
                        "season_id": season_id,
                    }
                )
                for cd in class_docs
            ]
        except Exception:
            classes = []

        try:
            starts_at = datetime.fromisoformat(str(data.get("starts_at", "")))
        except Exception:
            starts_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        try:
            ends_at = datetime.fromisoformat(str(data.get("ends_at", "")))
        except Exception:
            ends_at = datetime(2000, 1, 31, tzinfo=timezone.utc)

        return BracketSeason(
            season_id=season_id,
            starts_at=starts_at,
            ends_at=ends_at,
            classes=classes,
            status=data.get("status", "UPCOMING"),
        )
