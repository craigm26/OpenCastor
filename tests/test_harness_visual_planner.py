"""
Tests for castor/harness/visual_planner.py — visual-planner skill layer.

Covers config validation, task routing, plan stubs, and harness exports.
"""

from __future__ import annotations

import pytest

from castor.harness.visual_planner import (
    VISUAL_PLANNER_LAYER_SCHEMA,
    VISUAL_PLANNER_MODELS,
    VisualPlannerConfig,
    VisualPlannerLayer,
    make_visual_planner,
)

# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_default_config():
    cfg = VisualPlannerConfig()
    assert cfg.model == "none"
    assert cfg.goal_source == "oak_d"
    assert cfg.planning_horizon == 16
    assert cfg.cem_samples == 512
    assert cfg.latent_dim == 192
    assert cfg.device == "hailo"


def test_lewm_config_valid():
    cfg = VisualPlannerConfig(model="lewm", device="cpu")
    assert cfg.model == "lewm"
    assert cfg.device == "cpu"


def test_invalid_model_raises():
    with pytest.raises(ValueError, match="model must be one of"):
        VisualPlannerConfig(model="gpt-vision")


def test_invalid_goal_source_raises():
    with pytest.raises(ValueError, match="goal_source must be one of"):
        VisualPlannerConfig(goal_source="camera_x")


def test_invalid_planning_horizon_raises():
    with pytest.raises(ValueError, match="planning_horizon"):
        VisualPlannerConfig(planning_horizon=0)
    with pytest.raises(ValueError, match="planning_horizon"):
        VisualPlannerConfig(planning_horizon=200)


def test_invalid_cem_samples_raises():
    with pytest.raises(ValueError, match="cem_samples"):
        VisualPlannerConfig(cem_samples=0)


def test_from_dict_roundtrip():
    d = {
        "model": "lewm",
        "goal_source": "last_frame",
        "planning_horizon": 32,
        "cem_samples": 256,
        "latent_dim": 192,
        "device": "cpu",
    }
    cfg = VisualPlannerConfig.from_dict(d)
    assert cfg.to_dict() == d


def test_from_dict_defaults():
    cfg = VisualPlannerConfig.from_dict({})
    assert cfg.model == "none"


# ---------------------------------------------------------------------------
# Layer enabled/disabled
# ---------------------------------------------------------------------------


def test_layer_disabled_when_model_none():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="none"))
    assert not layer.enabled


def test_layer_enabled_when_lewm():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="lewm"))
    assert layer.enabled


# ---------------------------------------------------------------------------
# Task routing
# ---------------------------------------------------------------------------


def test_visual_task_detection_grip():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="lewm"))
    assert layer.is_visual_task("grip the red cup")
    assert layer.is_visual_task("pick up the object on the left")
    assert layer.is_visual_task("Navigate to the charging station")


def test_visual_task_detection_negative():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="lewm"))
    assert not layer.is_visual_task("what is the temperature in the room?")
    assert not layer.is_visual_task("tell me about yourself")


def test_plan_returns_not_routed_when_disabled():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="none"))
    result = layer.plan("grip the cup")
    assert not result.routed
    assert result.model_used == "none"
    assert "disabled" in (result.fallback_reason or "")


def test_plan_returns_not_routed_for_non_visual_task():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="lewm"))
    result = layer.plan("what is the weather today?")
    assert not result.routed
    assert not result.should_skip_llm


def test_lewm_plan_stub():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="lewm", planning_horizon=8))
    result = layer.plan("grip the cup")
    assert result.routed
    assert result.model_used == "lewm"
    assert isinstance(result.action_sequence, list)
    assert len(result.action_sequence) <= 8
    # Stub results should not block fallback awareness
    assert result.fallback_reason is not None  # stub note present


def test_dinowm_plan_stub():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="dinowm"))
    result = layer.plan("navigate to the door")
    assert result.routed
    assert result.model_used == "dinowm"


def test_should_skip_llm_false_for_empty_actions():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="dinowm"))
    result = layer.plan("navigate to the door")
    # dinowm stub returns empty action_sequence
    assert not result.should_skip_llm


def test_should_skip_llm_true_for_lewm():
    layer = VisualPlannerLayer(VisualPlannerConfig(model="lewm"))
    result = layer.plan("grip the red mug")
    # lewm stub returns non-empty action_sequence
    assert result.should_skip_llm


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_make_visual_planner():
    layer = make_visual_planner({"model": "lewm", "device": "cpu"})
    assert layer.enabled
    assert layer.config.device == "cpu"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_has_required_keys():
    assert VISUAL_PLANNER_LAYER_SCHEMA["id"] == "visual-planner"
    assert "config_schema" in VISUAL_PLANNER_LAYER_SCHEMA
    assert "hardware_notes" in VISUAL_PLANNER_LAYER_SCHEMA


def test_schema_models_match_constants():
    schema_models = VISUAL_PLANNER_LAYER_SCHEMA["config_schema"]["model"]["values"]
    assert set(schema_models) == set(VISUAL_PLANNER_MODELS)


def test_pi5_hailo_recommended_in_hardware_notes():
    notes = VISUAL_PLANNER_LAYER_SCHEMA["hardware_notes"]
    assert "pi5_hailo" in notes
    assert "Hailo" in notes["pi5_hailo"]


# ---------------------------------------------------------------------------
# Harness __init__ export
# ---------------------------------------------------------------------------


def test_exported_from_harness_package():
    from castor.harness import (
        VISUAL_PLANNER_MODELS,
        VisualPlannerConfig,
        VisualPlannerLayer,
        VisualPlannerResult,
        make_visual_planner,
    )

    assert VisualPlannerConfig is not None
    assert VisualPlannerLayer is not None
    assert VisualPlannerResult is not None
    assert make_visual_planner is not None
    assert len(VISUAL_PLANNER_MODELS) == 3
    assert "lewm" in VISUAL_PLANNER_MODELS
