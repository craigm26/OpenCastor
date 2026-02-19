"""ManipulatorSpecialist — arm/gripper control planning."""

from __future__ import annotations

import math
import time

from .base_specialist import BaseSpecialist, Task, TaskResult, TaskStatus

# Safe home joint angles for a 6-DOF arm (radians)
_HOME_JOINT_ANGLES: list[float] = [0.0, -0.785, 1.571, 0.0, 0.785, 0.0]

# Assumed current position (origin) for duration estimation
_CURRENT_POSITION: tuple[float, float, float] = (0.0, 0.0, 0.0)

# Approximate speed: 0.3 m/s end-effector
_EE_SPEED_MS: float = 0.3


def _vec_distance(a: list[float] | tuple, b: list[float] | tuple) -> float:
    """Euclidean distance between two 3D points."""
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b, strict=False)))


def _generate_joint_angles(
    approach_vector: list[float],
    gripper_closed: bool = True,
) -> list[float]:
    """
    Generate a 6-DOF joint angle sequence from an approach vector.

    This is a simplified kinematic stub; real IK would live in hardware drivers.
    Returns a list of 6 floats in radians.
    """
    ax, ay, az = approach_vector[:3] if len(approach_vector) >= 3 else (*approach_vector, 0.0)

    # Simple heuristic mapping approach direction → shoulder + elbow angles
    shoulder_pan = math.atan2(ay, ax)
    shoulder_lift = math.atan2(az, math.sqrt(ax**2 + ay**2)) - math.pi / 4
    elbow = math.pi / 3
    wrist_1 = -(shoulder_lift + elbow)
    wrist_2 = 0.0
    wrist_3 = shoulder_pan

    angles = [shoulder_pan, shoulder_lift, elbow, wrist_1, wrist_2, wrist_3]
    return [round(a, 4) for a in angles]


class ManipulatorSpecialist(BaseSpecialist):
    """Arm/gripper control planning specialist."""

    name = "manipulator"
    capabilities = ["grasp", "place", "push", "home"]

    def estimate_duration_s(self, task: Task) -> float:
        if task.type == "home":
            return 2.0
        pos = task.params.get("object_position")
        if pos and isinstance(pos, (list, tuple)) and len(pos) >= 3:
            dist = _vec_distance(_CURRENT_POSITION, pos[:3])
            return max(1.0, dist / _EE_SPEED_MS)
        return 3.0

    def health(self) -> dict:
        base = super().health()
        base["arm_dof"] = 6
        base["gripper"] = "available"
        return base

    async def execute(self, task: Task) -> TaskResult:
        start = time.monotonic()

        handler = {
            "grasp": self._grasp,
            "place": self._place,
            "push": self._push,
            "home": self._home,
        }.get(task.type)

        if handler is None:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                duration_s=time.monotonic() - start,
                error=f"ManipulatorSpecialist cannot handle task type '{task.type}'",
            )

        try:
            output = await handler(task)
        except Exception as exc:  # noqa: BLE001
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                duration_s=time.monotonic() - start,
                error=str(exc),
            )

        return TaskResult(
            task_id=task.id,
            status=TaskStatus.SUCCESS,
            output=output,
            duration_s=time.monotonic() - start,
        )

    # ------------------------------------------------------------------ #
    # Internal handlers
    # ------------------------------------------------------------------ #

    async def _grasp(self, task: Task) -> dict:
        params = task.params
        obj_pos = params.get("object_position")
        if obj_pos is None:
            raise ValueError("'object_position' is required for grasp tasks")
        if not isinstance(obj_pos, (list, tuple)) or len(obj_pos) < 3:
            raise ValueError("'object_position' must be a list/tuple of 3 floats [x, y, z]")

        obj_pos = [float(v) for v in obj_pos[:3]]

        # Approach vector: from current position toward object, normalised
        dx = obj_pos[0] - _CURRENT_POSITION[0]
        dy = obj_pos[1] - _CURRENT_POSITION[1]
        dz = obj_pos[2] - _CURRENT_POSITION[2]
        mag = math.sqrt(dx**2 + dy**2 + dz**2) or 1.0
        approach_vector = [round(dx / mag, 4), round(dy / mag, 4), round(dz / mag, 4)]

        joint_angles = _generate_joint_angles(approach_vector, gripper_closed=True)

        return {
            "joint_angles": joint_angles,
            "gripper_state": "closed",
            "approach_vector": approach_vector,
            "object_position": obj_pos,
        }

    async def _place(self, task: Task) -> dict:
        params = task.params
        place_pos = params.get("place_position") or params.get("object_position")
        if place_pos is None:
            raise ValueError("'place_position' is required for place tasks")
        place_pos = [float(v) for v in place_pos[:3]]

        dx = place_pos[0] - _CURRENT_POSITION[0]
        dy = place_pos[1] - _CURRENT_POSITION[1]
        dz = place_pos[2] - _CURRENT_POSITION[2]
        mag = math.sqrt(dx**2 + dy**2 + dz**2) or 1.0
        approach_vector = [round(dx / mag, 4), round(dy / mag, 4), round(dz / mag, 4)]
        joint_angles = _generate_joint_angles(approach_vector, gripper_closed=False)

        return {
            "joint_angles": joint_angles,
            "gripper_state": "open",
            "approach_vector": approach_vector,
            "place_position": place_pos,
        }

    async def _push(self, task: Task) -> dict:
        params = task.params
        target = params.get("target_position") or params.get("object_position")
        if target is None:
            raise ValueError("'target_position' is required for push tasks")
        target = [float(v) for v in target[:3]]

        dx = target[0] - _CURRENT_POSITION[0]
        dy = target[1] - _CURRENT_POSITION[1]
        dz = target[2] - _CURRENT_POSITION[2]
        mag = math.sqrt(dx**2 + dy**2 + dz**2) or 1.0
        approach_vector = [round(dx / mag, 4), round(dy / mag, 4), round(dz / mag, 4)]
        joint_angles = _generate_joint_angles(approach_vector, gripper_closed=False)
        push_vector = [round(-v, 4) for v in approach_vector]  # push direction

        return {
            "joint_angles": joint_angles,
            "gripper_state": "open",
            "approach_vector": approach_vector,
            "push_vector": push_vector,
            "target_position": target,
        }

    async def _home(self, task: Task) -> dict:  # noqa: ARG002
        return {
            "joint_angles": list(_HOME_JOINT_ANGLES),
            "gripper_state": "open",
            "approach_vector": [0.0, 0.0, 1.0],
            "position": "home",
        }
