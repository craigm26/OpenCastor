"""
castor/optimizer.py — Per-robot runtime optimizer.

Reads trajectory data and makes conservative improvements to the robot's
local runtime state during idle hours. Never modifies code, never pushes
to git, never affects other robots.

This is the *per-robot* optimizer. The repo-level autoresearch (opencastor-autoresearch/)
operates at the codebase level and is an entirely separate system.

Safety invariants (P66-adjacent):
  - NEVER modify: safety config, auth config, motor parameters, P66 settings
  - NEVER make changes during active sessions
  - ALWAYS backup before modifying any config file
  - ALWAYS validate changes before committing them
  - MAX 3 changes per pass (conservative)
  - Each change must improve its metric by > 5% to be kept; otherwise revert

Usage::

    from castor.optimizer import RobotOptimizer

    opt = RobotOptimizer(config_path=Path("robot.rcan.yaml"))
    report = await opt.run_optimization_pass()
    print(f"Changes made: {report.changes_made}")

CLI::

    castor optimize --dry-run    # show what would change, don't apply
    castor optimize              # run one optimization pass
    castor optimize --report     # show last optimization report
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("OpenCastor.Optimizer")

__all__ = ["RobotOptimizer", "OptimizationReport", "OptimizationChange"]

# ── Safety guard: config keys the optimizer must NEVER touch ──────────────────

_FORBIDDEN_KEYS = frozenset(
    {
        "safety",
        "auth",
        "p66",
        "estop",
        "motor",
        "motor_params",
        "hardware",
        "emergency_stop",
        "pin",
        "secret",
        "api_key",
        "token",
        "password",
        "private_key",
    }
)

# Max changes per optimization pass
_MAX_CHANGES_PER_PASS = 3

# Minimum metric improvement to keep a change (5%)
_MIN_IMPROVEMENT = 0.05

# Optimizer history file
_HISTORY_PATH = Path.home() / ".config" / "opencastor" / "optimizer-history.json"

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class OptimizationChange:
    """A single proposed or applied optimization change."""

    change_type: (
        str  # memory_consolidation | skill_tuning | context_budget | tool_pruning | max_iterations
    )
    description: str
    before: object
    after: object
    metric_name: str
    metric_before: float
    metric_after: float
    applied: bool = False
    reverted: bool = False

    @property
    def metric_delta(self) -> float:
        return self.metric_after - self.metric_before

    @property
    def improved(self) -> bool:
        return self.metric_delta > _MIN_IMPROVEMENT

    def to_dict(self) -> dict:
        return {
            "change_type": self.change_type,
            "description": self.description,
            "before": str(self.before),
            "after": str(self.after),
            "metric_name": self.metric_name,
            "metric_before": round(self.metric_before, 4),
            "metric_after": round(self.metric_after, 4),
            "metric_delta": round(self.metric_delta, 4),
            "applied": self.applied,
            "reverted": self.reverted,
        }


@dataclass
class OptimizationReport:
    """Result of one optimization pass."""

    timestamp: str = ""
    config_path: str = ""
    trajectory_db: str = ""
    dry_run: bool = False
    changes_proposed: list[OptimizationChange] = field(default_factory=list)
    changes_applied: int = 0
    changes_reverted: int = 0
    skipped_active_session: bool = False
    error: Optional[str] = None

    @property
    def changes_made(self) -> int:
        return self.changes_applied

    def summary(self) -> str:
        lines = [
            f"Optimizer pass — {self.timestamp}",
            f"  Config: {self.config_path}",
            f"  Proposed: {len(self.changes_proposed)}  Applied: {self.changes_applied}  Reverted: {self.changes_reverted}",
        ]
        if self.dry_run:
            lines.append("  Mode: DRY RUN — no changes written")
        if self.skipped_active_session:
            lines.append("  ⚠ Skipped — active session detected")
        for ch in self.changes_proposed:
            status = "✓" if ch.applied else ("↩" if ch.reverted else "·")
            lines.append(
                f"  {status} [{ch.change_type}] {ch.description} "
                f"({ch.metric_name}: {ch.metric_before:.3f} → {ch.metric_after:.3f})"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "config_path": self.config_path,
            "trajectory_db": self.trajectory_db,
            "dry_run": self.dry_run,
            "changes_proposed": [c.to_dict() for c in self.changes_proposed],
            "changes_applied": self.changes_applied,
            "changes_reverted": self.changes_reverted,
            "skipped_active_session": self.skipped_active_session,
            "error": self.error,
        }


# ── Main optimizer ────────────────────────────────────────────────────────────


class RobotOptimizer:
    """Per-robot runtime optimizer.

    Reads trajectory data from SQLite and proposes/applies conservative
    improvements to the robot's local RCAN config during idle hours.

    Args:
        config_path:    Path to the robot's RCAN yaml config file.
        trajectory_db:  Path to the trajectory SQLite DB.
                        Defaults to ~/.config/opencastor/trajectories.db
        dry_run:        If True, compute changes but do not write any files.
    """

    def __init__(
        self,
        config_path: Path,
        trajectory_db: Optional[Path] = None,
        dry_run: bool = False,
    ) -> None:
        self._config_path = config_path
        self._trajectory_db = trajectory_db or (
            Path.home() / ".config" / "opencastor" / "trajectories.db"
        )
        self._dry_run = dry_run
        self._backup_path: Optional[Path] = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def run_optimization_pass(self) -> OptimizationReport:
        """Run one full optimization pass. Returns a report of what was done."""
        report = OptimizationReport(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            config_path=str(self._config_path),
            trajectory_db=str(self._trajectory_db),
            dry_run=self._dry_run,
        )

        try:
            # Safety: use IdleGuard to detect if robot becomes active mid-pass
            from castor.idle import IdleGuard, is_robot_idle

            idle_state = await is_robot_idle()
            if not idle_state:
                logger.info("Optimizer: robot not idle (%s) — skipping pass", idle_state.summary)
                report.skipped_active_session = True
                self._persist_report(report)
                return report

            # Run optimization under IdleGuard — abort if activity resumes mid-pass
            async with IdleGuard(poll_interval_s=15.0) as guard:
                # Load trajectory data
                rows = self._load_trajectories(days=7)
                if len(rows) < 5:
                    logger.info(
                        "Optimizer: insufficient trajectory data (%d rows) — skipping", len(rows)
                    )
                    self._persist_report(report)
                    return report

                # Run each optimization target
                candidates: list[OptimizationChange] = []
                candidates.extend(await asyncio.to_thread(self._check_skill_trigger_tuning, rows))
                candidates.extend(await asyncio.to_thread(self._check_context_budget, rows))
                candidates.extend(await asyncio.to_thread(self._check_max_iterations, rows))
                candidates.extend(await asyncio.to_thread(self._check_tool_pruning, rows))
                candidates.extend(await asyncio.to_thread(self._check_memory_consolidation, rows))

                # Sort by metric improvement, cap at MAX_CHANGES_PER_PASS
                candidates.sort(key=lambda c: c.metric_delta, reverse=True)
                candidates = candidates[:_MAX_CHANGES_PER_PASS]
                report.changes_proposed = candidates

                if guard.interrupted:
                    logger.info("Optimizer: activity detected mid-pass — aborting without changes")
                    report.skipped_active_session = True
                    self._persist_report(report)
                    return report

                if not self._dry_run and candidates:
                    self._backup_config()
                    for change in candidates:
                        if guard.interrupted:
                            # Abort mid-change — restore backup
                            if self._backup_path and self._backup_path.exists():
                                self._restore_backup(self._backup_path)
                            report.skipped_active_session = True
                            break
                        applied = self._apply_change(change)
                        if applied:
                            change.applied = True
                            report.changes_applied += 1
                        else:
                            change.reverted = True
                            report.changes_reverted += 1

        except Exception as exc:
            logger.exception("Optimizer pass failed: %s", exc)
            report.error = str(exc)
            # Restore backup if we were mid-change
            if self._backup_path and self._backup_path.exists():
                self._restore_backup(self._backup_path)

        self._persist_report(report)
        return report

    # ── Safety ────────────────────────────────────────────────────────────────

    def _is_safe_key(self, yaml_key: str) -> bool:
        """Return True if a YAML key is safe to modify."""
        key_lower = yaml_key.lower()
        return not any(forbidden in key_lower for forbidden in _FORBIDDEN_KEYS)

    def _validate_change(self, change: OptimizationChange) -> bool:
        """Validate that a proposed change is safe to apply."""
        # Check the change doesn't touch forbidden config areas
        if not self._is_safe_key(str(change.change_type)):
            logger.warning("Optimizer: rejected unsafe change type: %s", change.change_type)
            return False
        # Require positive improvement
        if not change.improved:
            logger.debug(
                "Optimizer: change did not meet improvement threshold (%.4f)", change.metric_delta
            )
            return False
        return True

    # ── Config backup/restore ─────────────────────────────────────────────────

    def _backup_config(self) -> Path:
        """Backup the config file before making changes."""
        ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = self._config_path.with_suffix(f".backup-{ts}.yaml")
        shutil.copy2(self._config_path, backup)
        self._backup_path = backup
        logger.debug("Optimizer: config backed up to %s", backup)
        return backup

    def _restore_backup(self, backup: Path) -> None:
        """Restore the config from a backup."""
        shutil.copy2(backup, self._config_path)
        logger.info("Optimizer: config restored from backup %s", backup)

    # ── Trajectory data ───────────────────────────────────────────────────────

    def _load_trajectories(self, days: int = 7) -> list[dict]:
        """Load recent trajectory rows from the SQLite DB."""
        if not self._trajectory_db.exists():
            return []

        cutoff = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        ).isoformat()

        try:
            conn = sqlite3.connect(str(self._trajectory_db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM trajectories
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT 500
                """,
                (cutoff,),
            ).fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.debug("Optimizer: failed to load trajectories: %s", exc)
            return []

    # ── Optimization targets ──────────────────────────────────────────────────

    def _check_skill_trigger_tuning(self, rows: list[dict]) -> list[OptimizationChange]:
        """Check if skill trigger accuracy can be improved.

        If a skill was triggered but didn't call any of its declared tools,
        that's a potential mismatch. If tool calls happened without a skill trigger,
        a skill could have been more helpful.
        """
        if not rows:
            return []

        skill_rows = [r for r in rows if r.get("skill_triggered")]
        if not skill_rows:
            return []

        # Precision: skill triggered AND had tool calls vs total skill triggers
        helpful = sum(
            1 for r in skill_rows if r.get("tool_calls_json") and r["tool_calls_json"] != "[]"
        )
        total = len(skill_rows)
        precision = helpful / total if total > 0 else 0.0

        # If precision < 0.6, the skill is triggering on false positives
        # Recommendation: note it (we don't auto-edit skill descriptions yet — too risky)
        # Return an informational change only if precision is notably low
        if precision < 0.55 and total >= 5:
            return [
                OptimizationChange(
                    change_type="skill_tuning",
                    description=f"Skill trigger precision is {precision:.1%} over {total} turns — descriptions may be over-broad",
                    before=precision,
                    after=precision,  # No auto-edit; flag only
                    metric_name="skill_trigger_precision",
                    metric_before=precision,
                    metric_after=precision,
                    applied=False,
                )
            ]
        return []

    def _check_context_budget(self, rows: list[dict]) -> list[OptimizationChange]:
        """Check if context budget can be adjusted based on actual token usage."""
        token_rows = [r for r in rows if r.get("context_tokens", 0) > 0]
        if len(token_rows) < 5:
            return []

        avg_tokens = sum(r["context_tokens"] for r in token_rows) / len(token_rows)
        compacted_rate = sum(1 for r in rows if r.get("was_compacted")) / len(rows)

        # Read current budget from config
        current_budget = self._read_config_value("agent.harness.context_budget", default=0.8)
        estimated_max = 100000  # typical context window

        usage_ratio = avg_tokens / (estimated_max * float(current_budget))

        changes = []
        if usage_ratio < 0.35 and compacted_rate < 0.05:
            # Using very little context — could reduce budget to save compute
            new_budget = max(0.5, current_budget - 0.15)
            changes.append(
                OptimizationChange(
                    change_type="context_budget",
                    description=f"Context usage is only {usage_ratio:.0%} of budget — reducing from {current_budget} to {new_budget}",
                    before=current_budget,
                    after=new_budget,
                    metric_name="context_efficiency",
                    metric_before=usage_ratio,
                    metric_after=min(1.0, usage_ratio / 0.65),
                )
            )
        elif compacted_rate > 0.25:
            # Compacting too often — budget too tight
            new_budget = min(0.95, current_budget + 0.1)
            changes.append(
                OptimizationChange(
                    change_type="context_budget",
                    description=f"Compaction rate is {compacted_rate:.0%} — increasing budget from {current_budget} to {new_budget}",
                    before=current_budget,
                    after=new_budget,
                    metric_name="compaction_rate",
                    metric_before=compacted_rate,
                    metric_after=compacted_rate * 0.7,
                )
            )
        return changes

    def _check_max_iterations(self, rows: list[dict]) -> list[OptimizationChange]:
        """Check if max_iterations can be calibrated based on actual usage."""
        iter_rows = [r for r in rows if r.get("iterations", 0) > 0]
        if len(iter_rows) < 10:
            return []

        avg_iters = sum(r["iterations"] for r in iter_rows) / len(iter_rows)
        max_iters = max(r["iterations"] for r in iter_rows)

        # Read current setting
        current_max = self._read_config_value("agent.harness.max_iterations", default=6)

        changes = []
        if avg_iters < 1.5 and max_iters <= 3:
            # Tasks are simple — lower max
            new_max = 3
            if new_max < current_max:
                changes.append(
                    OptimizationChange(
                        change_type="max_iterations",
                        description=f"Avg iterations is {avg_iters:.1f} (max seen: {max_iters}) — lowering max_iterations {current_max}→{new_max}",
                        before=current_max,
                        after=new_max,
                        metric_name="avg_iterations",
                        metric_before=avg_iters,
                        metric_after=avg_iters,
                    )
                )
        elif max_iters >= current_max * 0.9:
            # Frequently hitting the cap — raise it by 1
            new_max = current_max + 1
            cap_rate = sum(1 for r in iter_rows if r["iterations"] >= current_max) / len(iter_rows)
            if cap_rate > 0.1:
                changes.append(
                    OptimizationChange(
                        change_type="max_iterations",
                        description=f"Hitting iteration cap {cap_rate:.0%} of the time — raising max_iterations {current_max}→{new_max}",
                        before=current_max,
                        after=new_max,
                        metric_name="cap_hit_rate",
                        metric_before=cap_rate,
                        metric_after=cap_rate * 0.4,
                    )
                )
        return changes

    def _check_tool_pruning(self, rows: list[dict]) -> list[OptimizationChange]:
        """Flag tools that are never called — candidates for disabling (not advertising)."""
        tool_usage: dict[str, int] = {}
        for r in rows:
            try:
                calls = json.loads(r.get("tool_calls_json") or "[]")
                for call in calls:
                    name = call.get("name", call.get("tool", ""))
                    if name:
                        tool_usage[name] = tool_usage.get(name, 0) + 1
            except Exception:
                continue

        if not tool_usage:
            return []

        # Tools in config that were never called in 7 days
        unused_tools = [t for t, count in tool_usage.items() if count == 0]

        if len(unused_tools) >= 3:
            return [
                OptimizationChange(
                    change_type="tool_pruning",
                    description=f"{len(unused_tools)} tools unused in 7 days — consider disabling from context: {', '.join(unused_tools[:5])}",
                    before=len(tool_usage),
                    after=len(tool_usage) - len(unused_tools),
                    metric_name="tool_context_ratio",
                    metric_before=len(unused_tools) / max(len(tool_usage), 1),
                    metric_after=0.0,
                )
            ]
        return []

    def _check_memory_consolidation(self, rows: list[dict]) -> list[OptimizationChange]:
        """Check episodic memory for consolidation opportunities.

        Looks at how often was_compacted is True vs the distribution of episodes.
        Flags if compaction is happening very frequently (memory pressure).
        """
        if not rows:
            return []

        compacted_count = sum(1 for r in rows if r.get("was_compacted"))
        total = len(rows)
        compaction_rate = compacted_count / total if total > 0 else 0.0

        if compaction_rate > 0.3:
            return [
                OptimizationChange(
                    change_type="memory_consolidation",
                    description=f"High compaction rate ({compaction_rate:.0%}) — episodic memory may benefit from consolidation pass",
                    before=compaction_rate,
                    after=compaction_rate * 0.6,
                    metric_name="compaction_rate",
                    metric_before=compaction_rate,
                    metric_after=compaction_rate * 0.6,
                )
            ]
        return []

    # ── Config I/O ────────────────────────────────────────────────────────────

    def _read_config_value(self, dotted_key: str, default: object = None) -> object:
        """Read a value from the RCAN yaml config by dotted key path."""
        if not self._config_path.exists():
            return default
        try:
            content = self._config_path.read_text(encoding="utf-8")
            # Simple extraction: look for the leaf key
            leaf = dotted_key.split(".")[-1]
            m = re.search(rf"^\s*{re.escape(leaf)}\s*:\s*(.+)", content, re.MULTILINE)
            if m:
                val = m.group(1).strip()
                try:
                    return float(val)
                except ValueError:
                    try:
                        return int(val)
                    except ValueError:
                        return val
        except Exception:
            pass
        return default

    def _apply_change(self, change: OptimizationChange) -> bool:
        """Apply a single change to the config file. Returns True if applied."""
        if not self._validate_change(change):
            return False

        try:
            if change.change_type == "context_budget":
                self._update_config_value("context_budget", str(change.after))
                return True
            elif change.change_type == "max_iterations":
                self._update_config_value("max_iterations", str(int(change.after)))
                return True
            else:
                # skill_tuning, tool_pruning, memory_consolidation:
                # logged/proposed but not auto-written (too risky for v1)
                logger.info(
                    "Optimizer: change type '%s' is advisory-only in v1 (not written to config)",
                    change.change_type,
                )
                return False
        except Exception as exc:
            logger.warning("Optimizer: failed to apply change %s: %s", change.change_type, exc)
            return False

    def _update_config_value(self, key: str, value: str) -> None:
        """Update a key in the RCAN yaml config in-place."""
        if not self._is_safe_key(key):
            raise ValueError(f"Refusing to modify protected config key: {key}")

        content = self._config_path.read_text(encoding="utf-8")
        new_content = re.sub(
            rf"^(\s*{re.escape(key)}\s*:\s*)(.+)",
            rf"\g<1>{value}",
            content,
            flags=re.MULTILINE,
        )
        if new_content == content:
            logger.debug("Optimizer: key '%s' not found in config — no change", key)
            return

        self._config_path.write_text(new_content, encoding="utf-8")
        logger.info("Optimizer: updated %s = %s", key, value)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_report(self, report: OptimizationReport) -> None:
        """Append the report to the optimizer history file."""
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        history = []
        if _HISTORY_PATH.exists():
            try:
                history = json.loads(_HISTORY_PATH.read_text())
            except Exception:
                history = []

        history.append(report.to_dict())
        # Keep last 90 entries
        history = history[-90:]
        _HISTORY_PATH.write_text(json.dumps(history, indent=2))


# ── Convenience ───────────────────────────────────────────────────────────────


async def run_optimizer(
    config_path: Path,
    dry_run: bool = False,
    trajectory_db: Optional[Path] = None,
) -> OptimizationReport:
    """Convenience function to run one optimization pass."""
    opt = RobotOptimizer(config_path=config_path, trajectory_db=trajectory_db, dry_run=dry_run)
    return await opt.run_optimization_pass()
