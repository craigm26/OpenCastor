"""Tests for EpisodeMemory k-means clustering (Issue #342)."""

from __future__ import annotations

import tempfile
import uuid

import pytest

from castor.memory import EpisodeMemory


def make_mem() -> EpisodeMemory:
    """Create an in-memory (temp file) EpisodeMemory for testing."""
    tmpfile = tempfile.mktemp(suffix=".db")
    return EpisodeMemory(db_path=tmpfile, max_episodes=0)


def add_episodes(mem: EpisodeMemory, action_types: list) -> list:
    """Add episodes with the specified action types and return their IDs."""
    ids = []
    for at in action_types:
        ep_id = str(uuid.uuid4())
        mem.log_episode(
            instruction=f"do {at}",
            raw_thought=f'{{"type":"{at}"}}',
            action={"type": at},
            latency_ms=100.0,
        )
        ids.append(ep_id)
    return ids


# ── K-means helper method tests ───────────────────────────────────────────────


def test_kmeans_distance_sq_zeros():
    d = EpisodeMemory._kmeans_distance_sq([0.0, 0.0], [0.0, 0.0])
    assert d == 0.0


def test_kmeans_distance_sq_known_value():
    d = EpisodeMemory._kmeans_distance_sq([0.0, 0.0], [3.0, 4.0])
    assert d == pytest.approx(25.0)


def test_kmeans_centroid_single_vector():
    c = EpisodeMemory._kmeans_centroid([[1.0, 2.0, 3.0]])
    assert c == pytest.approx([1.0, 2.0, 3.0])


def test_kmeans_centroid_two_vectors():
    c = EpisodeMemory._kmeans_centroid([[0.0, 0.0], [2.0, 4.0]])
    assert c == pytest.approx([1.0, 2.0])


def test_kmeans_centroid_empty():
    c = EpisodeMemory._kmeans_centroid([])
    assert c == []


# ── cluster_episodes tests ────────────────────────────────────────────────────


def test_cluster_episodes_raises_on_empty_db():
    mem = make_mem()
    with pytest.raises(ValueError, match="no episodes found"):
        mem.cluster_episodes()


def test_cluster_episodes_raises_on_unsupported_by():
    mem = make_mem()
    add_episodes(mem, ["move"] * 5)
    with pytest.raises(ValueError, match="unsupported 'by' value"):
        mem.cluster_episodes(by="embedding")


def test_cluster_episodes_returns_expected_keys():
    mem = make_mem()
    add_episodes(mem, ["move", "stop", "wait"])
    result = mem.cluster_episodes(n_clusters=2)
    for key in (
        "labels",
        "centroids",
        "episode_ids",
        "representative_ids",
        "n_clusters",
        "n_episodes",
        "action_types",
    ):
        assert key in result, f"Missing key: {key}"


def test_cluster_episodes_label_count_matches_episodes():
    mem = make_mem()
    add_episodes(mem, ["move"] * 6 + ["stop"] * 6)
    result = mem.cluster_episodes(n_clusters=2)
    assert len(result["labels"]) == result["n_episodes"]
    assert len(result["episode_ids"]) == result["n_episodes"]


def test_cluster_episodes_n_clusters_clamped():
    mem = make_mem()
    add_episodes(mem, ["move"] * 3)
    result = mem.cluster_episodes(n_clusters=10)
    assert result["n_clusters"] <= 3


def test_cluster_episodes_centroid_count_matches_k():
    mem = make_mem()
    add_episodes(mem, ["move"] * 4 + ["stop"] * 4)
    result = mem.cluster_episodes(n_clusters=2)
    assert len(result["centroids"]) == result["n_clusters"]


def test_cluster_episodes_representative_ids_per_cluster():
    mem = make_mem()
    add_episodes(mem, ["move"] * 5 + ["stop"] * 5)
    result = mem.cluster_episodes(n_clusters=2)
    for c_idx in range(result["n_clusters"]):
        key = str(c_idx)
        assert key in result["representative_ids"]
        ep_id = result["representative_ids"][key]
        assert ep_id in result["episode_ids"]


def test_cluster_episodes_labels_in_valid_range():
    mem = make_mem()
    add_episodes(mem, ["move", "stop", "wait", "grip"])
    result = mem.cluster_episodes(n_clusters=2)
    for lbl in result["labels"]:
        assert 0 <= lbl < result["n_clusters"]


def test_cluster_episodes_action_types_list():
    mem = make_mem()
    add_episodes(mem, ["move"])
    result = mem.cluster_episodes(n_clusters=1)
    expected = ["move", "stop", "wait", "grip", "nav_waypoint", "other"]
    assert result["action_types"] == expected


def test_cluster_episodes_single_cluster():
    mem = make_mem()
    add_episodes(mem, ["move"] * 5)
    result = mem.cluster_episodes(n_clusters=1)
    assert result["n_clusters"] == 1
    assert all(lbl == 0 for lbl in result["labels"])


def test_cluster_episodes_reproducible_with_seed():
    mem = make_mem()
    add_episodes(mem, ["move"] * 5 + ["stop"] * 5)
    r1 = mem.cluster_episodes(n_clusters=2, random_seed=42)
    r2 = mem.cluster_episodes(n_clusters=2, random_seed=42)
    assert r1["labels"] == r2["labels"]


def test_cluster_episodes_different_seeds_may_differ():
    mem = make_mem()
    # With sufficiently distinct action types, different seeds should converge to same result
    add_episodes(mem, ["move"] * 10 + ["stop"] * 10)
    result = mem.cluster_episodes(n_clusters=2)
    # Should always produce 2 clusters
    assert result["n_clusters"] == 2


def test_cluster_episodes_limit_respected():
    mem = make_mem()
    add_episodes(mem, ["move"] * 20)
    result = mem.cluster_episodes(n_clusters=2, limit=5)
    assert result["n_episodes"] <= 5
