"""
Workspace limits for a mobile base: refuse a velocity whose stopping path leaves the area.

The SafetyLayer can clamp a velocity, but a velocity says nothing about where the robot is. Keeping a
base inside a declared area needs its pose, and the pose has to come from independent localization
(an overhead camera, a motion-capture system, a localizer on its own sensors), not from integrating
the commands the robot was sent: a fence that dead-reckons from its own commands drifts away from
where the robot is (the EV-03 hostile-model test measured 0.73 m believed against 11.47 m actual
after ten minutes).

For each move the policy predicts the worst-case stopping path: the commanded (or current, if larger)
speed for one reaction time, then braking at the deceleration the base can always achieve, along the
heading the command drives. If any point of that path, plus a margin, is outside the keep-in polygon
or inside a keep-out polygon, the move is refused, and the SafetyLayer writes a command with zero
linear velocity (turning in place is still allowed) instead of leaving the last command running.

With no pose (the localizer has stopped, or has not started yet) every move is refused.

Usage::

    from castor.safety.workspace import BaseWorkspace, BaseWorkspacePolicy

    ws = BaseWorkspace(keep_in=[(0, 0), (6, 0), (6, 4), (0, 4)], top_speed_mps=1.5,
                       max_decel_mps2=1.0)
    policy = BaseWorkspacePolicy(ws, pose_provider=my_localizer.pose)   # (x, y, theta, v) or None
    safety = SafetyLayer(ns, perms, workspace_policy=policy)

From the RCAN config (``castor/main.py`` reads this; absent means no workspace policy)::

    safety:
      workspace:
        keep_in: [[0, 0], [6, 0], [6, 4], [0, 4]]   # metres, in the localizer's frame
        keep_out: [[[2, 1], [3, 1], [3, 2], [2, 2]]]
        top_speed_mps: 1.5      # required: m/s at linear = 1.0
        max_decel_mps2: 1.0     # required: the braking the base can ALWAYS achieve
        reaction_s: 0.05        # >= one enforcement period plus latency to the motors
        margin_m: 0.05          # add the localizer's error here
        enforce_hz: 50          # per-cycle re-check rate (castor.safety.workspace_enforcer)
        pose_source: overhead_cam   # the name a localizer registers under

The pose comes from :func:`register_pose_source`: OpenCastor ships no localizer, so whatever
provides one registers it under the name the config gives. Until something registers, after it
unregisters, when it returns None or raises, and always when ``pose_source`` is not set, there is no
pose and every translating move is refused. Stops and turning in place still work.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from castor.safety.workspace_enforcer import DEFAULT_ENFORCE_HZ

Point = tuple[float, float]
Pose = tuple[float, float, float, float]  # x (m), y (m), heading (rad), speed (m/s)


@dataclass
class BaseWorkspace:
    """Where a mobile base may be, in the localizer's frame (metres)."""

    keep_in: list[Point]
    keep_out: list[list[Point]] = field(default_factory=list)
    #: metres per second produced by ``linear = 1.0``
    top_speed_mps: float = 1.0
    #: braking the base can always achieve (m/s^2); use the worst case, not the typical one
    max_decel_mps2: float = 1.0
    #: time a command runs before braking can start (control period plus latency), seconds
    reaction_s: float = 0.05
    #: extra clearance on top of the stopping distance, metres
    margin_m: float = 0.05

    def __post_init__(self) -> None:
        if len(self.keep_in) < 3:
            raise ValueError("keep_in needs at least three points")
        for name in ("top_speed_mps", "max_decel_mps2"):
            value = getattr(self, name)
            if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a positive number, got {value!r}")

    def allowed(self, x: float, y: float) -> bool:
        if not _in_polygon(x, y, self.keep_in):
            return False
        return not any(_in_polygon(x, y, poly) for poly in self.keep_out)


class BaseWorkspacePolicy:
    """Refuses base motion whose worst-case stopping path leaves the workspace."""

    def __init__(
        self,
        workspace: BaseWorkspace,
        pose_provider: Callable[[], Optional[Any]],
        samples: int = 8,
    ):
        self.workspace = workspace
        self.pose_provider = pose_provider
        self.samples = max(2, int(samples))

    def _pose(self) -> Optional[Pose]:
        try:
            pose: Any = self.pose_provider()
        except Exception:  # noqa: BLE001 - a broken localizer is the same as none
            return None
        if pose is None:
            return None
        if isinstance(pose, dict):
            pose = (pose.get("x"), pose.get("y"), pose.get("theta"), pose.get("v", 0.0))
        try:
            # None, a non-number or the wrong length lands in except: no pose.
            x, y, theta, v = (float(p) for p in pose)
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(p) for p in (x, y, theta, v)):
            return None
        return x, y, theta, v

    def check(self, data: Any) -> tuple[bool, str]:
        """(True, "") if the move may run; otherwise (False, reason)."""
        if not isinstance(data, dict):
            return True, ""
        linear = data.get("linear", 0.0) or 0.0
        if linear == 0.0:
            return True, ""  # turning in place, or no translation at all
        pose = self._pose()
        if pose is None:
            return False, "no pose from the localizer; refusing to move"
        x, y, theta, v_now = pose
        ws = self.workspace
        v_cmd = float(linear) * ws.top_speed_mps
        speed = max(abs(v_cmd), abs(v_now))
        reach = speed * ws.reaction_s + speed * speed / (2.0 * ws.max_decel_mps2) + ws.margin_m
        heading = theta if v_cmd >= 0 else theta + math.pi
        for i in range(1, self.samples + 1):
            d = reach * i / self.samples
            px, py = x + d * math.cos(heading), y + d * math.sin(heading)
            if not ws.allowed(px, py):
                return False, (
                    f"stopping path ({reach:.2f} m at {speed:.2f} m/s) leaves the workspace "
                    f"near ({px:.2f}, {py:.2f})"
                )
        return True, ""

    @staticmethod
    def refusal_command(data: Any) -> dict:
        """What to write instead of a refused move: no translation, the same turn rate."""
        angular = data.get("angular", 0.0) if isinstance(data, dict) else 0.0
        return {"type": "move", "linear": 0.0, "angular": angular or 0.0}


def _in_polygon(x: float, y: float, poly: list[Point]) -> bool:
    inside, n = False, len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


# ----- pose sources -----------------------------------------------------------------------------
PoseProvider = Callable[[], Optional[Any]]

_pose_sources: dict[str, PoseProvider] = {}
_pose_sources_lock = threading.Lock()


def register_pose_source(name: str, provider: PoseProvider) -> None:
    """Register *provider* as the pose source called *name*.

    This is the hook a localizer uses (an overhead-camera tracker, motion capture, a localizer on
    the robot's own sensors); ``safety.workspace.pose_source`` names it. *provider* returns
    ``(x, y, heading_rad, speed_mps)``, or a dict with ``x``, ``y``, ``theta`` and ``v``, in the
    workspace's frame, and None whenever it has no current fix: a stale pose is worse than none.
    Registering a name again replaces its provider (a localizer that restarted).
    """
    if not isinstance(name, str) or not name:
        raise ValueError("a pose source needs a non-empty name")
    if not callable(provider):
        raise TypeError("a pose source must be callable")
    with _pose_sources_lock:
        _pose_sources[name] = provider


def unregister_pose_source(name: str) -> None:
    """Remove the pose source called *name*. A localizer that stops should call this."""
    with _pose_sources_lock:
        _pose_sources.pop(name, None)


def named_pose_source(name: Optional[str]) -> PoseProvider:
    """A pose provider that looks *name* up on every call.

    Late-bound, so a localizer can register after boot, restart, or go away. While nothing is
    registered under *name*, and always when *name* is None, it returns None, and
    :class:`BaseWorkspacePolicy` refuses every translating move.
    """

    def provider() -> Optional[Any]:
        if name is None:
            return None
        with _pose_sources_lock:
            source = _pose_sources.get(name)
        return None if source is None else source()

    return provider


# ----- configuration: the safety.workspace block ------------------------------------------------
class WorkspaceConfigError(ValueError):
    """The ``safety.workspace`` config block is invalid; the message names every problem."""


_REQUIRED_KEYS = ("keep_in", "top_speed_mps", "max_decel_mps2")
_OPTIONAL_KEYS = ("keep_out", "reaction_s", "margin_m", "enforce_hz", "pose_source")
#: The highest re-check rate the config accepts; above it the enforcer thread is a busy loop.
MAX_ENFORCE_HZ = 1000.0


@dataclass
class WorkspaceConfig:
    """A parsed ``safety.workspace`` block."""

    workspace: BaseWorkspace
    enforce_hz: float = DEFAULT_ENFORCE_HZ
    #: The registered pose source the policy reads; None means no pose, so no translation.
    pose_source: Optional[str] = None

    def build_policy(self) -> BaseWorkspacePolicy:
        """The policy for ``SafetyLayer(workspace_policy=...)``, reading the named pose source."""
        return BaseWorkspacePolicy(self.workspace, named_pose_source(self.pose_source))


def parse_workspace_config(block: Any) -> Optional[WorkspaceConfig]:
    """Parse the ``safety.workspace`` config block.

    An absent block (None) means no workspace policy, and None is returned. Anything else must be a
    complete, valid block, or :class:`WorkspaceConfigError` names every problem in it. Unknown keys
    are errors as well: a misspelt ``top_speed_mps`` quietly replaced by a default would understate
    the stopping distance.

    A block without ``pose_source`` is valid and fails closed: the policy never has a pose, so
    every translating move is refused.
    """
    if block is None:
        return None
    if not isinstance(block, Mapping):
        raise WorkspaceConfigError(
            f"safety.workspace must be a mapping of settings, got {type(block).__name__}"
        )
    problems: list[str] = []
    unknown = sorted(str(k) for k in block if k not in _REQUIRED_KEYS + _OPTIONAL_KEYS)
    if unknown:
        problems.append(
            f"unknown key(s) {', '.join(unknown)}; allowed: "
            f"{', '.join(_REQUIRED_KEYS + _OPTIONAL_KEYS)}"
        )
    for key in _REQUIRED_KEYS:
        if key not in block:
            problems.append(f"{key} is required")

    keep_in: Optional[list[Point]] = None
    if "keep_in" in block:
        keep_in = _parse_polygon(block["keep_in"], "keep_in", problems)
    keep_out: list[list[Point]] = []
    raw_keep_out = block.get("keep_out")
    if raw_keep_out is not None:
        if not isinstance(raw_keep_out, (list, tuple)):
            problems.append(f"keep_out must be a list of polygons, got {raw_keep_out!r}")
        else:
            for i, poly in enumerate(raw_keep_out):
                parsed = _parse_polygon(poly, f"keep_out[{i}]", problems)
                if parsed is not None:
                    keep_out.append(parsed)

    limits: dict[str, float] = {}
    for key, zero_ok in (
        ("top_speed_mps", False),
        ("max_decel_mps2", False),
        ("reaction_s", False),
        ("margin_m", True),
    ):
        if key not in block:
            continue
        value = _finite(block[key])
        if value is None or value < 0 or (value == 0 and not zero_ok):
            kind = "a non-negative" if zero_ok else "a positive"
            problems.append(f"{key} must be {kind} number, got {block[key]!r}")
        else:
            limits[key] = value

    enforce_hz = DEFAULT_ENFORCE_HZ
    if "enforce_hz" in block:
        hz = _finite(block["enforce_hz"])
        if hz is None or not 0 < hz <= MAX_ENFORCE_HZ:
            problems.append(
                f"enforce_hz must be a number above 0 and at most {MAX_ENFORCE_HZ:g}, "
                f"got {block['enforce_hz']!r}"
            )
        else:
            enforce_hz = hz

    pose_source = block.get("pose_source")
    if pose_source is not None and (not isinstance(pose_source, str) or not pose_source.strip()):
        problems.append(
            f"pose_source must be the name a localizer registers under, got {pose_source!r}"
        )

    if problems or keep_in is None:
        raise WorkspaceConfigError("safety.workspace: " + "; ".join(problems))
    try:
        workspace = BaseWorkspace(keep_in=keep_in, keep_out=keep_out, **limits)
    except ValueError as exc:
        raise WorkspaceConfigError(f"safety.workspace: {exc}") from exc
    period_s = 1.0 / enforce_hz
    if workspace.reaction_s < period_s:
        raise WorkspaceConfigError(
            f"safety.workspace: reaction_s ({workspace.reaction_s:g} s) must cover one enforcement "
            f"period (1 / enforce_hz = {period_s:.3f} s), or a command that passed one re-check "
            "could run past the stopping path it was checked against"
        )
    return WorkspaceConfig(workspace=workspace, enforce_hz=enforce_hz, pose_source=pose_source)


def _finite(value: Any) -> Optional[float]:
    """*value* as a float if it is a finite real number (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _parse_polygon(value: Any, name: str, problems: list[str]) -> Optional[list[Point]]:
    """A polygon of at least three [x, y] points with a non-zero area, or None and a problem."""
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        problems.append(f"{name} must be a list of at least three [x, y] points, got {value!r}")
        return None
    points: list[Point] = []
    for i, point in enumerate(value):
        x = y = None
        if isinstance(point, (list, tuple)) and len(point) == 2:
            x, y = _finite(point[0]), _finite(point[1])
        if x is None or y is None:
            problems.append(f"{name}[{i}] must be [x, y] in metres, got {point!r}")
            return None
        points.append((x, y))
    shifted = points[1:] + points[:1]
    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, shifted, strict=True))
    if abs(area) < 1e-9:
        problems.append(f"{name} encloses no area")
        return None
    return points
