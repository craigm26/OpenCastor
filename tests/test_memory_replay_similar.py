"""Tests for EpisodeMemory.replay_similar() and replay_episode() — issue #356."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture()
def mem(tmp_path):
    db = str(tmp_path / "mem.db")
    m = EpisodeMemory(db_path=db, max_episodes=0)
    m.log_episode(instruction="move forward quickly", raw_thought="go ahead", action={"type": "move"})
    m.log_episode(instruction="turn left sharply", raw_thought="rotate left", action={"type": "move"})
    m.log_episode(instruction="stop the robot now", raw_thought="halt", action={"type": "stop"})
    return m


@pytest.fixture()
def mem_with_embeddings(tmp_path):
    db = str(tmp_path / "embed.db")
    m = EpisodeMemory(db_path=db, max_episodes=0)
    ep1 = m.log_episode(instruction="drive forward", raw_thought="move ahead", action={"type": "move"})
    ep2 = m.log_episode(instruction="rotate left", raw_thought="turn", action={"type": "move"})
    ep3 = m.log_episode(instruction="emergency stop", raw_thought="halt now", action={"type": "stop"})
    embeddings = {ep1: [1.0, 0.0, 0.0], ep2: [0.0, 1.0, 0.0], ep3: [0.0, 0.0, 1.0]}
    con = sqlite3.connect(db)
    for ep_id, vec in embeddings.items():
        con.execute(
            "INSERT OR REPLACE INTO episode_embeddings (id, embedding_json) VALUES (?, ?)",
            (ep_id, json.dumps(vec)),
        )
    con.commit()
    con.close()
    return m, embeddings


# ── replay_similar: basic behaviour ───────────────────────────────────────────


def test_replay_similar_returns_list(mem):
    with patch.object(EpisodeMemory, "_embed_text", return_value=None):
        results = mem.replay_similar("forward")
    assert isinstance(results, list)


def test_replay_similar_empty_query_returns_empty(mem):
    assert mem.replay_similar("") == []


def test_replay_similar_whitespace_query_returns_empty(mem):
    assert mem.replay_similar("   ") == []


def test_replay_similar_results_have_similarity_score(mem):
    with patch.object(EpisodeMemory, "_embed_text", return_value=None):
        results = mem.replay_similar("forward", top_k=3)
    for r in results:
        assert "similarity_score" in r
        assert isinstance(r["similarity_score"], float)


def test_replay_similar_keyword_fallback_score_is_zero(mem):
    with patch.object(EpisodeMemory, "_embed_text", return_value=None):
        results = mem.replay_similar("forward")
    for r in results:
        assert r["similarity_score"] == 0.0


def test_replay_similar_top_k_limits_results(mem):
    with patch.object(EpisodeMemory, "_embed_text", return_value=None):
        results = mem.replay_similar("move", top_k=1)
    assert len(results) <= 1


def test_replay_similar_top_k_default_is_five(tmp_path):
    db = str(tmp_path / "topk.db")
    m = EpisodeMemory(db_path=db, max_episodes=0)
    for i in range(10):
        m.log_episode(instruction=f"move step {i}", raw_thought="ok", action={"type": "move"})
    with patch.object(EpisodeMemory, "_embed_text", return_value=None):
        results = m.replay_similar("move")
    assert len(results) <= 5


def test_replay_similar_empty_store_returns_empty(tmp_path):
    db = str(tmp_path / "empty.db")
    m = EpisodeMemory(db_path=db, max_episodes=0)
    with patch.object(EpisodeMemory, "_embed_text", return_value=None):
        results = m.replay_similar("forward")
    assert results == []


def test_replay_similar_returns_standard_episode_fields(mem):
    with patch.object(EpisodeMemory, "_embed_text", return_value=None):
        results = mem.replay_similar("stop")
    if results:
        r = results[0]
        for field in ("id", "instruction", "ts", "action", "has_image", "tags", "similarity_score"):
            assert field in r


# ── replay_similar: semantic path ─────────────────────────────────────────────


def test_replay_similar_semantic_sorted_descending(mem_with_embeddings):
    m, _ = mem_with_embeddings
    with patch.object(EpisodeMemory, "_embed_text", return_value=[1.0, 0.0, 0.0]):
        results = m.replay_similar("drive forward", top_k=3)
    assert len(results) == 3
    scores = [r["similarity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_replay_similar_semantic_top_result_is_most_similar(mem_with_embeddings):
    m, _ = mem_with_embeddings
    with patch.object(EpisodeMemory, "_embed_text", return_value=[1.0, 0.0, 0.0]):
        results = m.replay_similar("drive forward", top_k=3)
    assert len(results) >= 1
    assert results[0]["similarity_score"] == pytest.approx(1.0)


def test_replay_similar_no_embeddings_stored_falls_back_to_keyword(tmp_path):
    db = str(tmp_path / "nokw.db")
    m = EpisodeMemory(db_path=db, max_episodes=0)
    m.log_episode(instruction="move forward now", raw_thought="ok", action={"type": "move"})
    with patch.object(EpisodeMemory, "_embed_text", return_value=[1.0, 0.0, 0.0]):
        results = m.replay_similar("move forward", top_k=3)
    for r in results:
        assert r["similarity_score"] == 0.0


# ── replay_episode ─────────────────────────────────────────────────────────────


def test_replay_episode_returns_episode_by_id(mem):
    ep_id = mem.log_episode(
        instruction="go right", raw_thought="turn right", action={"type": "move"}
    )
    result = mem.replay_episode(ep_id)
    assert result is not None
    assert result["id"] == ep_id
    assert result["instruction"] == "go right"


def test_replay_episode_returns_none_for_missing_id(mem):
    assert mem.replay_episode("nonexistent-uuid-00000000") is None


def test_replay_episode_returns_all_expected_fields(mem):
    ep_id = mem.log_episode(
        instruction="nav waypoint",
        raw_thought="navigating",
        action={"type": "nav_waypoint", "x": 1.0, "y": 2.0},
        latency_ms=55.5,
        outcome="ok",
        source="api",
    )
    result = mem.replay_episode(ep_id)
    assert result is not None
    assert result["instruction"] == "nav waypoint"
    assert result["action"] == {"type": "nav_waypoint", "x": 1.0, "y": 2.0}
    assert result["latency_ms"] == pytest.approx(55.5)
    assert result["outcome"] == "ok"
    assert result["source"] == "api"


def test_replay_episode_returns_dict(mem):
    ep_id = mem.log_episode(instruction="test", raw_thought="ok", action={"type": "stop"})
    assert isinstance(mem.replay_episode(ep_id), dict)
