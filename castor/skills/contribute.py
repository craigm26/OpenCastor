"""castor.skills.contribute — Idle compute donation skill."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger("OpenCastor.Contribute")
_STATS_PATH = Path.home() / ".config" / "opencastor" / "contribute_stats.json"
_HISTORY_PATH = Path.home() / ".config" / "opencastor" / "contribute_history.json"
_skill_instance: ContributeSkill | None = None


class ContributeSkill:
    def __init__(self) -> None:
        self._active = False
        self._cancel_flag: list[bool] = [False]
        self._thread: threading.Thread | None = None
        self._config: dict[str, Any] = {}
        self._stats = self._load_stats()
        self._check_daily_reset()

    def _load_stats(self) -> dict:
        try:
            if _STATS_PATH.exists():
                return json.loads(_STATS_PATH.read_text())
        except Exception:
            pass
        return {
            "work_units_total": 0,
            "work_units_today": 0,
            "contribute_minutes_today": 0,
            "contribute_minutes_lifetime": 0,
            "last_reset_date": date.today().isoformat(),
        }

    def _save_stats(self) -> None:
        try:
            _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _STATS_PATH.write_text(json.dumps(self._stats))
        except Exception:
            pass

    def _check_daily_reset(self) -> None:
        """Reset daily counters at midnight, archive previous day."""
        today = date.today().isoformat()
        last_reset = self._stats.get("last_reset_date", "")
        if last_reset and last_reset != today:
            self._archive_day(last_reset)
            self._stats["work_units_today"] = 0
            self._stats["contribute_minutes_today"] = 0
            self._stats["last_reset_date"] = today
            self._save_stats()

    def _archive_day(self, day: str) -> None:
        """Append a day's stats to rolling 90-day history."""
        try:
            history: list[dict] = []
            if _HISTORY_PATH.exists():
                history = json.loads(_HISTORY_PATH.read_text())
            history.append(
                {
                    "date": day,
                    "work_units": self._stats.get("work_units_today", 0),
                    "minutes": self._stats.get("contribute_minutes_today", 0),
                }
            )
            history = history[-90:]
            _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            _HISTORY_PATH.write_text(json.dumps(history))
        except Exception:
            pass

    def get_history(self) -> list[dict]:
        """Return contribution history (up to 90 days)."""
        try:
            if _HISTORY_PATH.exists():
                return json.loads(_HISTORY_PATH.read_text())
        except Exception:
            pass
        return []

    def is_idle(self, last_active_ts: float, idle_after_minutes: int) -> bool:
        return (time.time() - last_active_ts) >= idle_after_minutes * 60

    def start(self, config: dict | None = None) -> None:
        if self._active:
            return
        self._config = config or {}
        self._cancel_flag[0] = False
        self._active = True
        self._thread = threading.Thread(target=self._contribute_loop, daemon=True)
        self._thread.start()
        log.info("Contribute started (projects=%s)", self._config.get("projects"))

    def stop(self) -> None:
        self._cancel_flag[0] = True
        self._active = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Contribute stopped")

    def status(self) -> dict:
        self._check_daily_reset()
        result: dict = {
            "enabled": self._config.get("enabled", False),
            "active": self._active,
            "project": (self._config.get("projects") or [None])[0],
            "work_units_total": self._stats.get("work_units_total", 0),
            "work_units_today": self._stats.get("work_units_today", 0),
            "contribute_minutes_today": self._stats.get("contribute_minutes_today", 0),
            "contribute_minutes_lifetime": self._stats.get("contribute_minutes_lifetime", 0),
        }
        if "hardware_tier" in self._stats:
            result["hardware_tier"] = self._stats["hardware_tier"]
        return result

    def _contribute_loop(self) -> None:
        from castor.contribute.coordinator import HarnessEvalCoordinator, make_coordinator
        from castor.contribute.hardware_profile import get_hw_profile
        from castor.contribute.harness_eval import detect_hardware_tier
        from castor.contribute.runner import run_work_unit

        projects = self._config.get("projects", ["science", "harness_research"])
        coordinator_type = self._config.get("coordinator", "simulated")
        coordinator_url = self._config.get("boinc_url", "")

        if "harness_research" in projects:
            coordinator = HarnessEvalCoordinator()
            hw = get_hw_profile()
            tier = detect_hardware_tier(hw)
            log.info("Contributing to OpenCastor harness research (hardware tier: %s)", tier)
            self._stats["hardware_tier"] = tier
        else:
            coordinator = make_coordinator(coordinator_type, coordinator_url)
            hw = {}
        while self._active and not self._cancel_flag[0]:
            self._check_daily_reset()
            try:
                wu = coordinator.fetch_work_unit(hw, projects)
                if wu is None:
                    time.sleep(30)
                    continue
                start_t = time.time()
                result = run_work_unit(wu, cancelled_flag=self._cancel_flag)
                if result.status == "complete":
                    coordinator.submit_result(result)
                    elapsed_min = max(1, int((time.time() - start_t) / 60))
                    self._stats["work_units_total"] = self._stats.get("work_units_total", 0) + 1
                    self._stats["work_units_today"] = self._stats.get("work_units_today", 0) + 1
                    self._stats["contribute_minutes_today"] = (
                        self._stats.get("contribute_minutes_today", 0) + elapsed_min
                    )
                    self._stats["contribute_minutes_lifetime"] = (
                        self._stats.get("contribute_minutes_lifetime", 0) + elapsed_min
                    )
                    self._save_stats()
            except Exception as exc:
                log.warning("Contribute loop error: %s", exc)
                time.sleep(10)


def get_contribute_skill() -> ContributeSkill:
    """Get or create the singleton ContributeSkill instance."""
    global _skill_instance
    if _skill_instance is None:
        _skill_instance = ContributeSkill()
    return _skill_instance


def get_contribute_status() -> dict:
    return get_contribute_skill().status()


def get_contribute_history() -> list[dict]:
    return get_contribute_skill().get_history()


def start_contribute(config: dict | None = None) -> dict:
    skill = get_contribute_skill()
    skill.start(config)
    return skill.status()


def stop_contribute() -> dict:
    skill = get_contribute_skill()
    skill.stop()
    return skill.status()
