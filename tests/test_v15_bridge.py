"""RCAN v1.5 Bridge integration tests.

Tests for v1.5 features in castor.cloud.bridge.CastorBridge:
  - ReplayCache integration (GAP-03)
  - SenderType audit trail (GAP-08)
  - QoS for ESTOP (GAP-11)
  - Offline mode (GAP-06)
  - Training data consent gate (GAP-10)

These tests use mocking to avoid real Firebase connections.

Spec: RCAN v1.5, OpenCastor v2026.3.17.0
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from castor.cloud.bridge import (
    CastorBridge,
    OFFLINE_THRESHOLD_S,
    ESTOP_ACK_DEADLINE_S,
    SAFETY_REPLAY_WINDOW_S,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

MINIMAL_CONFIG = {
    "rrn": "RRN-00000042",
    "metadata": {"name": "TestBot", "ruri": "rcan://test/bot"},
    "firebase_uid": "uid-test-owner",
    "owner": "rrn://test-owner",
}


def _make_bridge() -> CastorBridge:
    bridge = CastorBridge(
        config=MINIMAL_CONFIG,
        firebase_project="test-project",
    )
    # Provide a mock Firestore db and consent manager
    bridge._db = MagicMock()
    bridge._consent = MagicMock()
    bridge._consent.is_authorized.return_value = (True, "ok")
    return bridge


def _cmd_doc(
    scope: str = "chat",
    instruction: str = "say hello",
    issued_at: float | None = None,
    sender_type: str = "human",
    **kwargs: Any,
) -> dict[str, Any]:
    return {
        "scope": scope,
        "instruction": instruction,
        "issued_at": issued_at or time.time(),
        "sender_type": sender_type,
        "status": "pending",
        **kwargs,
    }


def _cmd_ref_mock():
    ref = MagicMock()
    ref.update = MagicMock()
    return ref


# ─────────────────────────────────────────────────────────────────────────────
# 1. ReplayCache integration (GAP-03)
# ─────────────────────────────────────────────────────────────────────────────

class TestReplayCacheIntegration:
    def test_replay_cache_created_on_init(self):
        """Bridge creates replay caches on __init__."""
        bridge = _make_bridge()
        assert bridge._replay_cache is not None
        assert bridge._safety_replay_cache is not None

    def test_safety_cache_10s_window(self):
        """Safety replay cache uses 10s window."""
        bridge = _make_bridge()
        assert bridge._safety_replay_cache.window_s == SAFETY_REPLAY_WINDOW_S
        assert bridge._safety_replay_cache.window_s == 10

    def test_fresh_command_passes_replay_check(self):
        """A fresh command (current timestamp) passes replay check."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(issued_at=time.time())
        result = bridge._check_replay(cmd_id, doc, is_safety=False)
        assert result is True

    def test_replayed_command_rejected(self):
        """Same cmd_id submitted twice fails replay check on second submission."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(issued_at=time.time())
        assert bridge._check_replay(cmd_id, doc, is_safety=False) is True
        assert bridge._check_replay(cmd_id, doc, is_safety=False) is False

    def test_stale_command_rejected(self):
        """A command with a 60s-old timestamp is rejected as stale."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(issued_at=time.time() - 60)
        result = bridge._check_replay(cmd_id, doc, is_safety=False)
        assert result is False

    def test_missing_issued_at_allowed(self):
        """A command without issued_at is allowed (no timestamp to check)."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = {"scope": "chat", "instruction": "hello", "status": "pending"}
        result = bridge._check_replay(cmd_id, doc, is_safety=False)
        assert result is True

    def test_estop_uses_safety_cache(self):
        """ESTOP commands use the 10s safety replay cache."""
        bridge = _make_bridge()
        # A command 11s old should be rejected by safety cache (10s) but not normal (30s)
        cmd_id = str(uuid.uuid4())
        old_doc = _cmd_doc(issued_at=time.time() - 11, scope="safety")
        # Safety cache (10s window) should reject
        result_safety = bridge._check_replay(cmd_id, old_doc, is_safety=True)
        assert result_safety is False

        # Normal cache (30s window) should accept the same age
        cmd_id2 = str(uuid.uuid4())
        result_normal = bridge._check_replay(cmd_id2, old_doc, is_safety=False)
        assert result_normal is True

    def test_replay_rejected_sets_status_in_firestore(self):
        """When a replay is detected, Firestore status is set to replay_rejected."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        issued_at = time.time()
        doc = _cmd_doc(issued_at=issued_at)

        # Mock Firestore references
        cmd_ref = _cmd_ref_mock()
        bridge._commands_ref = MagicMock(return_value=MagicMock())
        bridge._commands_ref().document = MagicMock(return_value=cmd_ref)

        # First execution — should succeed
        bridge._execute_command(cmd_id, doc)

        # Submit the same cmd_id again — should be replay_rejected
        cmd_ref2 = _cmd_ref_mock()
        bridge._commands_ref().document = MagicMock(return_value=cmd_ref2)
        bridge._execute_command(cmd_id, doc)

        # Check that the second call wrote replay_rejected
        update_calls = [str(c) for c in cmd_ref2.update.call_args_list]
        assert any("replay_rejected" in c for c in update_calls), (
            f"Expected replay_rejected in {update_calls}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. SenderType audit trail (GAP-08)
# ─────────────────────────────────────────────────────────────────────────────

class TestSenderTypeAudit:
    def test_sender_type_included_in_complete_entry(self):
        """sender_type from doc is echoed in the Firestore complete update."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(sender_type="cloud_function")

        cmd_ref = _cmd_ref_mock()
        bridge._commands_ref = MagicMock(return_value=MagicMock())
        bridge._commands_ref().document = MagicMock(return_value=cmd_ref)

        with patch.object(bridge, "_dispatch_to_gateway", return_value={"status": "ok"}):
            bridge._execute_command(cmd_id, doc)

        # Find the "complete" update call
        complete_call = None
        for call in cmd_ref.update.call_args_list:
            args = call[0][0] if call[0] else {}
            if args.get("status") == "complete":
                complete_call = args
                break

        assert complete_call is not None, "No 'complete' update found"
        assert complete_call.get("sender_type") == "cloud_function"

    def test_cloud_relay_true_for_cloud_function(self):
        """Commands from cloud_function get cloud_relay=True in the audit entry."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(sender_type="cloud_function")

        cmd_ref = _cmd_ref_mock()
        bridge._commands_ref = MagicMock(return_value=MagicMock())
        bridge._commands_ref().document = MagicMock(return_value=cmd_ref)

        with patch.object(bridge, "_dispatch_to_gateway", return_value={"status": "ok"}):
            bridge._execute_command(cmd_id, doc)

        complete_call = None
        for call in cmd_ref.update.call_args_list:
            args = call[0][0] if call[0] else {}
            if args.get("status") == "complete":
                complete_call = args
                break

        assert complete_call is not None
        assert complete_call.get("cloud_relay") is True

    def test_cloud_relay_not_set_for_human(self):
        """Human-origin commands do NOT get cloud_relay in the audit entry."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(sender_type="human")

        cmd_ref = _cmd_ref_mock()
        bridge._commands_ref = MagicMock(return_value=MagicMock())
        bridge._commands_ref().document = MagicMock(return_value=cmd_ref)

        with patch.object(bridge, "_dispatch_to_gateway", return_value={"status": "ok"}):
            bridge._execute_command(cmd_id, doc)

        complete_call = None
        for call in cmd_ref.update.call_args_list:
            args = call[0][0] if call[0] else {}
            if args.get("status") == "complete":
                complete_call = args
                break

        assert complete_call is not None
        assert complete_call.get("cloud_relay") is not True

    def test_sender_type_in_denied_entry(self):
        """sender_type is included in the denied audit entry."""
        bridge = _make_bridge()
        bridge._consent.is_authorized.return_value = (False, "no access")
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(sender_type="cloud_function")

        cmd_ref = _cmd_ref_mock()
        bridge._commands_ref = MagicMock(return_value=MagicMock())
        bridge._commands_ref().document = MagicMock(return_value=cmd_ref)

        bridge._execute_command(cmd_id, doc)

        denied_call = None
        for call in cmd_ref.update.call_args_list:
            args = call[0][0] if call[0] else {}
            if args.get("status") == "denied":
                denied_call = args
                break

        assert denied_call is not None
        assert denied_call.get("sender_type") == "cloud_function"


# ─────────────────────────────────────────────────────────────────────────────
# 3. QoS for ESTOP (GAP-11)
# ─────────────────────────────────────────────────────────────────────────────

class TestEstopQoS:
    def test_estop_ack_qos_written(self):
        """ESTOP commands get ack_qos='acknowledged' written to Firestore."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(scope="safety", instruction="estop now")

        cmd_ref = _cmd_ref_mock()
        bridge._commands_ref = MagicMock(return_value=MagicMock())
        bridge._commands_ref().document = MagicMock(return_value=cmd_ref)

        with patch.object(bridge, "_dispatch_to_gateway", return_value={"status": "ok"}):
            bridge._execute_command(cmd_id, doc)

        # Find the QoS ACK update
        ack_call = None
        for call in cmd_ref.update.call_args_list:
            args = call[0][0] if call[0] else {}
            if args.get("ack_qos") == "acknowledged":
                ack_call = args
                break

        assert ack_call is not None, "No ack_qos='acknowledged' update found for ESTOP"
        assert "ack_qos_at" in ack_call

    def test_non_estop_no_ack_qos(self):
        """Non-ESTOP commands do NOT get ack_qos field."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(scope="chat", instruction="say hello")

        cmd_ref = _cmd_ref_mock()
        bridge._commands_ref = MagicMock(return_value=MagicMock())
        bridge._commands_ref().document = MagicMock(return_value=cmd_ref)

        with patch.object(bridge, "_dispatch_to_gateway", return_value={"status": "ok"}):
            bridge._execute_command(cmd_id, doc)

        # Should NOT have ack_qos
        for call in cmd_ref.update.call_args_list:
            args = call[0][0] if call[0] else {}
            assert "ack_qos" not in args, f"Unexpected ack_qos in non-ESTOP call: {args}"

    def test_estop_ack_deadline_constant(self):
        """ESTOP_ACK_DEADLINE_S is 2.0 seconds as per spec."""
        assert ESTOP_ACK_DEADLINE_S == 2.0

    def test_estop_not_blocked_by_replay_cache(self):
        """ESTOP with a 9s-old timestamp passes the safety replay cache (10s window)."""
        bridge = _make_bridge()
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(scope="safety", instruction="estop", issued_at=time.time() - 9)
        result = bridge._check_replay(cmd_id, doc, is_safety=True)
        assert result is True, "ESTOP within 10s window should not be rejected"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Offline mode (GAP-06)
# ─────────────────────────────────────────────────────────────────────────────

class TestOfflineMode:
    def test_starts_online(self):
        """Bridge starts in online mode."""
        bridge = _make_bridge()
        assert bridge._offline_mode is False

    def test_enters_offline_after_threshold(self):
        """Bridge enters offline mode after OFFLINE_THRESHOLD_S of no Firestore contact."""
        bridge = _make_bridge()
        # Simulate no Firestore contact for >300s
        bridge._last_firestore_success = time.time() - (OFFLINE_THRESHOLD_S + 1)
        result = bridge._check_offline_mode()
        assert result is True
        assert bridge._offline_mode is True

    def test_online_command_allowed(self):
        """In online mode, all commands are allowed."""
        bridge = _make_bridge()
        assert bridge._is_command_allowed_offline("chat", "say hello") is True
        assert bridge._is_command_allowed_offline("control", "move forward") is True

    def test_offline_non_estop_blocked(self):
        """In offline mode, non-ESTOP commands are blocked."""
        bridge = _make_bridge()
        bridge._offline_mode = True
        assert bridge._is_command_allowed_offline("chat", "say hello") is False
        assert bridge._is_command_allowed_offline("control", "move forward") is False

    def test_offline_estop_always_allowed(self):
        """In offline mode, ESTOP is ALWAYS allowed (Protocol 66 invariant)."""
        bridge = _make_bridge()
        bridge._offline_mode = True
        assert bridge._is_command_allowed_offline("safety", "estop now") is True
        assert bridge._is_command_allowed_offline("safety", "ESTOP emergency") is True

    def test_reconnect_resets_offline_mode(self):
        """_record_firestore_success clears offline mode."""
        bridge = _make_bridge()
        bridge._offline_mode = True
        bridge._offline_since = time.time() - 100
        bridge._last_firestore_success = time.time() - (OFFLINE_THRESHOLD_S + 1)
        bridge._record_firestore_success()
        assert bridge._offline_mode is False

    def test_offline_threshold_is_300s(self):
        """OFFLINE_THRESHOLD_S is 300 (5 minutes) per spec."""
        assert OFFLINE_THRESHOLD_S == 300

    def test_offline_command_rejected_in_execute(self):
        """In offline mode, non-ESTOP commands receive 'denied' status."""
        bridge = _make_bridge()
        bridge._offline_mode = True
        cmd_id = str(uuid.uuid4())
        doc = _cmd_doc(scope="chat", instruction="say something")

        cmd_ref = _cmd_ref_mock()
        bridge._commands_ref = MagicMock(return_value=MagicMock())
        bridge._commands_ref().document = MagicMock(return_value=cmd_ref)

        bridge._execute_command(cmd_id, doc)

        # Find denied update
        denied_call = None
        for call in cmd_ref.update.call_args_list:
            args = call[0][0] if call[0] else {}
            if args.get("status") == "denied" and "offline" in args.get("error", ""):
                denied_call = args
                break

        assert denied_call is not None, "Expected offline_mode denial"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Training data consent (GAP-10)
# ─────────────────────────────────────────────────────────────────────────────

class TestTrainingConsent:
    def test_training_consent_not_required_by_default(self):
        """training_consent_required defaults to False."""
        bridge = _make_bridge()
        assert bridge.training_consent_required is False

    def test_training_consent_config_set(self):
        """training_consent_required is read from config."""
        config = {**MINIMAL_CONFIG, "training_consent_required": True}
        bridge = CastorBridge(config=config, firebase_project="test")
        assert bridge.training_consent_required is True

    def test_training_not_detected_when_not_required(self):
        """When consent not required, _is_training_data_command always False."""
        bridge = _make_bridge()
        bridge.training_consent_required = False
        assert bridge._is_training_data_command("control", "start recording", {}) is False

    def test_training_detected_by_keywords(self):
        """When consent required, training keywords trigger consent check."""
        config = {**MINIMAL_CONFIG, "training_consent_required": True}
        bridge = CastorBridge(config=config, firebase_project="test")
        assert bridge._is_training_data_command("control", "start training session", {}) is True
        assert bridge._is_training_data_command("control", "capture oak frames", {}) is True
        assert bridge._is_training_data_command("control", "record voice_clip", {}) is True

    def test_non_training_command_not_detected(self):
        """Regular commands are not detected as training data commands."""
        config = {**MINIMAL_CONFIG, "training_consent_required": True}
        bridge = CastorBridge(config=config, firebase_project="test")
        assert bridge._is_training_data_command("chat", "say hello", {}) is False
        assert bridge._is_training_data_command("control", "move forward 1m", {}) is False

    def test_training_blocked_without_consent(self):
        """Training data collection is blocked when consent is required but missing."""
        config = {**MINIMAL_CONFIG, "training_consent_required": True}
        bridge = CastorBridge(config=config, firebase_project="test")
        bridge._db = MagicMock()
        # Mock: no consent docs found
        mock_stream = MagicMock()
        mock_stream.stream.return_value = []
        bridge._robot_ref = MagicMock(return_value=MagicMock())
        bridge._robot_ref().collection().where().where().limit.return_value = mock_stream

        result = bridge._check_training_consent("rrn://some-owner", {})
        assert result is False

    def test_no_consent_required_always_passes(self):
        """Without consent requirement, check_training_consent always returns True."""
        bridge = _make_bridge()
        bridge.training_consent_required = False
        result = bridge._check_training_consent("rrn://any-owner", {})
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# 6. Version negotiation — bridge registers with rcan_version=1.5
# ─────────────────────────────────────────────────────────────────────────────

class TestVersionNegotiation:
    def test_bridge_version_is_v15(self):
        """BRIDGE_VERSION is updated for v1.5 release."""
        from castor.cloud.bridge import BRIDGE_VERSION
        major, minor = BRIDGE_VERSION.split(".")[:2]
        assert major == "1"
        assert int(minor) >= 5

    def test_register_includes_rcan_version(self):
        """_register() writes rcan_version='1.5' to Firestore."""
        bridge = _make_bridge()
        robot_ref = MagicMock()
        robot_ref.set = MagicMock()
        bridge._robot_ref = MagicMock(return_value=robot_ref)

        bridge._register()

        call_args = robot_ref.set.call_args[0][0]
        # v1.6: rcan_version bumped to "1.6" in OpenCastor v2026.3.17.1
        assert call_args.get("rcan_version") in ("1.5", "1.6")
