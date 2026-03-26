"""Tests for gated model provider authentication."""

import time

import pytest

from castor.auth.provider_auth import (
    ApiKeyAuth,
    AuthCredentials,
    BearerAuth,
    HuggingFaceAuth,
    MutualTLSAuth,
    OAuth2Auth,
    _resolve_secret,
    create_provider_auth,
)
from castor.providers.gated import GatedModelProvider

# ── Secret resolution ────────────────────────────────────────────────────


class TestResolveSecret:
    def test_plain_string(self):
        assert _resolve_secret("sk-abc123") == "sk-abc123"

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_SECRET", "my-secret-value")
        assert _resolve_secret("${TEST_SECRET}") == "my-secret-value"

    def test_env_var_missing(self):
        assert _resolve_secret("${NONEXISTENT_VAR_12345}") == ""

    def test_empty_string(self):
        assert _resolve_secret("") == ""

    def test_file_prefix(self, tmp_path):
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("file-secret-value\n")
        assert _resolve_secret(f"file:{secret_file}") == "file-secret-value"

    def test_file_missing(self):
        assert _resolve_secret("file:/nonexistent/path") == ""


# ── ApiKeyAuth ───────────────────────────────────────────────────────────


class TestApiKeyAuth:
    def test_basic_api_key(self):
        auth = ApiKeyAuth({"method": "api_key", "api_key": "sk-test123"})
        creds = auth.authenticate()
        assert creds.headers["Authorization"] == "Bearer sk-test123"

    def test_custom_header(self):
        auth = ApiKeyAuth(
            {
                "method": "api_key",
                "api_key": "key123",
                "header": "X-API-Key",
                "prefix": "",
            }
        )
        creds = auth.authenticate()
        assert creds.headers["X-API-Key"] == "key123"

    def test_env_var_key(self, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "env-key-value")
        auth = ApiKeyAuth({"method": "api_key", "api_key": "${MY_API_KEY}"})
        creds = auth.authenticate()
        assert creds.headers["Authorization"] == "Bearer env-key-value"

    def test_missing_key_raises(self):
        auth = ApiKeyAuth({"method": "api_key", "api_key": ""})
        with pytest.raises(ValueError, match="not configured"):
            auth.authenticate()


# ── BearerAuth ───────────────────────────────────────────────────────────


class TestBearerAuth:
    def test_basic_bearer(self):
        auth = BearerAuth({"method": "bearer", "token": "jwt-token-abc"})
        creds = auth.authenticate()
        assert creds.headers["Authorization"] == "Bearer jwt-token-abc"
        assert creds.expires_at == 0.0  # No expiry without refresh URL

    def test_with_refresh_url(self):
        auth = BearerAuth(
            {
                "method": "bearer",
                "token": "jwt-token",
                "refresh_url": "https://auth.example.com/refresh",
            }
        )
        creds = auth.authenticate()
        assert creds.expires_at > time.time()


# ── HuggingFaceAuth ─────────────────────────────────────────────────────


class TestHuggingFaceAuth:
    def test_basic_hf_token(self):
        auth = HuggingFaceAuth({"method": "huggingface", "token": "hf_abc123"})
        creds = auth.authenticate()
        assert creds.headers["Authorization"] == "Bearer hf_abc123"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_env_token")
        auth = HuggingFaceAuth({"method": "huggingface"})
        creds = auth.authenticate()
        assert creds.headers["Authorization"] == "Bearer hf_env_token"

    def test_missing_token(self, monkeypatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        auth = HuggingFaceAuth({"method": "huggingface", "token": ""})
        with pytest.raises(ValueError, match="not configured"):
            auth.authenticate()


# ── MutualTLSAuth ────────────────────────────────────────────────────────


class TestMutualTLSAuth:
    def test_basic_mtls(self, tmp_path):
        cert = tmp_path / "client.pem"
        key = tmp_path / "client-key.pem"
        cert.write_text("CERT")
        key.write_text("KEY")

        auth = MutualTLSAuth(
            {
                "method": "mutual_tls",
                "client_cert": str(cert),
                "client_key": str(key),
            }
        )
        creds = auth.authenticate()
        assert creds.client_cert == (str(cert), str(key))

    def test_with_ca_bundle(self, tmp_path):
        cert = tmp_path / "client.pem"
        key = tmp_path / "client-key.pem"
        ca = tmp_path / "ca.pem"
        cert.write_text("CERT")
        key.write_text("KEY")
        ca.write_text("CA")

        auth = MutualTLSAuth(
            {
                "method": "mutual_tls",
                "client_cert": str(cert),
                "client_key": str(key),
                "ca_bundle": str(ca),
            }
        )
        creds = auth.authenticate()
        assert creds.ca_bundle == str(ca)

    def test_missing_cert(self):
        auth = MutualTLSAuth(
            {
                "method": "mutual_tls",
                "client_cert": "/nonexistent/cert.pem",
                "client_key": "/nonexistent/key.pem",
            }
        )
        with pytest.raises(FileNotFoundError):
            auth.authenticate()


# ── Factory ──────────────────────────────────────────────────────────────


class TestFactory:
    def test_create_api_key(self):
        auth = create_provider_auth({"method": "api_key", "api_key": "test"})
        assert isinstance(auth, ApiKeyAuth)

    def test_create_oauth2(self):
        auth = create_provider_auth({"method": "oauth2"})
        assert isinstance(auth, OAuth2Auth)

    def test_unknown_method(self):
        with pytest.raises(ValueError, match="Unknown auth method"):
            create_provider_auth({"method": "kerberos"})

    def test_all_methods_registered(self):
        from castor.auth.provider_auth import _AUTH_HANDLERS

        expected = {"api_key", "bearer", "oauth2", "huggingface", "mutual_tls", "oidc"}
        assert set(_AUTH_HANDLERS.keys()) == expected


# ── AuthCredentials ──────────────────────────────────────────────────────


class TestAuthCredentials:
    def test_not_expired_by_default(self):
        creds = AuthCredentials()
        assert not creds.expired

    def test_expired(self):
        creds = AuthCredentials(expires_at=time.time() - 60)
        assert creds.expired

    def test_buffer_before_expiry(self):
        # 20 seconds from now (within 30s buffer)
        creds = AuthCredentials(expires_at=time.time() + 20)
        assert creds.expired  # Should be considered expired due to buffer


# ── GatedModelProvider ───────────────────────────────────────────────────


class TestGatedModelProvider:
    def test_telemetry_never_includes_credentials(self):
        provider = GatedModelProvider(
            {
                "name": "test-provider",
                "base_url": "https://api.example.com",
                "auth": {"method": "api_key", "api_key": "sk-super-secret"},
                "models": ["model-a"],
            }
        )
        telemetry = provider.telemetry()
        # Verify no secrets leak
        telemetry_str = str(telemetry)
        assert "sk-super-secret" not in telemetry_str
        assert "api_key" not in telemetry_str or telemetry["auth_method"] == "api_key"
        assert telemetry["provider"] == "test-provider"
        assert telemetry["models"] == ["model-a"]
        assert telemetry["auth_method"] == "api_key"

    def test_status_tracks_failures(self):
        provider = GatedModelProvider(
            {
                "name": "test",
                "base_url": "https://api.example.com",
            }
        )
        assert provider.status.available is True
        assert provider.status.consecutive_failures == 0

    async def test_model_not_in_list(self):
        provider = GatedModelProvider(
            {
                "name": "test",
                "base_url": "https://api.example.com",
                "models": ["model-a", "model-b"],
            }
        )
        result = await provider.inference(model="model-c", prompt="test")
        assert result.error is not None
        assert "not available" in result.error

    def test_fallback_config(self):
        provider = GatedModelProvider(
            {
                "name": "pi",
                "base_url": "https://api.physicalintelligence.company/v1",
                "auth": {"method": "oauth2"},
                "models": ["pi0"],
                "fallback_provider": "ollama",
                "fallback_model": "rt2-x",
            }
        )
        assert provider.fallback_model == "rt2-x"
        assert provider.fallback_provider == "ollama"

    def test_get_credentials_with_no_auth(self):
        provider = GatedModelProvider(
            {
                "name": "open-provider",
                "base_url": "https://api.example.com",
            }
        )
        assert provider.get_credentials() is None


# ── Credential caching ───────────────────────────────────────────────────


class TestCredentialCaching:
    def test_credentials_cached(self):
        auth = ApiKeyAuth({"method": "api_key", "api_key": "cached-key"})
        creds1 = auth.get_credentials()
        creds2 = auth.get_credentials()
        assert creds1 is creds2  # Same object

    def test_invalidate_forces_refresh(self):
        auth = ApiKeyAuth({"method": "api_key", "api_key": "key"})
        creds1 = auth.get_credentials()
        auth.invalidate()
        creds2 = auth.get_credentials()
        assert creds1 is not creds2  # Different objects
