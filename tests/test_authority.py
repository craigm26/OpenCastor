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


class TestFailClosed:
    """OC-13: an unconfigured runtime refuses every authority request."""

    def test_no_allowlist_refuses_every_request(self, caplog):
        import pytest

        from castor.authority import (
            TRUSTED_AUTHORITY_CONFIG_KEY,
            AuthorityNotRecognizedError,
        )

        recorded: list[str] = []
        handler = AuthorityRequestHandler(
            rrn="RRN-000000000003",
            notify_fn=lambda msg: recorded.append(msg),
            # No trusted_authority_ids at all — today's shipped state.
        )

        with caplog.at_level(logging.WARNING, logger="OpenCastor.Authority"):
            with pytest.raises(AuthorityNotRecognizedError):
                handler.handle(_valid_payload())

        # The owner is still notified, even though the request is refused.
        assert len(recorded) == 1
        assert "AUTHORITY ACCESS REQUEST" in recorded[0]
        # The log line names the config key the operator has to set.
        assert any(TRUSTED_AUTHORITY_CONFIG_KEY in r.message for r in caplog.records)
        assert any("REFUSED" in r.message for r in caplog.records)

    def test_explicit_none_is_not_accept_all(self):
        import pytest

        from castor.authority import AuthorityNotRecognizedError

        handler = AuthorityRequestHandler(rrn="RRN-1", trusted_authority_ids=None)
        assert handler.trusted_authority_ids == set()
        with pytest.raises(AuthorityNotRecognizedError):
            handler.handle(_valid_payload())

    def test_empty_set_is_not_accept_all(self):
        import pytest

        from castor.authority import AuthorityNotRecognizedError

        handler = AuthorityRequestHandler(rrn="RRN-1", trusted_authority_ids=set())
        with pytest.raises(AuthorityNotRecognizedError):
            handler.handle(_valid_payload())

    def test_rate_limit_rejects_the_second_request(self):
        import pytest

        from castor.authority import AuthorityRateLimitedError

        handler = AuthorityRequestHandler(
            rrn="RRN-1", trusted_authority_ids={"eu.aiact.notified-body.001"}
        )
        handler.handle(_valid_payload())
        with pytest.raises(AuthorityRateLimitedError):
            handler.handle(_valid_payload())

    def test_transparency_export_says_unavailable_rather_than_empty(self):
        handler = AuthorityRequestHandler(
            rrn="RRN-1", trusted_authority_ids={"eu.aiact.notified-body.001"}
        )
        payload = _valid_payload()
        payload["requested_data"] = ["transparency_records"]
        out = handler.handle(payload)
        note = out["data"]["export_notes"]["transparency_records"]
        assert note["unavailable"] is True
        assert "truncated" in note

    def test_allowlist_from_config(self):
        from castor.authority import trusted_authority_ids_from_config

        assert trusted_authority_ids_from_config(None) == set()
        assert trusted_authority_ids_from_config({}) == set()
        assert trusted_authority_ids_from_config(
            {"authority": {"trusted_authority_ids": ["a", "b"]}}
        ) == {"a", "b"}
        assert trusted_authority_ids_from_config({"trusted_authority_ids": "a"}) == {"a"}


class TestConformanceRowRequiresAllowlist:
    """OC-13: the conformance row can no longer pass on a config flag."""

    def _base_cfg(self) -> dict:
        return {
            "rcan_version": "2.1.0",
            "harness": {"message_handlers": {41: "castor.authority"}},
        }

    def test_conformance_v21_fails_with_empty_allowlist_even_when_flag_true(self):
        from castor.conformance import ConformanceChecker

        cfg = self._base_cfg()
        cfg["authority_handler_enabled"] = True  # the old escape hatch
        checker = ConformanceChecker(cfg)
        row = checker._v21_authority_handler()
        assert row.status == "fail"
        assert "allowlist" in row.detail

    def test_conformance_v21_fails_in_strict_mode_too(self):
        from castor.conformance import ConformanceChecker

        cfg = self._base_cfg()
        cfg["authority_handler_enabled"] = True
        checker = ConformanceChecker(cfg, annex_iii_strict=True)
        assert checker._v21_authority_handler().status == "fail"

    def test_conformance_v21_passes_with_allowlist_and_handler(self):
        from castor.conformance import ConformanceChecker

        cfg = self._base_cfg()
        cfg["authority"] = {"trusted_authority_ids": ["eu.aiact.notified-body.001"]}
        row = ConformanceChecker(cfg)._v21_authority_handler()
        assert row.status == "pass"

    def test_conformance_v21_fails_with_allowlist_but_no_handler(self):
        from castor.conformance import ConformanceChecker

        cfg = {"rcan_version": "2.1.0"}
        cfg["authority"] = {"trusted_authority_ids": ["eu.aiact.notified-body.001"]}
        row = ConformanceChecker(cfg)._v21_authority_handler()
        assert row.status == "fail"
        assert "not registered" in row.detail
