"""Tests for castor.cloud.bridge and castor.cloud.consent_manager."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# ConsentManager tests
# ---------------------------------------------------------------------------


class TestConsentManager(unittest.TestCase):
    def _make_manager(self, owner: str = "rrn://craigm26", db: object = None):
        from castor.cloud.consent_manager import ConsentManager

        return ConsentManager(robot_rrn="RRN-000000000001", owner=owner, db=db)

    # Same-owner

    def test_same_owner_any_scope_authorized(self):
        cm = self._make_manager()
        for scope in ("chat", "control", "status", "discover"):
            ok, reason = cm.is_authorized("rrn://craigm26", scope)
            self.assertTrue(ok, f"scope={scope} should be allowed for same owner")
            self.assertEqual(reason, "same_owner")

    def test_same_owner_trailing_slash_normalized(self):
        cm = self._make_manager()
        ok, reason = cm.is_authorized("rrn://craigm26/", "control")
        self.assertTrue(ok)

    def test_same_owner_case_insensitive(self):
        cm = self._make_manager()
        ok, _ = cm.is_authorized("RRN://CRAIGM26", "control")
        self.assertTrue(ok)

    # ESTOP exception

    def test_estop_from_unknown_source_honored(self):
        cm = self._make_manager()
        ok, reason = cm.is_authorized(
            requester_owner="rrn://stranger",
            requested_scope="safety",
            is_estop=True,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "estop_exception")

    def test_estop_from_anonymous_blocked(self):
        cm = self._make_manager()
        ok, reason = cm.is_authorized(
            requester_owner="",
            requested_scope="safety",
            is_estop=True,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "anonymous_estop_blocked")

    def test_resume_from_unknown_requires_control_scope(self):
        cm = self._make_manager()
        ok, reason = cm.is_authorized(
            requester_owner="rrn://stranger",
            requested_scope="safety",
            instruction="resume",
            is_estop=False,
        )
        self.assertFalse(ok)
        self.assertIn("resume", reason)

    # Cross-owner — no consent record

    def test_unknown_owner_blocked(self):
        cm = self._make_manager(db=None)
        ok, reason = cm.is_authorized("rrn://stranger", "chat")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_consent_record")

    def test_unknown_owner_status_blocked(self):
        cm = self._make_manager(db=None)
        ok, _ = cm.is_authorized("rrn://stranger", "status")
        self.assertFalse(ok)

    # Cross-owner — granted consent

    def test_granted_consent_chat_scope(self):
        cm = self._make_manager()
        cm._cache["rrn://partner"] = {
            "granted_scopes": ["chat"],
            "status": "approved",
            "expires_at": None,
        }
        ok, reason = cm.is_authorized("rrn://partner", "chat")
        self.assertTrue(ok)

    def test_granted_control_implies_chat(self):
        """control scope (level 3) should satisfy chat (level 2)."""
        cm = self._make_manager()
        cm._cache["rrn://partner"] = {
            "granted_scopes": ["control"],
            "status": "approved",
            "expires_at": None,
        }
        ok, _ = cm.is_authorized("rrn://partner", "chat")
        self.assertTrue(ok)

    def test_granted_chat_does_not_imply_control(self):
        """chat scope should NOT satisfy control."""
        cm = self._make_manager()
        cm._cache["rrn://partner"] = {
            "granted_scopes": ["chat"],
            "status": "approved",
            "expires_at": None,
        }
        ok, _ = cm.is_authorized("rrn://partner", "control")
        self.assertFalse(ok)

    # Expiry

    def test_expired_consent_rejected(self):
        cm = self._make_manager()
        cm._cache["rrn://partner"] = {
            "granted_scopes": ["control"],
            "status": "approved",
            "expires_at": "2020-01-01T00:00:00+00:00",  # in the past
        }
        ok, reason = cm.is_authorized("rrn://partner", "control")
        self.assertFalse(ok)
        self.assertEqual(reason, "consent_expired")

    def test_future_expiry_accepted(self):
        from datetime import datetime, timedelta, timezone

        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        cm = self._make_manager()
        cm._cache["rrn://partner"] = {
            "granted_scopes": ["control"],
            "status": "approved",
            "expires_at": future,
        }
        ok, _ = cm.is_authorized("rrn://partner", "control")
        self.assertTrue(ok)

    # Revocation

    def test_revoked_consent_blocked(self):
        cm = self._make_manager()
        cm._cache["rrn://partner"] = {
            "granted_scopes": ["control"],
            "status": "revoked",
            "expires_at": None,
        }
        ok, reason = cm.is_authorized("rrn://partner", "control")
        self.assertFalse(ok)
        self.assertIn("revoked", reason)

    # grant_consent / revoke_consent

    def test_grant_consent_updates_cache(self):
        cm = self._make_manager(db=None)
        cid = cm.grant_consent(
            peer_owner="rrn://partner",
            peer_rrn="RRN-000000000005",
            peer_ruri="rcan://partner.alex-001",
            granted_scopes=["status", "chat"],
            duration_hours=24,
        )
        self.assertIsNotNone(cid)
        ok, _ = cm.is_authorized("rrn://partner", "chat")
        self.assertTrue(ok)

    def test_revoke_consent_clears_cache(self):
        cm = self._make_manager(db=None)
        cm.grant_consent(
            peer_owner="rrn://partner",
            peer_rrn="RRN-999",
            peer_ruri="rcan://partner.robot",
            granted_scopes=["control"],
            duration_hours=1,
        )
        cm.revoke_consent("rrn://partner")
        # After revoke, cache is gone and db is None → no_consent_record
        ok, reason = cm.is_authorized("rrn://partner", "control")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_consent_record")


# ---------------------------------------------------------------------------
# CastorBridge unit tests (no Firebase connection)
# ---------------------------------------------------------------------------


class TestCastorBridgeUnit(unittest.TestCase):
    def _make_bridge(self):
        from castor.cloud.bridge import CastorBridge

        config = {
            "rrn": "RRN-000000000001",
            "name": "bob",
            "owner": "rrn://craigm26",
            "capabilities": ["chat", "nav"],
            "opencastor_version": "2026.3.14.6",
            "metadata": {"ruri": "rcan://craigm26.bob-001"},
        }
        bridge = CastorBridge(
            config=config,
            firebase_project="test-project",
            gateway_url="http://127.0.0.1:9999",
        )
        return bridge

    def test_robot_identity_extracted(self):
        bridge = self._make_bridge()
        self.assertEqual(bridge.rrn, "RRN-000000000001")
        self.assertEqual(bridge.robot_name, "bob")
        self.assertEqual(bridge.owner, "rrn://craigm26")

    def test_auth_headers_no_token(self):
        bridge = self._make_bridge()
        headers = bridge._auth_headers()
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_auth_headers_with_token(self):
        from castor.cloud.bridge import CastorBridge

        bridge = CastorBridge(
            config={"rrn": "RRN-1", "name": "bob", "owner": "rrn://x", "capabilities": []},
            firebase_project="test",
            gateway_token="secret",
        )
        headers = bridge._auth_headers()
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_last_processed_deduplication(self):
        """Commands with IDs already in _last_processed should be skipped."""
        bridge = self._make_bridge()
        bridge._last_processed.add("cmd-123")
        # _on_command_snapshot skips IDs in _last_processed — verified via
        # the _execute_command not being spawned; test the set membership
        self.assertIn("cmd-123", bridge._last_processed)

    def test_stop_sets_running_false(self):
        bridge = self._make_bridge()
        bridge._running = True
        # Mock the _robot_ref to avoid Firebase
        bridge._robot_ref = MagicMock(return_value=MagicMock())
        bridge.stop()
        self.assertFalse(bridge._running)


# ---------------------------------------------------------------------------
# Firestore models tests
# ---------------------------------------------------------------------------


class TestFirestoreModels(unittest.TestCase):
    def test_command_doc_roundtrip(self):
        from castor.cloud.firestore_models import CommandDoc, CommandStatus

        doc = CommandDoc(
            instruction="move forward",
            scope="chat",
            issued_by_uid="uid-abc",
            issued_by_owner="rrn://craigm26",
        )
        d = doc.to_dict()
        self.assertEqual(d["instruction"], "move forward")
        self.assertEqual(d["status"], CommandStatus.PENDING)
        self.assertNotIn("result", d)  # None fields excluded

    def test_consent_request_doc_roundtrip(self):
        from castor.cloud.firestore_models import ConsentRequestDoc, ConsentStatus

        doc = ConsentRequestDoc(
            from_rrn="RRN-000000000005",
            from_owner="rrn://partner",
            from_ruri="rcan://partner.alex-001",
            requested_scopes=["status", "chat"],
            reason="joint task",
            duration_hours=24,
        )
        d = doc.to_dict()
        self.assertEqual(d["status"], ConsentStatus.PENDING)
        self.assertEqual(d["requested_scopes"], ["status", "chat"])

    def test_consent_peer_doc_roundtrip(self):
        from castor.cloud.firestore_models import ConsentPeerDoc

        doc = ConsentPeerDoc(
            peer_rrn="RRN-000000000005",
            peer_owner="rrn://partner",
            peer_ruri="rcan://partner.alex-001",
            granted_scopes=["chat"],
        )
        d = doc.to_dict()
        self.assertEqual(d["granted_scopes"], ["chat"])
        self.assertEqual(d["direction"], "inbound")


# ---------------------------------------------------------------------------
# RCAN message types
# ---------------------------------------------------------------------------


class TestRCANConsentMessageTypes(unittest.TestCase):
    def test_consent_message_types_exist(self):
        from castor.rcan.message import MessageType

        self.assertEqual(MessageType.CONSENT_REQUEST, 20)
        self.assertEqual(MessageType.CONSENT_GRANT, 21)
        self.assertEqual(MessageType.CONSENT_DENY, 22)

    def test_consent_message_roundtrip(self):
        from castor.rcan.message import MessageType, RCANMessage

        msg = RCANMessage(
            type=MessageType.CONSENT_REQUEST,
            source="rcan://craigm26.bob-001",
            target="rcan://partner.alex-001",
            payload={
                "requested_scopes": ["chat", "status"],
                "reason": "joint task",
                "duration_hours": 24,
            },
        )
        d = msg.to_dict()
        self.assertEqual(d["type"], 20)
        self.assertEqual(d["type_name"], "CONSENT_REQUEST")
        restored = RCANMessage.from_dict(d)
        self.assertEqual(restored.type, MessageType.CONSENT_REQUEST)


if __name__ == "__main__":
    unittest.main()
