"""Tests for castor.authority.AuthorityRequestHandler.

Pins the three notify_fn behaviors that the cross-cutting notify-wiring pr
relies on:
  1. When wired, the handler emits the AUTHORITY ACCESS summary to notify_fn
     before returning the response.
  2. When notify_fn is None, the handler logs a warning but still completes
     the request (today's behavior — must not regress).
  3. When notify_fn raises, the existing try/except absorbs it; response
     still produced.
"""

from __future__ import annotations

import logging

from castor.authority import AuthorityRequestHandler


def _valid_payload() -> dict:
    """Builds a minimal AUTHORITY_ACCESS payload that passes validation."""
    return {
        "authority_id": "eu.aiact.notified-body.001",
        "request_id": "req-test-001",
        "requested_data": ["safety_manifest"],
        "justification": "compliance audit",
        "expires_at": 0,  # 0 means "no expiry"
    }


class TestNotifyOwner:
    def test_notify_fn_receives_authority_access_summary(self):
        recorded: list[str] = []

        handler = AuthorityRequestHandler(
            rrn="RRN-000000000003",
            notify_fn=lambda msg: recorded.append(msg),
            trusted_authority_ids={"eu.aiact.notified-body.001"},
        )

        result = handler.handle(_valid_payload())

        assert len(recorded) == 1
        summary = recorded[0]
        assert "AUTHORITY ACCESS REQUEST" in summary
        assert "eu.aiact.notified-body.001" in summary
        assert "req-test-001" in summary
        assert "safety_manifest" in summary
        assert "compliance audit" in summary
        # Response was still produced
        assert result["request_id"] == "req-test-001"
        assert result["rrn"] == "RRN-000000000003"

    def test_notify_fn_none_logs_warning_and_completes(self, caplog):
        handler = AuthorityRequestHandler(
            rrn="RRN-000000000003",
            notify_fn=None,
            trusted_authority_ids={"eu.aiact.notified-body.001"},
        )

        with caplog.at_level(logging.WARNING, logger="OpenCastor.Authority"):
            result = handler.handle(_valid_payload())

        # Today's protective branch: warning is emitted
        assert any("No notify_fn configured" in r.message for r in caplog.records)
        # ... but the response is still produced
        assert result["request_id"] == "req-test-001"

    def test_notify_fn_exception_does_not_break_response(self, caplog):
        def boom(_msg: str) -> None:
            raise RuntimeError("notify channel exploded")

        handler = AuthorityRequestHandler(
            rrn="RRN-000000000003",
            notify_fn=boom,
            trusted_authority_ids={"eu.aiact.notified-body.001"},
        )

        with caplog.at_level(logging.ERROR, logger="OpenCastor.Authority"):
            result = handler.handle(_valid_payload())

        # Existing try/except at authority.py:287-290 absorbs it
        assert any("Failed to notify owner" in r.message for r in caplog.records)
        assert result["request_id"] == "req-test-001"
