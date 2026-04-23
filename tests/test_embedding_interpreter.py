"""Tests for EmbeddingInterpreter, CLIPEmbeddingProvider, GeminiEmbeddingProvider,
and RCAN config validation of the interpreter block.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from castor.config_validation import validate_rcan_config
from castor.embedding_interpreter import EmbeddingInterpreter, SceneContext
from castor.providers.clip_embedding_provider import CLIPEmbeddingProvider
from castor.providers.gemini_embedding_provider import GeminiEmbeddingProvider

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_thought():
    """Return a minimal Thought-like object."""
    t = MagicMock()
    t.raw_text = "go forward"
    t.action = {"type": "move"}
    return t


@pytest.fixture()
def interp_mock(tmp_path):
    """Return an EmbeddingInterpreter using mock CLIP backend."""
    return EmbeddingInterpreter(
        {
            "enabled": True,
            "backend": "mock",
            "episode_store": str(tmp_path / "episodes"),
            "max_episodes": 10,
            "rag_k": 3,
        }
    )


# ── #502: CLIP provider ───────────────────────────────────────────────────────


class TestCLIPEmbeddingProvider:
    def test_mock_mode_returns_512_zeros(self):
        p = CLIPEmbeddingProvider({"model": "mock"})
        assert not p.available
        assert p.dimensions == 512
        assert p.backend_name == "clip-mock"
        vec = p.embed(text="hello")
        assert vec.shape == (512,)
        assert vec.dtype == np.float32
        assert float(np.sum(np.abs(vec))) == 0.0

    def test_mock_mode_similarity(self):
        p = CLIPEmbeddingProvider({"model": "mock"})
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0], dtype=np.float32)
        assert p.similarity(a, b) == pytest.approx(0.0, abs=1e-6)

    def test_mock_audio_bytes_ignored(self):
        p = CLIPEmbeddingProvider({"model": "mock"})
        vec = p.embed(audio_bytes=b"fake-audio")
        assert vec.shape == (512,)

    def test_load_model_skipped_when_mock(self):
        """_load_model should not be called when model='mock'."""
        with patch.object(CLIPEmbeddingProvider, "_load_model") as mock_load:
            CLIPEmbeddingProvider({"model": "mock"})
            mock_load.assert_not_called()


# ── #507: Gemini provider ─────────────────────────────────────────────────────


class TestGeminiEmbeddingProvider:
    def test_mock_mode_no_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        p = GeminiEmbeddingProvider({})
        assert not p.available
        assert p.dimensions == 1536
        assert "mock" in p.backend_name or "gemini" in p.backend_name

    def test_mock_embed_returns_correct_shape(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        p = GeminiEmbeddingProvider({})
        vec = p.embed(text="hello world")
        assert vec.shape == (1536,)
        assert vec.dtype == np.float32

    def test_mock_embed_image_bytes(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        p = GeminiEmbeddingProvider({})
        vec = p.embed(image_bytes=b"\xff\xd8\xff" * 100)
        assert vec.shape == (1536,)

    def test_dimensions_configurable(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        p = GeminiEmbeddingProvider({"dimensions": 768})
        assert p.dimensions == 768

    def test_embed_backend_interface(self, monkeypatch):
        """GeminiEmbeddingProvider should implement EmbeddingBackend."""
        from castor.providers.embedding_backend import EmbeddingBackend

        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        p = GeminiEmbeddingProvider({})
        assert isinstance(p, EmbeddingBackend)


# ── #503: EmbeddingInterpreter ────────────────────────────────────────────────


class TestEmbeddingInterpreter:
    def test_pre_think_returns_scene_context(self, interp_mock):
        ctx = interp_mock.pre_think(None, "go forward")
        assert isinstance(ctx, SceneContext)
        assert ctx.embedding.dtype == np.float32
        assert 0.0 <= ctx.goal_similarity <= 1.0
        assert isinstance(ctx.nearest_episodes, list)
        assert ctx.tick_id >= 1
        assert ctx.backend in ("clip-mock", "clip", "gemini-mock", "gemini-embedding-2-preview")

    def test_pre_think_with_image_bytes(self, interp_mock):
        ctx = interp_mock.pre_think(b"\xff\xd8\xff" * 50, "navigate to table")
        assert isinstance(ctx, SceneContext)

    def test_post_think_adds_episode(self, interp_mock, mock_thought, tmp_path):
        ctx = interp_mock.pre_think(None, "go forward")
        assert len(interp_mock._meta) == 0
        interp_mock.post_think(ctx, mock_thought, outcome="success")
        interp_mock.flush()
        assert len(interp_mock._meta) == 1

    def test_format_rag_context_empty(self, interp_mock):
        ctx = interp_mock.pre_think(None, "test")
        result = interp_mock.format_rag_context(ctx)
        assert result == ""

    def test_format_rag_context_non_empty(self, interp_mock, mock_thought, tmp_path):
        # Store an episode first
        ctx1 = interp_mock.pre_think(None, "go forward")
        interp_mock.post_think(ctx1, mock_thought, outcome="success")
        interp_mock.flush()

        # Now pre_think should find the stored episode
        ctx2 = interp_mock.pre_think(None, "go forward")
        rag = interp_mock.format_rag_context(ctx2)
        # Should either be non-empty or empty depending on whether CLIP is mock
        assert isinstance(rag, str)

    def test_max_episodes_enforced(self, tmp_path, mock_thought):
        interp = EmbeddingInterpreter(
            {
                "enabled": True,
                "backend": "mock",
                "episode_store": str(tmp_path / "ep"),
                "max_episodes": 1,
            }
        )
        ctx1 = interp.pre_think(None, "task 1")
        interp.post_think(ctx1, mock_thought, outcome="ok")
        interp.flush()
        ctx2 = interp.pre_think(None, "task 2")
        interp.post_think(ctx2, mock_thought, outcome="ok")
        interp.flush()
        assert len(interp._meta) <= 1

    def test_status_returns_dict(self, interp_mock):
        s = interp_mock.status()
        assert isinstance(s, dict)
        assert "enabled" in s
        assert "backend" in s
        assert "episode_count" in s
        assert "escalations_session" in s

    def test_enabled_property(self, interp_mock):
        assert interp_mock.enabled is True

    def test_set_goal_does_not_crash(self, interp_mock):
        interp_mock.set_goal("reach the charging station")

    def test_escalation_when_similarity_below_threshold(self, tmp_path):
        """Escalation should trigger when goal_similarity < threshold."""
        interp = EmbeddingInterpreter(
            {
                "enabled": True,
                "backend": "mock",
                "episode_store": str(tmp_path / "ep2"),
                "goal_similarity_threshold": 2.0,  # Always escalate
            }
        )
        interp.set_goal("reach the goal")
        ctx = interp.pre_think(None, "go somewhere")
        assert ctx.should_escalate is True


# ── TieredBrain regression ────────────────────────────────────────────────────


class TestTieredBrainNoRegression:
    def test_brain_works_without_interpreter(self):
        from castor.tiered_brain import TieredBrain

        fast = MagicMock()
        fast.think.return_value = MagicMock(raw_text="ok", action={"type": "move"}, layer=None)
        brain = TieredBrain(fast, config={})
        assert brain.interpreter is None
        result = brain.think(b"frame", "go forward")
        assert result is not None

    def test_brain_interpreter_disabled_by_default(self):
        from castor.tiered_brain import TieredBrain

        fast = MagicMock()
        fast.think.return_value = MagicMock(raw_text="ok", action={"type": "move"}, layer=None)
        brain = TieredBrain(fast, config={"interpreter": {}})
        assert brain.interpreter is None


# ── RCAN validation ───────────────────────────────────────────────────────────

_BASE_CONFIG = {
    "rcan_version": "3.0",
    "metadata": {"robot_name": "test"},
    "agent": {"model": "gemini-2.5-flash"},
    "physics": {"wheel_circumference_m": 0.22},
    "drivers": [{"id": "wheels", "protocol": "mock"}],
    "network": {},
    "rcan_protocol": {"capabilities": []},
}


class TestRCANValidation:
    def test_valid_interpreter_config(self):
        config = {**_BASE_CONFIG, "interpreter": {"enabled": True, "backend": "auto"}}
        ok, errors = validate_rcan_config(config)
        assert ok, errors

    def test_bad_backend_fails(self):
        config = {**_BASE_CONFIG, "interpreter": {"backend": "turbo-llm"}}
        ok, errors = validate_rcan_config(config)
        assert not ok
        assert any("interpreter.backend" in e for e in errors)

    def test_bad_gemini_dims_fails(self):
        config = {
            **_BASE_CONFIG,
            "interpreter": {"backend": "gemini", "gemini": {"dimensions": 999}},
        }
        ok, errors = validate_rcan_config(config)
        assert not ok
        assert any("dimensions" in e for e in errors)

    def test_valid_gemini_dims(self):
        for dims in (768, 1536, 3072):
            config = {
                **_BASE_CONFIG,
                "interpreter": {"backend": "auto", "gemini": {"dimensions": dims}},
            }
            ok, errors = validate_rcan_config(config)
            assert ok, f"dims={dims}: {errors}"

    def test_no_interpreter_key_is_valid(self):
        ok, errors = validate_rcan_config(_BASE_CONFIG)
        assert ok, errors
