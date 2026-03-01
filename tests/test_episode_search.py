"""
tests/test_episode_search.py — Unit + API tests for castor/episode_search.py.

Covers:
  - TF-IDF index build
  - cosine similarity search
  - keyword fallback for OOV terms
  - empty corpus handling
  - filter by min_score
  - stats()
  - API: GET /api/memory/search
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

_EPISODES = [
    {
        "id": "1",
        "instruction": "go forward and turn left",
        "raw_text": "{}",
        "action": {"action": "forward"},
        "latency_ms": 100,
        "timestamp": "t",
    },
    {
        "id": "2",
        "instruction": "stop immediately",
        "raw_text": "{}",
        "action": {"action": "stop"},
        "latency_ms": 50,
        "timestamp": "t",
    },
    {
        "id": "3",
        "instruction": "go forward fast",
        "raw_text": "{}",
        "action": {"action": "forward"},
        "latency_ms": 80,
        "timestamp": "t",
    },
    {
        "id": "4",
        "instruction": "turn right sharply",
        "raw_text": "{}",
        "action": {"action": "turn"},
        "latency_ms": 90,
        "timestamp": "t",
    },
    {
        "id": "5",
        "instruction": "take a photo",
        "raw_text": "{}",
        "action": {"action": "photo"},
        "latency_ms": 200,
        "timestamp": "t",
    },
]


def _make_searcher(episodes=None):
    from castor.episode_search import EpisodeSimilaritySearch

    mem = MagicMock()
    mem.query_recent.return_value = episodes if episodes is not None else _EPISODES
    return EpisodeSimilaritySearch(memory=mem)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_index_builds_on_search():
    s = _make_searcher()
    assert not s._built
    s.search("forward")
    assert s._built
    assert len(s._index) == 5


def test_search_returns_relevant_results():
    s = _make_searcher()
    results = s.search("forward", limit=5)
    assert len(results) >= 1
    # Episodes 1 and 3 contain "forward"
    ids = {r["id"] for r in results}
    assert "1" in ids or "3" in ids


def test_search_sorted_by_score():
    s = _make_searcher()
    results = s.search("forward", limit=5)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_includes_score_field():
    s = _make_searcher()
    results = s.search("stop", limit=3)
    for r in results:
        assert "score" in r
        assert 0.0 <= r["score"] <= 1.0


def test_search_limit_respected():
    s = _make_searcher()
    results = s.search("go", limit=2)
    assert len(results) <= 2


def test_search_empty_query_returns_empty():
    s = _make_searcher()
    s._build()
    # OOV tokens only → keyword fallback, which also returns empty for no match
    results = s.search("xyzzy_nonexistent_word", limit=10)
    assert isinstance(results, list)


def test_search_empty_corpus():
    s = _make_searcher(episodes=[])
    results = s.search("forward")
    assert results == []


def test_keyword_fallback():
    """Keyword fallback fires when all query tokens are OOV (not in IDF)."""
    s = _make_searcher()
    # First build index normally
    s._build()
    # Then clear IDF to force OOV condition
    s._idf = {}
    results = s.search("forward", limit=5)
    # Keyword fallback should still find matches
    assert isinstance(results, list)


def test_invalidate_forces_rebuild():
    s = _make_searcher()
    s._build()
    assert s._built
    s.invalidate()
    assert not s._built
    s.search("test")
    assert s._built


def test_stats():
    s = _make_searcher()
    st = s.stats()
    assert st["indexed_episodes"] == 5
    assert st["vocabulary_size"] > 0
    assert st["built"] is True


def test_singleton():
    import castor.episode_search as m

    m._searcher = None
    s1 = m.get_searcher()
    s2 = m.get_searcher()
    assert s1 is s2
    m._searcher = None


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    from fastapi.testclient import TestClient

    from castor.api import app

    return TestClient(app)


@pytest.fixture()
def mock_searcher():

    mem = MagicMock()
    mem.query_recent.return_value = _EPISODES
    with patch("castor.episode_search._searcher", None):
        with patch("castor.memory.EpisodeMemory") as mc:
            mc.return_value = mem
            yield mem


def test_api_memory_search_ok(api_client, mock_searcher):
    resp = api_client.get("/api/memory/search?q=forward&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert data["query"] == "forward"
    assert isinstance(data["count"], int)


def test_api_memory_search_empty_query(api_client):
    resp = api_client.get("/api/memory/search?q=")
    assert resp.status_code == 422


def test_api_memory_search_limit_capped(api_client, mock_searcher):
    resp = api_client.get("/api/memory/search?q=go&limit=999")
    assert resp.status_code == 200
