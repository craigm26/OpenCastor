"""RCAN v1.6 DISCOVER endpoint tests.

Verifies that:
  - GET /api/v1/transports returns correct transport info
  - POST /api/rcan/message with DISCOVER includes v1.6 fields
  - GET /api/v1/media/{chunk_id} returns 404 stub correctly

Spec: RCAN v1.6, OpenCastor v2026.3.17.1
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# ─────────────────────────────────────────────────────────────────────────────
# App fixture
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Create a TestClient for the castor API app."""
    from castor.api import app

    return TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: GET /api/v1/transports
# ─────────────────────────────────────────────────────────────────────────────


class TestTransportsEndpoint:
    """GET /api/v1/transports returns supported transport encodings."""

    def test_transports_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/v1/transports")
        assert resp.status_code == 200

    def test_transports_includes_http(self, client: TestClient) -> None:
        resp = client.get("/api/v1/transports")
        data = resp.json()
        assert "http" in data["supported"]

    def test_transports_includes_compact(self, client: TestClient) -> None:
        resp = client.get("/api/v1/transports")
        data = resp.json()
        assert "compact" in data["supported"]

    def test_preferred_transport_is_http(self, client: TestClient) -> None:
        resp = client.get("/api/v1/transports")
        data = resp.json()
        assert data["preferred"] == "http"

    def test_transports_structure(self, client: TestClient) -> None:
        resp = client.get("/api/v1/transports")
        data = resp.json()
        assert "supported" in data
        assert "preferred" in data
        assert isinstance(data["supported"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: GET /api/v1/media/{chunk_id}
# ─────────────────────────────────────────────────────────────────────────────


class TestMediaEndpoint:
    """GET /api/v1/media/{chunk_id} returns 404 (stub for v1.6)."""

    def test_media_chunk_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/media/unknown-chunk-id")
        assert resp.status_code == 404

    def test_media_chunk_error_message(self, client: TestClient) -> None:
        resp = client.get("/api/v1/media/some-chunk-xyz")
        data = resp.json()
        # The app may use "detail" (HTTPException) or "error" (custom handler)
        msg = data.get("detail", data.get("error", "")).lower()
        assert "not yet implemented" in msg

    def test_media_different_chunk_ids_all_404(self, client: TestClient) -> None:
        for chunk_id in ["chunk-001", "abc123", "image-uuid-xyz"]:
            resp = client.get(f"/api/v1/media/{chunk_id}")
            assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Tests: RCAN DISCOVER response includes v1.6 fields
# ─────────────────────────────────────────────────────────────────────────────


class TestDiscoverV16Fields:
    """POST /api/rcan/message with DISCOVER (msg_type=1) includes v1.6 fields."""

    def test_discover_response_200(self, client: TestClient) -> None:
        resp = client.post(
            "/api/rcan/message",
            json={"msg_type": 1, "source": "rcan://test/client"},
        )
        assert resp.status_code == 200

    def test_discover_includes_supported_transports(self, client: TestClient) -> None:
        resp = client.post(
            "/api/rcan/message",
            json={"msg_type": 1, "source": "rcan://test/client"},
        )
        data = resp.json()
        assert "supported_transports" in data
        assert "http" in data["supported_transports"]
        assert "compact" in data["supported_transports"]

    def test_discover_includes_rcan_version_16(self, client: TestClient) -> None:
        resp = client.post(
            "/api/rcan/message",
            json={"msg_type": 1, "source": "rcan://test/client"},
        )
        data = resp.json()
        assert data.get("rcan_version") == "1.6"

    def test_discover_includes_loa_enforcement(self, client: TestClient) -> None:
        resp = client.post(
            "/api/rcan/message",
            json={"msg_type": 1, "source": "rcan://test/client"},
        )
        data = resp.json()
        assert "loa_enforcement" in data
        assert data["loa_enforcement"] is False

    def test_discover_includes_min_loa_for_control(self, client: TestClient) -> None:
        resp = client.post(
            "/api/rcan/message",
            json={"msg_type": 1, "source": "rcan://test/client"},
        )
        data = resp.json()
        assert "min_loa_for_control" in data
        assert data["min_loa_for_control"] == 1

    def test_discover_includes_federation_enabled(self, client: TestClient) -> None:
        resp = client.post(
            "/api/rcan/message",
            json={"msg_type": 1, "source": "rcan://test/client"},
        )
        data = resp.json()
        assert "federation_enabled" in data
        assert data["federation_enabled"] is False

    def test_discover_includes_ruri(self, client: TestClient) -> None:
        resp = client.post(
            "/api/rcan/message",
            json={"msg_type": 1, "source": "rcan://test/client"},
        )
        data = resp.json()
        assert "ruri" in data

    def test_discover_includes_capabilities(self, client: TestClient) -> None:
        resp = client.post(
            "/api/rcan/message",
            json={"msg_type": 1, "source": "rcan://test/client"},
        )
        data = resp.json()
        assert "capabilities" in data
        assert isinstance(data["capabilities"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: CommandRequest model has v1.6 fields
# ─────────────────────────────────────────────────────────────────────────────


class TestCommandRequestV16:
    """CommandRequest model includes v1.6 transport and media_chunks fields."""

    def test_command_request_default_transport(self) -> None:
        from castor.api import CommandRequest

        req = CommandRequest(instruction="hello")
        assert req.transport == "http"

    def test_command_request_custom_transport(self) -> None:
        from castor.api import CommandRequest

        req = CommandRequest(instruction="hello", transport="compact")
        assert req.transport == "compact"

    def test_command_request_default_media_chunks_empty(self) -> None:
        from castor.api import CommandRequest

        req = CommandRequest(instruction="hello")
        assert req.media_chunks == []

    def test_command_request_with_media_chunks(self) -> None:
        from castor.api import CommandRequest

        chunks = [{"id": "c1", "type": "image/jpeg", "data": "aGVsbG8="}]
        req = CommandRequest(instruction="describe image", media_chunks=chunks)
        assert len(req.media_chunks) == 1
        assert req.media_chunks[0]["id"] == "c1"
