"""DockSpecialist — return-to-base / charging."""

from __future__ import annotations

import math
import time

from .base_specialist import BaseSpecialist, Task, TaskResult, TaskStatus

# Speed for return-home navigation (m/s)
_TRAVEL_SPEED_MS: float = 0.5
# Deceleration steps for final approach
_DECEL_STEPS: int = 5
# Battery threshold: refuse to dock if battery > 80%
_BATTERY_DOCK_THRESHOLD: float = 80.0
# Home position in world coordinates
_HOME_POSITION: tuple[float, float] = (0.0, 0.0)


def _generate_approach_path(
    current: tuple[float, float],
    dock: tuple[float, float],
) -> list[dict]:
    """
    Generate a smooth deceleration path from current position to dock position.

    Produces _DECEL_STEPS intermediate waypoints with reducing speed.
    """
    waypoints: list[dict] = []
    cx, cy = current
    dx, dy = dock

    for i in range(1, _DECEL_STEPS + 1):
        t = i / _DECEL_STEPS  # 0 < t <= 1
        # Ease-in deceleration: slow down as we approach dock
        speed_factor = 1.0 - (t**2)  # quadratic deceleration
        wx = round(cx + t * (dx - cx), 4)
        wy = round(cy + t * (dy - cy), 4)
        waypoints.append(
            {
                "x": wx,
                "y": wy,
                "speed": round(max(0.05, _TRAVEL_SPEED_MS * speed_factor), 4),
                "step": i,
            }
        )
    return waypoints


def _estimate_travel_time(
    current: tuple[float, float],
    target: tuple[float, float],
) -> float:
    dist = math.sqrt((target[0] - current[0]) ** 2 + (target[1] - current[1]) ** 2)
    return max(1.0, dist / _TRAVEL_SPEED_MS)


class DockSpecialist(BaseSpecialist):
    """Return-to-base / charging dock specialist."""

    name = "dock"
    capabilities = ["dock", "undock", "charge", "return_home"]

    def estimate_duration_s(self, task: Task) -> float:
        if task.type in ("dock", "return_home"):
            current = task.params.get("current_position", [0.0, 0.0])
            if task.type == "dock":
                target = task.params.get("dock_position", [0.0, 0.0])
            else:
                target = list(_HOME_POSITION)
            if isinstance(current, (list, tuple)) and len(current) >= 2:
                cx, cy = float(current[0]), float(current[1])
            else:
                cx, cy = 0.0, 0.0
            if isinstance(target, (list, tuple)) and len(target) >= 2:
                tx, ty = float(target[0]), float(target[1])
            else:
                tx, ty = 0.0, 0.0
            return _estimate_travel_time((cx, cy), (tx, ty))
        return 2.0

    def health(self) -> dict:
        base = super().health()
        base["dock_threshold_pct"] = _BATTERY_DOCK_THRESHOLD
        base["home_position"] = list(_HOME_POSITION)
        return base

    async def execute(self, task: Task) -> TaskResult:
        start = time.monotonic()

        handler = {
            "dock": self._dock,
            "undock": self._undock,
            "charge": self._charge,
            "return_home": self._return_home,
        }.get(task.type)

        if handler is None:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                duration_s=time.monotonic() - start,
                error=f"DockSpecialist cannot handle task type '{task.type}'",
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

    async def _dock(self, task: Task) -> dict:
        params = task.params

        # Battery check
        battery = params.get("battery_level")
        if battery is not None:
            try:
                battery_pct = float(battery)
            except (TypeError, ValueError):
                raise ValueError(f"'battery_level' must be a number, got {battery!r}") from None
            if battery_pct > _BATTERY_DOCK_THRESHOLD:
                raise ValueError(
                    f"Battery level {battery_pct:.1f}% exceeds dock threshold "
                    f"{_BATTERY_DOCK_THRESHOLD:.1f}%; docking unnecessary"
                )

        dock_position = params.get("dock_position")

        if dock_position is None:
            # No known dock — return search instructions
            return {
                "action": "search_for_dock",
                "instructions": [
                    "Rotate 360° scanning for dock marker",
                    "Move toward detected dock marker",
                    "Align with dock IR/vision sensor",
                    "Execute slow final approach",
                ],
                "dock_position": None,
                "waypoints": [],
            }

        # Known dock position — generate approach path
        if not isinstance(dock_position, (list, tuple)) or len(dock_position) < 2:
            raise ValueError("'dock_position' must be a list/tuple of [x, y]")

        dock_pos = (float(dock_position[0]), float(dock_position[1]))
        current_position = params.get("current_position", [0.0, 0.0])
        if isinstance(current_position, (list, tuple)) and len(current_position) >= 2:
            current = (float(current_position[0]), float(current_position[1]))
        else:
            current = (0.0, 0.0)

        waypoints = _generate_approach_path(current, dock_pos)

        return {
            "action": "dock",
            "dock_position": list(dock_pos),
            "current_position": list(current),
            "waypoints": waypoints,
            "final_speed": 0.05,
            "battery_level": battery,
        }

    async def _undock(self, task: Task) -> dict:
        params = task.params
        current_position = params.get("current_position", [0.0, 0.0])
        if isinstance(current_position, (list, tuple)) and len(current_position) >= 2:
            cx, cy = float(current_position[0]), float(current_position[1])
        else:
            cx, cy = 0.0, 0.0

        # Reverse out slowly, then free
        waypoints = [
            {"x": round(cx - 0.1, 4), "y": cy, "speed": 0.1, "step": 1},
            {"x": round(cx - 0.3, 4), "y": cy, "speed": 0.2, "step": 2},
            {"x": round(cx - 0.5, 4), "y": cy, "speed": 0.3, "step": 3},
        ]

        return {
            "action": "undock",
            "current_position": [cx, cy],
            "waypoints": waypoints,
        }

    async def _charge(self, task: Task) -> dict:
        """Ensure robot is docked and initiate charging."""
        params = task.params
        battery = params.get("battery_level")
        if battery is not None:
            battery_pct = float(battery)
            if battery_pct > _BATTERY_DOCK_THRESHOLD:
                raise ValueError(
                    f"Battery at {battery_pct:.1f}%; charging not needed above {_BATTERY_DOCK_THRESHOLD:.1f}%"
                )

        dock_position = params.get("dock_position")
        if dock_position is None:
            return {
                "action": "charge",
                "status": "search_required",
                "message": "No dock position known; run 'dock' task first",
            }

        return {
            "action": "charge",
            "status": "charging_initiated",
            "dock_position": dock_position,
            "battery_level": battery,
            "estimated_full_charge_min": 60,
        }

    async def _return_home(self, task: Task) -> dict:
        """Generate waypoints from current position back to home (0, 0)."""
        params = task.params
        current_position = params.get("current_position", [0.0, 0.0])
        if isinstance(current_position, (list, tuple)) and len(current_position) >= 2:
            current = (float(current_position[0]), float(current_position[1]))
        else:
            current = (0.0, 0.0)

        waypoints = _generate_approach_path(current, _HOME_POSITION)

        return {
            "action": "return_home",
            "home_position": list(_HOME_POSITION),
            "current_position": list(current),
            "waypoints": waypoints,
            "estimated_time_s": _estimate_travel_time(current, _HOME_POSITION),
        }
