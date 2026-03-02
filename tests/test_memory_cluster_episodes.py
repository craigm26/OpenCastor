"""Tests for EpisodeMemory.cluster_episodes() (#385)."""

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture
def mem(tmp_path):
    db = str(tmp_path / "mem.db")
    return EpisodeMemory(db_path=db)


@pytest.fixture
def mem_with_data(mem):
    action_types = ["move", "stop", "wait", "move", "stop", "move", "wait", "stop"]
    for i, at in enumerate(action_types):
        mem.log_episode(
            instruction=f"step {i}",
            raw_thought="ok",
            action={"type": at},
            latency_ms=50.0,
        )
    return mem


# ── empty memory raises ───────────────────────────────────────────────────────

def test_cluster_episodes_empty_raises(mem):
    with pytest.raises(ValueError, match="no episodes"):
        mem.cluster_episodes()


# ── basic return shape ────────────────────────────────────────────────────────

def test_cluster_episodes_returns_dict(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=3)
    assert isinstance(result, dict)


def test_cluster_episodes_has_required_keys(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=2)
    for key in ("labels", "centroids", "episode_ids", "representative_ids",
                "n_clusters", "n_episodes", "action_types"):
        assert key in result, f"missing key: {key}"


def test_cluster_episodes_n_episodes_correct(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=2)
    assert result["n_episodes"] == 8


def test_cluster_episodes_labels_length_matches_episodes(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=3)
    assert len(result["labels"]) == result["n_episodes"]


def test_cluster_episodes_episode_ids_length_matches(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=3)
    assert len(result["episode_ids"]) == result["n_episodes"]


def test_cluster_episodes_n_clusters_field(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=3)
    assert result["n_clusters"] >= 1
    assert result["n_clusters"] <= 3


def test_cluster_episodes_centroids_count(mem_with_data):
    k = 3
    result = mem_with_data.cluster_episodes(n_clusters=k)
    assert len(result["centroids"]) == result["n_clusters"]


def test_cluster_episodes_action_types_list(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=2)
    at = result["action_types"]
    assert isinstance(at, list)
    assert "move" in at
    assert "stop" in at


# ── labels are valid cluster indices ─────────────────────────────────────────

def test_cluster_episodes_labels_are_valid_indices(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=3)
    k = result["n_clusters"]
    for label in result["labels"]:
        assert 0 <= label < k


# ── representative_ids ────────────────────────────────────────────────────────

def test_cluster_episodes_representative_ids_has_entries(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=2)
    assert len(result["representative_ids"]) > 0


def test_cluster_episodes_representative_ids_are_valid_episode_ids(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=2)
    ep_set = set(result["episode_ids"])
    for rep_id in result["representative_ids"].values():
        assert rep_id in ep_set


# ── parameter edge cases ──────────────────────────────────────────────────────

def test_cluster_episodes_n_clusters_1(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=1)
    assert result["n_clusters"] == 1
    assert all(lbl == 0 for lbl in result["labels"])


def test_cluster_episodes_n_clusters_clamped_to_episodes(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=100)
    assert result["n_clusters"] <= result["n_episodes"]


def test_cluster_episodes_unsupported_by_raises(mem_with_data):
    with pytest.raises(ValueError, match="unsupported"):
        mem_with_data.cluster_episodes(by="semantic")


# ── limit param ───────────────────────────────────────────────────────────────

def test_cluster_episodes_limit_restricts_episodes(mem_with_data):
    result = mem_with_data.cluster_episodes(n_clusters=2, limit=3)
    assert result["n_episodes"] <= 3


# ── reproducibility ───────────────────────────────────────────────────────────

def test_cluster_episodes_same_seed_reproducible(mem_with_data):
    r1 = mem_with_data.cluster_episodes(n_clusters=2, random_seed=42)
    r2 = mem_with_data.cluster_episodes(n_clusters=2, random_seed=42)
    assert r1["labels"] == r2["labels"]
