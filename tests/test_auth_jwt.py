"""Tests for multi-user JWT authentication (Issue #124)."""

import os
from unittest.mock import MagicMock, patch

import pytest

# Skip all tests if PyJWT is not installed
jwt = pytest.importorskip("jwt", reason="PyJWT not installed")


class TestParseUsersEnv:
    def test_parse_users_env_empty(self):
        """No OPENCASTOR_USERS env var returns empty dict."""
        from castor.auth_jwt import parse_users_env

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENCASTOR_USERS", None)
            result = parse_users_env()
        assert result == {}

    def test_parse_users_env_single(self):
        """A single user entry is parsed correctly."""
        import hashlib

        from castor.auth_jwt import parse_users_env

        with patch.dict(os.environ, {"OPENCASTOR_USERS": "admin:secret123:admin"}):
            result = parse_users_env()

        assert "admin" in result
        assert result["admin"]["role"] == "admin"
        expected_hash = hashlib.sha256("secret123".encode()).hexdigest()
        assert result["admin"]["password_hash"] == expected_hash

    def test_parse_users_env_multiple(self):
        """Multiple comma-separated users are all parsed."""
        from castor.auth_jwt import parse_users_env

        env_val = "admin:pass1:admin,operator:pass2:operator,viewer:pass3:viewer"
        with patch.dict(os.environ, {"OPENCASTOR_USERS": env_val}):
            result = parse_users_env()

        assert set(result.keys()) == {"admin", "operator", "viewer"}
        assert result["admin"]["role"] == "admin"
        assert result["operator"]["role"] == "operator"
        assert result["viewer"]["role"] == "viewer"


class TestCreateDecodeToken:
    def test_create_and_decode_token(self):
        """create_token and decode_token should round-trip correctly."""
        from castor.auth_jwt import create_token, decode_token

        token = create_token("alice", "operator", secret="test-secret-xyz")
        payload = decode_token(token, secret="test-secret-xyz")

        assert payload["sub"] == "alice"
        assert payload["role"] == "operator"

    def test_expired_token_raises(self):
        """An expired token should raise jwt.ExpiredSignatureError."""
        from castor.auth_jwt import create_token, decode_token

        # Create a token with expires_h=0 is not directly possible,
        # so create one in the past using a manual payload
        import datetime

        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
        payload = {
            "sub": "expired_user",
            "role": "viewer",
            "iat": past,
            "exp": past + datetime.timedelta(seconds=1),
        }
        token = jwt.encode(payload, "test-secret-xyz", algorithm="HS256")
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token, secret="test-secret-xyz")

    def test_invalid_token_raises(self):
        """A garbage string should raise jwt.InvalidTokenError."""
        from castor.auth_jwt import decode_token

        with pytest.raises(jwt.exceptions.DecodeError):
            decode_token("this.is.not.a.valid.jwt", secret="test-secret-xyz")


class TestAPIEndpoints:
    """Tests for the FastAPI /auth/token and /auth/me endpoints."""

    @pytest.fixture
    def client(self):
        """Return a TestClient for the OpenCastor API with clean env."""
        from fastapi.testclient import TestClient

        import castor.api as api_mod

        # Patch out the static API token so no-auth tests work cleanly
        with patch.object(api_mod, "API_TOKEN", None):
            with patch.dict(
                os.environ,
                {
                    "OPENCASTOR_USERS": "admin:adminpass:admin,viewer:viewerpass:viewer,ops:opspass:operator",
                    "JWT_SECRET": "test-jwt-secret-for-tests",
                },
            ):
                yield TestClient(api_mod.app)

    def test_token_endpoint_valid_credentials(self, client):
        """POST /auth/token with valid credentials returns 200 and access_token."""
        resp = client.post("/auth/token", json={"username": "admin", "password": "adminpass"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["role"] == "admin"
        assert data["expires_in"] == 86400

    def test_token_endpoint_wrong_password(self, client):
        """POST /auth/token with wrong password returns 401."""
        resp = client.post("/auth/token", json={"username": "admin", "password": "wrongpass"})
        assert resp.status_code == 401

    def test_auth_me_jwt(self, client):
        """GET /auth/me with a valid JWT returns username and role."""
        # Get a token first
        resp = client.post("/auth/token", json={"username": "admin", "password": "adminpass"})
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        data = me_resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
        assert data["auth_type"] == "jwt"

    def test_auth_me_static_token(self):
        """GET /auth/me with a static bearer token returns auth_type: static."""
        from fastapi.testclient import TestClient

        import castor.api as api_mod

        static = "my-static-test-token"
        with patch.object(api_mod, "API_TOKEN", static):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("JWT_SECRET", None)
                os.environ.pop("OPENCASTOR_JWT_SECRET", None)
                os.environ.pop("OPENCASTOR_USERS", None)
                c = TestClient(api_mod.app)
                resp = c.get("/auth/me", headers={"Authorization": f"Bearer {static}"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_type"] == "static"

    def test_viewer_blocked_from_command(self, client):
        """A viewer JWT should receive 403 when calling POST /api/command."""
        # Get viewer token
        resp = client.post("/auth/token", json={"username": "viewer", "password": "viewerpass"})
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        # Attempt to send a command — viewers should be blocked
        cmd_resp = client.post(
            "/api/command",
            json={"instruction": "go forward"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert cmd_resp.status_code == 403

    def test_operator_allowed_command(self, client):
        """An operator JWT should NOT receive 403 for POST /api/command."""
        # Get operator token
        resp = client.post("/auth/token", json={"username": "ops", "password": "opspass"})
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        # Attempt command — operators can send commands (may fail for other reasons but not 403)
        cmd_resp = client.post(
            "/api/command",
            json={"instruction": "go forward"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert cmd_resp.status_code != 403
