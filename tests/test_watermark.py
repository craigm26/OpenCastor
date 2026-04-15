"""Tests for castor.watermark — AI output watermark token."""

import re

from castor.watermark import (
    compute_watermark_token,
    verify_token_format,
    verify_watermark_token,
)

FAKE_KEY = b"x" * 64  # stand-in for ML-DSA private key bytes
RRN = "RRN-000000000001"
THOUGHT_ID = "thought-abc123"
TIMESTAMP = "2026-04-10T14:32:01.123456"


class TestComputeWatermarkToken:
    def test_returns_correct_prefix(self):
        token = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, FAKE_KEY)
        assert token.startswith("rcan-wm-v1:")

    def test_returns_32_hex_chars_after_prefix(self):
        token = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, FAKE_KEY)
        hex_part = token.split(":", 1)[1]
        assert len(hex_part) == 32
        assert re.fullmatch(r"[0-9a-f]{32}", hex_part)

    def test_deterministic(self):
        t1 = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, FAKE_KEY)
        t2 = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, FAKE_KEY)
        assert t1 == t2

    def test_different_rrn_gives_different_token(self):
        t1 = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, FAKE_KEY)
        t2 = compute_watermark_token("RRN-000000000002", THOUGHT_ID, TIMESTAMP, FAKE_KEY)
        assert t1 != t2

    def test_different_thought_id_gives_different_token(self):
        t1 = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, FAKE_KEY)
        t2 = compute_watermark_token(RRN, "thought-xyz999", TIMESTAMP, FAKE_KEY)
        assert t1 != t2

    def test_different_key_gives_different_token(self):
        t1 = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, b"a" * 64)
        t2 = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, b"b" * 64)
        assert t1 != t2


class TestVerifyTokenFormat:
    def test_valid_token(self):
        token = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, FAKE_KEY)
        assert verify_token_format(token) is True

    def test_invalid_prefix(self):
        assert verify_token_format("rcan-wm-v2:a3f9c1d2b8e47f20a3f9c1d2b8e47f20") is False

    def test_too_short_hex(self):
        assert verify_token_format("rcan-wm-v1:a3f9c1d2") is False

    def test_non_hex_chars(self):
        assert verify_token_format("rcan-wm-v1:gggggggggggggggggggggggggggggggg") is False

    def test_empty_string(self):
        assert verify_token_format("") is False


class TestVerifyWatermarkToken:
    def test_returns_entry_when_found(self):
        token = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, FAKE_KEY)
        entry = {"watermark_token": token, "event": "motor_command"}
        audit_mock = type("A", (), {"_watermark_index": {token: entry}})()
        result = verify_watermark_token(token, audit_mock)
        assert result == entry

    def test_returns_none_when_not_found(self):
        audit_mock = type("A", (), {"_watermark_index": {}})()
        result = verify_watermark_token("rcan-wm-v1:" + "a" * 32, audit_mock)
        assert result is None

    def test_returns_none_for_invalid_format(self):
        audit_mock = type("A", (), {"_watermark_index": {}})()
        result = verify_watermark_token("invalid", audit_mock)
        assert result is None

    def test_returns_none_for_real_audit_log_without_index(self):
        """verify_watermark_token must not raise AttributeError before AuditLog has _watermark_index."""
        import os
        import tempfile

        from castor.audit import AuditLog

        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as f:
            log_path = f.name
        try:
            audit = AuditLog(log_path=log_path)
            # Remove _watermark_index to simulate pre-Task-2 state
            if hasattr(audit, "_watermark_index"):
                del audit._watermark_index
            token = compute_watermark_token(RRN, THOUGHT_ID, TIMESTAMP, FAKE_KEY)
            result = verify_watermark_token(token, audit)
            assert result is None
        finally:
            os.unlink(log_path)
