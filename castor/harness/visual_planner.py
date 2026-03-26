"""
castor/harness/visual_planner.py — Visual-planner skill layer.

Provides a harness layer that routes motor-planning tasks (grip, navigate,
place, reach) through a visual world model instead of a text LLM.

Supported backends
------------------
- ``none``    — disabled; skill-executor falls back to the LLM model-router
- ``lewm``    — LeWorldModel (JEPA, ~15M params, raw pixels, ~1 s planning)
               Paper: https://le-wm.github.io/
               Runs on Pi5+Hailo8L (26 TOPS) fully offline
- ``dinowm``  — DINO-based world model (heavier, ~47 s baseline reference)

Architecture (LeWM)
-------------------
  OAK-D frame ─► Encoder ─► z_t (192-dim latent)
  Goal frame  ─►┘
  Predictor(z_t, a_t) ─► ẑ_{t+1}
  Cross-Entropy Method optimises action sequence until ẑ_T ≈ z_goal

Config schema
-------------
  skill: visual-planner
  config:
    model: lewm          # lewm | dinowm | none
    goal_source: oak_d   # oak_d | static_image | last_frame
    planning_horizon: 16
    cem_samples: 512
    latent_dim: 192
    device: hailo        # hailo | cpu | cuda
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.Harness.VisualPlanner")

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

VISUAL_PLANNER_MODELS = ("none", "lewm", "dinowm")
VISUAL_PLANNER_GOAL_SOURCES = ("oak_d", "static_image", "last_frame")
VISUAL_PLANNER_DEVICES = ("hailo", "cpu", "cuda")

# Motor-planning task keywords — these route to visual planner when active
VISUAL_TASK_KEYWORDS: frozenset[str] = frozenset(
    [
        "grip",
        "grasp",
        "pick",
        "place",
        "put",
        "navigate",
        "move to",
        "reach",
        "approach",
        "avoid",
        "push",
        "pull",
    ]
)


@dataclass
class VisualPlannerConfig:
    """Validated config for the visual-planner skill layer."""

    model: str = "none"
    goal_source: str = "oak_d"
    planning_horizon: int = 16
    cem_samples: int = 512
    latent_dim: int = 192
    device: str = "hailo"

    def __post_init__(self) -> None:
        if self.model not in VISUAL_PLANNER_MODELS:
            raise ValueError(
                f"visual_planner.model must be one of {VISUAL_PLANNER_MODELS}, got {self.model!r}"
            )
        if self.goal_source not in VISUAL_PLANNER_GOAL_SOURCES:
            raise ValueError(
                f"visual_planner.goal_source must be one of "
                f"{VISUAL_PLANNER_GOAL_SOURCES}, got {self.goal_source!r}"
            )
        if self.planning_horizon < 1 or self.planning_horizon > 128:
            raise ValueError(
                f"visual_planner.planning_horizon must be 1–128, got {self.planning_horizon}"
            )
        if self.cem_samples < 1 or self.cem_samples > 4096:
            raise ValueError(f"visual_planner.cem_samples must be 1–4096, got {self.cem_samples}")
        if self.device not in VISUAL_PLANNER_DEVICES:
            raise ValueError(
                f"visual_planner.device must be one of "
                f"{VISUAL_PLANNER_DEVICES}, got {self.device!r}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualPlannerConfig:
        return cls(
            model=str(data.get("model", "none")),
            goal_source=str(data.get("goal_source", "oak_d")),
            planning_horizon=int(data.get("planning_horizon", 16)),
            cem_samples=int(data.get("cem_samples", 512)),
            latent_dim=int(data.get("latent_dim", 192)),
            device=str(data.get("device", "hailo")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "goal_source": self.goal_source,
            "planning_horizon": self.planning_horizon,
            "cem_samples": self.cem_samples,
            "latent_dim": self.latent_dim,
            "device": self.device,
        }


# ---------------------------------------------------------------------------
# Layer implementation
# ---------------------------------------------------------------------------


@dataclass
class VisualPlannerResult:
    """Result from a visual-planner invocation."""

    routed: bool  # True if visual planner handled the task
    model_used: str  # 'none' | 'lewm' | 'dinowm'
    action_sequence: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    confidence: float = 0.0
    fallback_reason: Optional[str] = None

    @property
    def should_skip_llm(self) -> bool:
        """True when visual planner produced an action sequence."""
        return self.routed and bool(self.action_sequence)


class VisualPlannerLayer:
    """
    Visual-planner harness skill layer.

    Intercepts motor-planning tasks before they reach the LLM model-router
    and routes them through a visual world model when configured.

    Usage::

        layer = VisualPlannerLayer(VisualPlannerConfig(model='lewm'))
        result = layer.plan(instruction='grip the cup', frame=current_frame)
        if result.should_skip_llm:
            executor.run(result.action_sequence)
        else:
            llm_router.route(instruction)
    """

    def __init__(self, config: VisualPlannerConfig) -> None:
        self.config = config
        self._model_loaded = False
        logger.info(
            "VisualPlannerLayer init: model=%s device=%s",
            config.model,
            config.device,
        )

    @property
    def enabled(self) -> bool:
        return self.config.model != "none"

    def is_visual_task(self, instruction: str) -> bool:
        """Return True if the instruction matches motor-planning keywords."""
        lower = instruction.lower()
        return any(kw in lower for kw in VISUAL_TASK_KEYWORDS)

    def plan(
        self,
        instruction: str,
        frame: Optional[Any] = None,
        goal_frame: Optional[Any] = None,
    ) -> VisualPlannerResult:
        """
        Attempt to plan an action sequence for the given instruction.

        Parameters
        ----------
        instruction:
            Natural-language motor command.
        frame:
            Current observation frame (numpy array or bytes).  If None, the
            layer will attempt to fetch from the configured goal_source.
        goal_frame:
            Optional goal image.  If None and goal_source='oak_d', the layer
            uses the most recent OAK-D depth+RGB frame as the goal.

        Returns
        -------
        VisualPlannerResult
            ``routed=True`` when the visual planner handled the task.
            ``routed=False`` when the task should fall through to the LLM.
        """
        if not self.enabled:
            return VisualPlannerResult(
                routed=False,
                model_used="none",
                fallback_reason="visual_planner disabled (model=none)",
            )

        if not self.is_visual_task(instruction):
            return VisualPlannerResult(
                routed=False,
                model_used=self.config.model,
                fallback_reason=f"instruction not a motor task: {instruction[:60]}",
            )

        if self.config.model == "lewm":
            return self._plan_lewm(instruction, frame, goal_frame)
        elif self.config.model == "dinowm":
            return self._plan_dinowm(instruction, frame, goal_frame)

        return VisualPlannerResult(
            routed=False,
            model_used=self.config.model,
            fallback_reason=f"unknown model: {self.config.model}",
        )

    def _plan_lewm(
        self,
        instruction: str,
        frame: Optional[Any],
        goal_frame: Optional[Any],
    ) -> VisualPlannerResult:
        """
        LeWM planning stub.

        In production this calls the LeWM encoder + CEM planner via the
        Hailo runtime or local PyTorch inference.  Currently returns a stub
        result so the harness layer registers in config without crashing.

        Real integration TODO:
          1. Load LeWM weights (~15M params) onto Hailo8L or CPU
          2. Encode current_frame → z_t (192-dim)
          3. Encode goal_frame → z_goal (192-dim)
          4. CEM: sample cem_samples action sequences of length planning_horizon
          5. Roll out predictor for each; pick sequence minimising ||ẑ_T - z_goal||
          6. Return action_sequence as list of {'joint': ..., 'delta': ...} dicts
        """
        logger.info(
            "LeWM plan: instruction=%r horizon=%d cem_samples=%d",
            instruction[:60],
            self.config.planning_horizon,
            self.config.cem_samples,
        )

        # Stub: return a minimal valid action sequence so downstream
        # components can be tested without real model weights.
        stub_actions = [
            {"step": i, "type": "motor_delta", "values": [0.0] * 6, "stub": True}
            for i in range(min(self.config.planning_horizon, 4))
        ]

        return VisualPlannerResult(
            routed=True,
            model_used="lewm",
            action_sequence=stub_actions,
            latency_ms=1000.0,  # target ~1s on Hailo8L
            confidence=0.0,  # stub
            fallback_reason="stub — real LeWM weights not loaded",
        )

    def _plan_dinowm(
        self,
        instruction: str,
        frame: Optional[Any],
        goal_frame: Optional[Any],
    ) -> VisualPlannerResult:
        """DINO-WM planning stub (heavier baseline, ~47 s reference)."""
        logger.info("DINO-WM plan stub: instruction=%r", instruction[:60])
        return VisualPlannerResult(
            routed=True,
            model_used="dinowm",
            action_sequence=[],
            latency_ms=47000.0,
            confidence=0.0,
            fallback_reason="stub — DINO-WM not implemented",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_visual_planner(config_dict: dict[str, Any]) -> VisualPlannerLayer:
    """Build a VisualPlannerLayer from a raw config dict."""
    config = VisualPlannerConfig.from_dict(config_dict)
    return VisualPlannerLayer(config)


# ---------------------------------------------------------------------------
# Harness layer descriptor (used by harness designer)
# ---------------------------------------------------------------------------

VISUAL_PLANNER_LAYER_SCHEMA: dict[str, Any] = {
    "id": "visual-planner",
    "label": "Visual Planner",
    "description": (
        "Routes motor-planning tasks (grip, navigate, place) through a visual "
        "world model instead of a text LLM. LeWM runs on Pi5+Hailo8L in ~1s "
        "fully offline. Set model=none to disable."
    ),
    "scope": "skill",
    "builtin": True,
    "config_schema": {
        "model": {
            "type": "enum",
            "values": list(VISUAL_PLANNER_MODELS),
            "default": "none",
            "description": "Visual world model backend",
        },
        "goal_source": {
            "type": "enum",
            "values": list(VISUAL_PLANNER_GOAL_SOURCES),
            "default": "oak_d",
            "description": "Source of goal frames for planning",
        },
        "planning_horizon": {
            "type": "int",
            "min": 1,
            "max": 128,
            "default": 16,
            "description": "Action sequence length to plan",
        },
        "cem_samples": {
            "type": "int",
            "min": 1,
            "max": 4096,
            "default": 512,
            "description": "Cross-Entropy Method candidate count",
        },
        "latent_dim": {
            "type": "int",
            "min": 64,
            "max": 1024,
            "default": 192,
            "description": "Latent embedding dimensionality",
        },
        "device": {
            "type": "enum",
            "values": list(VISUAL_PLANNER_DEVICES),
            "default": "hailo",
            "description": "Inference device",
        },
    },
    "hardware_notes": {
        "pi5_hailo": "Recommended — LeWM runs on Hailo8L (26 TOPS)",
        "pi5_8gb": "CPU fallback, ~8s planning",
        "pi5_4gb": "CPU fallback, ~15s planning",
        "server": "CUDA fast, use for training",
        "jetson": "CUDA, ~2s planning",
    },
}
