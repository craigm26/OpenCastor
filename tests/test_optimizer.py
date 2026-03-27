"""Tests for castor/optimizer.py — per-robot runtime optimizer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from castor.optimizer import (
    _FORBIDDEN_KEYS,
    OptimizationChange,
    OptimizationReport,
    RobotOptimizer,
    run_optimizer,
)
from castor.trajectory import TrajectoryLogger

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def dummy_config(tmp_path: Path) -> Path:
    """Minimal RCAN yaml config for testing."""
    cfg = tmp_path / "robot.rcan.yaml"
    cfg.write_text(
        """
robot:
  name: test-robot
  rrn: RRN-000000000001

agent:
  harness:
    enabled: true
    context_budget: 0.8
    max_iterations: 6
    drift_detection: true
""".strip()
    )
    return cfg


@pytest.fixture()
def dummy_trajectory_db(tmp_path: Path) -> Path:
    """SQLite DB with sample trajectory rows."""
    db_path = tmp_path / "trajectories.db"
    # Use TrajectoryLogger to create the schema
    TrajectoryLogger.set_db_path(str(db_path))

    # Insert some rows directly
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trajectories (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            session_id TEXT,
            robot_rrn TEXT,
            instruction TEXT,
            scope TEXT,
            surface TEXT,
            skill_triggered TEXT,
            context_tokens INTEGER,
            was_compacted INTEGER,
            tool_calls_json TEXT,
            final_response TEXT,
            total_latency_ms INTEGER,
            primary_model TEXT,
            secondary_model TEXT,
            secondary_verdict_json TEXT,
            drift_score REAL,
            iterations INTEGER,
            p66_consent_req INTEGER,
            p66_consent_ok INTEGER,
            p66_blocked INTEGER,
            p66_estop INTEGER,
            error TEXT,
            schema_version INTEGER
        )
    """)

    import datetime
    import uuid

    base_ts = datetime.datetime.now(datetime.timezone.utc)

    for i in range(20):
        ts = (base_ts - datetime.timedelta(hours=i)).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO trajectories
               (id, timestamp, session_id, robot_rrn, instruction, scope, surface,
                skill_triggered, context_tokens, was_compacted, tool_calls_json,
                final_response, total_latency_ms, primary_model, iterations,
                schema_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                ts,
                "test-session",
                "RRN-000000000001",
                f"test instruction {i}",
                "chat",
                "test",
                "camera-describe" if i % 3 == 0 else None,
                5000 + i * 100,
                1 if i % 5 == 0 else 0,  # 20% compaction rate
                "[]",
                "ok",
                500,
                "test-model",
                2 if i % 4 != 3 else 6,  # mostly 2 iters, sometimes 6
                1,
            ),
        )

    conn.commit()
    conn.close()

    TrajectoryLogger.set_db_path(None)  # reset
    return db_path


# ── Unit Tests ────────────────────────────────────────────────────────────────


class TestSafetyGuards:
    def test_forbidden_keys_defined(self):
        assert "safety" in _FORBIDDEN_KEYS
        assert "auth" in _FORBIDDEN_KEYS
        assert "p66" in _FORBIDDEN_KEYS
        assert "estop" in _FORBIDDEN_KEYS
        assert "motor" in _FORBIDDEN_KEYS

    def test_safe_key_check(self, dummy_config: Path):
        opt = RobotOptimizer(dummy_config)
        assert opt._is_safe_key("context_budget")
        assert opt._is_safe_key("max_iterations")
        assert not opt._is_safe_key("safety")
        assert not opt._is_safe_key("p66_settings")
        assert not opt._is_safe_key("auth_token")
        assert not opt._is_safe_key("estop_pin")
        assert not opt._is_safe_key("motor_params")

    def test_validate_change_requires_improvement(self, dummy_config: Path):
        opt = RobotOptimizer(dummy_config)
        bad_change = OptimizationChange(
            change_type="context_budget",
            description="test",
            before=0.8,
            after=0.7,
            metric_name="test_metric",
            metric_before=0.5,
            metric_after=0.51,  # only 0.01 improvement < 0.05 threshold
        )
        assert not opt._validate_change(bad_change)

    def test_validate_change_rejects_forbidden_type(self, dummy_config: Path):
        opt = RobotOptimizer(dummy_config)
        bad_change = OptimizationChange(
            change_type="safety_config",  # forbidden
            description="test",
            before="x",
            after="y",
            metric_name="test",
            metric_before=0.0,
            metric_after=1.0,
        )
        assert not opt._validate_change(bad_change)


class TestConfigIO:
    def test_read_config_value(self, dummy_config: Path):
        opt = RobotOptimizer(dummy_config)
        val = opt._read_config_value("agent.harness.context_budget", default=0.8)
        assert val == pytest.approx(0.8)

    def test_read_config_value_missing_returns_default(self, dummy_config: Path):
        opt = RobotOptimizer(dummy_config)
        val = opt._read_config_value("nonexistent.key", default=42)
        assert val == 42

    def test_update_config_value(self, dummy_config: Path):
        opt = RobotOptimizer(dummy_config)
        opt._update_config_value("context_budget", "0.65")
        content = dummy_config.read_text()
        assert "context_budget: 0.65" in content

    def test_update_config_refuses_forbidden_key(self, dummy_config: Path):
        opt = RobotOptimizer(dummy_config)
        with pytest.raises(ValueError, match="protected"):
            opt._update_config_value("safety", "false")

    def test_backup_and_restore(self, dummy_config: Path):
        opt = RobotOptimizer(dummy_config)
        original = dummy_config.read_text()
        backup = opt._backup_config()
        assert backup.exists()

        # Modify config
        dummy_config.write_text("broken content")

        # Restore
        opt._restore_backup(backup)
        assert dummy_config.read_text() == original


class TestOptimizationTargets:
    def test_check_context_budget_low_usage(self, dummy_config: Path, dummy_trajectory_db: Path):
        opt = RobotOptimizer(dummy_config, trajectory_db=dummy_trajectory_db)
        rows = opt._load_trajectories(days=30)
        assert len(rows) > 0

        changes = opt._check_context_budget(rows)
        # Low token usage (5000-7000 out of 80000 budget) → should suggest reducing budget
        assert isinstance(changes, list)

    def test_check_max_iterations(self, dummy_config: Path, dummy_trajectory_db: Path):
        opt = RobotOptimizer(dummy_config, trajectory_db=dummy_trajectory_db)
        rows = opt._load_trajectories(days=30)
        changes = opt._check_max_iterations(rows)
        assert isinstance(changes, list)

    def test_check_skill_trigger(self, dummy_config: Path, dummy_trajectory_db: Path):
        opt = RobotOptimizer(dummy_config, trajectory_db=dummy_trajectory_db)
        rows = opt._load_trajectories(days=30)
        changes = opt._check_skill_trigger_tuning(rows)
        assert isinstance(changes, list)

    def test_check_memory_consolidation(self, dummy_config: Path, dummy_trajectory_db: Path):
        opt = RobotOptimizer(dummy_config, trajectory_db=dummy_trajectory_db)
        rows = opt._load_trajectories(days=30)
        changes = opt._check_memory_consolidation(rows)
        assert isinstance(changes, list)

    def test_load_trajectories_empty_db(self, dummy_config: Path, tmp_path: Path):
        empty_db = tmp_path / "empty.db"
        opt = RobotOptimizer(dummy_config, trajectory_db=empty_db)
        rows = opt._load_trajectories()
        assert rows == []


class TestDryRun:
    def test_dry_run_makes_no_changes(self, dummy_config: Path, dummy_trajectory_db: Path):
        """Dry run must never write to config."""
        import asyncio

        original = dummy_config.read_text()
        opt = RobotOptimizer(dummy_config, trajectory_db=dummy_trajectory_db, dry_run=True)
        report = asyncio.run(opt.run_optimization_pass())

        # Config must be unchanged
        assert dummy_config.read_text() == original
        assert report.dry_run is True
        assert report.changes_applied == 0

    def test_report_has_timestamp(self, dummy_config: Path, dummy_trajectory_db: Path):
        import asyncio

        opt = RobotOptimizer(dummy_config, trajectory_db=dummy_trajectory_db, dry_run=True)
        report = asyncio.run(opt.run_optimization_pass())
        assert report.timestamp
        assert "T" in report.timestamp  # ISO format

    def test_report_persisted(self, dummy_config: Path, dummy_trajectory_db: Path, tmp_path: Path):
        import asyncio

        # Temporarily monkeypatch history path to tmp
        import castor.optimizer as opt_mod

        original_path = opt_mod._HISTORY_PATH
        opt_mod._HISTORY_PATH = tmp_path / "history.json"

        try:
            opt = RobotOptimizer(dummy_config, trajectory_db=dummy_trajectory_db, dry_run=True)
            asyncio.run(opt.run_optimization_pass())
            assert opt_mod._HISTORY_PATH.exists()
            history = json.loads(opt_mod._HISTORY_PATH.read_text())
            assert len(history) >= 1
            assert "timestamp" in history[0]
        finally:
            opt_mod._HISTORY_PATH = original_path


class TestOptimizationReport:
    def test_report_summary(self):
        report = OptimizationReport(
            timestamp="2026-03-17T10:00:00+00:00",
            config_path="/tmp/robot.rcan.yaml",
            changes_applied=2,
            changes_reverted=1,
        )
        summary = report.summary()
        assert "2026-03-17" in summary
        assert "Applied: 2" in summary

    def test_report_to_dict(self):
        report = OptimizationReport(
            timestamp="2026-03-17T10:00:00+00:00",
            changes_applied=1,
        )
        d = report.to_dict()
        assert d["changes_applied"] == 1
        assert "timestamp" in d

    def test_changes_made_property(self):
        report = OptimizationReport(changes_applied=3)
        assert report.changes_made == 3


class TestConvenienceFunction:
    def test_run_optimizer_dry_run(self, dummy_config: Path, dummy_trajectory_db: Path):
        import asyncio

        report = asyncio.run(
            run_optimizer(dummy_config, dry_run=True, trajectory_db=dummy_trajectory_db)
        )
        assert report is not None
        assert report.dry_run is True
        assert report.changes_applied == 0
