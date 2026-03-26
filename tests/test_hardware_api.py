"""
Tests for GET /api/hardware — hardware profile + LLMFit tier detection.
"""

import contextlib
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_mod():
    """Return the castor.api module for direct state manipulation."""
    import castor.api as mod
    return mod


@pytest.fixture()
def client(api_mod):
    """TestClient with startup/shutdown bypassed and no config loaded."""
    from castor.api import app

    original_startup = app.router.on_startup[:]
    original_shutdown = app.router.on_shutdown[:]
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    app.router.lifespan_context = _noop_lifespan
    api_mod.state.config = None
    api_mod.API_TOKEN = "test-token"

    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.router.on_startup[:] = original_startup
        app.router.on_shutdown[:] = original_shutdown
        app.router.lifespan_context = original_lifespan
        api_mod.API_TOKEN = None
        api_mod.state.config = None


_AUTH = {"Authorization": "Bearer test-token"}


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestHardwareAuth:
    def test_requires_auth_no_token_env(self, client, api_mod):
        """Without a token env var the endpoint is open — just check 200."""
        api_mod.API_TOKEN = None
        resp = client.get("/api/hardware")
        assert resp.status_code == 200

    def test_requires_auth_with_token_set(self, client, api_mod):
        """When API_TOKEN is set, missing auth yields 401/403."""
        api_mod.API_TOKEN = "secret"
        resp = client.get("/api/hardware")
        assert resp.status_code in (401, 403)
        api_mod.API_TOKEN = "test-token"

    def test_valid_token_accepted(self, client):
        resp = client.get("/api/hardware", headers=_AUTH)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


class TestHardwareResponseShape:
    def test_returns_required_keys(self, client):
        resp = client.get("/api/hardware", headers=_AUTH)
        data = resp.json()
        required = {
            "hostname",
            "arch",
            "platform",
            "cpu_model",
            "cpu_cores",
            "ram_gb",
            "ram_available_gb",
            "storage_free_gb",
            "accelerators",
            "accessories",
            "hardware_tier",
            "ollama_models",
            "rcan_hardware",
        }
        missing = required - data.keys()
        assert not missing, f"Missing keys: {missing}"

    def test_hardware_tier_is_string(self, client):
        resp = client.get("/api/hardware", headers=_AUTH)
        assert isinstance(resp.json()["hardware_tier"], str)

    def test_ollama_models_is_list(self, client):
        resp = client.get("/api/hardware", headers=_AUTH)
        assert isinstance(resp.json()["ollama_models"], list)

    def test_accelerators_is_list(self, client):
        resp = client.get("/api/hardware", headers=_AUTH)
        assert isinstance(resp.json()["accelerators"], list)

    def test_cpu_cores_positive_int(self, client):
        resp = client.get("/api/hardware", headers=_AUTH)
        data = resp.json()
        assert isinstance(data["cpu_cores"], int)
        assert data["cpu_cores"] >= 1

    def test_platform_is_lowercase_string(self, client):
        resp = client.get("/api/hardware", headers=_AUTH)
        plat = resp.json()["platform"]
        assert isinstance(plat, str)
        assert plat == plat.lower()

    def test_ram_gb_is_non_negative(self, client):
        resp = client.get("/api/hardware", headers=_AUTH)
        assert resp.json()["ram_gb"] >= 0


# ---------------------------------------------------------------------------
# Hardware tier logic (_compute_hardware_tier)
# ---------------------------------------------------------------------------


class TestComputeHardwareTier:
    def _tier(self, ram_gb, cpu_model, accelerators):
        from castor.api import _compute_hardware_tier
        return _compute_hardware_tier(ram_gb, cpu_model, accelerators)

    def test_hailo_accelerator_returns_pi5_hailo(self):
        assert self._tier(8, "Cortex-A76", ["hailo-8l"]) == "pi5-hailo"

    def test_hailo_case_insensitive(self):
        assert self._tier(8, "Cortex-A76", ["Hailo-8L"]) == "pi5-hailo"

    def test_server_tier_16gb(self):
        assert self._tier(16, "x86_64", []) == "server"

    def test_server_tier_32gb(self):
        assert self._tier(32, "AMD EPYC", []) == "server"

    def test_pi5_8gb_with_a76(self):
        assert self._tier(8, "Cortex-A76", []) == "pi5-8gb"

    def test_pi4_8gb_without_a76(self):
        assert self._tier(8, "Cortex-A72", []) == "pi4-8gb"

    def test_pi5_4gb_with_a76(self):
        assert self._tier(4, "Cortex-A76", []) == "pi5-4gb"

    def test_pi4_4gb_without_a76(self):
        assert self._tier(4, "Cortex-A72", []) == "pi4-4gb"

    def test_minimal_tier_below_4gb(self):
        assert self._tier(1, "ARMv6", []) == "minimal"

    def test_minimal_zero_ram(self):
        assert self._tier(0, "", []) == "minimal"


# ---------------------------------------------------------------------------
# RCAN config integration
# ---------------------------------------------------------------------------


class TestHardwareRcanConfig:
    def test_rcan_hardware_populated_from_config(self, client, api_mod):
        api_mod.state.config = {
            "hardware": {
                "accessories": ["oak-d", "pca9685"],
                "accelerators": [],
            }
        }
        resp = client.get("/api/hardware", headers=_AUTH)
        data = resp.json()
        assert "oak-d" in data["accessories"]
        assert data["rcan_hardware"] != {}

    def test_hailo_accessory_added_to_accelerators(self, client, api_mod):
        api_mod.state.config = {
            "hardware": {
                "accessories": ["hailo-8l"],
                "accelerators": [],
            }
        }
        resp = client.get("/api/hardware", headers=_AUTH)
        data = resp.json()
        assert any("hailo" in a.lower() for a in data["accelerators"])
        assert data["hardware_tier"] == "pi5-hailo"

    def test_no_config_returns_empty_rcan_hardware(self, client, api_mod):
        api_mod.state.config = None
        resp = client.get("/api/hardware", headers=_AUTH)
        assert resp.json()["rcan_hardware"] == {}


# ---------------------------------------------------------------------------
# Ollama models
# ---------------------------------------------------------------------------


class TestOllamaModels:
    def test_ollama_not_installed_returns_empty_list(self, client):
        with patch("castor.api._get_ollama_models", return_value=[]):
            resp = client.get("/api/hardware", headers=_AUTH)
        assert resp.json()["ollama_models"] == []

    def test_ollama_models_listed(self, client):
        fake_models = ["gemma3:1b", "gemma3:4b"]
        with patch("castor.api._get_ollama_models", return_value=fake_models):
            resp = client.get("/api/hardware", headers=_AUTH)
        assert resp.json()["ollama_models"] == fake_models
