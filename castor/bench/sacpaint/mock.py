"""A dependency-free plotter world, so the whole benchmark runs with no robot.

``PlotterEmbodiment`` is a pen over the reference's canvas (300 x 400 mm for
the built-in). Actions are absolute Cartesian targets (x, y, z) in metres in
the canvas frame: x to the right, y up the sheet, z above the paper. The pen
marks whenever z is at or below ``pen_down_z`` at both ends of a step, drawing
the straight segment between them. It renders two cameras: ``overhead`` (the
canvas) and ``reference`` (the fixed target image, so an image-reading policy
sees what it must draw). A real rig exposes the same two streams.

On a **colour** reference the action carries a fourth number, ``color``: the
palette index the stroke is inked in. The plotter draws in that colour and
serves a third stream, ``reference_color``, the picture reduced to the palette.
A mono reference is exactly what it was: three dimensions, black ink, two
streams. Colour is a property of the ink alone; no motion changes because of it.

With ``strokes=True`` the plotter also accepts the **stroke** primitive
(:mod:`castor.bench.sacpaint.strokes`): one call carrying up to 24 points,
executed as the same per-target sequence the plotter always drew, inked the
same way, with one observation at the end. The prompt is byte-identical to
every run before strokes existed when the option is off.

``TracePolicy`` is the oracle: it plays the reference strokes back, in the
commonest colour under each stroke when the reference has one. It is the
ceiling of the scorers, not a contestant, and with ``strokes=True`` it plays
them back as strokes, so it is the ceiling of the new primitive too.
``IdlePolicy`` declares done at once and is the floor.
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

from castor.bench.sacpaint import palette as pal
from castor.bench.sacpaint import strokes as stroke_lib
from castor.bench.sacpaint.reference import (
    DEFAULT_REFERENCE,
    ReferenceSpec,
    color_reference,
    get_spec,
    reference_image,
)
from castor.bench.sacpaint.scorers import CANONICAL_FLAG, OVERHEAD

REFERENCE_CAM = "reference"
#: Shown only on a colour task: the reference photograph quantised to the palette.
REFERENCE_COLOR_CAM = "reference_color"
PEN_DOWN_Z = 0.002
PEN_UP_Z = 0.005
Z_MAX = 0.05
_LABELS = ("x", "y", "z")
#: Unit string for a colour task's state field: three metres and one palette index.
STATE_UNIT_COLOR = "m for x, y, z; palette index for color"


def color_docs(spec: ReferenceSpec) -> str:
    """The palette paragraph appended to a colour task's prompt. Empty for a mono task.

    Colour is a property of the ink and of nothing else: no motion changes, no
    tool changes, no pen is swapped. On a real arm there is no pen at all in the
    virtual medium, so a colour is simply what the telemetry ink is drawn in.
    """
    if not spec.has_color:
        return ""
    return (
        " This is a colour task. Every target carries a fourth number, 'color', which is the "
        "palette index the stroke is laid down in: " + pal.describe() + ". It is rounded to the "
        "nearest index, it defaults to 0 (black), and it changes nothing about how the arm moves "
        "- it only says what colour the ink is. Choose a colour per stroke. The "
        f"'{REFERENCE_COLOR_CAM}' camera shows the picture reduced to exactly these colours, "
        "framed like the sheet, so you can read off which colour belongs where; the 'reference' "
        "camera still shows the original picture. Line accuracy and colour are scored separately."
    )


def _docs(spec: ReferenceSpec, strokes: bool = False) -> str:
    w, h = spec.canvas_mm
    return (
        f"You hold a pen over a white {w:.0f} x {h:.0f} mm portrait sheet. Targets are metres "
        f"in the canvas frame: x runs right across the sheet (0 to {w / 1000:.2f}), y runs up the "
        f"sheet (0 to {h / 1000:.2f}, so y=0 is the bottom edge), z is height above the paper "
        f"(0 to {Z_MAX:.2f}). The pen draws whenever z <= {PEN_DOWN_Z} for the whole segment; move "
        f"with z at {PEN_UP_Z} or higher to travel without marking. The 'overhead' camera shows "
        f"the sheet upright (image top = y {h / 1000:.2f}). The 'reference' camera shows the "
        "picture you must reproduce, framed exactly as the sheet is: its left, right, top and "
        "bottom edges are the sheet's edges." + color_docs(spec)
    ) + (stroke_lib.docs_paragraph(colored=spec.has_color) if strokes else "")


def action_space(spec: ReferenceSpec) -> Box:
    """The canvas-frame Cartesian action box shared by the mock and any real embodiment.

    A colour reference adds one dimension, ``color``: the palette index the
    stroke is inked in. A mono reference's box is exactly the three it has
    always been, so every existing task and policy is untouched. The colour
    dimension declares a ``max_step`` spanning the whole palette, because a
    per-step delta limit on a colour index would make the palette unreachable.
    """
    w, h = spec.canvas_mm
    if not spec.has_color:
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
    top = float(len(pal.NAMES) - 1)
    return Box(
        shape=(4,),
        low=np.array([0.0, 0.0, 0.0, 0.0]),
        high=np.array([w / 1000.0, h / 1000.0, Z_MAX, top]),
        semantics=ActionSemantics(
            control_mode="eef_abs_pose",
            frame="base",
            dim_labels=(*_LABELS, pal.COLOR_DIM_LABEL),
            max_step=(0.02, 0.02, Z_MAX, top),
        ),
    )


def observation_space(spec: ReferenceSpec) -> ObservationSpace:
    """The overhead at the canonical canvas size, the reference at its own size, plus the pen position.

    A colour reference declares one more camera, ``reference_color``: the same
    picture reduced to the palette, offered exactly the way the line reference
    is offered (a stream the policy can ask for on demand). Its ``eef_pos``
    carries a fourth number too, the palette index in force, because an
    absolute-target policy locates itself against a state field the same width
    as its action.
    """
    w, h = spec.canonical_size()
    rw, rh = spec.reference_size()
    cameras = [CameraSpec(OVERHEAD, h, w, 3), CameraSpec(REFERENCE_CAM, rh, rw, 3)]
    if spec.has_color:
        cameras.append(CameraSpec(REFERENCE_COLOR_CAM, h, w, 3))
    return ObservationSpace(
        cameras=tuple(cameras),
        state=StateSpec((StateField("eef_pos", (4,), STATE_UNIT_COLOR),))
        if spec.has_color
        else StateSpec((StateField("eef_pos", (3,), "m"),)),
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
        strokes: bool = False,
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
        self.strokes = bool(strokes)
        self.num_steps = 0
        #: One entry per target a stroke sent, so a stroke is auditable target by target.
        self.stroke_log: list[dict[str, Any]] = []
        self._stroke_id = 0
        self._last_observation: Observation | None = None
        self._space = action_space(self.spec)
        self._dim = self._space.dim
        self._low = np.asarray(self._space.low, dtype=np.float64)
        self._high = np.asarray(self._space.high, dtype=np.float64)
        self._eef = self._park()
        self.color = pal.DEFAULT_INDEX
        self._canvas = self._blank()
        self._instruction: str | None = None
        self._reference = reference_image(self.spec.name)
        self._reference_color = color_reference(self.spec.name) if self.spec.has_color else None
        self.info = EmbodimentInfo(
            name="sacpaint_plotter",
            action_space=action_space(self.spec),
            observation_space=observation_space(self.spec),
            control_hz=10.0,
            is_simulated=True,
            capabilities=frozenset({SEEDABLE, RESETTABLE, RENDERABLE}),
            supported_target_kinds=frozenset({"reference_drawing"}),
            docs=_docs(self.spec, self.strokes),
        )

    def _blank(self) -> np.ndarray:
        w, h = self.spec.canonical_size()
        return np.full((h, w, 3), 255, dtype=np.uint8)

    def _park(self) -> np.ndarray:
        """The pen up at the bottom-left corner, in black on a colour task."""
        park = [0.0, 0.0, PEN_UP_Z]
        return np.array(park + ([float(pal.DEFAULT_INDEX)] if self._dim == 4 else []))

    def _fit(self, data: Any) -> np.ndarray:
        """Accept a 3- or 4-vector, so a mono policy still drives a colour canvas in black."""
        arr = np.asarray(data, dtype=np.float64).reshape(-1)
        if arr.size == 3 and self._dim == 4:
            arr = np.append(arr, float(pal.DEFAULT_INDEX))
        if arr.size != self._dim:
            raise ValueError(f"action must have {self._dim} values, got {arr.size}")
        return np.clip(arr, self._low, self._high)

    def reset(self, scene: Scene, *, seed: int | None = None) -> Observation:
        """Fresh sheet, pen parked up at the bottom-left corner."""
        self._canvas = self._blank()
        self._eef = self._park()
        self.color = pal.DEFAULT_INDEX
        self._instruction = scene.instruction
        self.num_steps = 0
        self.stroke_log = []
        self._stroke_id = 0
        self._last_observation = None
        return self._observe()

    def step(self, action: Action) -> StepResult:
        """Move the pen in a straight line to the target, marking if down at both ends.

        A target that a stroke is still in the middle of gets the stroke's
        standing view back rather than a fresh one: a stroke is looked at once,
        at its end. Every target is still its own step, so the action log and
        the efficiency term count exactly what the arm did.
        """
        self._apply(self._fit(action.data))
        if self.strokes and stroke_lib.is_open(action.meta) and self._last_observation is not None:
            return StepResult(observation=self._last_observation, terminated=False)
        return StepResult(observation=self._observe(), terminated=False)

    def _apply(self, target: np.ndarray) -> None:
        """One per-target motion: mark if the pen was down at both ends, then count it."""
        if self._dim == 4:
            self.color = pal.index_of(target[3])
        if self._eef[2] <= self.pen_down_z and target[2] <= self.pen_down_z:
            p0 = self.spec.mm_to_px((self._eef[0] * 1000.0, self._eef[1] * 1000.0))
            p1 = self.spec.mm_to_px((target[0] * 1000.0, target[1] * 1000.0))
            # The canvas is stored RGB, so the palette's RGB triple is the right order here.
            cv2.line(self._canvas, p0, p1, pal.rgb_of(self.color), self.stroke_px)
        self._eef = target
        self.num_steps += 1

    def stroke(
        self,
        points: Any,
        color: str | int | float | None = None,
        *,
        stroke_id: int | None = None,
    ) -> StepResult:
        """Draw one polyline through ``points``, then look at the sheet once.

        The stroke is planned and checked first: a point off the sheet raises
        :class:`castor.bench.sacpaint.strokes.StrokeError` with the pen exactly
        where it was. What is executed afterwards is the ordinary per-target
        sequence, so nothing about how a segment is inked changes.
        """
        targets = stroke_lib.plan(
            points,
            low=self._low,
            high=self._high,
            pen_down_z=self.pen_down_z,
            travel_z=PEN_UP_Z,
            color=color if self._dim == 4 else None,
        )
        self._stroke_id = self._stroke_id + 1 if stroke_id is None else int(stroke_id)
        for index, target in enumerate(targets):
            self._apply(self._fit(target))
            self.stroke_log.append(
                {
                    **stroke_lib.meta_for(self._stroke_id, index, len(targets)),
                    "target": [float(v) for v in target],
                }
            )
        return StepResult(observation=self._observe(), terminated=False)

    def observe_parked(self) -> Observation:
        """Lift the pen clear of the sheet and return an unobstructed final view."""
        self._eef = self._park()
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
        images = {OVERHEAD: overhead, REFERENCE_CAM: self._reference.copy()}
        if self._reference_color is not None:
            images[REFERENCE_COLOR_CAM] = self._reference_color.copy()
            extra["palette"] = list(pal.NAMES)
            extra["color"] = pal.name_of(self.color)
        observation = Observation(
            images=images,
            state={"eef_pos": self._eef.copy()},
            instruction=self._instruction,
            extra=extra,
        )
        self._last_observation = observation
        return observation


def _flag(value: Any) -> Any:
    """Coerce a ``-E``/``-P`` string into a bool; anything else is passed through."""
    if not isinstance(value, str):
        return value
    text = value.strip().lower()
    if text not in ("true", "false", "1", "0", "yes", "no"):
        raise ValueError(f"strokes must be true or false, got {value!r}")
    return text in ("true", "1", "yes")


def plotter_embodiment(**kwargs: Any) -> PlotterEmbodiment:
    """Registry factory for ``--embodiment sacpaint_plotter`` (``-E reference=NAME -E photo_mode=sheet``)."""
    if "strokes" in kwargs:
        kwargs["strokes"] = _flag(kwargs["strokes"])
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


def stroke_color(spec: ReferenceSpec, stroke: list[tuple[float, float]]) -> int:
    """The palette index a stroke should be laid down in: the commonest colour under it.

    Used by the oracle policy to give the colour metric something to calibrate
    against. It reads the stored colour reference, which is the same image the
    policy is offered on the ``reference_color`` camera, so it is not privileged
    information, only a diligent reading of it.
    """
    if not spec.has_color:
        return pal.DEFAULT_INDEX
    indices = pal.quantize(spec.color_reference_image(canonical=False))
    h, w = indices.shape
    mm_w, mm_h = spec.canvas_mm
    pad = 3  # a stroke belongs to the area it outlines, not to the pixel under it
    seen: list[int] = []
    for i, (x, y) in enumerate(stroke):
        points = [(x, y)]
        if i:  # sample the segment, not just its ends
            px, py = stroke[i - 1]
            points = [(px + (x - px) * t / 4.0, py + (y - py) * t / 4.0) for t in range(1, 5)]
        for sx, sy in points:
            col = min(max(int(sx / mm_w * w), 0), w - 1)
            row = min(max(int((1.0 - sy / mm_h) * h), 0), h - 1)
            top, left = max(row - pad, 0), max(col - pad, 0)
            window = indices[top : row + pad + 1, left : col + pad + 1]
            seen.extend(window.reshape(-1).tolist())
    if not seen:
        return pal.DEFAULT_INDEX
    return int(np.bincount(seen, minlength=len(pal.NAMES)).argmax())


def stroke_action_groups(
    spec: ReferenceSpec, pen_down_z: float = 0.0, max_xy: float = 0.014, max_z: float = 0.0024
) -> list[list[np.ndarray]]:
    """The same pen-up / pen-down targets as :func:`stroke_actions`, one list per reference stroke.

    Grouping is the only difference: concatenating the groups gives back
    exactly the flat list, target for target, so an oracle that draws stroke by
    stroke draws the same picture as one that draws target by target.
    """
    colored = spec.has_color
    out: list[list[np.ndarray]] = []
    here = np.array([0.0, 0.0, PEN_UP_Z])
    for group in spec.strokes.values():
        for stroke in group:
            pts = [np.array([x / 1000.0, y / 1000.0]) for x, y in stroke]
            targets = [np.array([*pts[0], PEN_UP_Z]), np.array([*pts[0], pen_down_z])]
            targets += [np.array([*p, pen_down_z]) for p in pts[1:]]
            targets.append(np.array([*pts[-1], PEN_UP_Z]))
            color = float(stroke_color(spec, stroke)) if colored else None
            one: list[np.ndarray] = []
            for t in targets:
                for step in _split(here, t, max_xy, max_z):
                    one.append(step if color is None else np.append(step, color))
                here = t
            out.append(one)
    return out


def stroke_actions(
    spec: ReferenceSpec, pen_down_z: float = 0.0, max_xy: float = 0.014, max_z: float = 0.0024
) -> list[np.ndarray]:
    """Turn a reference's strokes into a pen-up / pen-down action list, split to the delta limits.

    On a colour reference every target carries a fourth number, the palette
    index for the stroke it belongs to; on a mono reference the vectors are the
    three they have always been.
    """
    return [
        action for group in stroke_action_groups(spec, pen_down_z, max_xy, max_z) for action in group
    ]


def _stop(observation: Observation, dim: int = 3) -> ActionChunk:
    hold = np.asarray(observation.state["eef_pos"], dtype=np.float64).reshape(-1)[:dim]
    if hold.size < dim:  # a body that reports only x, y, z on a colour task
        hold = np.append(hold, float(pal.DEFAULT_INDEX))
    return ActionChunk(
        actions=[Action(data=hold, meta={"request_stop": True, "stop_reason": "done"})]
    )


class TracePolicy(PolicyBase):
    """Oracle: replay the reference strokes, then declare done.

    With ``strokes=True`` it plays each reference stroke back as one stroke —
    one call, every target of that stroke, the stroke's id on every one of them
    — so it is the ceiling of the stroke primitive as well as of the per-target
    action. The targets themselves are identical either way, which is what
    makes the two ceilings comparable.
    """

    def __init__(
        self, *, reference: str = DEFAULT_REFERENCE, chunk_size: int = 8, strokes: bool = False
    ):
        self.spec = get_spec(reference)
        self.chunk_size = chunk_size
        self.strokes = bool(strokes)
        self.info = PolicyInfo(
            name="sacpaint_trace",
            action_space=action_space(self.spec),
            observation_space=observation_space(self.spec),
        )
        self.config = PolicyConfig(action_horizon=chunk_size)
        self._queue: list[np.ndarray] = []
        self._groups: list[list[np.ndarray]] = []
        self._stroke_id = 0

    def reset(self, scene: Scene) -> None:
        """Rebuild the stroke queue for a fresh trial, from the scene's own reference when it names one."""
        name = self.spec.name
        if scene.target is not None and scene.target.spec.get("reference"):
            name = str(scene.target.spec["reference"])
        if self.strokes:
            self._groups = stroke_action_groups(get_spec(name))
            self._stroke_id = 0
            self._queue = []
            return
        self._queue = stroke_actions(get_spec(name))

    def act(self, observation: Observation) -> ActionChunk:
        """Emit the next chunk of pen targets; the last chunk carries the stop request."""
        if self.strokes:
            return self._act_stroke(observation)
        if not self._queue:
            return _stop(observation, self.info.action_space.dim)
        batch, self._queue = self._queue[: self.chunk_size], self._queue[self.chunk_size :]
        actions = [Action(data=a) for a in batch]
        if not self._queue:
            last = actions[-1]
            actions[-1] = Action(data=last.data, meta={"request_stop": True, "stop_reason": "done"})
        return ActionChunk(actions=actions, control_hz=10.0)

    def _act_stroke(self, observation: Observation) -> ActionChunk:
        """One whole reference stroke per call, every target tagged with the stroke's id."""
        if not self._groups:
            return _stop(observation, self.info.action_space.dim)
        batch, self._groups = self._groups[0], self._groups[1:]
        self._stroke_id += 1
        total = len(batch)
        actions = [
            Action(data=a, meta=stroke_lib.meta_for(self._stroke_id, i, total))
            for i, a in enumerate(batch)
        ]
        if not self._groups:
            last = actions[-1]
            actions[-1] = Action(
                data=last.data,
                meta={**last.meta, "request_stop": True, "stop_reason": "done"},
            )
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
        return _stop(observation, self.info.action_space.dim)


def trace_policy(**kwargs: Any) -> TracePolicy:
    """Registry factory for ``--policy sacpaint_trace`` (``-P strokes=true`` for the stroke oracle)."""
    if "strokes" in kwargs:
        kwargs["strokes"] = _flag(kwargs["strokes"])
    return TracePolicy(**kwargs)


def idle_policy(**kwargs: Any) -> IdlePolicy:
    """Registry factory for ``--policy sacpaint_idle``."""
    return IdlePolicy(**kwargs)
