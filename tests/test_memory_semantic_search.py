"""Tests for EpisodeMemory semantic search (issue #301)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture()
def mem(tmp_path):
    db = str(tmp_path / "mem.db")
    m = EpisodeMemory(db_path=db, max_episodes=0)
    m.log_episode(instruction="go forward fast", raw_thought="move ahead", action={"type": "move"})
    m.log_episode(instruction="turn left sharply", raw_thought="rotate", action={"type": "move"})
    m.log_episode(instruction="stop the robot", raw_thought="halt", action={"type": "stop"})
    return m


# ── Mode parameter ─────────────────────────────────────────────────────────────


def test_search_keyword_mode_explicit(mem):
    results = mem.search("forward", mode="keyword")
    assert len(results) == 1
    assert "forward" in results[0]["instruction"]


def test_search_default_mode_is_keyword(mem):
    results = mem.search("forward")
    assert len(results) == 1


def test_search_mode_keyword_returns_list(mem):
    results = mem.search("stop", mode="keyword")
    assert isinstance(results, list)


def test_search_mode_semantic_returns_list(mem):
    """Semantic search always returns a list (may fall back to keyword if ST unavailable)."""
    results = mem.search("stop", mode="semantic")
    assert isinstance(results, list)


def test_search_semantic_fallback_when_st_unavailable(mem):
    """When sentence_transformers is not installed, semantic falls back to keyword."""
    with patch.object(EpisodeMemory, "_embed_text", return_value=None):
        results = mem.search("forward", mode="semantic")
    # Should fall back to keyword — still get a result
    assert isinstance(results, list)


# ── _embed_text ───────────────────────────────────────────────────────────────


def test_embed_text_returns_none_for_empty():
    result = EpisodeMemory._embed_text("")
    assert result is None


def test_embed_text_returns_none_for_whitespace():
    result = EpisodeMemory._embed_text("   ")
    assert result is None


def test_embed_text_returns_list_or_none():
    result = EpisodeMemory._embed_text("hello world")
    # Either a list of floats (ST available) or None (ST not installed)
    assert result is None or (
        isinstance(result, list) and all(isinstance(x, float) for x in result)
    )


# ── _cosine ───────────────────────────────────────────────────────────────────


def test_cosine_identical_vectors():
    from castor.memory import EpisodeMemory

    v = [1.0, 0.0, 0.0]
    assert EpisodeMemory._cosine(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    from castor.memory import EpisodeMemory

    assert EpisodeMemory._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_length_mismatch_returns_zero():
    from castor.memory import EpisodeMemory

    assert EpisodeMemory._cosine([1.0, 0.0], [1.0]) == 0.0


# ── episode_embeddings table ──────────────────────────────────────────────────


def test_embeddings_table_created(tmp_path):
    import sqlite3

    db = str(tmp_path / "mem.db")
    EpisodeMemory(db_path=db, max_episodes=0)
    con = sqlite3.connect(db)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "episode_embeddings" in tables


def test_semantic_search_empty_query_returns_empty(mem):
    results = mem.search("", mode="semantic")
    assert results == []


def test_semantic_search_limit_respected(mem):
    results = mem.search("stop", mode="semantic", limit=1)
    assert len(results) <= 1


# ── API endpoint mode=semantic ────────────────────────────────────────────────


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    import castor.api as _api

    return TestClient(_api.app)


def test_api_memory_search_mode_semantic(client):
    resp = client.get("/api/memory/search", params={"q": "forward", "mode": "semantic"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "semantic"
    assert "results" in data


def test_api_memory_search_mode_keyword_unchanged(client):
    resp = client.get("/api/memory/search", params={"q": "forward", "mode": "keyword"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "keyword"
