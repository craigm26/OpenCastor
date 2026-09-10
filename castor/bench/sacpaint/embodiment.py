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
#: The scorer's preferred rectification input: four normalised TL TR BR BL corners.
CORNERS_KEY = "canvas_corners"
#: Set only by the virtual medium. A photograph is not a canonical canvas; telemetry ink is.
CANONICAL_FLAG = "canonical_canvas"
#: ``observation.extra["medium"]``: ``pen`` (paper, the real thing) or ``virtual`` (telemetry ink).
MEDIUM_KEY = "medium"
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


def _canonical_size(name: str) -> tuple[int, int]:
    """The canonical canvas image size (width, height) in pixels."""
    from castor.bench.sacpaint import reference as ref

    get_spec = getattr(ref, "get_spec", None)
    if callable(get_spec):
        return tuple(int(v) for v in get_spec(name).canonical_size())  # type: ignore[return-value]
    return tuple(int(v) for v in ref.canonical_size())  # type: ignore[return-value]


def action_space(canvas_mm: tuple[float, float]) -> Box:
    """The canvas-frame Cartesian action box — the same contract the mock declares."""
    width, height = canvas_mm
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


def observation_space(
    canonical_wh: tuple[int, int], overhead_wh: tuple[int, int]
) -> ObservationSpace:
    """Two cameras plus the pen position. The overhead spec is the *camera's* size."""
    ref_w, ref_h = canonical_wh
    over_w, over_h = overhead_wh
    return ObservationSpace(
        cameras=(
            CameraSpec(OVERHEAD, over_h, over_w, 3),
            CameraSpec(REFERENCE_CAM, ref_h, ref_w, 3),
        ),
        state=StateSpec((StateField("eef_pos", (3,), "m"),)),
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


class VirtualInk:
    """A canonical canvas inked from measured tip positions: the ``virtual`` medium's sheet."""

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

    def segment(self, a_m: np.ndarray, b_m: np.ndarray) -> None:
        """Ink the straight segment between two measured tip positions (canvas metres)."""
        cv2.line(self._canvas, self._px(a_m), self._px(b_m), (0, 0, 0), self._stroke_px)
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

        self.num_steps = 0
        self.misses = 0
        self._instruction: str | None = None
        self._eef = np.array([0.0, 0.0, self.travel_z])
        self._commanded = np.array([0.0, 0.0, self.travel_z])

        self.info = EmbodimentInfo(
            name="opencastor" if self.medium == MEDIUM_PEN else f"opencastor-{self.medium}",
            action_space=action_space(self.canvas_mm),
            observation_space=observation_space(self._reference_wh, self._overhead_wh()),
            control_hz=None,
            is_simulated=False,
            capabilities=frozenset({SELF_PACED}),
            supported_target_kinds=frozenset({"reference_drawing"}),
            docs=_docs(self.canvas_mm) if self._ink is None else _virtual_docs(self.canvas_mm),
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
        if self._ink is not None:
            self._ink.clear()
        return self._observe()

    def step(self, action: Action) -> StepResult:
        """Move the pen to one absolute canvas-frame target and look at the result."""
        target = np.clip(
            np.asarray(action.data, dtype=np.float64).reshape(3),
            self.info.action_space.low,
            self.info.action_space.high,
        )
        self._move_to(target)
        self.num_steps += 1
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
            self._ink.segment(previous, self._eef)

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
        if self._ink is not None:
            images = {OVERHEAD: self._ink.image(), REFERENCE_CAM: self.reference_camera.fetch()}
            self._report(images[OVERHEAD])
            extra[CANONICAL_FLAG] = True  # telemetry ink is already the canonical canvas
            if self.overhead is not None:
                images["scene"] = (
                    self.overhead.fetch()
                )  # a real camera, if one is watching, for the record
            return Observation(
                images=images,
                state={"eef_pos": self._eef.copy()},
                instruction=self._instruction,
                extra=extra,
            )
        assert self.overhead is not None
        images = {OVERHEAD: self.overhead.fetch(), REFERENCE_CAM: self.reference_camera.fetch()}
        self._report(
            None
        )  # a real camera's frames are already on the console; only the count is new
        corners = self._canvas_corners()
        if corners is not None:
            extra[CORNERS_KEY] = [[float(x), float(y)] for x, y in corners]
        # CANONICAL_FLAG is deliberately absent: this is a photograph of a sheet.
        return Observation(
            images=images,
            state={"eef_pos": self._eef.copy()},
            instruction=self._instruction,
            extra=extra,
        )

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


_BOOL_FLAGS = ("no_prompt", "strict_reach")
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
    return out
