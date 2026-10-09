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
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

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
            pose = self.pose_provider()
        except Exception:  # noqa: BLE001 - a broken localizer is the same as none
            return None
        if pose is None:
            return None
        if isinstance(pose, dict):
            pose = (pose.get("x"), pose.get("y"), pose.get("theta"), pose.get("v", 0.0))
        try:
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
