"""
Physical bounds enforcement for OpenCastor.

Provides workspace, joint, and force limit checking to prevent
the robot from exceeding safe operating boundaries.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Safety.Bounds")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class BoundsStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    VIOLATION = "violation"


@dataclass
class BoundsResult:
    """Outcome of a bounds check."""

    status: BoundsStatus = BoundsStatus.OK
    details: str = ""
    margin: float = float("inf")  # distance to nearest limit

    # Helpers
    @property
    def ok(self) -> bool:
        return self.status == BoundsStatus.OK

    @property
    def violated(self) -> bool:
        return self.status == BoundsStatus.VIOLATION

    @staticmethod
    def combine(results: List[BoundsResult]) -> BoundsResult:
        """Return the worst result from a list."""
        if not results:
            return BoundsResult()
        worst = BoundsResult()
        priority = {BoundsStatus.OK: 0, BoundsStatus.WARNING: 1, BoundsStatus.VIOLATION: 2}
        for r in results:
            if priority.get(r.status, 0) > priority.get(worst.status, 0):
                worst = r
            elif priority.get(r.status, 0) == priority.get(worst.status, 0):
                if r.margin < worst.margin:
                    worst = r
        return worst


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


@dataclass
class Sphere:
    cx: float = 0.0
    cy: float = 0.0
    cz: float = 0.0
    radius: float = 1.0


@dataclass
class Box:
    x_min: float = -1.0
    y_min: float = -1.0
    z_min: float = -1.0
    x_max: float = 1.0
    y_max: float = 1.0
    z_max: float = 1.0


def _distance_to_sphere_surface(x: float, y: float, z: float, s: Sphere) -> float:
    """Signed distance from point to sphere surface. Negative = inside."""
    d = math.sqrt((x - s.cx) ** 2 + (y - s.cy) ** 2 + (z - s.cz) ** 2)
    return d - s.radius


def _distance_to_box_surface(x: float, y: float, z: float, b: Box) -> float:
    """Signed distance from point to box surface. Negative = inside."""
    # Distance to each face (negative when inside on that axis)
    dx = max(b.x_min - x, x - b.x_max, 0.0)
    dy = max(b.y_min - y, y - b.y_max, 0.0)
    dz = max(b.z_min - z, z - b.z_max, 0.0)

    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        # Inside: distance is negative (closest face)
        return -min(
            x - b.x_min,
            b.x_max - x,
            y - b.y_min,
            b.y_max - y,
            z - b.z_min,
            b.z_max - z,
        )
    # Outside: Euclidean distance to nearest corner/edge/face
    return math.sqrt(dx**2 + dy**2 + dz**2)


def _point_in_sphere(x: float, y: float, z: float, s: Sphere) -> bool:
    return _distance_to_sphere_surface(x, y, z, s) <= 0.0


def _point_in_box(x: float, y: float, z: float, b: Box) -> bool:
    return _distance_to_box_surface(x, y, z, b) <= 0.0


# ---------------------------------------------------------------------------
# WorkspaceBounds
# ---------------------------------------------------------------------------

WARNING_MARGIN = 0.05  # 5 cm default warning zone


class WorkspaceBounds:
    """Workspace envelope with optional forbidden zones."""

    def __init__(
        self,
        sphere: Optional[Sphere] = None,
        box: Optional[Box] = None,
        forbidden_spheres: Optional[List[Sphere]] = None,
        forbidden_boxes: Optional[List[Box]] = None,
        warning_margin: float = WARNING_MARGIN,
    ):
        self.sphere = sphere
        self.box = box
        self.forbidden_spheres = forbidden_spheres or []
        self.forbidden_boxes = forbidden_boxes or []
        self.warning_margin = warning_margin

    def check_position(self, x: float, y: float, z: float) -> BoundsResult:
        results: List[BoundsResult] = []

        # Check allowed envelope (sphere)
        if self.sphere is not None:
            d = _distance_to_sphere_surface(x, y, z, self.sphere)
            if d > 0:
                results.append(
                    BoundsResult(
                        BoundsStatus.VIOLATION, f"outside workspace sphere by {d:.4f}m", -d
                    )
                )
            elif -d < self.warning_margin:
                results.append(
                    BoundsResult(
                        "warning", f"near workspace sphere boundary ({-d:.4f}m margin)", -d
                    )
                )
            else:
                results.append(BoundsResult(BoundsStatus.OK, "inside workspace sphere", -d))

        # Check allowed envelope (box)
        if self.box is not None:
            d = _distance_to_box_surface(x, y, z, self.box)
            if d > 0:
                results.append(
                    BoundsResult(BoundsStatus.VIOLATION, f"outside workspace box by {d:.4f}m", -d)
                )
            elif -d < self.warning_margin:
                results.append(
                    BoundsResult(
                        BoundsStatus.WARNING, f"near workspace box boundary ({-d:.4f}m margin)", -d
                    )
                )
            else:
                results.append(BoundsResult(BoundsStatus.OK, "inside workspace box", -d))

        # Check forbidden zones
        for i, fs in enumerate(self.forbidden_spheres):
            d = _distance_to_sphere_surface(x, y, z, fs)
            if d <= 0:
                results.append(
                    BoundsResult(BoundsStatus.VIOLATION, f"inside forbidden sphere zone {i}", d)
                )
            elif d < self.warning_margin:
                results.append(
                    BoundsResult(
                        BoundsStatus.WARNING, f"near forbidden sphere zone {i} ({d:.4f}m)", d
                    )
                )

        for i, fb in enumerate(self.forbidden_boxes):
            d = _distance_to_box_surface(x, y, z, fb)
            if d <= 0:
                results.append(
                    BoundsResult(BoundsStatus.VIOLATION, f"inside forbidden box zone {i}", d)
                )
            elif d < self.warning_margin:
                results.append(
                    BoundsResult(BoundsStatus.WARNING, f"near forbidden box zone {i} ({d:.4f}m)", d)
                )

        return BoundsResult.combine(results) if results else BoundsResult()


# ---------------------------------------------------------------------------
# JointBounds
# ---------------------------------------------------------------------------


@dataclass
class JointLimits:
    """Limits for a single joint."""

    position_min: float = -math.pi
    position_max: float = math.pi
    velocity_max: float = 2.0  # rad/s
    torque_max: float = 50.0  # Nm


class JointBounds:
    """Per-joint position, velocity, and torque limits."""

    def __init__(self, joints: Optional[Dict[str, JointLimits]] = None):
        self.joints: Dict[str, JointLimits] = joints or {}

    def set_joint(self, joint_id: str, limits: JointLimits) -> None:
        self.joints[joint_id] = limits

    def check_joint(
        self,
        joint_id: str,
        position: Optional[float] = None,
        velocity: Optional[float] = None,
        torque: Optional[float] = None,
    ) -> BoundsResult:
        if joint_id not in self.joints:
            return BoundsResult(
                "warning", f"no limits defined for joint '{joint_id}'", float("inf")
            )

        lim = self.joints[joint_id]
        results: List[BoundsResult] = []

        if position is not None:
            pos_range = lim.position_max - lim.position_min
            warning_zone = pos_range * 0.05 if pos_range > 0 else 0.01
            if position < lim.position_min:
                margin = lim.position_min - position
                results.append(
                    BoundsResult(
                        "violation",
                        f"joint {joint_id} position {position:.4f} below min {lim.position_min:.4f}",
                        -margin,
                    )
                )
            elif position > lim.position_max:
                margin = position - lim.position_max
                results.append(
                    BoundsResult(
                        "violation",
                        f"joint {joint_id} position {position:.4f} above max {lim.position_max:.4f}",
                        -margin,
                    )
                )
            else:
                margin = min(position - lim.position_min, lim.position_max - position)
                if margin < warning_zone:
                    results.append(
                        BoundsResult(
                            "warning",
                            f"joint {joint_id} position near limit ({margin:.4f} rad margin)",
                            margin,
                        )
                    )
                else:
                    results.append(
                        BoundsResult(BoundsStatus.OK, f"joint {joint_id} position ok", margin)
                    )

        if velocity is not None:
            abs_vel = abs(velocity)
            margin = lim.velocity_max - abs_vel
            if abs_vel > lim.velocity_max:
                results.append(
                    BoundsResult(
                        "violation",
                        f"joint {joint_id} velocity {abs_vel:.4f} exceeds max {lim.velocity_max:.4f}",
                        -abs(margin),
                    )
                )
            elif margin < lim.velocity_max * 0.1:
                results.append(
                    BoundsResult(
                        "warning",
                        f"joint {joint_id} velocity near limit ({margin:.4f} rad/s margin)",
                        margin,
                    )
                )
            else:
                results.append(
                    BoundsResult(BoundsStatus.OK, f"joint {joint_id} velocity ok", margin)
                )

        if torque is not None:
            abs_t = abs(torque)
            margin = lim.torque_max - abs_t
            if abs_t > lim.torque_max:
                results.append(
                    BoundsResult(
                        "violation",
                        f"joint {joint_id} torque {abs_t:.4f} exceeds max {lim.torque_max:.4f}",
                        -abs(margin),
                    )
                )
            elif margin < lim.torque_max * 0.1:
                results.append(
                    BoundsResult(
                        "warning",
                        f"joint {joint_id} torque near limit ({margin:.4f} Nm margin)",
                        margin,
                    )
                )
            else:
                results.append(BoundsResult(BoundsStatus.OK, f"joint {joint_id} torque ok", margin))

        return BoundsResult.combine(results) if results else BoundsResult()


# ---------------------------------------------------------------------------
# ForceBounds
# ---------------------------------------------------------------------------


class ForceBounds:
    """End-effector and gripper force limits with human-proximity mode."""

    def __init__(
        self,
        max_ee_force: float = 50.0,
        max_ee_force_human: float = 10.0,
        max_contact_force: float = 80.0,
        max_gripper_force: float = 40.0,
        warning_fraction: float = 0.85,
    ):
        self.max_ee_force = max_ee_force
        self.max_ee_force_human = max_ee_force_human
        self.max_contact_force = max_contact_force
        self.max_gripper_force = max_gripper_force
        self.warning_fraction = warning_fraction
        self._human_nearby = False

    @property
    def effective_ee_limit(self) -> float:
        return self.max_ee_force_human if self._human_nearby else self.max_ee_force

    def set_human_proximity(self, detected: bool) -> None:
        if detected != self._human_nearby:
            logger.info(
                "Human proximity %s — EE force limit now %.1fN",
                "DETECTED" if detected else "CLEARED",
                self.max_ee_force_human if detected else self.max_ee_force,
            )
        self._human_nearby = detected

    def check_force(self, force_n: float) -> BoundsResult:
        limit = self.effective_ee_limit
        margin = limit - abs(force_n)
        if abs(force_n) > limit:
            return BoundsResult(
                "violation",
                f"EE force {abs(force_n):.2f}N exceeds limit {limit:.2f}N",
                -abs(margin),
            )
        if abs(force_n) > limit * self.warning_fraction:
            return BoundsResult(
                "warning",
                f"EE force {abs(force_n):.2f}N near limit {limit:.2f}N ({margin:.2f}N margin)",
                margin,
            )
        return BoundsResult(BoundsStatus.OK, "force within limits", margin)

    def check_contact_force(self, force_n: float) -> BoundsResult:
        margin = self.max_contact_force - abs(force_n)
        if abs(force_n) > self.max_contact_force:
            return BoundsResult(
                "violation",
                f"contact force {abs(force_n):.2f}N exceeds limit {self.max_contact_force:.2f}N",
                -abs(margin),
            )
        return BoundsResult(BoundsStatus.OK, "contact force ok", margin)

    def check_gripper_force(self, force_n: float) -> BoundsResult:
        margin = self.max_gripper_force - abs(force_n)
        if abs(force_n) > self.max_gripper_force:
            return BoundsResult(
                "violation",
                f"gripper force {abs(force_n):.2f}N exceeds limit {self.max_gripper_force:.2f}N",
                -abs(margin),
            )
        return BoundsResult(BoundsStatus.OK, "gripper force ok", margin)


# ---------------------------------------------------------------------------
# Default robot configs
# ---------------------------------------------------------------------------

DEFAULT_CONFIGS: Dict[str, Dict[str, Any]] = {
    "differential_drive": {
        "workspace": {
            "box": {
                "x_min": -5.0,
                "y_min": -5.0,
                "z_min": 0.0,
                "x_max": 5.0,
                "y_max": 5.0,
                "z_max": 0.5,
            }
        },
        "joints": {
            "left_wheel": {
                "position_min": -1e9,
                "position_max": 1e9,
                "velocity_max": 10.0,
                "torque_max": 5.0,
            },
            "right_wheel": {
                "position_min": -1e9,
                "position_max": 1e9,
                "velocity_max": 10.0,
                "torque_max": 5.0,
            },
        },
        "force": {
            "max_ee_force": 20.0,
            "max_ee_force_human": 5.0,
            "max_contact_force": 30.0,
            "max_gripper_force": 0.0,
        },
    },
    "arm": {
        "workspace": {"sphere": {"cx": 0.0, "cy": 0.0, "cz": 0.5, "radius": 0.8}},
        "joints": {
            f"joint_{i}": {
                "position_min": -3.14,
                "position_max": 3.14,
                "velocity_max": 2.0,
                "torque_max": 50.0,
            }
            for i in range(6)
        },
        "force": {
            "max_ee_force": 50.0,
            "max_ee_force_human": 10.0,
            "max_contact_force": 80.0,
            "max_gripper_force": 40.0,
        },
    },
    "arm_mobile": {
        "workspace": {
            "box": {
                "x_min": -10.0,
                "y_min": -10.0,
                "z_min": 0.0,
                "x_max": 10.0,
                "y_max": 10.0,
                "z_max": 2.0,
            }
        },
        "joints": {
            **{
                f"joint_{i}": {
                    "position_min": -3.14,
                    "position_max": 3.14,
                    "velocity_max": 2.0,
                    "torque_max": 50.0,
                }
                for i in range(6)
            },
            "left_wheel": {
                "position_min": -1e9,
                "position_max": 1e9,
                "velocity_max": 10.0,
                "torque_max": 5.0,
            },
            "right_wheel": {
                "position_min": -1e9,
                "position_max": 1e9,
                "velocity_max": 10.0,
                "torque_max": 5.0,
            },
        },
        "force": {
            "max_ee_force": 50.0,
            "max_ee_force_human": 10.0,
            "max_contact_force": 80.0,
            "max_gripper_force": 40.0,
        },
    },
}


# ---------------------------------------------------------------------------
# BoundsChecker (facade)
# ---------------------------------------------------------------------------


class BoundsChecker:
    """Aggregates workspace, joint, and force bounds checking."""

    def __init__(
        self,
        workspace: Optional[WorkspaceBounds] = None,
        joints: Optional[JointBounds] = None,
        force: Optional[ForceBounds] = None,
    ):
        self.workspace = workspace or WorkspaceBounds()
        self.joints = joints or JointBounds()
        self.force = force or ForceBounds()

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> BoundsChecker:
        """Build a BoundsChecker from a config dict."""
        ws_cfg = config.get("workspace", {})
        workspace = WorkspaceBounds(
            sphere=Sphere(**ws_cfg["sphere"]) if "sphere" in ws_cfg else None,
            box=Box(**ws_cfg["box"]) if "box" in ws_cfg else None,
            forbidden_spheres=[Sphere(**s) for s in ws_cfg.get("forbidden_spheres", [])],
            forbidden_boxes=[Box(**b) for b in ws_cfg.get("forbidden_boxes", [])],
        )

        jb = JointBounds()
        for jid, jcfg in config.get("joints", {}).items():
            jb.set_joint(jid, JointLimits(**jcfg))

        force_cfg = config.get("force", {})
        force = ForceBounds(**force_cfg) if force_cfg else ForceBounds()

        return cls(workspace=workspace, joints=jb, force=force)

    @classmethod
    def from_robot_type(cls, robot_type: str) -> BoundsChecker:
        """Load default config for a known robot type."""
        if robot_type not in DEFAULT_CONFIGS:
            raise ValueError(
                f"Unknown robot type '{robot_type}'. Known: {list(DEFAULT_CONFIGS.keys())}"
            )
        return cls.from_config(DEFAULT_CONFIGS[robot_type])

    @classmethod
    def from_virtual_fs(cls, ns: Any) -> BoundsChecker:
        """Load config from /etc/safety/bounds in the virtual filesystem."""
        config_data = ns.read("/etc/safety/bounds") if ns else None
        if config_data and isinstance(config_data, dict):
            return cls.from_config(config_data)
        # Fallback to arm defaults
        return cls.from_robot_type("arm")

    def check_action(self, action: Dict[str, Any]) -> BoundsResult:
        """One-call validation of an action dict.

        Supported keys:
            position: [x, y, z]
            joints: {joint_id: {position, velocity, torque}}
            force: float (end-effector force in N)
            contact_force: float
            gripper_force: float
        """
        results: List[BoundsResult] = []

        pos = action.get("position")
        if pos and len(pos) >= 3:
            results.append(self.workspace.check_position(pos[0], pos[1], pos[2]))

        joints = action.get("joints", {})
        for jid, jvals in joints.items():
            if isinstance(jvals, dict):
                results.append(
                    self.joints.check_joint(
                        jid,
                        position=jvals.get("position"),
                        velocity=jvals.get("velocity"),
                        torque=jvals.get("torque"),
                    )
                )

        if "force" in action:
            results.append(self.force.check_force(action["force"]))
        if "contact_force" in action:
            results.append(self.force.check_contact_force(action["contact_force"]))
        if "gripper_force" in action:
            results.append(self.force.check_gripper_force(action["gripper_force"]))

        return BoundsResult.combine(results) if results else BoundsResult()


# ---------------------------------------------------------------------------
# Integration helper for SafetyLayer
# ---------------------------------------------------------------------------


def check_write_bounds(
    checker: BoundsChecker,
    path: str,
    data: Any,
) -> BoundsResult:
    """Check bounds for a write to /dev/motor or /dev/arm paths.

    Returns the BoundsResult. Caller should block on violation, log on warning.
    """
    if not isinstance(data, dict):
        return BoundsResult()

    action: Dict[str, Any] = {}

    if path.startswith("/dev/arm"):
        # Arm writes may contain position, joints, force
        if "position" in data:
            action["position"] = data["position"]
        if "joints" in data:
            action["joints"] = data["joints"]
        if "force" in data:
            action["force"] = data["force"]
        if "gripper_force" in data:
            action["gripper_force"] = data["gripper_force"]

    elif path.startswith("/dev/motor"):
        # Motor writes may contain joints/velocities
        if "joints" in data:
            action["joints"] = data["joints"]
        if "velocity" in data and "joint_id" in data:
            action["joints"] = {data["joint_id"]: {"velocity": data["velocity"]}}

    if not action:
        return BoundsResult()

    return checker.check_action(action)
