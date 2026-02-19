"""ScoutSpecialist — autonomous exploration and mapping."""

from __future__ import annotations

import math
import time
from copy import deepcopy

from .base_specialist import BaseSpecialist, Task, TaskResult, TaskStatus

# Grid configuration
_GRID_SIZE = 20  # 20x20 cells
_CELL_SIZE_M = 0.5  # metres per cell
_ROBOT_START = (10, 10)  # centre cell (grid coords)

CellState = str  # "free" | "occupied" | "unknown"


def _build_initial_grid() -> dict[tuple[int, int], CellState]:
    """Create a 20x20 grid with all cells unknown except the start cell."""
    grid: dict[tuple[int, int], CellState] = {}
    for r in range(_GRID_SIZE):
        for c in range(_GRID_SIZE):
            grid[(r, c)] = "unknown"
    # Robot start cell is known-free
    grid[_ROBOT_START] = "free"
    return grid


def _cell_to_world(cell: tuple[int, int]) -> tuple[float, float]:
    """Convert grid cell (row, col) to world coordinates (x, y) in metres."""
    r, c = cell
    # Origin is at robot start; x = col offset, y = row offset
    x = (c - _ROBOT_START[1]) * _CELL_SIZE_M
    y = (r - _ROBOT_START[0]) * _CELL_SIZE_M
    return round(x, 3), round(y, 3)


def _world_to_cell(x: float, y: float) -> tuple[int, int]:
    """Convert world coordinates to grid cell."""
    col = int(round(x / _CELL_SIZE_M)) + _ROBOT_START[1]
    row = int(round(y / _CELL_SIZE_M)) + _ROBOT_START[0]
    return (
        max(0, min(_GRID_SIZE - 1, row)),
        max(0, min(_GRID_SIZE - 1, col)),
    )


def _neighbours(cell: tuple[int, int]) -> list[tuple[int, int]]:
    """Return 4-connected neighbours within grid bounds."""
    r, c = cell
    result = []
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = r + dr, c + dc
        if 0 <= nr < _GRID_SIZE and 0 <= nc < _GRID_SIZE:
            result.append((nr, nc))
    return result


def _find_frontiers(
    grid: dict[tuple[int, int], CellState],
    robot_cell: tuple[int, int],
) -> list[tuple[int, int]]:
    """
    Frontier cells: known-free cells adjacent to at least one unknown cell.
    Returns sorted by distance from robot_cell (nearest first).
    """
    frontiers = []
    for cell, state in grid.items():
        if state != "free":
            continue
        for nb in _neighbours(cell):
            if grid.get(nb, "unknown") == "unknown":
                frontiers.append(cell)
                break

    # Sort by Euclidean distance in grid coords
    def dist(c: tuple[int, int]) -> float:
        return math.sqrt((c[0] - robot_cell[0]) ** 2 + (c[1] - robot_cell[1]) ** 2)

    frontiers.sort(key=dist)
    return frontiers


class ScoutSpecialist(BaseSpecialist):
    """Autonomous exploration and mapping specialist."""

    name = "scout"
    capabilities = ["scout", "map", "search", "explore"]

    def __init__(self) -> None:
        # Occupancy grid: serializable dict, no numpy
        self._grid: dict[tuple[int, int], CellState] = _build_initial_grid()
        self._robot_cell: tuple[int, int] = _ROBOT_START

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @property
    def grid(self) -> dict[tuple[int, int], CellState]:
        """Read-only view; use update_cell to modify."""
        return deepcopy(self._grid)

    def update_cell(self, cell: tuple[int, int], state: CellState) -> None:
        """Mark a grid cell as free/occupied/unknown."""
        if 0 <= cell[0] < _GRID_SIZE and 0 <= cell[1] < _GRID_SIZE:
            self._grid[cell] = state

    def mark_path_free(self, cells: list[tuple[int, int]]) -> None:
        """Mark a list of cells as free (e.g., after traversal)."""
        for cell in cells:
            self.update_cell(cell, "free")

    def estimate_duration_s(self, task: Task) -> float:
        if task.type in ("scout", "explore"):
            # Count unknown cells and estimate time
            unknown = sum(1 for s in self._grid.values() if s == "unknown")
            return max(2.0, unknown * 0.05)
        return 2.0

    def health(self) -> dict:
        base = super().health()
        free = sum(1 for s in self._grid.values() if s == "free")
        unknown = sum(1 for s in self._grid.values() if s == "unknown")
        occupied = sum(1 for s in self._grid.values() if s == "occupied")
        base.update(
            {
                "grid_size": f"{_GRID_SIZE}x{_GRID_SIZE}",
                "cell_size_m": _CELL_SIZE_M,
                "cells_free": free,
                "cells_unknown": unknown,
                "cells_occupied": occupied,
                "robot_cell": list(self._robot_cell),
            }
        )
        return base

    async def execute(self, task: Task) -> TaskResult:
        start = time.monotonic()

        handler = {
            "scout": self._scout,
            "map": self._map,
            "search": self._search,
            "explore": self._explore,
        }.get(task.type)

        if handler is None:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                duration_s=time.monotonic() - start,
                error=f"ScoutSpecialist cannot handle task type '{task.type}'",
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

    async def _scout(self, task: Task) -> dict:  # noqa: ARG002
        """Generate frontier-based exploration waypoints."""
        frontiers = _find_frontiers(self._grid, self._robot_cell)
        waypoints = []

        # Pick up to 5 nearest frontier waypoints
        for i, cell in enumerate(frontiers[:5]):
            wx, wy = _cell_to_world(cell)
            waypoints.append(
                {
                    "x": wx,
                    "y": wy,
                    "reason": f"frontier cell {cell}, step {i + 1}",
                }
            )
            # Mark adjacent unknown cells as free to simulate expansion
            for nb in _neighbours(cell):
                if self._grid.get(nb) == "unknown":
                    self._grid[nb] = "free"
            self._grid[cell] = "free"

        if not waypoints:
            waypoints.append(
                {"x": 0.0, "y": 0.0, "reason": "no frontiers found, returning to origin"}
            )

        return {
            "waypoints": waypoints,
            "frontiers_found": len(frontiers),
            "robot_cell": list(self._robot_cell),
        }

    async def _explore(self, task: Task) -> dict:
        """Alias for scout with optional step count."""
        params = task.params
        steps = int(params.get("steps", 3))
        frontiers = _find_frontiers(self._grid, self._robot_cell)
        waypoints = []

        for i, cell in enumerate(frontiers[:steps]):
            wx, wy = _cell_to_world(cell)
            waypoints.append(
                {
                    "x": wx,
                    "y": wy,
                    "reason": f"explore step {i + 1}, frontier at {cell}",
                }
            )
            # Simulate moving to cell and discovering neighbours
            self._robot_cell = cell
            self._grid[cell] = "free"
            for nb in _neighbours(cell):
                if self._grid.get(nb) == "unknown":
                    self._grid[nb] = "free"

        if not waypoints:
            waypoints.append({"x": 0.0, "y": 0.0, "reason": "fully explored or no frontiers"})

        return {
            "waypoints": waypoints,
            "steps_taken": len(waypoints),
            "robot_cell": list(self._robot_cell),
        }

    async def _map(self, task: Task) -> dict:  # noqa: ARG002
        """Return a serializable snapshot of the occupancy grid."""
        # Serialize keys as strings for JSON-compatibility
        serialized = {f"{r},{c}": state for (r, c), state in self._grid.items()}
        free = sum(1 for s in self._grid.values() if s == "free")
        unknown = sum(1 for s in self._grid.values() if s == "unknown")
        occupied = sum(1 for s in self._grid.values() if s == "occupied")

        return {
            "grid": serialized,
            "grid_size": _GRID_SIZE,
            "cell_size_m": _CELL_SIZE_M,
            "summary": {
                "free": free,
                "unknown": unknown,
                "occupied": occupied,
            },
        }

    async def _search(self, task: Task) -> dict:
        """Search for a named target object in the scene."""
        params = task.params
        target_name = params.get("target") or params.get("object") or params.get("target_name")
        if not target_name:
            raise ValueError("'target' is required for search tasks (name of object to find)")

        # Simulate search: generate waypoints covering quadrants
        quadrant_centers = [
            (_ROBOT_START[0] - 5, _ROBOT_START[1] - 5),
            (_ROBOT_START[0] - 5, _ROBOT_START[1] + 5),
            (_ROBOT_START[0] + 5, _ROBOT_START[1] - 5),
            (_ROBOT_START[0] + 5, _ROBOT_START[1] + 5),
        ]
        waypoints = []
        for i, cell in enumerate(quadrant_centers):
            wx, wy = _cell_to_world(cell)
            waypoints.append(
                {
                    "x": wx,
                    "y": wy,
                    "reason": f"searching quadrant {i + 1} for '{target_name}'",
                }
            )

        # Simulate no target found unless params specify found=True
        found = bool(params.get("found", False))
        found_position = params.get("found_position")

        return {
            "target": target_name,
            "found": found,
            "found_position": found_position,
            "search_waypoints": waypoints,
            "cells_searched": len(waypoints),
        }
