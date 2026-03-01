"""Tests for EpisodeMemory.search() — keyword search (issue #293)."""

from __future__ import annotations

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture()
def mem(tmp_path):
    db = str(tmp_path / "mem.db")
    m = EpisodeMemory(db_path=db, max_episodes=0)
    m.log_episode(instruction="go forward fast", raw_thought="move ahead", action={"type": "move"})
    m.log_episode(instruction="turn left sharply", raw_thought="rotate", action={"type": "move"})
    m.log_episode(instruction="stop the robot", raw_thought="halt", action={"type": "stop"})
    m.log_episode(instruction="grip object", raw_thought="grasp it", action={"type": "grip"})
    return m


def test_search_matches_instruction(mem):
    results = mem.search("forward")
    assert len(results) == 1
    assert "forward" in results[0]["instruction"]


def test_search_matches_raw_thought(mem):
    results = mem.search("halt")
    assert len(results) == 1
    assert results[0]["instruction"] == "stop the robot"


def test_search_matches_action_json(mem):
    results = mem.search('"type": "grip"')
    assert len(results) >= 1


def test_search_case_insensitive(mem):
    results = mem.search("FORWARD")
    assert len(results) == 1


def test_search_empty_query_returns_empty(mem):
    assert mem.search("") == []
    assert mem.search("   ") == []


def test_search_no_match_returns_empty(mem):
    results = mem.search("nonexistentxyzabc123")
    assert results == []


def test_search_multiple_matches(mem):
    results = mem.search("move")
    # "move ahead" and action type "move" appear in multiple episodes
    assert len(results) >= 1


def test_search_limit_respected(mem):
    # Insert many matching episodes
    for i in range(50):
        mem.log_episode(
            instruction=f"patrol step {i}", raw_thought="moving", action={"type": "move"}
        )
    results = mem.search("patrol", limit=5)
    assert len(results) <= 5


def test_search_limit_capped_at_500(mem):
    results = mem.search("go", limit=9999)
    assert len(results) <= 500


def test_search_returns_standard_dict_keys(mem):
    results = mem.search("forward")
    assert len(results) == 1
    r = results[0]
    assert "id" in r
    assert "instruction" in r
    assert "ts" in r
    assert "has_image" in r
    assert "tags" in r


def test_search_newest_first(mem):
    import time

    m = mem
    # Add two episodes with known ordering
    m.log_episode(instruction="first unique search target abc", raw_thought="", action={})
    time.sleep(0.01)
    m.log_episode(instruction="second unique search target abc", raw_thought="", action={})
    results = m.search("unique search target abc")
    assert len(results) == 2
    assert results[0]["ts"] >= results[1]["ts"]


# ── API endpoint ──────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    import castor.api as _api

    return TestClient(_api.app)


def test_api_memory_search_keyword(client):
    resp = client.get("/api/memory/search", params={"q": "forward", "mode": "keyword"})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert data["mode"] == "keyword"


def test_api_memory_search_empty_query(client):
    resp = client.get("/api/memory/search", params={"q": ""})
    assert resp.status_code == 422


def test_api_memory_search_default_mode_is_keyword(client):
    resp = client.get("/api/memory/search", params={"q": "test"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "keyword"
