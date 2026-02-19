"""Integration tests: Sisyphus → PatchSync hookup.

Tests that ApplyStage._broadcast_to_swarm() is called when swarm is enabled
and that it does nothing when disabled or unconfigured.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from castor.learner.apply_stage import ApplyStage
from castor.learner.patches import BehaviorPatch, ConfigPatch
from castor.learner.qa_stage import QAResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_qa_approved() -> QAResult:
    result = MagicMock(spec=QAResult)
    result.approved = True
    return result


def _make_qa_rejected() -> QAResult:
    result = MagicMock(spec=QAResult)
    result.approved = False
    return result


def _make_config_patch(key: str = "max_velocity", new_value: float = 1.5) -> ConfigPatch:
    return ConfigPatch(
        key=key,
        new_value=new_value,
        file="config.yaml",
        rationale="speed tuning",
    )


def _make_behavior_patch() -> BehaviorPatch:
    return BehaviorPatch(
        rule_name="avoid_walls",
        conditions={"obstacle_distance_m": {"lt": 0.5}},
        action={"type": "turn", "angular": 0.5},
        rationale="wall avoidance",
    )


# ---------------------------------------------------------------------------
# set_swarm_config
# ---------------------------------------------------------------------------


class TestSetSwarmConfig:
    def test_set_swarm_config_stores_config(self, tmp_path):
        stage = ApplyStage(config_dir=tmp_path)
        cfg = {"enabled": True, "patch_sync": True, "robot_id": "bot-1"}
        stage.set_swarm_config(cfg)
        assert stage._swarm_config == cfg

    def test_swarm_config_not_set_by_default(self, tmp_path):
        stage = ApplyStage(config_dir=tmp_path)
        assert getattr(stage, "_swarm_config", None) is None


# ---------------------------------------------------------------------------
# _broadcast_to_swarm: disabled paths
# ---------------------------------------------------------------------------


class TestBroadcastDisabled:
    def test_no_swarm_config_does_nothing(self, tmp_path):
        """_broadcast_to_swarm does nothing when _swarm_config is absent."""
        stage = ApplyStage(config_dir=tmp_path)
        p = _make_config_patch()
        # No exception, no broadcast
        with patch("castor.swarm.patch_sync.PatchSync") as mock_ps:
            stage._broadcast_to_swarm(p)
            mock_ps.assert_not_called()

    def test_swarm_disabled_flag_does_nothing(self, tmp_path):
        stage = ApplyStage(config_dir=tmp_path)
        stage.set_swarm_config({"enabled": False, "patch_sync": True, "robot_id": "bot-1"})
        p = _make_config_patch()
        with patch("castor.swarm.patch_sync.PatchSync") as mock_ps:
            stage._broadcast_to_swarm(p)
            mock_ps.assert_not_called()

    def test_patch_sync_disabled_does_nothing(self, tmp_path):
        stage = ApplyStage(config_dir=tmp_path)
        stage.set_swarm_config({"enabled": True, "patch_sync": False, "robot_id": "bot-1"})
        p = _make_config_patch()
        with patch("castor.swarm.patch_sync.PatchSync") as mock_ps:
            stage._broadcast_to_swarm(p)
            mock_ps.assert_not_called()


# ---------------------------------------------------------------------------
# _broadcast_to_swarm: enabled path
# ---------------------------------------------------------------------------


class TestBroadcastEnabled:
    def _swarm_cfg(self, tmp_path: Path) -> dict:
        return {
            "enabled": True,
            "patch_sync": True,
            "robot_id": "bot-test",
            "shared_memory_path": str(tmp_path / "swarm_mem.json"),
        }

    def test_config_patch_published(self, tmp_path):
        """A ConfigPatch is serialized and published to PatchSync."""
        stage = ApplyStage(config_dir=tmp_path)
        stage.set_swarm_config(self._swarm_cfg(tmp_path))

        from castor.swarm.shared_memory import SharedMemory
        from castor.swarm.patch_sync import PatchSync

        published = []

        def _fake_publish(patch_type, patch_data, rationale, qa_passed):
            published.append(
                {
                    "patch_type": patch_type,
                    "patch_data": patch_data,
                    "rationale": rationale,
                    "qa_passed": qa_passed,
                }
            )
            return "fake-uuid"

        with patch.object(PatchSync, "publish_patch", side_effect=_fake_publish):
            p = _make_config_patch()
            stage._broadcast_to_swarm(p)

        assert len(published) == 1
        assert published[0]["patch_type"] == "config"
        assert published[0]["patch_data"]["key"] == "max_velocity"
        assert published[0]["qa_passed"] is True

    def test_behavior_patch_published(self, tmp_path):
        """A BehaviorPatch is serialized and published."""
        stage = ApplyStage(config_dir=tmp_path)
        stage.set_swarm_config(self._swarm_cfg(tmp_path))

        from castor.swarm.patch_sync import PatchSync

        published = []

        def _fake_publish(patch_type, patch_data, rationale, qa_passed):
            published.append(patch_type)
            return "fake-uuid"

        with patch.object(PatchSync, "publish_patch", side_effect=_fake_publish):
            p = _make_behavior_patch()
            stage._broadcast_to_swarm(p)

        assert published == ["behavior"]

    def test_broadcast_exception_is_swallowed(self, tmp_path):
        """Exceptions in broadcast must not propagate — broadcast is best-effort."""
        stage = ApplyStage(config_dir=tmp_path)
        stage.set_swarm_config(self._swarm_cfg(tmp_path))

        from castor.swarm.patch_sync import PatchSync

        with patch.object(PatchSync, "publish_patch", side_effect=RuntimeError("network down")):
            # Must not raise
            stage._broadcast_to_swarm(_make_config_patch())


# ---------------------------------------------------------------------------
# apply() integration: broadcast called on success only
# ---------------------------------------------------------------------------


class TestApplyCallsBroadcast:
    def test_apply_calls_broadcast_when_enabled(self, tmp_path):
        """apply() must call _broadcast_to_swarm after successful patch application."""
        stage = ApplyStage(config_dir=tmp_path)
        stage.set_swarm_config(
            {"enabled": True, "patch_sync": True, "robot_id": "bot-1"}
        )

        with patch.object(stage, "_broadcast_to_swarm") as mock_broadcast:
            p = _make_config_patch()
            qa = _make_qa_approved()
            result = stage.apply(p, qa)

        assert result is True
        mock_broadcast.assert_called_once_with(p)

    def test_apply_no_broadcast_when_qa_rejected(self, tmp_path):
        """apply() must NOT call _broadcast_to_swarm when QA rejects the patch."""
        stage = ApplyStage(config_dir=tmp_path)
        stage.set_swarm_config(
            {"enabled": True, "patch_sync": True, "robot_id": "bot-1"}
        )

        with patch.object(stage, "_broadcast_to_swarm") as mock_broadcast:
            p = _make_config_patch()
            qa = _make_qa_rejected()
            result = stage.apply(p, qa)

        assert result is False
        mock_broadcast.assert_not_called()

    def test_apply_broadcast_not_called_when_swarm_disabled(self, tmp_path):
        """apply() calls _broadcast_to_swarm but broadcast does nothing if disabled."""
        stage = ApplyStage(config_dir=tmp_path)
        stage.set_swarm_config(
            {"enabled": False, "patch_sync": False, "robot_id": "bot-1"}
        )

        with patch.object(stage, "_broadcast_to_swarm", wraps=stage._broadcast_to_swarm) as mock_bcast:
            with patch("castor.swarm.patch_sync.PatchSync") as mock_ps:
                p = _make_config_patch()
                qa = _make_qa_approved()
                stage.apply(p, qa)

        # _broadcast_to_swarm is called (always) but PatchSync.publish_patch is not
        mock_bcast.assert_called_once()
        mock_ps.assert_not_called()


# ---------------------------------------------------------------------------
# SisyphusLoop.apply_stage is accessible and can receive swarm config
# ---------------------------------------------------------------------------


class TestSisyphusLoopSwarmInjection:
    def test_apply_stage_accessible(self):
        """SisyphusLoop exposes apply_stage so main.py can inject swarm config."""
        from castor.learner.sisyphus import SisyphusLoop

        loop = SisyphusLoop(config={})
        assert hasattr(loop, "apply_stage")
        assert isinstance(loop.apply_stage, ApplyStage)

    def test_swarm_config_injectable_via_apply_stage(self):
        """set_swarm_config on loop.apply_stage should be reachable from main.py."""
        from castor.learner.sisyphus import SisyphusLoop

        loop = SisyphusLoop(config={})
        swarm_cfg = {"enabled": True, "patch_sync": True, "robot_id": "bot-999"}
        loop.apply_stage.set_swarm_config(swarm_cfg)
        assert loop.apply_stage._swarm_config == swarm_cfg
