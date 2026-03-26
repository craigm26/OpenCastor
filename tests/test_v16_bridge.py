"""RCAN v1.6 Bridge integration tests.

Tests for v1.6 features in castor.cloud.bridge.CastorBridge:
  - Federation bypass for ESTOP (GAP-14 / P66 invariant)
  - LoA log-only mode (GAP-16)
  - Transport encoding detection (GAP-17)
  - Media chunk handling (GAP-18)

These tests use mocking to avoid real Firebase connections.

Spec: RCAN v1.6, OpenCastor v2026.3.17.1
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from castor.cloud.bridge import (
    CastorBridge,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

MINIMAL_CONFIG: dict[str, Any] = {
    "rrn": "RRN-00000042",
    "metadata": {
        "name": "TestBot",
        "ruri": "rcan://test-registry/bot",
    },
    "firebase_uid": "uid-test-owner",
    "owner": "rrn://test-owner",
    "min_loa_for_control": 1,
    "loa_enforcement": False,
}


def _make_bridge(**kwargs: Any) -> CastorBridge:
    cfg = {**MINIMAL_CONFIG, **kwargs}
    bridge = CastorBridge(
        config=cfg,
        firebase_project="test-project",
    )
    bridge._db = MagicMock()
    bridge._consent = MagicMock()
    bridge._consent.is_authorized.return_value = (True, "ok")
    return bridge


def _cmd_doc(**overrides: Any) -> dict[str, Any]:
    """Build a minimal command Firestore doc."""
    base: dict[str, Any] = {
        "scope": "chat",
        "instruction": "hello",
        "sender_type": "human",
        "status": "pending",
    }
    return {**base, **overrides}


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Federation — ESTOP bypass (GAP-14 / P66 invariant)
# ─────────────────────────────────────────────────────────────────────────────


class TestFederationEstopBypass:
    """ESTOP commands must bypass federation checks (Protocol 66 invariant)."""

    def test_estop_bypasses_federation_check(self, caplog: pytest.LogCaptureFixture) -> None:
        """ESTOP from a cross-registry source must still be allowed."""
        bridge = _make_bridge()

        # Simulate cross-registry ESTOP doc
        doc = _cmd_doc(
            scope="safety",
            instruction="estop",
            from_rrn="rrn://foreign-registry/robot/some-bot",
        )

        with caplog.at_level(logging.DEBUG, logger="castor.cloud.bridge"):
            result = bridge._check_federation("cmd-estop-001", doc, scope="safety")

        assert result is True
        assert "ESTOP bypasses federation check" in caplog.text

    def test_cross_registry_normal_command_logs_loa(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Cross-registry non-ESTOP command logs 'Cross-registry command from ...'."""
        bridge = _make_bridge()

        doc = _cmd_doc(
            scope="chat",
            instruction="move forward",
            from_rrn="rrn://foreign-registry/robot/some-bot",
        )
        doc["loa"] = 2  # LoA >= 2 required for cross-registry commands

        with caplog.at_level(logging.INFO, logger="castor.cloud.bridge"):
            result = bridge._check_federation("cmd-cross-001", doc, scope="chat")

        assert result is True
        assert "Cross-registry command from foreign-registry" in caplog.text

    def test_same_registry_skips_federation(self, caplog: pytest.LogCaptureFixture) -> None:
        """Commands from same registry don't trigger cross-registry check."""
        bridge = _make_bridge()

        # from_rrn matches own registry "test-registry"
        doc = _cmd_doc(
            scope="chat",
            instruction="ping",
            from_rrn="rrn://test-registry/robot/other-bot",
        )

        with caplog.at_level(logging.DEBUG, logger="castor.cloud.bridge"):
            result = bridge._check_federation("cmd-same-001", doc, scope="chat")

        assert result is True
        assert "Cross-registry command" not in caplog.text

    def test_no_from_rrn_passes_through(self) -> None:
        """Commands without from_rrn field pass through federation check."""
        bridge = _make_bridge()
        doc = _cmd_doc(scope="control", instruction="grip open")

        result = bridge._check_federation("cmd-no-rrn", doc, scope="control")
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# Tests: LoA log-only mode (GAP-16)
# ─────────────────────────────────────────────────────────────────────────────


class TestLoaLogOnlyMode:
    """LoA is extracted and logged, but enforcement is off by default."""

    def test_loa_logged_for_control_scope(self, caplog: pytest.LogCaptureFixture) -> None:
        """LoA check logs scope, loa, required, and enforcement state."""
        bridge = _make_bridge(loa_enforcement=False)

        doc = _cmd_doc(scope="control", instruction="move arm")

        with caplog.at_level(logging.INFO, logger="castor.cloud.bridge"):
            result = bridge._check_loa("cmd-loa-001", doc, scope="control")

        assert result is True
        assert "LoA check" in caplog.text
        assert "scope=control" in caplog.text
        assert "enforcement=off (log-only)" in caplog.text

    def test_loa_not_enforced_when_flag_off(self) -> None:
        """With loa_enforcement=False, LoA check always returns True."""
        bridge = _make_bridge(loa_enforcement=False, min_loa_for_control=3)

        # Even if LoA is only 1 and control requires LoA 3 — not enforced
        doc = _cmd_doc(scope="control", instruction="move")
        result = bridge._check_loa("cmd-loa-002", doc, scope="control")
        assert result is True

    def test_loa_enforced_when_flag_on(self) -> None:
        """With loa_enforcement=True and high min_loa, LoA 1 should be rejected."""
        bridge = _make_bridge(loa_enforcement=True, min_loa_for_control=3)

        # Stub always returns LoA 1, required is 3 → should fail when enforcement is on
        # But our stub _validate_loa_stub always returns True regardless
        # So we patch to simulate actual enforcement
        with patch(
            "castor.cloud.bridge._validate_loa_for_scope", return_value=False
        ), patch("castor.cloud.bridge._extract_loa_from_jwt", return_value=1):
            doc = _cmd_doc(scope="control", instruction="move arm", token="fake.jwt.token")
            result = bridge._check_loa("cmd-loa-003", doc, scope="control")
        assert result is False

    def test_loa_enforcement_on_logs_on(self, caplog: pytest.LogCaptureFixture) -> None:
        """When enforcement is on, the log says 'enforcement=on'."""
        bridge = _make_bridge(loa_enforcement=True)

        doc = _cmd_doc(scope="control", instruction="move")

        with caplog.at_level(logging.INFO, logger="castor.cloud.bridge"):
            bridge._check_loa("cmd-loa-004", doc, scope="control")

        assert "enforcement=on" in caplog.text


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Transport encoding detection (GAP-17)
# ─────────────────────────────────────────────────────────────────────────────


class TestTransportEncodingDetection:
    """Transport encoding field in Firestore docs triggers correct handling."""

    def test_minimal_encoding_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """transport_encoding='minimal' should log a WARNING."""
        bridge = _make_bridge()

        doc = _cmd_doc(
            transport_encoding="minimal",
            instruction="ping",
        )

        with caplog.at_level(logging.WARNING, logger="castor.cloud.bridge"):
            result_doc = bridge._check_transport_encoding(doc)

        assert "Minimal encoding command received via cloud" in caplog.text
        assert "upgrading to HTTP acknowledgment" in caplog.text
        # Doc should pass through unchanged (no decode needed for minimal)
        assert result_doc.get("instruction") == "ping"

    def test_http_encoding_passthrough(self, caplog: pytest.LogCaptureFixture) -> None:
        """Default HTTP encoding should pass through without any log."""
        bridge = _make_bridge()

        doc = _cmd_doc(transport_encoding="http", instruction="say hello")

        with caplog.at_level(logging.WARNING, logger="castor.cloud.bridge"):
            result_doc = bridge._check_transport_encoding(doc)

        assert "Minimal encoding" not in caplog.text
        assert result_doc.get("instruction") == "say hello"

    def test_no_encoding_field_defaults_to_http(self) -> None:
        """Docs without transport_encoding field default to 'http' (pass-through)."""
        bridge = _make_bridge()
        doc = _cmd_doc(instruction="status")
        result_doc = bridge._check_transport_encoding(doc)
        assert result_doc.get("instruction") == "status"

    def test_compact_encoding_attempts_decode(self) -> None:
        """transport_encoding='compact' with compact_payload triggers decode attempt."""
        bridge = _make_bridge()

        # Provide a fake compact_payload (just base64-encoded JSON for stub test)
        fake_payload = base64.b64encode(b'{"instruction": "decoded_cmd"}').decode()
        doc = _cmd_doc(
            transport_encoding="compact",
            compact_payload=fake_payload,
            instruction="original",
        )

        # rcan.transport not available — should fall back to raw doc without crashing
        result_doc = bridge._check_transport_encoding(doc)
        # Without rcan.transport, falls back to original doc
        assert result_doc is not None  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Media chunk handling (GAP-18)
# ─────────────────────────────────────────────────────────────────────────────


class TestMediaChunkHandling:
    """Media chunks are extracted, logged, and hashed for TRAINING_DATA commands."""

    def _make_chunk(self, text: str = "fake-image-data") -> dict[str, Any]:
        """Create a fake base64 image chunk."""
        encoded = base64.b64encode(text.encode()).decode()
        return {"id": "chunk-001", "type": "image/jpeg", "data": encoded}

    def test_no_media_chunks_returns_empty(self) -> None:
        """Commands without media_chunks return empty list."""
        bridge = _make_bridge()
        doc = _cmd_doc(instruction="move")
        chunks = bridge._handle_media_chunks("cmd-001", doc, scope="chat")
        assert chunks == []

    def test_single_chunk_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """A command with media_chunks logs count and total bytes."""
        bridge = _make_bridge()

        chunk = self._make_chunk("hello world image data")
        doc = _cmd_doc(instruction="describe image", media_chunks=[chunk])

        with caplog.at_level(logging.INFO, logger="castor.cloud.bridge"):
            chunks = bridge._handle_media_chunks("cmd-media-001", doc, scope="chat")

        assert len(chunks) == 1
        assert "Command has 1 media chunks" in caplog.text

    def test_multiple_chunks_total_bytes_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Multiple chunks: total byte count is logged."""
        bridge = _make_bridge()

        chunks = [
            self._make_chunk("data-a"),
            self._make_chunk("data-b-longer"),
        ]
        doc = _cmd_doc(instruction="describe images", media_chunks=chunks)

        with caplog.at_level(logging.INFO, logger="castor.cloud.bridge"):
            result = bridge._handle_media_chunks("cmd-media-002", doc, scope="chat")

        assert len(result) == 2
        assert "Command has 2 media chunks" in caplog.text

    def test_training_data_logs_sha256_hashes(self, caplog: pytest.LogCaptureFixture) -> None:
        """TRAINING_DATA commands log SHA-256 hashes of each chunk."""
        bridge = _make_bridge()

        chunk_data = b"training image bytes"
        encoded = base64.b64encode(chunk_data).decode()
        expected_hash = hashlib.sha256(chunk_data).hexdigest()

        chunk = {"id": "train-chunk-001", "type": "image/jpeg", "data": encoded}
        doc = _cmd_doc(
            scope="training_data",
            instruction="record training scene",
            media_chunks=[chunk],
        )

        with caplog.at_level(logging.INFO, logger="castor.cloud.bridge"):
            bridge._handle_media_chunks("cmd-train-001", doc, scope="training_data")

        assert "TRAINING_DATA media audit" in caplog.text
        assert expected_hash in caplog.text

    def test_training_scope_keyword_triggers_hash_audit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Commands with 'training' in scope string also get hash audit."""
        bridge = _make_bridge()

        chunk_data = b"scene data"
        encoded = base64.b64encode(chunk_data).decode()
        chunk = {"id": "c2", "type": "image/jpeg", "data": encoded}

        # scope doesn't have to be exactly "training_data" — check keyword
        doc = _cmd_doc(
            scope="training_collection",
            instruction="collect training data",
            media_chunks=[chunk],
        )

        with caplog.at_level(logging.INFO, logger="castor.cloud.bridge"):
            bridge._handle_media_chunks("cmd-train-002", doc, scope="training_collection")

        assert "TRAINING_DATA media audit" in caplog.text


# ─────────────────────────────────────────────────────────────────────────────
# Tests: v1.6 configuration fields
# ─────────────────────────────────────────────────────────────────────────────


class TestV16ConfigFields:
    """Verify v1.6 fields are read from config correctly."""

    def test_default_loa_enforcement_off(self) -> None:
        bridge = _make_bridge()
        assert bridge.loa_enforcement is False

    def test_default_min_loa_for_control(self) -> None:
        bridge = _make_bridge()
        assert bridge.min_loa_for_control == 1

    def test_custom_loa_config(self) -> None:
        bridge = _make_bridge(loa_enforcement=True, min_loa_for_control=2)
        assert bridge.loa_enforcement is True
        assert bridge.min_loa_for_control == 2

    def test_trust_anchor_cache_instantiated(self) -> None:
        bridge = _make_bridge()
        assert bridge.trust_anchor_cache is not None

    def test_bridge_version_is_v16(self) -> None:
        from castor.cloud.bridge import BRIDGE_VERSION

        assert BRIDGE_VERSION == "1.6.0"
