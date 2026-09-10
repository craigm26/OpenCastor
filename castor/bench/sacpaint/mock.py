"""A dependency-free plotter world, so the whole benchmark runs with no robot.

``PlotterEmbodiment`` is a pen over the reference's canvas (300 x 400 mm for
the built-in). Actions are absolute Cartesian targets (x, y, z) in metres in
the canvas frame: x to the right, y up the sheet, z above the paper. The pen
marks whenever z is at or below ``pen_down_z`` at both ends of a step, drawing
the straight segment between them. It renders two cameras: ``overhead`` (the
canvas) and ``reference`` (the fixed target image, so an image-reading policy
sees what it must draw). A real rig exposes the same two streams.

``TracePolicy`` is the oracle: it plays the reference strokes back. It is the
ceiling of the scorers, not a contestant. ``IdlePolicy`` declares done at once
and is the floor.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from inspect_robots.embodiment import RENDERABLE, RESETTABLE, SEEDABLE, EmbodimentInfo
from inspect_robots.policy import PolicyBase, PolicyConfig, PolicyInfo
from inspect_robots.scene import Scene
from inspect_robots.spaces import (
    ActionSemantics,
    Box,
    CameraSpec,
    ObservationSpace,
    StateField,
    StateSpec,
)
from inspect_robots.types import Action, ActionChunk, Observation, StepResult

from castor.bench.sacpaint.reference import (
    DEFAULT_REFERENCE,
    ReferenceSpec,
    get_spec,
    reference_image,
)
from castor.bench.sacpaint.scorers import CANONICAL_FLAG, OVERHEAD

REFERENCE_CAM = "reference"
PEN_DOWN_Z = 0.002
PEN_UP_Z = 0.005
Z_MAX = 0.05
_LABELS = ("x", "y", "z")


def _docs(spec: ReferenceSpec) -> str:
    w, h = spec.canvas_mm
    return (
        f"You hold a pen over a white {w:.0f} x {h:.0f} mm portrait sheet. Targets are metres "
        f"in the canvas frame: x runs right across the sheet (0 to {w / 1000:.2f}), y runs up the "
        f"sheet (0 to {h / 1000:.2f}, so y=0 is the bottom edge), z is height above the paper "
        f"(0 to {Z_MAX:.2f}). The pen draws whenever z <= {PEN_DOWN_Z} for the whole segment; move "
        f"with z at {PEN_UP_Z} or higher to travel without marking. The 'overhead' camera shows "
        f"the sheet upright (image top = y {h / 1000:.2f}). The 'reference' camera shows the "
        "picture you must reproduce, framed exactly as the sheet is: its left, right, top and "
        "bottom edges are the sheet's edges."
    )


def action_space(spec: ReferenceSpec) -> Box:
    """The canvas-frame Cartesian action box shared by the mock and any real embodiment."""
    w, h = spec.canvas_mm
    return Box(
        shape=(3,),
        low=np.array([0.0, 0.0, 0.0]),
        high=np.array([w / 1000.0, h / 1000.0, Z_MAX]),
        semantics=ActionSemantics(
            control_mode="eef_abs_pose",
            frame="base",
            dim_labels=_LABELS,
            max_step=(0.02, 0.02, Z_MAX),
        ),
    )


def observation_space(spec: ReferenceSpec) -> ObservationSpace:
    """The overhead at the canonical canvas size, the reference at its own size, plus the pen position."""
    w, h = spec.canonical_size()
    rw, rh = spec.reference_size()
    return ObservationSpace(
        cameras=(CameraSpec(OVERHEAD, h, w, 3), CameraSpec(REFERENCE_CAM, rh, rw, 3)),
        state=StateSpec((StateField("eef_pos", (3,), "m"),)),
    )


class PlotterEmbodiment:
    """A pen plotter over a blank canonical canvas."""

    def __init__(
        self,
        *,
        reference: str = DEFAULT_REFERENCE,
        pen_down_z: float = PEN_DOWN_Z,
        stroke_px: int | None = None,
        photo_mode: str | bool = False,
    ):
        self.spec = get_spec(reference)
        self.pen_down_z = pen_down_z
        self.stroke_px = stroke_px if stroke_px is not None else self.spec.stroke_px
        # photo_mode="markers" wraps the canvas in the marker fixture; "sheet" lays it on a
        # dark desk with no markers. Either forces the scorer through rectification, the
        # same path a real overhead camera takes. True means "markers".
        self.photo_mode = "markers" if photo_mode is True else (photo_mode or None)
        if self.photo_mode not in (None, "markers", "sheet"):
            raise ValueError(f"photo_mode must be False, 'markers', or 'sheet', got {photo_mode!r}")
        self.num_steps = 0
        self._low = np.array([0.0, 0.0, 0.0])
        self._high = np.array(
            [self.spec.canvas_mm[0] / 1000.0, self.spec.canvas_mm[1] / 1000.0, Z_MAX]
        )
        self._eef = np.array([0.0, 0.0, PEN_UP_Z])
        self._canvas = self._blank()
        self._instruction: str | None = None
        self._reference = reference_image(self.spec.name)
        self.info = EmbodimentInfo(
            name="sacpaint_plotter",
            action_space=action_space(self.spec),
            observation_space=observation_space(self.spec),
            control_hz=10.0,
            is_simulated=True,
            capabilities=frozenset({SEEDABLE, RESETTABLE, RENDERABLE}),
            supported_target_kinds=frozenset({"reference_drawing"}),
            docs=_docs(self.spec),
        )

    def _blank(self) -> np.ndarray:
        w, h = self.spec.canonical_size()
        return np.full((h, w, 3), 255, dtype=np.uint8)

    def reset(self, scene: Scene, *, seed: int | None = None) -> Observation:
        """Fresh sheet, pen parked up at the bottom-left corner."""
        self._canvas = self._blank()
        self._eef = np.array([0.0, 0.0, PEN_UP_Z])
        self._instruction = scene.instruction
        self.num_steps = 0
        return self._observe()

    def step(self, action: Action) -> StepResult:
        """Move the pen in a straight line to the target, marking if down at both ends."""
        target = np.clip(
            np.asarray(action.data, dtype=np.float64).reshape(3), self._low, self._high
        )
        if self._eef[2] <= self.pen_down_z and target[2] <= self.pen_down_z:
            p0 = self.spec.mm_to_px((self._eef[0] * 1000.0, self._eef[1] * 1000.0))
            p1 = self.spec.mm_to_px((target[0] * 1000.0, target[1] * 1000.0))
            cv2.line(self._canvas, p0, p1, (0, 0, 0), self.stroke_px)
        self._eef = target
        self.num_steps += 1
        return StepResult(observation=self._observe(), terminated=False)

    def observe_parked(self) -> Observation:
        """Lift the pen clear of the sheet and return an unobstructed final view."""
        self._eef = np.array([0.0, 0.0, PEN_UP_Z])
        return self._observe()

    def close(self) -> None:
        """Nothing to release."""

    def canvas(self) -> np.ndarray:
        """The current canonical canvas (RGB uint8)."""
        return self._canvas.copy()

    def _observe(self) -> Observation:
        extra: dict[str, Any] = {}
        if self.photo_mode == "markers":
            from castor.bench.sacpaint.rectify import compose_fixture_view

            overhead = compose_fixture_view(self._canvas)
        elif self.photo_mode == "sheet":
            from castor.bench.sacpaint.rectify import compose_sheet_view

            overhead = compose_sheet_view(self._canvas)
        else:
            overhead = self._canvas.copy()
            extra = {CANONICAL_FLAG: True}
        extra["medium"] = "sim"
        return Observation(
            images={OVERHEAD: overhead, REFERENCE_CAM: self._reference.copy()},
            state={"eef_pos": self._eef.copy()},
            instruction=self._instruction,
            extra=extra,
        )


def plotter_embodiment(**kwargs: Any) -> PlotterEmbodiment:
    """Registry factory for ``--embodiment sacpaint_plotter`` (``-E reference=NAME -E photo_mode=sheet``)."""
    return PlotterEmbodiment(**kwargs)


def _split(a: np.ndarray, b: np.ndarray, max_xy: float, max_z: float) -> list[np.ndarray]:
    """Waypoints from a to b (exclusive of a) that fit the per-step delta limits.

    The core's default guardrail clamps each step to 5% of a dimension's range,
    so a move that ignores that gets clamped mid-air and the next pen-down lands
    in the wrong place. Splitting here keeps the oracle honest under guardrails.
    """
    d = b - a
    n = max(1, int(np.ceil(np.linalg.norm(d[:2]) / max_xy)), int(np.ceil(abs(d[2]) / max_z)))
    return [a + d * (k / n) for k in range(1, n + 1)]


def stroke_actions(
    spec: ReferenceSpec, pen_down_z: float = 0.0, max_xy: float = 0.014, max_z: float = 0.0024
) -> list[np.ndarray]:
    """Turn a reference's strokes into a pen-up / pen-down action list, split to the delta limits."""
    out: list[np.ndarray] = []
    here = np.array([0.0, 0.0, PEN_UP_Z])
    for group in spec.strokes.values():
        for stroke in group:
            pts = [np.array([x / 1000.0, y / 1000.0]) for x, y in stroke]
            targets = [np.array([*pts[0], PEN_UP_Z]), np.array([*pts[0], pen_down_z])]
            targets += [np.array([*p, pen_down_z]) for p in pts[1:]]
            targets.append(np.array([*pts[-1], PEN_UP_Z]))
            for t in targets:
                out.extend(_split(here, t, max_xy, max_z))
                here = t
    return out


def _stop(observation: Observation) -> ActionChunk:
    hold = np.asarray(observation.state["eef_pos"], dtype=np.float64)
    return ActionChunk(
        actions=[Action(data=hold, meta={"request_stop": True, "stop_reason": "done"})]
    )


class TracePolicy(PolicyBase):
    """Oracle: replay the reference strokes, then declare done."""

    def __init__(self, *, reference: str = DEFAULT_REFERENCE, chunk_size: int = 8):
        self.spec = get_spec(reference)
        self.chunk_size = chunk_size
        self.info = PolicyInfo(
            name="sacpaint_trace",
            action_space=action_space(self.spec),
            observation_space=observation_space(self.spec),
        )
        self.config = PolicyConfig(action_horizon=chunk_size)
        self._queue: list[np.ndarray] = []

    def reset(self, scene: Scene) -> None:
        """Rebuild the stroke queue for a fresh trial, from the scene's own reference when it names one."""
        name = self.spec.name
        if scene.target is not None and scene.target.spec.get("reference"):
            name = str(scene.target.spec["reference"])
        self._queue = stroke_actions(get_spec(name))

    def act(self, observation: Observation) -> ActionChunk:
        """Emit the next chunk of pen targets; the last chunk carries the stop request."""
        if not self._queue:
            return _stop(observation)
        batch, self._queue = self._queue[: self.chunk_size], self._queue[self.chunk_size :]
        actions = [Action(data=a) for a in batch]
        if not self._queue:
            last = actions[-1]
            actions[-1] = Action(data=last.data, meta={"request_stop": True, "stop_reason": "done"})
        return ActionChunk(actions=actions, control_hz=10.0)


class IdlePolicy(PolicyBase):
    """Floor: draw nothing and declare done on the first decision."""

    def __init__(self, *, reference: str = DEFAULT_REFERENCE) -> None:
        spec = get_spec(reference)
        self.info = PolicyInfo(
            name="sacpaint_idle",
            action_space=action_space(spec),
            observation_space=observation_space(spec),
        )
        self.config = PolicyConfig(action_horizon=1)

    def act(self, observation: Observation) -> ActionChunk:
        """Hold position and stop."""
        return _stop(observation)


def trace_policy(**kwargs: Any) -> TracePolicy:
    """Registry factory for ``--policy sacpaint_trace``."""
    return TracePolicy(**kwargs)


def idle_policy(**kwargs: Any) -> IdlePolicy:
    """Registry factory for ``--policy sacpaint_idle``."""
    return IdlePolicy(**kwargs)
