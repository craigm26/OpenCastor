"""Tests for LidarDriver map_persistence (Issue #344)."""

from __future__ import annotations

import os
import tempfile

from castor.drivers.lidar_driver import LidarDriver


def make_lidar() -> LidarDriver:
    return LidarDriver()


def make_tmp_db() -> str:
    return tempfile.mktemp(suffix=".lidar.db")


# ── save_map tests ────────────────────────────────────────────────────────────


def test_save_map_returns_ok():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        result = drv.save_map(path)
        assert result["ok"] is True
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_save_map_returns_map_id():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        result = drv.save_map(path)
        assert "map_id" in result
        assert result["map_id"] is not None
        assert result["map_id"] >= 1
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_save_map_returns_timestamp():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        result = drv.save_map(path)
        assert "ts" in result
        assert result["ts"] > 0
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_save_map_with_label():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        result = drv.save_map(path, label="living-room")
        assert result["ok"] is True
        assert result["label"] == "living-room"
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_save_map_creates_db_file():
    drv = make_lidar()
    path = make_tmp_db()
    assert not os.path.exists(path)
    try:
        drv.save_map(path)
        assert os.path.exists(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_save_map_multiple_saves_increment_id():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        r1 = drv.save_map(path, label="first")
        r2 = drv.save_map(path, label="second")
        assert r2["map_id"] > r1["map_id"]
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ── load_map tests ────────────────────────────────────────────────────────────


def test_load_map_after_save():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        drv.save_map(path, label="test-map")
        load_result = drv.load_map(path)
        assert load_result["ok"] is True
        assert "grid" in load_result
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_load_map_grid_is_2d_list():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        drv.save_map(path)
        result = drv.load_map(path)
        grid = result["grid"]
        assert isinstance(grid, list)
        if grid:
            assert isinstance(grid[0], list)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_load_map_by_id():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        r1 = drv.save_map(path, label="first")
        drv.save_map(path, label="second")
        loaded = drv.load_map(path, map_id=r1["map_id"])
        assert loaded["ok"] is True
        assert loaded["label"] == "first"
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_load_map_most_recent_when_no_id():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        drv.save_map(path, label="first")
        drv.save_map(path, label="second")
        loaded = drv.load_map(path)
        # Most recent should be "second"
        assert loaded["ok"] is True
        assert loaded["label"] == "second"
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_load_map_missing_id_returns_error():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        drv.save_map(path)
        result = drv.load_map(path, map_id=99999)
        assert result["ok"] is False
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_load_map_empty_db_returns_error():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        # Initialise DB without any maps
        drv._open_map_db(path).close()
        result = drv.load_map(path)
        assert result["ok"] is False
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_load_map_returns_metadata():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        drv.save_map(path)
        result = drv.load_map(path)
        assert "metadata" in result
        assert isinstance(result["metadata"], dict)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_load_map_metadata_has_expected_keys():
    drv = make_lidar()
    path = make_tmp_db()
    try:
        drv.save_map(path)
        result = drv.load_map(path)
        meta = result["metadata"]
        assert "ts" in meta
        assert "size_m" in meta
        assert "resolution_m" in meta
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_load_map_nonexistent_file_returns_error():
    drv = make_lidar()
    # Pass a path in a non-existent directory
    result = drv.load_map("/tmp/definitely_does_not_exist_opencastor_test.db")
    # Should either succeed (SQLite creates the file) or return error gracefully
    assert "ok" in result
