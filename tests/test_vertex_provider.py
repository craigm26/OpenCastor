"""Tests for the Vertex AI provider.

All Google Cloud / google-genai SDK calls are mocked — no real GCP
credentials or network access required.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers: build a minimal fake google.genai module tree so that importing
# VertexAIProvider succeeds even when the real SDK is not installed.
# ---------------------------------------------------------------------------

def _make_genai_mock():
    """Return a MagicMock that looks like the google.genai package."""
    genai_mod = MagicMock()

    # types sub-module
    types_mod = MagicMock()
    image_part = MagicMock()
    types_mod.Part.from_bytes.return_value = image_part
    genai_mod.types = types_mod

    return genai_mod


def _install_fake_genai(genai_mock=None):
    """Inject a fake google.genai into sys.modules."""
    if genai_mock is None:
        genai_mock = _make_genai_mock()

    # google namespace package
    google_mod = sys.modules.get("google") or types.ModuleType("google")
    google_mod.genai = genai_mock
    sys.modules["google"] = google_mod
    sys.modules["google.genai"] = genai_mock
    sys.modules["google.genai.types"] = genai_mock.types
    return genai_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_vertex_module():
    """Remove vertex_provider from sys.modules before each test so that
    HAS_VERTEX and the Client import are re-evaluated with fresh mocks."""
    sys.modules.pop("castor.providers.vertex_provider", None)
    yield
    sys.modules.pop("castor.providers.vertex_provider", None)


@pytest.fixture()
def fake_genai():
    """Install a fake google.genai and return the mock."""
    return _install_fake_genai()


# ---------------------------------------------------------------------------
# Test: missing VERTEX_PROJECT raises ValueError
# ---------------------------------------------------------------------------

class TestVertexProviderMissingProject:
    def test_vertex_provider_missing_project(self, fake_genai, monkeypatch):
        """VertexAIProvider should raise ValueError when VERTEX_PROJECT is unset."""
        monkeypatch.delenv("VERTEX_PROJECT", raising=False)

        # Import fresh (module was cleared by autouse fixture)
        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        with pytest.raises(ValueError, match="VERTEX_PROJECT"):
            vmod.VertexAIProvider({"provider": "vertex_ai"})


# ---------------------------------------------------------------------------
# Test: think() returns a Thought with parsed action
# ---------------------------------------------------------------------------

class TestVertexProviderThink:
    def test_vertex_provider_think(self, fake_genai, monkeypatch):
        """think() should call generate_content and return a Thought."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")
        monkeypatch.delenv("VERTEX_LOCATION", raising=False)

        # Wire up a fake Client instance
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.text = '{"type": "move", "linear": 0.5, "angular": 0.0}'
        fake_client.models.generate_content.return_value = fake_response
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})

        sample_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        thought = provider.think(sample_image, "What do you see?")

        assert thought is not None
        assert "move" in thought.raw_text
        assert thought.action is not None
        assert thought.action["type"] == "move"
        assert thought.action["linear"] == 0.5

        # Verify generate_content was called with the model name
        fake_client.models.generate_content.assert_called_once()
        call_kwargs = fake_client.models.generate_content.call_args
        assert call_kwargs.kwargs["model"] == vmod.VertexAIProvider.DEFAULT_MODEL

    def test_vertex_provider_think_text_only(self, fake_genai, monkeypatch):
        """think() with blank image_bytes should send a text-only request."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.text = '{"type": "stop"}'
        fake_client.models.generate_content.return_value = fake_response
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})
        thought = provider.think(b"", "stop")

        assert thought.action is not None
        assert thought.action["type"] == "stop"

        # Contents should be a single string (no image Part)
        call_contents = fake_client.models.generate_content.call_args.kwargs["contents"]
        assert isinstance(call_contents, list)
        assert len(call_contents) == 1
        assert isinstance(call_contents[0], str)

    def test_vertex_provider_think_sdk_error_returns_error_thought(self, fake_genai, monkeypatch):
        """think() should return an error Thought when the SDK raises."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = RuntimeError("quota exceeded")
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})
        thought = provider.think(b"frame", "move forward")

        assert "Error" in thought.raw_text
        assert "quota exceeded" in thought.raw_text
        assert thought.action is None


# ---------------------------------------------------------------------------
# Test: health_check()
# ---------------------------------------------------------------------------

class TestVertexProviderHealthCheck:
    def test_vertex_provider_health_check_ok(self, fake_genai, monkeypatch):
        """health_check() should return ok=True when models.list() succeeds."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        fake_client = MagicMock()
        # models.list() returns an iterable with at least one item
        fake_client.models.list.return_value = iter([MagicMock(name="gemini-2.0-flash-001")])
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})
        result = provider.health_check()

        assert result["ok"] is True
        assert result["error"] is None
        assert isinstance(result["latency_ms"], float)

    def test_vertex_provider_health_check_fail(self, fake_genai, monkeypatch):
        """health_check() should return ok=False when models.list() raises."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        fake_client = MagicMock()
        fake_client.models.list.side_effect = PermissionError("credentials expired")
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})
        result = provider.health_check()

        assert result["ok"] is False
        assert "credentials expired" in result["error"]
        assert isinstance(result["latency_ms"], float)


# ---------------------------------------------------------------------------
# Test: get_provider() factory returns VertexAIProvider
# ---------------------------------------------------------------------------

class TestGetProviderVertexAI:
    def test_get_provider_vertex_ai(self, fake_genai, monkeypatch):
        """get_provider({'provider': 'vertex_ai'}) should return VertexAIProvider."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        fake_client = MagicMock()
        fake_genai.Client.return_value = fake_client

        # Patch VertexAIProvider inside the factory lookup path
        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        from castor.providers import get_provider

        provider = get_provider({"provider": "vertex_ai"})
        assert isinstance(provider, vmod.VertexAIProvider)

    def test_get_provider_vertex_alias(self, fake_genai, monkeypatch):
        """get_provider({'provider': 'vertex'}) should also work."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        fake_client = MagicMock()
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        from castor.providers import get_provider

        provider = get_provider({"provider": "vertex"})
        assert isinstance(provider, vmod.VertexAIProvider)

    def test_get_provider_vertexai_alias(self, fake_genai, monkeypatch):
        """get_provider({'provider': 'vertexai'}) should also work."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        fake_client = MagicMock()
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        from castor.providers import get_provider

        provider = get_provider({"provider": "vertexai"})
        assert isinstance(provider, vmod.VertexAIProvider)


# ---------------------------------------------------------------------------
# Test: SDK not installed raises helpful ValueError
# ---------------------------------------------------------------------------

class TestVertexProviderSdkNotInstalled:
    def test_raises_when_has_vertex_false(self, monkeypatch):
        """If HAS_VERTEX is False, __init__ should raise a helpful ValueError."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        # Ensure module is loaded then force HAS_VERTEX=False
        _install_fake_genai()
        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = False

        with pytest.raises(ValueError, match="google-genai"):
            vmod.VertexAIProvider({"provider": "vertex_ai"})


# ---------------------------------------------------------------------------
# Test: default model name and location
# ---------------------------------------------------------------------------

class TestVertexProviderDefaults:
    def test_default_model_name(self, fake_genai, monkeypatch):
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")
        monkeypatch.delenv("VERTEX_LOCATION", raising=False)

        fake_client = MagicMock()
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})
        assert provider.model_name == vmod.VertexAIProvider.DEFAULT_MODEL

    def test_default_location(self, fake_genai, monkeypatch):
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")
        monkeypatch.delenv("VERTEX_LOCATION", raising=False)

        fake_client = MagicMock()
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})
        assert provider.location == vmod.VertexAIProvider.DEFAULT_LOCATION

    def test_custom_model_from_config(self, fake_genai, monkeypatch):
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        fake_client = MagicMock()
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider(
            {"provider": "vertex_ai", "model": "gemini-1.5-pro-001"}
        )
        assert provider.model_name == "gemini-1.5-pro-001"

    def test_custom_location_from_env(self, fake_genai, monkeypatch):
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")
        monkeypatch.setenv("VERTEX_LOCATION", "europe-west4")

        fake_client = MagicMock()
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})
        assert provider.location == "europe-west4"

    def test_project_from_config_fallback(self, fake_genai, monkeypatch):
        """VERTEX_PROJECT missing from env → fall back to config key."""
        monkeypatch.delenv("VERTEX_PROJECT", raising=False)

        fake_client = MagicMock()
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider(
            {"provider": "vertex_ai", "vertex_project": "cfg-project"}
        )
        assert provider.project == "cfg-project"


# ---------------------------------------------------------------------------
# Test: think_stream()
# ---------------------------------------------------------------------------

class TestVertexProviderThinkStream:
    def test_think_stream_yields_chunks(self, fake_genai, monkeypatch):
        """think_stream() should yield text chunks from generate_content_stream."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        chunk1 = MagicMock()
        chunk1.text = '{"type":'
        chunk2 = MagicMock()
        chunk2.text = '"stop"}'

        fake_client = MagicMock()
        fake_client.models.generate_content_stream.return_value = iter([chunk1, chunk2])
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})
        result = list(provider.think_stream(b"frame", "stop"))

        assert result == ['{"type":', '"stop"}']

    def test_think_stream_error_yields_error_message(self, fake_genai, monkeypatch):
        """think_stream() should yield an error string when the SDK raises."""
        monkeypatch.setenv("VERTEX_PROJECT", "my-gcp-project")

        fake_client = MagicMock()
        fake_client.models.generate_content_stream.side_effect = RuntimeError("stream broken")
        fake_genai.Client.return_value = fake_client

        import castor.providers.vertex_provider as vmod
        vmod.HAS_VERTEX = True

        provider = vmod.VertexAIProvider({"provider": "vertex_ai"})
        result = list(provider.think_stream(b"frame", "move"))

        assert len(result) == 1
        assert "Error" in result[0]
        assert "stream broken" in result[0]
