"""Tests for /api/memory/trajectory endpoint (issue #303)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import castor.api as _api
from castor.memory import EpisodeMemory


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    db = str(tmp_path / "mem.db")
    monkeypatch.setenv("CASTOR_MEMORY_DB", db)
    m = EpisodeMemory(db_path=db, max_episodes=0)
    return m


@pytest.fixture()
def client():
    return TestClient(_api.app)


@pytest.fixture()
def episodes(mem):
    """Create 3 sequential episodes and return their IDs."""
    ids = []
    for i in range(3):
        ep_id = mem.log_episode(
            instruction=f"step {i}",
            raw_thought="ok",
            action={"type": "move", "linear": 0.3},
        )
        ids.append(ep_id)
        time.sleep(0.01)
    return ids


# ── Dry-run ────────────────────────────────────────────────────────────────────


def test_trajectory_dry_run_returns_episodes(client, episodes, mem, monkeypatch):
    monkeypatch.setenv("CASTOR_MEMORY_DB", mem.db_path)
    resp = client.post(
        "/api/memory/trajectory",
        params={"start_id": episodes[0], "end_id": episodes[-1], "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dry_run"] is True
    assert data["episode_count"] >= 1


def test_trajectory_dry_run_no_action_executed(client, episodes, mem, monkeypatch):
    monkeypatch.setenv("CASTOR_MEMORY_DB", mem.db_path)
    with patch("castor.api._execute_action") as mock_exec:
        resp = client.post(
            "/api/memory/trajectory",
            params={"start_id": episodes[0], "end_id": episodes[-1], "dry_run": True},
        )
    assert resp.status_code == 200
    mock_exec.assert_not_called()


def test_trajectory_dry_run_includes_action_list(client, episodes, mem, monkeypatch):
    monkeypatch.setenv("CASTOR_MEMORY_DB", mem.db_path)
    resp = client.post(
        "/api/memory/trajectory",
        params={"start_id": episodes[0], "end_id": episodes[-1], "dry_run": True},
    )
    data = resp.json()
    assert "episodes" in data
    assert isinstance(data["episodes"], list)


# ── 404 cases ─────────────────────────────────────────────────────────────────


def test_trajectory_missing_start_id_404(client):
    resp = client.post(
        "/api/memory/trajectory",
        params={"start_id": "00000000-0000-0000-0000-000000000000", "end_id": "x"},
    )
    assert resp.status_code == 404


def test_trajectory_missing_end_id_404(client, episodes, mem, monkeypatch):
    monkeypatch.setenv("CASTOR_MEMORY_DB", mem.db_path)
    resp = client.post(
        "/api/memory/trajectory",
        params={"start_id": episodes[0], "end_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 404


# ── 422 — start newer than end ────────────────────────────────────────────────


def test_trajectory_start_newer_than_end_422(client, episodes, mem, monkeypatch):
    monkeypatch.setenv("CASTOR_MEMORY_DB", mem.db_path)
    resp = client.post(
        "/api/memory/trajectory",
        params={"start_id": episodes[-1], "end_id": episodes[0]},
    )
    assert resp.status_code == 422


# ── Response shape ─────────────────────────────────────────────────────────────


def test_trajectory_dry_run_has_duration(client, episodes, mem, monkeypatch):
    monkeypatch.setenv("CASTOR_MEMORY_DB", mem.db_path)
    resp = client.post(
        "/api/memory/trajectory",
        params={"start_id": episodes[0], "end_id": episodes[-1], "dry_run": True},
    )
    data = resp.json()
    assert "duration_s" in data
    assert data["duration_s"] >= 0


def test_trajectory_dry_run_episode_has_required_keys(client, episodes, mem, monkeypatch):
    monkeypatch.setenv("CASTOR_MEMORY_DB", mem.db_path)
    resp = client.post(
        "/api/memory/trajectory",
        params={"start_id": episodes[0], "end_id": episodes[-1], "dry_run": True},
    )
    eps = resp.json()["episodes"]
    assert len(eps) > 0
    ep = eps[0]
    assert "id" in ep
    assert "ts" in ep
    assert "action" in ep


# ── Live replay (with mock driver) ────────────────────────────────────────────


def test_trajectory_live_replay_503_without_driver(client, episodes, mem, monkeypatch):
    """Without a driver, live replay returns 503."""
    monkeypatch.setenv("CASTOR_MEMORY_DB", mem.db_path)
    import castor.api as _api_mod

    saved = _api_mod.state.driver
    _api_mod.state.driver = None
    try:
        resp = client.post(
            "/api/memory/trajectory",
            params={"start_id": episodes[0], "end_id": episodes[-1]},
        )
        assert resp.status_code == 503
    finally:
        _api_mod.state.driver = saved
