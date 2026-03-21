"""ThresholdRaceManager — jackpot competition that ends on first verified target hit."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from castor.competitions.models import RaceStatus, ThresholdEntry, ThresholdRace, VerificationStatus

log = logging.getLogger("OpenCastor.Competitions")

# Firestore collection paths
_COL_RACES = "competitions"
_COL_ENTRIES_SUB = "entries"

# Verification: avg of 3 re-evals must be >= target * (1 - TOLERANCE)
_VERIFICATION_RUNS = 3
_VERIFICATION_TOLERANCE = 0.02


def _get_firestore_client():
    """Return a Firestore client using service account or ADC; None if unavailable."""
    import os
    from pathlib import Path

    try:
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
    except Exception as exc:
        log.debug("Firestore unavailable: %s", exc)
        return None


def _run_verification_eval(candidate_id: str, hardware_tier: str) -> float:
    """Run a single harness eval re-evaluation and return composite score."""
    from castor.contribute.harness_eval import (
        _ENVIRONMENTS,
        _SCENARIOS_PER_ENV,
        run_single_scenario,
    )

    config: dict = {}
    scenario_results = []
    for env in _ENVIRONMENTS:
        for i in range(_SCENARIOS_PER_ENV):
            scenario_id = f"verify_{env}_{i}"
            result = run_single_scenario(config, scenario_id, env, candidate_id=candidate_id)
            scenario_results.append(result)

    n = len(scenario_results)
    success_rate = sum(1 for r in scenario_results if r["success"]) / n
    p66_rate = sum(1 for r in scenario_results if r["p66_compliant"]) / n
    token_efficiency = max(0.0, 1.0 - config.get("thinking_budget", 1024) / 8000.0)
    max_iter = config.get("max_iterations", 6)
    latency_score = max(0.0, 0.5 - (max_iter / 24.0))

    return success_rate * 0.50 + p66_rate * 0.25 + token_efficiency * 0.15 + latency_score * 0.10


class ThresholdRaceManager:
    """Manages threshold race competitions.

    Firestore layout::

        competitions/{race_id}/          — ThresholdRace document
        competitions/{race_id}/entries/{rrn}  — ThresholdEntry document
    """

    def __init__(self) -> None:
        # In-memory fallback stores (used when Firestore is unavailable)
        self._races: dict[str, ThresholdRace] = {}
        self._entries: dict[str, dict[str, ThresholdEntry]] = {}  # race_id → {rrn → entry}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_race(
        self,
        name: str,
        hardware_tier: str,
        model_id: str | None,
        target_score: float,
        prize_pool: int,
        soft_deadline: datetime,
        scenario_pack_id: str = "default",
    ) -> ThresholdRace:
        """Create a new threshold race and persist to Firestore.

        Returns:
            The newly created ThresholdRace.
        """
        race = ThresholdRace(
            id=str(uuid.uuid4()),
            name=name,
            hardware_tier=hardware_tier,
            model_id=model_id,
            target_score=target_score,
            scenario_pack_id=scenario_pack_id,
            prize_pool_credits=prize_pool,
            soft_deadline=soft_deadline,
        )
        self._races[race.id] = race
        self._entries[race.id] = {}

        try:
            db = _get_firestore_client()
            if db is not None:
                db.collection(_COL_RACES).document(race.id).set(race.to_dict())
                log.info(
                    "ThresholdRace created: id=%s name=%r target=%.4f", race.id, name, target_score
                )
        except Exception as exc:
            log.debug("Firestore write failed for create_race: %s — stored in memory", exc)

        return race

    def submit_claim(
        self,
        race_id: str,
        rrn: str,
        score: float,
        candidate_id: str,
    ) -> ThresholdEntry:
        """Record a score submission for a race.

        If score >= target_score the entry is put into VERIFYING state and
        ``verify_claim`` is triggered synchronously.

        Args:
            race_id: ID of the race to submit to.
            rrn: Robot Registration Number of the submitting robot.
            score: Composite score achieved.
            candidate_id: Harness candidate ID used to reproduce the run.

        Returns:
            The created or updated ThresholdEntry.
        """
        race = self._load_race(race_id)
        if race is None:
            raise ValueError(f"Race not found: {race_id!r}")
        if race.status != RaceStatus.OPEN:
            raise ValueError(f"Race {race_id!r} is not open (status={race.status.value})")

        existing = self._load_entry(race_id, rrn)
        if existing is not None and score <= existing.best_score:
            # New score is not better — return unchanged entry
            return existing

        status = (
            VerificationStatus.VERIFYING
            if score >= race.target_score
            else VerificationStatus.PENDING
        )
        entry = ThresholdEntry(
            race_id=race_id,
            rrn=rrn,
            best_score=score,
            submitted_at=int(time.time()),
            verification_status=status,
        )
        self._save_entry(entry)

        log.info(
            "ThresholdRace submit: race=%s rrn=%s score=%.4f status=%s",
            race_id,
            rrn,
            score,
            status.value,
        )

        if status == VerificationStatus.VERIFYING:
            self.verify_claim(race_id, rrn, candidate_id)

        return entry

    def verify_claim(self, race_id: str, rrn: str, candidate_id: str) -> bool:
        """Run 3 independent re-evaluations to verify a claim.

        If avg of 3 runs >= target_score * (1 - TOLERANCE), the claim is
        VERIFIED and ``_award_winner`` is called.  Otherwise the entry is
        marked FAILED and the race continues.

        Returns:
            True if claim verified, False otherwise.
        """
        race = self._load_race(race_id)
        if race is None:
            log.warning("verify_claim: race not found %s", race_id)
            return False

        scores = []
        for run_idx in range(_VERIFICATION_RUNS):
            try:
                s = _run_verification_eval(
                    candidate_id=f"{candidate_id}_v{run_idx}",
                    hardware_tier=race.hardware_tier,
                )
                scores.append(s)
            except Exception as exc:
                log.debug("Verification run %d failed: %s", run_idx, exc)

        if not scores:
            log.warning(
                "verify_claim: all verification runs failed for race=%s rrn=%s", race_id, rrn
            )
            self._set_entry_status(race_id, rrn, VerificationStatus.FAILED)
            return False

        avg_score = sum(scores) / len(scores)
        threshold = race.target_score * (1.0 - _VERIFICATION_TOLERANCE)
        verified = avg_score >= threshold

        log.info(
            "verify_claim: race=%s rrn=%s avg=%.4f threshold=%.4f verified=%s",
            race_id,
            rrn,
            avg_score,
            threshold,
            verified,
        )

        if verified:
            self._set_entry_status(race_id, rrn, VerificationStatus.VERIFIED)
            self._award_winner(race_id, rrn)
        else:
            self._set_entry_status(race_id, rrn, VerificationStatus.FAILED)

        return verified

    def check_soft_deadline(self, race_id: str) -> dict:
        """Check if the soft deadline has passed; award partial payout if no winner yet.

        If the deadline has passed and no winner exists, finds the current leader
        and awards 50% of the prize pool, then closes the race.

        Returns:
            {action: "none"|"partial_payout"|"already_closed", rrn: str|None, credits_awarded: int}
        """
        race = self._load_race(race_id)
        if race is None:
            return {"action": "none", "rrn": None, "credits_awarded": 0}

        if race.status != RaceStatus.OPEN:
            return {"action": "already_closed", "rrn": race.winner_rrn, "credits_awarded": 0}

        now = datetime.now(timezone.utc)
        if now < race.soft_deadline:
            return {"action": "none", "rrn": None, "credits_awarded": 0}

        # Past deadline — award partial payout to leader
        standings = self.get_standings(race_id)
        if not standings:
            # No entries at all — just close
            race.status = RaceStatus.EXPIRED
            self._save_race(race)
            return {"action": "expired", "rrn": None, "credits_awarded": 0}

        leader = standings[0]
        partial = race.prize_pool_credits // 2

        log.info(
            "Soft deadline reached: race=%s leader=%s score=%.4f partial_payout=%d",
            race_id,
            leader.rrn,
            leader.best_score,
            partial,
        )

        self._award_credits(leader.rrn, partial, reason=f"soft_deadline_payout race={race_id}")
        race.status = RaceStatus.COMPLETED
        race.winner_rrn = leader.rrn
        self._save_race(race)

        return {"action": "partial_payout", "rrn": leader.rrn, "credits_awarded": partial}

    def get_standings(self, race_id: str) -> list[ThresholdEntry]:
        """Return all entries for a race sorted by best_score descending."""
        entries_map = self._entries.get(race_id)
        if entries_map is None:
            # Try to load from Firestore
            entries_map = self._load_entries_from_firestore(race_id)
            self._entries[race_id] = entries_map

        return sorted(entries_map.values(), key=lambda e: e.best_score, reverse=True)

    def list_open_races(self) -> list[ThresholdRace]:
        """Return all open threshold races."""
        open_races = [r for r in self._races.values() if r.status == RaceStatus.OPEN]

        # Also try Firestore for races not yet in memory
        try:
            db = _get_firestore_client()
            if db is not None:
                docs = (
                    db.collection(_COL_RACES).where("status", "==", RaceStatus.OPEN.value).stream()
                )
                for doc in docs:
                    data = doc.to_dict() or {}
                    race = ThresholdRace.from_dict(data)
                    if race.id not in self._races:
                        self._races[race.id] = race
                        self._entries.setdefault(race.id, {})
                        open_races.append(race)
        except Exception as exc:
            log.debug("Firestore list_open_races failed: %s — using in-memory", exc)

        # De-duplicate (Firestore may have already-in-memory races)
        seen: set[str] = set()
        result = []
        for r in open_races:
            if r.id not in seen and r.status == RaceStatus.OPEN:
                seen.add(r.id)
                result.append(r)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _award_winner(self, race_id: str, rrn: str) -> None:
        """Award full prize pool and close the race."""
        race = self._load_race(race_id)
        if race is None:
            return

        log.info(
            "ThresholdRace winner: race=%s rrn=%s prize=%d",
            race_id,
            rrn,
            race.prize_pool_credits,
        )
        self._award_credits(
            rrn, race.prize_pool_credits, reason=f"threshold_race_winner race={race_id}"
        )
        race.status = RaceStatus.COMPLETED
        race.winner_rrn = rrn
        self._save_race(race)

    def _award_credits(self, rrn: str, amount: int, reason: str) -> None:
        """Award credits to a robot's owner via the credits subsystem."""
        try:
            from castor.contribute.credits import _get_firestore_client as _creds_fs

            db = _creds_fs()
            db.collection("contributors").document(rrn).set(
                {"credits": amount, "credits_redeemable": amount},
                merge=True,
            )
            db.collection("contributors").document(rrn).collection("credit_log").add(
                {"amount": amount, "reason": reason, "ts": int(time.time()), "rrn": rrn}
            )
        except Exception as exc:
            log.debug("_award_credits Firestore unavailable: %s", exc)

    def _load_race(self, race_id: str) -> ThresholdRace | None:
        if race_id in self._races:
            return self._races[race_id]

        try:
            db = _get_firestore_client()
            if db is not None:
                doc = db.collection(_COL_RACES).document(race_id).get()
                if doc.exists:
                    race = ThresholdRace.from_dict(doc.to_dict() or {})
                    self._races[race_id] = race
                    self._entries.setdefault(race_id, {})
                    return race
        except Exception as exc:
            log.debug("_load_race Firestore unavailable: %s", exc)

        return None

    def _save_race(self, race: ThresholdRace) -> None:
        self._races[race.id] = race
        try:
            db = _get_firestore_client()
            if db is not None:
                db.collection(_COL_RACES).document(race.id).set(race.to_dict(), merge=True)
        except Exception as exc:
            log.debug("_save_race Firestore unavailable: %s", exc)

    def _load_entry(self, race_id: str, rrn: str) -> ThresholdEntry | None:
        entries = self._entries.get(race_id, {})
        if rrn in entries:
            return entries[rrn]

        try:
            db = _get_firestore_client()
            if db is not None:
                doc = (
                    db.collection(_COL_RACES)
                    .document(race_id)
                    .collection(_COL_ENTRIES_SUB)
                    .document(rrn)
                    .get()
                )
                if doc.exists:
                    entry = ThresholdEntry.from_dict(doc.to_dict() or {})
                    self._entries.setdefault(race_id, {})[rrn] = entry
                    return entry
        except Exception as exc:
            log.debug("_load_entry Firestore unavailable: %s", exc)

        return None

    def _save_entry(self, entry: ThresholdEntry) -> None:
        self._entries.setdefault(entry.race_id, {})[entry.rrn] = entry
        try:
            db = _get_firestore_client()
            if db is not None:
                (
                    db.collection(_COL_RACES)
                    .document(entry.race_id)
                    .collection(_COL_ENTRIES_SUB)
                    .document(entry.rrn)
                    .set(entry.to_dict(), merge=True)
                )
        except Exception as exc:
            log.debug("_save_entry Firestore unavailable: %s", exc)

    def _set_entry_status(self, race_id: str, rrn: str, status: VerificationStatus) -> None:
        entry = self._load_entry(race_id, rrn)
        if entry is None:
            return
        entry.verification_status = status
        self._save_entry(entry)

    def _load_entries_from_firestore(self, race_id: str) -> dict[str, ThresholdEntry]:
        entries: dict[str, ThresholdEntry] = {}
        try:
            db = _get_firestore_client()
            if db is not None:
                docs = (
                    db.collection(_COL_RACES)
                    .document(race_id)
                    .collection(_COL_ENTRIES_SUB)
                    .stream()
                )
                for doc in docs:
                    data = doc.to_dict() or {}
                    entry = ThresholdEntry.from_dict(data)
                    entries[entry.rrn] = entry
        except Exception as exc:
            log.debug("_load_entries_from_firestore unavailable: %s", exc)
        return entries
