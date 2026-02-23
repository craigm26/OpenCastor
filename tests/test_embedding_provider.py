"""Tests for castor.providers.sentence_transformers_provider (EmbeddingProvider)."""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Singleton reset between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    import castor.providers.sentence_transformers_provider as mod
    mod._instance = None
    yield
    mod._instance = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_mode_provider(model_name=None):
    """Return an EmbeddingProvider forced into mock mode (HAS_ST=False)."""
    with patch("castor.providers.sentence_transformers_provider.HAS_ST", False):
        from castor.providers.sentence_transformers_provider import EmbeddingProvider
        return EmbeddingProvider(model_name=model_name)


def _real_model_provider():
    """Return an EmbeddingProvider with a mocked SentenceTransformer."""
    mock_model = MagicMock()
    # encode returns list of numpy-like arrays; use plain lists with .tolist()
    import numpy as np
    mock_model.encode.side_effect = lambda texts, **kwargs: np.array(
        [[float(i)] * 384 for i in range(len(texts))]
    )
    with patch("castor.providers.sentence_transformers_provider.HAS_ST", True):
        with patch(
            "castor.providers.sentence_transformers_provider.SentenceTransformer",
            return_value=mock_model,
            create=True,  # needed when sentence_transformers is not installed (HAS_ST=False at import time)
        ):
            from castor.providers.sentence_transformers_provider import EmbeddingProvider
            return EmbeddingProvider()


# ---------------------------------------------------------------------------
# Mock mode (HAS_ST = False)
# ---------------------------------------------------------------------------


class TestEmbeddingProviderMockMode:
    def test_mode_is_mock(self):
        p = _mock_mode_provider()
        assert p._mode == "mock"

    def test_encode_returns_zero_vectors(self):
        p = _mock_mode_provider()
        vecs = p.encode(["hello", "world"])
        assert len(vecs) == 2
        assert all(v == 0.0 for v in vecs[0])
        assert len(vecs[0]) == 384

    def test_encode_empty_list_returns_empty(self):
        p = _mock_mode_provider()
        assert p.encode([]) == []

    def test_similarity_returns_zero(self):
        p = _mock_mode_provider()
        score = p.similarity("cat", "dog")
        assert score == 0.0

    def test_search_returns_empty(self):
        p = _mock_mode_provider()
        results = p.search("query", ["a", "b", "c"])
        assert results == []

    def test_health_check_not_ok_in_mock(self):
        p = _mock_mode_provider()
        h = p.health_check()
        assert h["ok"] is False
        assert h["mode"] == "mock"

    def test_health_check_contains_model_name(self):
        p = _mock_mode_provider(model_name="all-MiniLM-L6-v2")
        h = p.health_check()
        assert "all-MiniLM-L6-v2" in h["model"]


# ---------------------------------------------------------------------------
# Hardware-like mode (mocked SentenceTransformer)
# ---------------------------------------------------------------------------


class TestEmbeddingProviderHardwareMode:
    def test_mode_is_hardware(self):
        p = _real_model_provider()
        assert p._mode == "hardware"

    def test_encode_returns_vectors_of_correct_shape(self):
        p = _real_model_provider()
        vecs = p.encode(["sentence one", "sentence two"])
        assert len(vecs) == 2
        assert len(vecs[0]) == 384

    def test_similarity_returns_float(self):
        p = _real_model_provider()
        score = p.similarity("hello", "world")
        assert isinstance(score, float)

    def test_search_returns_sorted_results(self):
        p = _real_model_provider()
        results = p.search("query", ["a", "b", "c"], top_k=2)
        assert len(results) <= 2
        # Each result is (index, score)
        for idx, score in results:
            assert isinstance(idx, int)
            assert isinstance(score, float)

    def test_health_check_ok_in_hardware(self):
        p = _real_model_provider()
        h = p.health_check()
        assert h["ok"] is True
        assert h["mode"] == "hardware"


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


def test_get_embedding_provider_singleton():
    with patch("castor.providers.sentence_transformers_provider.HAS_ST", False):
        from castor.providers.sentence_transformers_provider import get_embedding_provider
        p1 = get_embedding_provider()
        p2 = get_embedding_provider()
    assert p1 is p2


# ---------------------------------------------------------------------------
# _cosine helper (internal, importable)
# ---------------------------------------------------------------------------


def test_cosine_identical_vectors():
    from castor.providers.sentence_transformers_provider import _cosine
    v = [1.0, 0.0, 0.0]
    assert _cosine(v, v) == pytest.approx(1.0, abs=1e-6)


def test_cosine_zero_vector_returns_zero():
    from castor.providers.sentence_transformers_provider import _cosine
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
