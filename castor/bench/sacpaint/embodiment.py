"""``OpenCastorEmbodiment`` — Bob's SO-ARM101 as a sacpaint body.

The pen is bolted to the wrist, the sheet is taped to the desk, and the policy
still speaks the same canvas-frame metres the mock plotter speaks. Three things
sit between those two facts:

* :mod:`castor.bench.sacpaint.calibration` turns canvas metres into arm-base
  millimetres through a transform taught from the sheet corners;
* :mod:`castor.bench.sacpaint.gateway` puts every one of those millimetre targets
  through the robot-md-gateway, so each stroke leaves an Ed25519-signed receipt
  and a refusal is a signed promise that nothing moved;
* :mod:`castor.bench.sacpaint.cameras` fetches the overhead picture from whatever
  is looking at the sheet — Bob's console, the carbot phone bridge, or the
  OpenCastor iOS app.

What this adapter refuses to do is as much the point as what it does. A gateway
deny raises ``SafetyAbort`` rather than continuing blind. A camera that is down
raises ``EmbodimentFault`` rather than scoring a blank sheet. An arm that
reports it did not reach raises rather than drawing the next stroke from a
position nobody knows. And it never sets ``canonical_canvas``: a photograph of
a sheet is not a canonical canvas, and saying so would skip the rectification
that makes the score mean anything.

The overhead frame carries ``extra["canvas_corners"]`` whenever the corners are
known — four normalised ``(x, y)`` pairs in TL TR BR BL order, as tapped in the
phone app — which is the scorer's first and best rectification route.

**Media.** ``-E medium=pen`` (default) is the benchmark proper: a pen, a sheet,
a camera. ``-E medium=virtual`` is for a rig with no paper and no pen: the arm
makes every motion for real through the gateway, and the "ink" is drawn from
where the arm *measured* its tip after each pen-down move. That frame is a
canonical canvas (nothing to rectify), and every score it produces is labelled
``medium=virtual``: its own leaderboard category, never ranked against paper. Pair it with
``-E calibration=easel``, an upright sheet the arm can reach.

**Colour.** When the reference carries a colour target, the action gains a
fourth number, the palette index the virtual ink is drawn in, and a third
camera, ``reference_color``, serves the picture reduced to that palette. The arm
is not involved: it holds no pen, it changes no colour, and every gateway call
carries the same three millimetre coordinates it carried before. The colour is
recorded in the observation, in the progress file the console reads, and beside
each retained receipt under ``sacpaint_ink``; it is never added to the signed
envelope, because nothing the gateway attested to has a colour in it.

**Strokes.** ``-E strokes=true`` adds the stroke primitive
(:mod:`castor.bench.sacpaint.strokes`) beside the per-target action: one call
carrying up to 24 points on the sheet, executed as the very targets this
adapter already sends. Nothing reaches the wire differently: one gateway call
per target, three millimetre coordinates, one receipt, the same tolerance and
the same miss handling. Nothing is scored differently either. What batches is the
policy's turn and the photograph: the sheet is looked at once, at the end of
the stroke, instead of once per corner. The option is off by default, and with
it off the prompt is byte-identical to every run before strokes existed.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from inspect_robots.embodiment import SELF_PACED, EmbodimentInfo
from inspect_robots.errors import ConfigError, EmbodimentFault
from inspect_robots.scene import Scene
from inspect_robots.spaces import (
    ActionSemantics,
    Box,
    CameraSpec,
    ObservationSpace,
    StateField,
    StateSpec,
)
from inspect_robots.types import Action, Observation, StepResult

from castor.bench.sacpaint import calibration as calib
from castor.bench.sacpaint import palette as pal
from castor.bench.sacpaint import strokes as stroke_lib
from castor.bench.sacpaint.cameras import (
    FrameSource,
    HttpCornerSource,
    HttpFrameSource,
    StaticCornerSource,
    StaticFrameSource,
    parse_corner_flag,
)
from castor.bench.sacpaint.gateway import (
    MOTION_SCOPE,
    OBSERVE_SCOPE,
    GatewayClient,
    GatewayMiss,
    load_pair_payload,
    read_eef_mm,
    read_reached,
)

logger = logging.getLogger(__name__)

OVERHEAD = "overhead"
REFERENCE_CAM = "reference"
#: Served only on a colour task: the picture reduced to the pen palette.
REFERENCE_COLOR_CAM = "reference_color"
#: The scorer's preferred rectification input: four normalised TL TR BR BL corners.
CORNERS_KEY = "canvas_corners"
#: Set only by the virtual medium. A photograph is not a canonical canvas; telemetry ink is.
CANONICAL_FLAG = "canonical_canvas"
#: ``observation.extra["medium"]``: ``pen`` (paper, the real thing) or ``virtual`` (telemetry ink).
MEDIUM_KEY = "medium"
#: ``observation.extra`` keys a colour task adds: the palette, and the colour last commanded.
PALETTE_KEY = "palette"
#: How many misses a run keeps with their positions (the count is always kept).
MAX_MISS_LOG = 500
COLOR_KEY = "color"
MEDIUM_PEN = "pen"
MEDIUM_VIRTUAL = "virtual"
MEDIA = (MEDIUM_PEN, MEDIUM_VIRTUAL)
#: ``-E calibration=easel``: the virtual upright sheet from :func:`calibration.easel`.
EASEL = "easel"

#: Canvas-frame heights, metres. Identical to the mock's, because the contract is.
PEN_DOWN_Z = 0.002
PEN_UP_Z = 0.005
Z_MAX = 0.05
_LABELS = ("x", "y", "z")
#: Unit string for a colour task's state field: three metres and one palette index.
STATE_UNIT_COLOR = "m for x, y, z; palette index for color"

#: Defaults chosen from the rig as it stands: Bob's gateway on 8080, his console
#: on 8002. Both are overridable, and ``pair_payload`` fills them in one flag.
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080"
DEFAULT_CONSOLE_URL = "http://127.0.0.1:8002"
DEFAULT_ACTUATOR = "so-arm101"
DEFAULT_MOVE_TOOL = "arm.move_to"
DEFAULT_STATE_TOOL = "arm.state"
DEFAULT_HOME_TOOL = "arm.home"
_FALLBACK_CANVAS_MM = (300.0, 400.0)
_FALLBACK_REFERENCE = "sacramento-line-v0"


# -- reference / space plumbing ----------------------------------------------


def _default_reference_name() -> str:
    """The reference this benchmark draws when the operator names none."""
    from castor.bench.sacpaint import reference as ref

    return str(getattr(ref, "DEFAULT_REFERENCE", _FALLBACK_REFERENCE))


def _reference_image(name: str) -> np.ndarray:
    """Load a named reference, tolerating a ``reference_image()`` that predates names."""
    from castor.bench.sacpaint import reference as ref

    try:
        takes_name = bool(inspect.signature(ref.reference_image).parameters)
    except (TypeError, ValueError):  # pragma: no cover - builtins only
        takes_name = False
    if takes_name:
        return ref.reference_image(name)
    if name != _default_reference_name():
        raise ConfigError(
            f"this sacpaint build has a single built-in reference and cannot serve {name!r}; "
            "drop -E reference or upgrade sacpaint"
        )
    return ref.reference_image()


def _canvas_mm(name: str) -> tuple[float, float]:
    """The sheet's width and height in millimetres for the named reference."""
    from castor.bench.sacpaint import reference as ref

    get_spec = getattr(ref, "get_spec", None)
    if callable(get_spec):
        return tuple(float(v) for v in get_spec(name).canvas_mm)  # type: ignore[return-value]
    return tuple(float(v) for v in getattr(ref, "CANVAS_MM", _FALLBACK_CANVAS_MM))  # type: ignore[return-value]


def _reference_size(name: str) -> tuple[int, int]:
    """Size (width, height) of the image served on the ``reference`` camera (a photo's native size)."""
    from castor.bench.sacpaint import reference as ref

    get_spec = getattr(ref, "get_spec", None)
    if callable(get_spec):
        spec = get_spec(name)
        size = getattr(spec, "reference_size", None)
        return tuple(int(v) for v in (size() if callable(size) else spec.canonical_size()))  # type: ignore[return-value]
    return _canonical_size(name)


def _has_color(name: str) -> bool:
    """True when the named reference carries a colour target beside its skeleton."""
    from castor.bench.sacpaint import reference as ref

    get_spec = getattr(ref, "get_spec", None)
    if not callable(get_spec):  # pragma: no cover - a sacpaint build older than colour
        return False
    return bool(getattr(get_spec(name), "has_color", False))


def _color_reference_image(name: str) -> np.ndarray:
    """The colour target on the canonical canvas, for the ``reference_color`` stream."""
    from castor.bench.sacpaint import reference as ref

    return ref.color_reference(name)


def _canonical_size(name: str) -> tuple[int, int]:
    """The canonical canvas image size (width, height) in pixels."""
    from castor.bench.sacpaint import reference as ref

    get_spec = getattr(ref, "get_spec", None)
    if callable(get_spec):
        return tuple(int(v) for v in get_spec(name).canonical_size())  # type: ignore[return-value]
    return tuple(int(v) for v in ref.canonical_size())  # type: ignore[return-value]


def action_space(canvas_mm: tuple[float, float], colored: bool = False) -> Box:
    """The canvas-frame Cartesian action box: the same contract the mock declares.

    A colour task adds a fourth dimension, ``color``, the palette index the ink
    is drawn in. The arm does not see it: the gateway call is the same three
    millimetre coordinates it always was, and nothing about the motion changes.
    A mono task's box is exactly the three dimensions it has always been.
    """
    width, height = canvas_mm
    if not colored:
        return Box(
            shape=(3,),
            low=np.array([0.0, 0.0, 0.0]),
            high=np.array([width / 1000.0, height / 1000.0, Z_MAX]),
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
        high=np.array([width / 1000.0, height / 1000.0, Z_MAX, top]),
        semantics=ActionSemantics(
            control_mode="eef_abs_pose",
            frame="base",
            dim_labels=(*_LABELS, pal.COLOR_DIM_LABEL),
            # The whole palette in one step: a delta limit on a colour index
            # would make most of the palette unreachable.
            max_step=(0.02, 0.02, Z_MAX, top),
        ),
    )


def observation_space(
    canonical_wh: tuple[int, int],
    overhead_wh: tuple[int, int],
    color_wh: tuple[int, int] | None = None,
) -> ObservationSpace:
    """Two cameras plus the pen position, and a third on a colour task.

    The overhead spec is the *camera's* size; ``reference_color`` is the colour
    target on the canonical canvas, offered the same way the line reference is.
    A colour task's ``eef_pos`` carries a fourth number, the palette index in
    force, because an absolute-target policy locates itself against a state
    field the same width as its action.
    """
    ref_w, ref_h = canonical_wh
    over_w, over_h = overhead_wh
    cameras = [
        CameraSpec(OVERHEAD, over_h, over_w, 3),
        CameraSpec(REFERENCE_CAM, ref_h, ref_w, 3),
    ]
    if color_wh is not None:
        cameras.append(CameraSpec(REFERENCE_COLOR_CAM, color_wh[1], color_wh[0], 3))
    return ObservationSpace(
        cameras=tuple(cameras),
        state=StateSpec((StateField("eef_pos", (4,), STATE_UNIT_COLOR),))
        if color_wh is not None
        else StateSpec((StateField("eef_pos", (3,), "m"),)),
    )


def _docs(canvas_mm: tuple[float, float]) -> str:
    width, height = canvas_mm
    return (
        f"You hold a pen fixed to a robot wrist over a white {width:.0f} x {height:.0f} mm "
        "portrait sheet. Targets are metres in the canvas frame: x runs right across the "
        f"sheet (0 to {width / 1000:.2f}), y runs up the sheet (0 to {height / 1000:.2f}, so "
        f"y=0 is the bottom edge), z is height above the paper (0 to {Z_MAX:.2f}). The pen "
        f"draws whenever z <= {PEN_DOWN_Z} for the whole segment; move with z at {PEN_UP_Z} "
        "or higher to travel without marking. Each target is a real arm motion that takes "
        "time, so prefer long strokes to many tiny ones. The 'overhead' camera is a "
        "photograph of the real sheet, at an angle, with the arm sometimes in shot; the "
        "'reference' camera shows the drawing you must reproduce."
    )


def _virtual_docs(canvas_mm: tuple[float, float]) -> str:
    width, height = canvas_mm
    return (
        f"You move a robot arm's tip over an imaginary upright {width:.0f} x {height:.0f} mm sheet: "
        "there is no paper and no pen, and the 'overhead' image is drawn from where the arm "
        "measured its tip after each move, so it is exact and never obstructed. Targets are metres "
        f"in the canvas frame: x runs right across the sheet (0 to {width / 1000:.2f}), y runs up "
        f"the sheet (0 to {height / 1000:.2f}, so y=0 is the bottom edge), z is height off the "
        f"sheet (0 to {Z_MAX:.2f}). A segment is inked when both its ends were commanded at "
        f"z <= {PEN_DOWN_Z}; move with z at {PEN_UP_Z} or higher to travel without marking. Each "
        "target is a real arm motion that takes time, so prefer long strokes to many tiny ones. "
        "The 'reference' camera shows the picture you must reproduce."
    )


def _color_docs() -> str:
    """The palette paragraph a colour task appends. The arm is not mentioned, on purpose.

    Nothing about the motion changes with the colour: the arm holds no pen, no
    cartridge is swapped, and the gateway call carries the same three
    coordinates it always carried. The colour is a property of the virtual ink
    and of nothing else.
    """
    return (
        " This is a colour task. Every target carries a fourth number, 'color', the palette index "
        "the stroke is inked in: " + pal.describe() + ". It is rounded to the nearest index and "
        "defaults to 0 (black). It changes nothing about how the arm moves - the arm holds no pen "
        "and swaps no colour - it only says what colour the ink is drawn in. Choose a colour per "
        f"stroke. The '{REFERENCE_COLOR_CAM}' camera shows the picture reduced to exactly these "
        "colours, framed like the sheet, so you can read off which colour belongs where; the "
        "'reference' camera still shows the original picture. Line accuracy and colour are scored "
        "separately, so drawing the right shapes in the wrong colours still scores the shapes."
    )


def _plan_docs(budget: int) -> str:
    """The planning paragraph a run with a known call budget appends.

    Off by default so the packaged benchmark prompt is byte-identical to every
    run before it; the console turns it on when the operator's profile caps the
    model calls, because a policy that knows its budget lays the whole sheet
    out before it spends the budget on detail.
    """
    return (
        f" You have about {int(budget)} model calls for this drawing, and each call may issue "
        "many targets, so batch strokes. Plan the whole sheet before you draw: place the largest "
        "shapes across the entire canvas first, edge to edge, then return for detail with "
        "whatever calls remain. Do not stop while a region of the sheet is still empty."
    )


class VirtualInk:
    """A canonical canvas inked from measured tip positions: the ``virtual`` medium's sheet.

    The colour a segment is drawn in comes from the policy's action; it is a
    property of this canvas and of nothing on the robot.
    """

    def __init__(
        self, canonical_wh: tuple[int, int], canvas_mm: tuple[float, float], stroke_px: int = 3
    ) -> None:
        self._w, self._h = int(canonical_wh[0]), int(canonical_wh[1])
        self._px_per_m = 1000.0 * self._w / float(canvas_mm[0])
        self._stroke_px = int(stroke_px)
        self._canvas = self._blank()
        self.segments = 0

    def _blank(self) -> np.ndarray:
        return np.full((self._h, self._w, 3), 255, dtype=np.uint8)

    def clear(self) -> None:
        self._canvas = self._blank()
        self.segments = 0

    def _px(self, canvas_m: np.ndarray) -> tuple[int, int]:
        col = round(float(canvas_m[0]) * self._px_per_m)
        row = round(self._h - float(canvas_m[1]) * self._px_per_m)
        return min(max(col, 0), self._w - 1), min(max(row, 0), self._h - 1)

    def segment(
        self, a_m: np.ndarray, b_m: np.ndarray, color: str | int | float | None = None
    ) -> None:
        """Ink the straight segment between two measured tip positions (canvas metres)."""
        # The canvas is stored RGB, so the palette's RGB triple is the right order here.
        cv2.line(self._canvas, self._px(a_m), self._px(b_m), pal.rgb_of(color), self._stroke_px)
        self.segments += 1

    def image(self) -> np.ndarray:
        return self._canvas.copy()


# -- the embodiment ----------------------------------------------------------


class OpenCastorEmbodiment:
    """A pen on Bob's SO-ARM101, driven through the robot-md-gateway."""

    def __init__(
        self,
        *,
        # gateway
        gateway_url: str | None = None,
        manifest_path: str | None = None,
        manifest_kid: str | None = None,
        ruri: str | None = None,
        actuator_name: str | None = DEFAULT_ACTUATOR,
        token: str | None = None,
        token_env: str = "ROBOT_MD_TOKEN",
        pair_payload: str | None = None,
        timeout_s: float = 90.0,  # a worst-case arm.reach_point (25 servo steps + a walk-back) is ~45 s
        move_tool: str = DEFAULT_MOVE_TOOL,
        state_tool: str | None = DEFAULT_STATE_TOOL,
        home_tool: str = DEFAULT_HOME_TOOL,
        move_args: str = "move_to",
        speed: float | None = None,
        tolerance_mm: float = 3.0,
        strict_reach: bool = True,
        llm_budget: int | None = None,
        strokes: bool = False,
        # geometry
        calibration: str | None = None,
        reference: str | None = None,
        medium: str = MEDIUM_PEN,
        easel_distance_mm: float = calib.EASEL_DISTANCE_MM,
        easel_elevation_deg: float = calib.EASEL_ELEVATION_DEG,
        easel_azimuth_deg: float = calib.EASEL_AZIMUTH_DEG,
        pen_down_z: float = PEN_DOWN_Z,
        travel_z: float = PEN_UP_Z,
        park_x: float = 0.0,
        park_y: float = 0.0,
        park_z: float = 0.03,
        # cameras
        overhead_url: str | None = None,
        overhead_prime_url: str | None = None,
        camera_token: str | None = None,
        camera_token_env: str = "CONSOLE_TOKEN",
        camera_timeout_s: float = 5.0,
        corners_url: str | None = None,
        canvas_corners: str | Sequence[Sequence[float]] | None = None,
        # operator + logs
        no_prompt: bool = False,
        receipts_dir: str | None = None,
        # a console that started this run reads these (castor/console/paint.py)
        progress_path: str | None = None,
        canvas_post_url: str | None = None,
        canvas_post_token_env: str = "CONSOLE_TOKEN",
        # seams (tests inject these; the CLI never does)
        client: GatewayClient | None = None,
        overhead_source: FrameSource | None = None,
        reference_source: FrameSource | None = None,
        corner_source: Any = None,
        input_fn: Callable[[str], str] | None = None,
        isatty_fn: Callable[[], bool] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        payload = load_pair_payload(pair_payload) if pair_payload else {}

        self.reference_name = reference or _default_reference_name()
        self.canvas_mm = _canvas_mm(self.reference_name)
        self._canonical_wh = _canonical_size(self.reference_name)
        self._reference_wh = _reference_size(self.reference_name)
        self.colored = _has_color(self.reference_name)

        if medium not in MEDIA:
            raise ConfigError(f"-E medium must be one of {MEDIA}, got {medium!r}")
        self.medium = medium
        self._easel = (
            float(easel_distance_mm),
            float(easel_elevation_deg),
            float(easel_azimuth_deg),
        )
        self._calibration = self._resolve_calibration(calibration)
        self._ink: VirtualInk | None = (
            VirtualInk(self._canonical_wh, self.canvas_mm) if medium == MEDIUM_VIRTUAL else None
        )

        self.pen_down_z = float(pen_down_z)
        self.travel_z = float(travel_z)
        self._park = np.array([float(park_x), float(park_y), float(park_z)])
        if self.travel_z <= self.pen_down_z:
            raise ConfigError(
                f"travel_z ({self.travel_z}) must be above pen_down_z ({self.pen_down_z}), "
                "or every travel move would draw"
            )

        self.move_tool = move_tool
        self.state_tool = state_tool or None
        self.home_tool = home_tool
        if move_args not in ("move_to", "reach_point"):
            raise ConfigError(
                f"move_args must be 'move_to' or 'reach_point', got {move_args!r}. Use "
                "'reach_point' against a gateway whose cartesian tool is arm.reach_point."
            )
        self.move_args = move_args
        self.speed = speed
        self.tolerance_mm = float(tolerance_mm)
        self.strict_reach = bool(strict_reach)

        self.client = (
            client
            if client is not None
            else GatewayClient(
                gateway_url or payload.get("gateway_url") or DEFAULT_GATEWAY_URL,
                token=self._resolve_token(token, token_env, payload.get("bearer")),
                manifest_path=manifest_path or payload.get("manifest_path"),
                manifest_kid=manifest_kid,
                ruri=ruri or payload.get("ruri") or _default_ruri(),
                actuator_name=actuator_name or None,
                timeout_s=timeout_s,
            )
        )

        cam_token = camera_token or os.environ.get(camera_token_env) or payload.get("console_token")
        console_url = str(payload.get("console_url") or DEFAULT_CONSOLE_URL).rstrip("/")
        self.overhead: FrameSource | None
        if self._ink is not None and overhead_source is None and not overhead_url:
            self.overhead = None  # the virtual medium has no sheet to photograph
        else:
            self.overhead = overhead_source or HttpFrameSource(
                overhead_url or f"{console_url}/camera/{OVERHEAD}/snapshot",
                name=OVERHEAD,
                token=cam_token,
                timeout_s=camera_timeout_s,
                prime_url=overhead_prime_url,
            )
        self.reference_camera = reference_source or StaticFrameSource(
            _reference_image(self.reference_name), name=REFERENCE_CAM
        )
        self.color_reference_camera: FrameSource | None = (
            StaticFrameSource(
                _color_reference_image(self.reference_name), name=REFERENCE_COLOR_CAM
            )
            if self.colored
            else None
        )
        self._corner_source = self._resolve_corners(
            corner_source, canvas_corners, corners_url, cam_token, camera_timeout_s
        )
        self._corners: tuple[tuple[float, float], ...] | None = None

        self.no_prompt = bool(no_prompt)
        self._input_fn = input_fn
        self._isatty_fn: Callable[[], bool] = isatty_fn or sys.stdin.isatty
        self._sleep: Callable[[float], None] = sleep_fn or time.sleep
        self._session: Any = None
        self._envelope: Any = None

        self.receipts_dir = receipts_dir
        self._progress_path = Path(progress_path).expanduser() if progress_path else None
        self._canvas_post_url = canvas_post_url or None
        self._canvas_post_token = os.environ.get(canvas_post_token_env) or cam_token
        self._started_at = time.time()
        self._receipts_written = False
        self._trial: tuple[str, int] | None = None
        self._move_receipt: int | None = None

        self.strokes = bool(strokes)
        self.num_steps = 0
        self.misses = 0
        self.miss_log: list[dict[str, Any]] = []
        #: One entry per target a stroke sent, so a stroke is auditable target by target.
        self.stroke_log: list[dict[str, Any]] = []
        self._stroke_id = 0
        self._stroke: int | None = None
        self._last_observation: Observation | None = None
        self._instruction: str | None = None
        self._eef = np.array([0.0, 0.0, self.travel_z])
        self._commanded = np.array([0.0, 0.0, self.travel_z])
        self.color = pal.DEFAULT_INDEX

        docs = _docs(self.canvas_mm) if self._ink is None else _virtual_docs(self.canvas_mm)
        if self.colored:
            docs += _color_docs()
        self.llm_budget = int(llm_budget) if llm_budget else None
        if self.llm_budget:
            docs += _plan_docs(self.llm_budget)
        if self.strokes:
            docs += stroke_lib.docs_paragraph(colored=self.colored)
        self.info = EmbodimentInfo(
            name="opencastor" if self.medium == MEDIUM_PEN else f"opencastor-{self.medium}",
            action_space=action_space(self.canvas_mm, self.colored),
            observation_space=observation_space(
                self._reference_wh,
                self._overhead_wh(),
                self._canonical_wh if self.colored else None,
            ),
            control_hz=None,
            is_simulated=False,
            capabilities=frozenset({SELF_PACED}),
            supported_target_kinds=frozenset({"reference_drawing"}),
            docs=docs,
        )

    # -- construction helpers ---------------------------------------------

    @staticmethod
    def _resolve_token(token: str | None, token_env: str, payload_token: Any) -> str | None:
        """Bearer precedence: explicit flag, then environment, then the pairing payload."""
        if token:
            return token
        from_env = os.environ.get(token_env)
        if from_env:
            return from_env
        return str(payload_token) if payload_token else None

    def _resolve_calibration(
        self, path: str | calib.CanvasCalibration | None
    ) -> calib.CanvasCalibration:
        """Load the taught transform, refusing to move without one."""
        if isinstance(path, calib.CanvasCalibration):  # injected in tests
            return path
        if path == EASEL:
            if self.medium != MEDIUM_VIRTUAL:
                raise ConfigError(
                    "-E calibration=easel describes a sheet that is not there, so it is only allowed "
                    "with -E medium=virtual. With a real pen, teach the real sheet: "
                    "`python -m castor.bench.sacpaint.calibrate --out canvas.json`."
                )
            distance_mm, elevation_deg, azimuth_deg = self._easel
            return calib.easel(
                self.reference_name,
                distance_mm=distance_mm,
                elevation_deg=elevation_deg,
                azimuth_deg=azimuth_deg,
            )
        if not path:
            raise calib.CalibrationError(
                "no canvas calibration: this arm cannot know where the sheet is. Teach one "
                "with `python -m castor.bench.sacpaint.calibrate --out canvas.json` and pass "
                "-E calibration=canvas.json"
            )
        return calib.load(path)

    def _resolve_corners(
        self,
        corner_source: Any,
        literal: str | Sequence[Sequence[float]] | None,
        url: str | None,
        token: str | None,
        timeout_s: float,
    ) -> Any:
        """Corners come from an injected source, a literal flag, a URL, or nowhere."""
        if corner_source is not None:
            return corner_source
        if literal is not None:
            if isinstance(literal, str):
                return StaticCornerSource(parse_corner_flag(literal))
            return StaticCornerSource(literal)
        if url:
            return HttpCornerSource(url, token=token, timeout_s=timeout_s)
        return None

    def _overhead_wh(self) -> tuple[int, int]:
        """Declared overhead size. Unknown until a frame arrives, so declare the canonical one."""
        return self._canonical_wh

    # -- optional core hooks ----------------------------------------------

    def bind_task(self, envelope: Any) -> None:
        """Learn the rollout horizon so the operator can see how long this will take."""
        self._envelope = envelope
        steps = getattr(envelope, "max_steps", None)
        name = getattr(envelope, "name", "?")
        if steps:
            self._say(f"task {name}: up to {steps} arm moves this trial")

    def connect_operator_session(self, session: Any) -> None:
        """Accept the framework console. From here on we neither print nor read stdin ourselves."""
        self._session = session

    def on_trial_start(self, scene_id: str, epoch: int, log_dir: str, run_id: str) -> None:
        """Duck-typed: the core offers this to policies only, but a wrapper may call it."""
        self._trial = (scene_id, epoch)

    def on_trial_end(self, record: Any, log_dir: str, run_id: str) -> None:
        """Duck-typed: write the run's receipts beside the eval log if anyone calls it."""
        scene_id, epoch = self._trial or (
            getattr(record, "scene_id", "trial"),
            getattr(record, "epoch", 0),
        )
        name = f"{str(scene_id).replace('/', '_')}-epoch{epoch}.jsonl"
        self.write_receipts(Path(log_dir) / "receipts" / str(run_id) / name)

    # -- lifecycle ---------------------------------------------------------

    def reset(self, scene: Scene, *, seed: int | None = None) -> Observation:
        """Gate on the operator, home the arm, then look at the fresh sheet.

        The gate comes *before* the home command on purpose: homing is motion,
        and nobody's hand should be near the arm when an unattended eval starts
        its first trial.
        """
        self._wait_ready()
        self.client.invoke(self.home_tool, {}, scope=MOTION_SCOPE)
        self._eef = self._read_eef(default=np.array([0.0, 0.0, self.travel_z]))
        self._commanded = np.array([0.0, 0.0, self.travel_z])
        self._instruction = scene.instruction
        self.num_steps = 0
        self._corners = None
        self.stroke_log = []
        self._stroke_id = 0
        self._stroke = None
        self._last_observation = None
        self.color = pal.DEFAULT_INDEX
        if self._ink is not None:
            self._ink.clear()
        return self._observe()

    def _fit(self, data: Any) -> np.ndarray:
        """Accept a 3- or 4-vector, so a mono policy still drives a colour task in black."""
        arr = np.asarray(data, dtype=np.float64).reshape(-1)
        dim = self.info.action_space.dim
        if arr.size == 3 and dim == 4:
            arr = np.append(arr, float(pal.DEFAULT_INDEX))
        if arr.size != dim:
            raise EmbodimentFault(f"action must have {dim} values, got {arr.size}")
        return np.clip(arr, self.info.action_space.low, self.info.action_space.high)

    def step(self, action: Action) -> StepResult:
        """Move the pen to one absolute canvas-frame target and look at the result.

        A target a stroke is still in the middle of gets the stroke's standing
        view back instead of a fresh photograph: a stroke is looked at once, at
        its end. The motion itself is unchanged, and every target is still its
        own step, so the action log and the efficiency term count what the arm
        actually did.
        """
        self._stroke = action.meta.get(stroke_lib.STROKE_KEY) if action.meta else None
        self._apply(self._fit(action.data))
        if self.strokes and stroke_lib.is_open(action.meta) and self._last_observation is not None:
            return StepResult(observation=self._last_observation, terminated=False)
        return StepResult(observation=self._observe(), terminated=False)

    def _apply(self, target: np.ndarray) -> None:
        """One per-target motion: the colour in force, the gateway call, the step count."""
        if self.colored:
            # Read before the motion, so the segment this step inks is this step's colour.
            self.color = pal.index_of(target[3])
        self._move_to(target[:3])
        self.num_steps += 1

    def stroke(
        self,
        points: Any,
        color: str | int | float | None = None,
        *,
        stroke_id: int | None = None,
    ) -> StepResult:
        """Draw one polyline through ``points``, then take one photograph.

        The stroke is planned and checked before anything moves: a point off
        the sheet raises
        :class:`castor.bench.sacpaint.strokes.StrokeError` with the arm exactly
        where it was. What runs afterwards is the ordinary per-target path, so
        the gateway sees the calls it always saw.
        """
        targets = stroke_lib.plan(
            points,
            low=self.info.action_space.low,
            high=self.info.action_space.high,
            pen_down_z=self.pen_down_z,
            travel_z=self.travel_z,
            color=color if self.colored else None,
        )
        self._stroke_id = self._stroke_id + 1 if stroke_id is None else int(stroke_id)
        self._stroke = self._stroke_id
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
        """Lift the pen clear of the sheet and take one fresh, unobstructed photograph.

        This is the frame every scorer reads, so it is fetched after the motion
        completes, never reused from the last step.
        """
        self._move_to(self._park)
        return self._observe()

    def close(self) -> None:
        """Flush the receipts. Guaranteed fallback for a core that offers embodiments no trial hooks.

        No motion here: an adapter that moves on ``close()`` moves after the
        operator thinks the run is over.
        """
        if self._receipts_written or not self.client.receipts:
            return
        base = Path(self.receipts_dir) if self.receipts_dir else Path("logs") / "receipts"
        self.write_receipts(base / f"opencastor-{time.strftime('%Y%m%dT%H%M%S')}.jsonl")

    # -- receipts ----------------------------------------------------------

    @property
    def receipts(self) -> list[dict[str, Any]]:
        """Every gateway call this body made, allowed or denied, in order."""
        return self.client.receipts

    def write_receipts(self, path: str | Path) -> Path:
        """Write the retained receipts as JSONL and remember that we did."""
        written = self.client.write_receipts(path)
        self._receipts_written = True
        self._say(f"{len(self.client.receipts)} signed receipts written to {written}")
        return written

    # -- motion ------------------------------------------------------------

    def _move_to(self, canvas_target_m: np.ndarray) -> None:
        """One gateway motion: canvas metres in, arm-base millimetres on the wire."""
        base_mm = self._calibration.canvas_to_base(canvas_target_m)
        # Which retained receipt this motion will be, so the ink note lands on the
        # move and not on the state call that may follow it.
        self._move_receipt = len(self.client.receipts)
        try:
            result = self.client.invoke(
                self.move_tool, self._move_payload(base_mm), scope=MOTION_SCOPE
            )
        except GatewayMiss as miss:
            if self.strict_reach:
                raise EmbodimentFault(
                    f"{miss} The pen is not where the policy thinks, so every later stroke would "
                    "start from the wrong place. Pass -E strict_reach=false to carry on from the "
                    "measured position instead (the virtual medium inks what was measured)."
                ) from miss
            self._say(
                f"missed {tuple(round(float(v), 1) for v in base_mm)} mm by {miss.error_mm} mm; carrying on from the measured pose"
            )
            self.misses += 1
            previous = self._eef
            self._eef = self._read_eef(default=canvas_target_m.copy())
            # Keep the miss itself, not only the count: where the sheet was asked
            # for, where the arm said it got to, and by how much. A run's misses
            # cluster somewhere (an edge, a corner, one row), and a count cannot
            # say where. Capped so a bad afternoon does not bloat the record.
            if len(self.miss_log) < MAX_MISS_LOG:
                self.miss_log.append(
                    {
                        "step": self.num_steps,
                        "target_mm": [round(float(v), 1) for v in base_mm],
                        "measured_m": [round(float(v), 4) for v in self._eef],
                        "error_mm": float(getattr(miss, "error_mm", 0.0) or 0.0),
                    }
                )
            self._ink_segment(previous, canvas_target_m)
            self._commanded = canvas_target_m.copy()
            return

        reached = read_reached(result.telemetry)
        if reached is False and self.strict_reach:
            raise EmbodimentFault(
                f"the arm reports it did not reach {tuple(round(float(v), 1) for v in base_mm)} mm "
                f"(telemetry {dict(result.telemetry)}). The pen is somewhere unknown, so every "
                "later stroke would be drawn from a wrong start. Check the arm, then re-run. "
                "Pass -E strict_reach=false to score runs with unreached targets anyway."
            )

        eef_mm = read_eef_mm(result.telemetry)
        # A tool that does not report the tip (today's status.report does not) leaves the
        # commanded target as the honest best estimate, which is what the arm was asked for.
        previous = self._eef
        if eef_mm is not None:
            self._eef = self._calibration.base_to_canvas(eef_mm)
        elif self._ink is not None and self.state_tool:
            # Virtual ink is drawn from measurement wherever measurement exists: ask the arm.
            self._eef = self._read_eef(default=canvas_target_m.copy())
        else:
            self._eef = canvas_target_m.copy()
        self._ink_segment(previous, canvas_target_m)
        self._commanded = canvas_target_m.copy()

    def _ink_segment(self, previous: np.ndarray, canvas_target_m: np.ndarray) -> None:
        """Virtual medium: ink from the last measured pose to the new one if the pen was down for both."""
        if self._ink is None:
            return
        # Pen state is what was *commanded* (a servo's few millimetres of z error must not
        # lift or drop the pen); the geometry is what was *measured*.
        down_before = float(self._commanded[2]) <= self.pen_down_z
        down_now = float(canvas_target_m[2]) <= self.pen_down_z
        if down_before and down_now:
            self._ink.segment(previous, self._eef, self.color)
        self._note_ink_on_receipt(down_before and down_now)

    def _note_ink_on_receipt(self, inked: bool) -> None:
        """Record what the virtual ink did on the retained receipt for the move just made.

        This annotates the local record only. The signed envelope the gateway
        attested to is untouched: it never carried a colour, because the arm was
        never asked for one. The annotation sits under its own key so a reader
        cannot mistake it for something the gateway verified.
        """
        index = self._move_receipt
        if self._ink is None or index is None or index >= len(self.client.receipts):
            return
        note: dict[str, Any] = {
            "medium": self.medium,
            "inked": bool(inked),
            "color": pal.name_of(self.color),
            "rgb": list(pal.rgb_of(self.color)),
            "note": "virtual ink, recorded by sacpaint; not part of the signed envelope",
        }
        if self._stroke is not None:
            note["stroke"] = int(self._stroke)
        self.client.receipts[index]["sacpaint_ink"] = note

    def _move_payload(self, base_mm: np.ndarray) -> dict[str, Any]:
        """Build the cartesian tool's arguments in whichever spelling the gateway speaks."""
        x, y, z = (float(v) for v in base_mm)
        if self.move_args == "reach_point":
            return {"target_mm": [x, y, z], "tolerance_mm": self.tolerance_mm}
        args: dict[str, Any] = {"x_mm": x, "y_mm": y, "z_mm": z}
        if self.speed is not None:
            args["speed"] = self.speed
        return args

    def _read_eef(self, *, default: np.ndarray) -> np.ndarray:
        """Ask the arm where its tip is, falling back when the state tool cannot say."""
        if not self.state_tool:
            return default
        result = self.client.invoke(self.state_tool, {}, scope=OBSERVE_SCOPE)
        eef_mm = read_eef_mm(result.telemetry)
        if eef_mm is None:
            self._say(
                f"{self.state_tool} reported no eef_mm; using the commanded pose for eef_pos "
                "(joint-only state cannot locate the pen tip)"
            )
            return default
        return self._calibration.base_to_canvas(eef_mm)

    # -- observation -------------------------------------------------------

    def _report(self, canvas: np.ndarray | None) -> None:
        """Tell a watching console how far along the run is. Never raises: reporting is not the run."""
        if self._progress_path is not None:
            try:
                payload = {
                    "steps": self.num_steps,
                    "misses": self.misses,
                    "medium": self.medium,
                    "elapsed_s": round(time.time() - self._started_at, 1),
                    "updated_at": time.time(),
                }
                if self.colored:
                    payload["color"] = pal.name_of(self.color)
                    payload["palette"] = list(pal.NAMES)
                if self.miss_log:
                    payload["miss_log"] = list(self.miss_log)
                tmp = self._progress_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload))
                tmp.replace(self._progress_path)
            except OSError as exc:  # pragma: no cover - disk trouble must not stop the arm
                logger.warning("opencastor: progress not written: %s", exc)
        if self._canvas_post_url and canvas is not None:
            try:
                ok, buf = cv2.imencode(
                    ".jpg", cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85]
                )
                if ok:
                    req = urllib.request.Request(
                        self._canvas_post_url,
                        data=buf.tobytes(),
                        method="POST",
                        headers={"Content-Type": "image/jpeg"},
                    )
                    if self._canvas_post_token:
                        req.add_header("Authorization", f"Bearer {self._canvas_post_token}")
                    urllib.request.urlopen(req, timeout=5).read()
            except Exception as exc:  # noqa: BLE001 - the console is an audience, not a dependency
                logger.warning("opencastor: canvas not posted: %s", exc)

    def _observe(self) -> Observation:
        """One fresh overhead photograph (or the virtual ink), the reference, the pen position, the corners."""
        extra: dict[str, Any] = {MEDIUM_KEY: self.medium, "misses": self.misses}
        if self.colored:
            extra[PALETTE_KEY] = list(pal.NAMES)
            extra[COLOR_KEY] = pal.name_of(self.color)
        if self._ink is not None:
            images = {OVERHEAD: self._ink.image(), REFERENCE_CAM: self.reference_camera.fetch()}
            if self.color_reference_camera is not None:
                images[REFERENCE_COLOR_CAM] = self.color_reference_camera.fetch()
            self._report(images[OVERHEAD])
            extra[CANONICAL_FLAG] = True  # telemetry ink is already the canonical canvas
            if self.overhead is not None:
                images["scene"] = (
                    self.overhead.fetch()
                )  # a real camera, if one is watching, for the record
            return self._remember(
                Observation(
                    images=images,
                    state={"eef_pos": self._state()},
                    instruction=self._instruction,
                    extra=extra,
                )
            )
        assert self.overhead is not None
        images = {OVERHEAD: self.overhead.fetch(), REFERENCE_CAM: self.reference_camera.fetch()}
        if self.color_reference_camera is not None:
            images[REFERENCE_COLOR_CAM] = self.color_reference_camera.fetch()
        self._report(
            None
        )  # a real camera's frames are already on the console; only the count is new
        corners = self._canvas_corners()
        if corners is not None:
            extra[CORNERS_KEY] = [[float(x), float(y)] for x, y in corners]
        # CANONICAL_FLAG is deliberately absent: this is a photograph of a sheet.
        return self._remember(
            Observation(
                images=images,
                state={"eef_pos": self._state()},
                instruction=self._instruction,
                extra=extra,
            )
        )

    def _remember(self, observation: Observation) -> Observation:
        """Keep the latest view, so a stroke's middle targets need no second photograph."""
        self._last_observation = observation
        return observation

    def _state(self) -> np.ndarray:
        """``eef_pos``: the measured tip, plus the palette index in force on a colour task."""
        if not self.colored:
            return self._eef.copy()
        return np.append(self._eef.copy(), float(self.color))

    def _canvas_corners(self) -> tuple[tuple[float, float], ...] | None:
        """The tapped corners, fetched once per trial and then reused."""
        if self._corner_source is None:
            return None
        if self._corners is None:
            self._corners = self._corner_source.fetch()
        return self._corners

    # -- operator ----------------------------------------------------------

    def _wait_ready(self) -> None:
        """Block until a human says the sheet is fresh and the workspace is clear.

        Skipped under ``-E no_prompt=true`` or without a TTY, because an
        unattended run has nobody to ask — and an adapter that blocks on a dead
        stdin turns an overnight eval into a hung process.
        """
        prompt = (
            "Fresh sheet taped down, pen capped off, hands clear of the arm — press Enter to start: "
            if self._ink is None
            else "Virtual easel: no paper, no pen; the arm will sweep the space in front of it. "
            "Hands and objects clear of the arm — press Enter to start: "
        )
        if self._session is not None:
            self._session.gate(
                prompt,
                hint="Run sacpaint on the robot's own terminal, or pass -E no_prompt=true for an "
                "unattended run (the arm will start moving with no confirmation).",
            )
            return
        if self.no_prompt or not self._isatty_fn():
            self._say("unattended: skipping the operator readiness gate; the arm moves immediately")
            return
        reader = self._input_fn or input
        try:
            reader(prompt)
        except (EOFError, OSError) as exc:
            raise EmbodimentFault(
                "the operator readiness gate could not read stdin. Run from a real terminal, "
                "or pass -E no_prompt=true to accept an unattended start."
            ) from exc

    def _say(self, text: str) -> None:
        """Human-facing output that respects the framework console when one is attached."""
        if self._session is not None:
            self._session.write_line(f"opencastor: {text}")
            return
        logger.info("opencastor: %s", text)


def _default_ruri() -> str:
    """The RCAN resource id, overridable by the environment for a differently-named robot."""
    from castor.bench.sacpaint.gateway import DEFAULT_RURI

    return os.environ.get("ROBOT_MD_RURI") or DEFAULT_RURI


def opencastor_embodiment(**kwargs: Any) -> OpenCastorEmbodiment:
    """Registry factory for ``--embodiment opencastor``.

    Every keyword is a ``-E name=value`` flag; the CLI passes strings, so the
    booleans and numbers are coerced here rather than failing deep inside a
    motion call.
    """
    return OpenCastorEmbodiment(**_coerce(kwargs))


_BOOL_FLAGS = ("no_prompt", "strict_reach", "strokes")
_INT_FLAGS = ("llm_budget",)
_FLOAT_FLAGS = (
    "timeout_s",
    "speed",
    "tolerance_mm",
    "pen_down_z",
    "travel_z",
    "park_x",
    "park_y",
    "park_z",
    "camera_timeout_s",
    "easel_distance_mm",
    "easel_elevation_deg",
    "easel_azimuth_deg",
)


def _coerce(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Turn ``-E`` strings into the types the constructor wants, loudly."""
    out = dict(kwargs)
    for key in _BOOL_FLAGS:
        if isinstance(out.get(key), str):
            text = out[key].strip().lower()
            if text not in ("true", "false", "1", "0", "yes", "no"):
                raise ConfigError(f"-E {key} must be true or false, got {out[key]!r}")
            out[key] = text in ("true", "1", "yes")
    for key in _FLOAT_FLAGS:
        value = out.get(key)
        if isinstance(value, str):
            try:
                out[key] = float(value)
            except ValueError as exc:
                raise ConfigError(f"-E {key} must be a number, got {value!r}") from exc
    for key in _INT_FLAGS:
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = int(float(value))
    return out
