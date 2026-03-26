"""
Tests for castor/auth/m2m_trusted.py — RCAN v2.1 M2M_TRUSTED authentication.
"""

from __future__ import annotations

import base64
import json
import time
import unittest

from castor.auth.m2m_trusted import (
    M2MTrustedAuthError,
    M2MTrustedSession,
    RevocationCache,
    _token_hash,
    validate_m2m_trusted_message,
    register_session,
    terminate_session,
    get_active_sessions,
    has_active_m2m_trusted_sessions,
    _active_sessions,
)


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "EdDSA", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{body}.fakesig"


VALID_PAYLOAD = {
    "sub":         "orchestrator:fleet-brain",
    "iss":         "rrf.rcan.dev",
    "rcan_scopes": ["fleet.trusted"],
    "fleet_rrns":  ["RRN-000000000001", "RRN-000000000005"],
    "exp":         int(time.time()) + 86400,
    "rrf_sig":     "fakesig123",
}


class TestValidateM2MTrustedMessage(unittest.TestCase):

    def setUp(self):
        _active_sessions.clear()

    def test_valid_token_accepted(self):
        token = _make_jwt(VALID_PAYLOAD)
        session = validate_m2m_trusted_message(token, "RRN-000000000001")
        self.assertEqual(session.orchestrator_id, "orchestrator:fleet-brain")
        self.assertIn("RRN-000000000001", session.fleet_rrns)
        self.assertFalse(session.is_expired)

    def test_wrong_issuer_rejected(self):
        p = {**VALID_PAYLOAD, "iss": "evil.attacker.com"}
        token = _make_jwt(p)
        with self.assertRaises(M2MTrustedAuthError) as ctx:
            validate_m2m_trusted_message(token, "RRN-000000000001")
        self.assertIn("M2M_INVALID_ISSUER", ctx.exception.code)

    def test_expired_token_rejected(self):
        p = {**VALID_PAYLOAD, "exp": int(time.time()) - 1}
        token = _make_jwt(p)
        with self.assertRaises(M2MTrustedAuthError) as ctx:
            validate_m2m_trusted_message(token, "RRN-000000000001")
        self.assertIn("M2M_TOKEN_EXPIRED", ctx.exception.code)

    def test_missing_fleet_trusted_scope_rejected(self):
        p = {**VALID_PAYLOAD, "rcan_scopes": ["status"]}
        token = _make_jwt(p)
        with self.assertRaises(M2MTrustedAuthError) as ctx:
            validate_m2m_trusted_message(token, "RRN-000000000001")
        self.assertIn("M2M_MISSING_SCOPE", ctx.exception.code)

    def test_target_rrn_not_in_fleet_rejected(self):
        token = _make_jwt(VALID_PAYLOAD)
        with self.assertRaises(M2MTrustedAuthError) as ctx:
            validate_m2m_trusted_message(token, "RRN-000000000099")
        self.assertIn("M2M_NOT_IN_FLEET", ctx.exception.code)

    def test_missing_rrf_sig_rejected(self):
        p = {**VALID_PAYLOAD}
        del p["rrf_sig"]
        token = _make_jwt(p)
        with self.assertRaises(M2MTrustedAuthError) as ctx:
            validate_m2m_trusted_message(token, "RRN-000000000001")
        self.assertIn("M2M_MISSING_SIG", ctx.exception.code)

    def test_revoked_orchestrator_rejected(self):
        cache = RevocationCache()
        cache.update(["orchestrator:fleet-brain"], [])
        token = _make_jwt(VALID_PAYLOAD)
        with self.assertRaises(M2MTrustedAuthError) as ctx:
            validate_m2m_trusted_message(token, "RRN-000000000001", revocation_cache=cache)
        self.assertIn("M2M_REVOKED", ctx.exception.code)

    def test_non_revoked_orchestrator_passes(self):
        cache = RevocationCache()
        cache.update(["orchestrator:other-brain"], [])
        token = _make_jwt(VALID_PAYLOAD)
        session = validate_m2m_trusted_message(token, "RRN-000000000001", revocation_cache=cache)
        self.assertEqual(session.orchestrator_id, "orchestrator:fleet-brain")

    def test_malformed_token_rejected(self):
        with self.assertRaises(M2MTrustedAuthError):
            validate_m2m_trusted_message("not.a.valid.jwt.at.all", "RRN-000000000001")


class TestSessionLifecycle(unittest.TestCase):

    def setUp(self):
        _active_sessions.clear()

    def test_register_and_retrieve(self):
        session = M2MTrustedSession(
            orchestrator_id="orch:test",
            fleet_rrns=["RRN-000000000001"],
            exp=int(time.time()) + 3600,
            token_hash="abc123",
        )
        register_session(session)
        sessions = get_active_sessions()
        self.assertIn("orch:test", sessions)

    def test_terminate_session(self):
        session = M2MTrustedSession(
            orchestrator_id="orch:test",
            fleet_rrns=["RRN-000000000001"],
            exp=int(time.time()) + 3600,
            token_hash="abc123",
        )
        register_session(session)
        terminate_session("orch:test", reason="test")
        self.assertNotIn("orch:test", get_active_sessions())

    def test_has_active_sessions_true(self):
        session = M2MTrustedSession(
            orchestrator_id="orch:active",
            fleet_rrns=["RRN-000000000001"],
            exp=int(time.time()) + 3600,
            token_hash="abc",
        )
        register_session(session)
        self.assertTrue(has_active_m2m_trusted_sessions())

    def test_has_active_sessions_false_when_empty(self):
        self.assertFalse(has_active_m2m_trusted_sessions())

    def test_expired_sessions_purged_on_check(self):
        session = M2MTrustedSession(
            orchestrator_id="orch:expired",
            fleet_rrns=["RRN-000000000001"],
            exp=int(time.time()) - 1,  # already expired
            token_hash="abc",
        )
        _active_sessions["orch:expired"] = session
        # has_active_m2m_trusted_sessions should purge expired and return False
        result = has_active_m2m_trusted_sessions()
        self.assertFalse(result)
        self.assertNotIn("orch:expired", _active_sessions)


class TestRevocationCache(unittest.TestCase):

    def test_empty_cache_not_revoked(self):
        cache = RevocationCache()
        self.assertFalse(cache.is_revoked("orch:any"))

    def test_revoked_orchestrator_detected(self):
        cache = RevocationCache()
        cache.update(["orch:bad"], [])
        self.assertTrue(cache.is_revoked("orch:bad"))

    def test_non_revoked_orchestrator_passes(self):
        cache = RevocationCache()
        cache.update(["orch:bad"], [])
        self.assertFalse(cache.is_revoked("orch:good"))

    def test_revoked_jti_detected(self):
        cache = RevocationCache()
        cache.update([], ["jti-abc123"])
        self.assertTrue(cache.is_revoked("orch:any", jti="jti-abc123"))

    def test_cache_staleness(self):
        cache = RevocationCache()
        # Brand new cache is stale (never fetched)
        self.assertTrue(cache.is_stale)
        cache.update([], [])
        # Just updated — not stale
        self.assertFalse(cache.is_stale)


if __name__ == "__main__":
    unittest.main()
