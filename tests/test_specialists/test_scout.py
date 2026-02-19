"""Tests for ScoutSpecialist."""

from __future__ import annotations

import asyncio

import pytest

from castor.specialists.base_specialist import Task, TaskStatus
from castor.specialists.scout import (
    ScoutSpecialist,
    _GRID_SIZE,
    _ROBOT_START,
    _build_initial_grid,
    _cell_to_world,
    _find_frontiers,
    _neighbours,
    _world_to_cell,
)


def run(coro):
    return asyncio.run(coro)


class TestOccupancyGrid:
    def test_initial_grid_size(self):
        grid = _build_initial_grid()
        assert len(grid) == _GRID_SIZE * _GRID_SIZE

    def test_start_cell_is_free(self):
        grid = _build_initial_grid()
        assert grid[_ROBOT_START] == "free"

    def test_other_cells_unknown(self):
        grid = _build_initial_grid()
        for cell, state in grid.items():
            if cell != _ROBOT_START:
                assert state == "unknown", f"Cell {cell} should be unknown, got {state}"

    def test_grid_is_dict(self):
        grid = _build_initial_grid()
        assert isinstance(grid, dict)

    def test_grid_keys_are_tuples(self):
        grid = _build_initial_grid()
        for key in grid:
            assert isinstance(key, tuple)
            assert len(key) == 2

    def test_cell_to_world_center(self):
        x, y = _cell_to_world(_ROBOT_START)
        assert x == 0.0
        assert y == 0.0

    def test_cell_to_world_offset(self):
        # One cell right from center = 0.5m in x
        cell = (_ROBOT_START[0], _ROBOT_START[1] + 1)
        x, y = _cell_to_world(cell)
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.0)

    def test_world_to_cell_origin(self):
        cell = _world_to_cell(0.0, 0.0)
        assert cell == _ROBOT_START

    def test_world_to_cell_roundtrip(self):
        original = (_ROBOT_START[0] + 3, _ROBOT_START[1] - 2)
        x, y = _cell_to_world(original)
        recovered = _world_to_cell(x, y)
        assert recovered == original


class TestNeighbours:
    def test_center_has_4_neighbours(self):
        nbs = _neighbours(_ROBOT_START)
        assert len(nbs) == 4

    def test_corner_has_2_neighbours(self):
        nbs = _neighbours((0, 0))
        assert len(nbs) == 2

    def test_edge_has_3_neighbours(self):
        nbs = _neighbours((0, 5))
        assert len(nbs) == 3

    def test_all_within_bounds(self):
        nbs = _neighbours((5, 5))
        for r, c in nbs:
            assert 0 <= r < _GRID_SIZE
            assert 0 <= c < _GRID_SIZE


class TestFrontierFinding:
    def test_start_is_frontier(self):
        grid = _build_initial_grid()
        frontiers = _find_frontiers(grid, _ROBOT_START)
        assert _ROBOT_START in frontiers

    def test_frontiers_sorted_by_distance(self):
        grid = _build_initial_grid()
        # Mark a few cells free far away
        far_cell = (0, 0)
        near_cell = (_ROBOT_START[0] + 1, _ROBOT_START[1])
        grid[far_cell] = "free"
        grid[near_cell] = "free"
        frontiers = _find_frontiers(grid, _ROBOT_START)
        # Near cell should come before far cell
        if near_cell in frontiers and far_cell in frontiers:
            assert frontiers.index(near_cell) < frontiers.index(far_cell)

    def test_occupied_cell_not_frontier(self):
        grid = _build_initial_grid()
        # Mark start as occupied instead
        grid[_ROBOT_START] = "occupied"
        frontiers = _find_frontiers(grid, _ROBOT_START)
        assert _ROBOT_START not in frontiers

    def test_fully_known_grid_has_no_frontiers(self):
        grid = {(r, c): "free" for r in range(_GRID_SIZE) for c in range(_GRID_SIZE)}
        frontiers = _find_frontiers(grid, _ROBOT_START)
        assert len(frontiers) == 0


class TestScoutSpecialist:
    def setup_method(self):
        self.spec = ScoutSpecialist()

    def test_name(self):
        assert self.spec.name == "scout"

    def test_capabilities(self):
        assert set(self.spec.capabilities) == {"scout", "map", "search", "explore"}

    def test_can_handle_scout(self):
        task = Task(type="scout", goal="explore")
        assert self.spec.can_handle(task) is True

    def test_cannot_handle_grasp(self):
        task = Task(type="grasp", goal="grasp")
        assert self.spec.can_handle(task) is False

    # ------------------------------------------------------------------ #
    # Scout task
    # ------------------------------------------------------------------ #

    def test_scout_returns_waypoints(self):
        task = Task(type="scout", goal="explore area")
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert "waypoints" in result.output
        assert isinstance(result.output["waypoints"], list)

    def test_scout_waypoints_have_required_keys(self):
        task = Task(type="scout", goal="explore")
        result = run(self.spec.execute(task))
        for wp in result.output["waypoints"]:
            assert "x" in wp
            assert "y" in wp
            assert "reason" in wp

    def test_scout_waypoints_are_floats(self):
        task = Task(type="scout", goal="explore")
        result = run(self.spec.execute(task))
        for wp in result.output["waypoints"]:
            assert isinstance(wp["x"], float)
            assert isinstance(wp["y"], float)

    def test_scout_frontiers_count(self):
        task = Task(type="scout", goal="explore")
        result = run(self.spec.execute(task))
        assert "frontiers_found" in result.output
        assert result.output["frontiers_found"] >= 0

    def test_scout_updates_grid(self):
        unknown_before = sum(1 for s in self.spec._grid.values() if s == "unknown")
        task = Task(type="scout", goal="explore")
        run(self.spec.execute(task))
        unknown_after = sum(1 for s in self.spec._grid.values() if s == "unknown")
        # Grid should have fewer unknown cells after scouting
        assert unknown_after <= unknown_before

    # ------------------------------------------------------------------ #
    # Explore task
    # ------------------------------------------------------------------ #

    def test_explore_returns_waypoints(self):
        task = Task(type="explore", goal="explore", params={"steps": 3})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert "waypoints" in result.output

    def test_explore_respects_steps(self):
        task = Task(type="explore", goal="explore", params={"steps": 2})
        result = run(self.spec.execute(task))
        # Steps taken should not exceed requested steps
        assert result.output["steps_taken"] <= 2

    def test_explore_multiple_steps(self):
        task = Task(type="explore", goal="explore", params={"steps": 5})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert len(result.output["waypoints"]) <= 5

    # ------------------------------------------------------------------ #
    # Map task
    # ------------------------------------------------------------------ #

    def test_map_returns_grid(self):
        task = Task(type="map", goal="get map")
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert "grid" in result.output
        assert isinstance(result.output["grid"], dict)

    def test_map_serializable(self):
        import json

        task = Task(type="map", goal="get map")
        result = run(self.spec.execute(task))
        # Should be JSON-serializable
        json_str = json.dumps(result.output)
        assert len(json_str) > 0

    def test_map_summary(self):
        task = Task(type="map", goal="get map")
        result = run(self.spec.execute(task))
        summary = result.output["summary"]
        total = summary["free"] + summary["unknown"] + summary["occupied"]
        assert total == _GRID_SIZE * _GRID_SIZE

    # ------------------------------------------------------------------ #
    # Search task
    # ------------------------------------------------------------------ #

    def test_search_returns_waypoints(self):
        task = Task(type="search", goal="find cup", params={"target": "cup"})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.SUCCESS
        assert "search_waypoints" in result.output

    def test_search_target_in_output(self):
        task = Task(type="search", goal="find cup", params={"target": "cup"})
        result = run(self.spec.execute(task))
        assert result.output["target"] == "cup"

    def test_search_not_found_by_default(self):
        task = Task(type="search", goal="find cup", params={"target": "cup"})
        result = run(self.spec.execute(task))
        assert result.output["found"] is False

    def test_search_found_flag(self):
        task = Task(
            type="search",
            goal="find cup",
            params={"target": "cup", "found": True, "found_position": [1.0, 2.0]},
        )
        result = run(self.spec.execute(task))
        assert result.output["found"] is True

    def test_search_missing_target(self):
        task = Task(type="search", goal="find nothing", params={})
        result = run(self.spec.execute(task))
        assert result.status == TaskStatus.FAILED
        assert result.error is not None

    # ------------------------------------------------------------------ #
    # Occupancy grid property
    # ------------------------------------------------------------------ #

    def test_grid_property_returns_copy(self):
        grid = self.spec.grid
        grid[(0, 0)] = "occupied"
        # Original should be unchanged
        assert self.spec._grid[(0, 0)] != "occupied"

    def test_update_cell(self):
        self.spec.update_cell((5, 5), "occupied")
        assert self.spec._grid[(5, 5)] == "occupied"

    def test_mark_path_free(self):
        cells = [(3, 3), (3, 4), (3, 5)]
        self.spec.mark_path_free(cells)
        for cell in cells:
            assert self.spec._grid[cell] == "free"

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    def test_health_keys(self):
        h = self.spec.health()
        assert "grid_size" in h
        assert "cells_free" in h
        assert "cells_unknown" in h
        assert "robot_cell" in h

    # ------------------------------------------------------------------ #
    # Duration estimation
    # ------------------------------------------------------------------ #

    def test_estimate_scout_duration(self):
        task = Task(type="scout", goal="scout")
        d = self.spec.estimate_duration_s(task)
        assert d >= 2.0
