"""Tests for pick-and-place UX: intent detection, task doc helpers, dispatch."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from castor.cloud.bridge import CastorBridge, _detect_pick_place_intent

MINIMAL_CONFIG = {
    "rrn": "RRN-00000042",
    "metadata": {"name": "TestBot", "ruri": "rcan://test/bot"},
    "firebase_uid": "uid-test-owner",
    "owner": "rrn://test-owner",
    "task_execution": "ask",
}


def _make_bridge(task_execution: str = "ask") -> CastorBridge:
    cfg = {**MINIMAL_CONFIG, "task_execution": task_execution}
    bridge = CastorBridge(config=cfg, firebase_project="test-project")
    bridge._db = MagicMock()
    bridge._consent = MagicMock()
    bridge._consent.is_authorized.return_value = (True, "ok")
    return bridge


# ── 1. Intent detector ────────────────────────────────────────────────────────

class TestDetectPickPlaceIntent:
    def test_basic_pick_into(self):
        result = _detect_pick_place_intent("pick the red lego brick into the bowl")
        assert result == ("the red lego brick", "the bowl")

    def test_grab_and_place(self):
        result = _detect_pick_place_intent("grab lego and place it into the container")
        assert result == ("lego", "the container")

    def test_take_to(self):
        result = _detect_pick_place_intent("take the cube to the tray")
        assert result == ("the cube", "the tray")

    def test_get_in(self):
        result = _detect_pick_place_intent("get the blue block in the bin")
        assert result == ("the blue block", "the bin")

    def test_no_match_move(self):
        assert _detect_pick_place_intent("move forward") is None

    def test_no_match_chat(self):
        assert _detect_pick_place_intent("what do you see") is None

    def test_no_match_hi(self):
        assert _detect_pick_place_intent("hi") is None

    def test_no_match_status(self):
        assert _detect_pick_place_intent("STATUS") is None


# ── 2. Firestore task doc helpers ─────────────────────────────────────────────

class TestTaskDocHelpers:
    def test_write_task_doc_creates_document(self):
        bridge = _make_bridge()
        task_ref_mock = MagicMock()
        bridge._robot_ref().collection.return_value.document.return_value = task_ref_mock

        bridge._write_task_doc(
            task_id="abc123",
            target="red lego",
            destination="bowl",
            status="pending_confirmation",
        )

        task_ref_mock.set.assert_called_once()
        call_args = task_ref_mock.set.call_args[0][0]
        assert call_args["task_id"] == "abc123"
        assert call_args["target"] == "red lego"
        assert call_args["destination"] == "bowl"
        assert call_args["status"] == "pending_confirmation"
        assert call_args["type"] == "pick_place"
        assert call_args["confirmed"] is False

    def test_write_task_doc_swallows_firestore_error(self):
        bridge = _make_bridge()
        bridge._robot_ref().collection.return_value.document.return_value.set.side_effect = Exception("timeout")
        # Must not raise
        bridge._write_task_doc("abc", "obj", "dest", "running")

    def test_update_task_doc_does_partial_update(self):
        bridge = _make_bridge()
        task_ref_mock = MagicMock()
        bridge._robot_ref().collection.return_value.document.return_value = task_ref_mock

        bridge._update_task_doc("abc123", {"phase": "APPROACH", "status": "running"})

        task_ref_mock.update.assert_called_once()
        call_args = task_ref_mock.update.call_args[0][0]
        assert call_args["phase"] == "APPROACH"
        assert call_args["status"] == "running"
        assert "updated_at" in call_args

    def test_update_task_doc_swallows_error(self):
        bridge = _make_bridge()
        bridge._robot_ref().collection.return_value.document.return_value.update.side_effect = Exception("boom")
        bridge._update_task_doc("abc", {"status": "failed"})


# ── 3. Wait for confirmation ───────────────────────────────────────────────────

class TestWaitForConfirmation:
    def test_returns_true_when_confirmed_immediately(self):
        bridge = _make_bridge()

        # Simulate: Firestore listener fires immediately with confirmed=True
        def fake_on_snapshot(callback, error_callback=None, snapshot_listener_info=None):
            snap = MagicMock()
            snap.exists = True
            snap.to_dict.return_value = {"confirmed": True, "status": "pending_confirmation"}
            callback([snap], None, None)
            return MagicMock()  # listener handle

        bridge._tasks_ref().document.return_value.on_snapshot = fake_on_snapshot

        result = bridge._wait_for_confirmation("task-abc", timeout_s=5)
        assert result is True

    def test_returns_false_on_timeout(self):
        bridge = _make_bridge()

        # Simulate: listener never fires confirmed
        def fake_on_snapshot(callback, error_callback=None, snapshot_listener_info=None):
            return MagicMock()  # handle, but never fires

        bridge._tasks_ref().document.return_value.on_snapshot = fake_on_snapshot

        result = bridge._wait_for_confirmation("task-xyz", timeout_s=0.1)
        assert result is False

    def test_returns_false_when_confirmed_false(self):
        bridge = _make_bridge()

        def fake_on_snapshot(callback, error_callback=None, snapshot_listener_info=None):
            snap = MagicMock()
            snap.exists = True
            snap.to_dict.return_value = {"confirmed": False}
            callback([snap], None, None)
            return MagicMock()

        bridge._tasks_ref().document.return_value.on_snapshot = fake_on_snapshot

        result = bridge._wait_for_confirmation("task-abc", timeout_s=0.2)
        assert result is False


# ── 4. _dispatch_pick_place ───────────────────────────────────────────────────

class TestDispatchPickPlace:
    def test_routes_pick_intent_to_pick_place_endpoint(self):
        bridge = _make_bridge(task_execution="automatic")

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"status": "complete", "log": []}
            mock_resp.headers = {"content-type": "application/json"}
            mock_resp.raise_for_status = MagicMock()
            mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

            doc = {"scope": "chat", "instruction": "pick lego into bowl",
                   "issued_at": time.time(), "sender_type": "human"}
            result = bridge._dispatch_to_gateway("chat", "pick lego into bowl", doc)

        # Should have called /api/arm/pick_place, not /api/command
        call_args = mock_client_cls.return_value.__enter__.return_value.post.call_args
        assert "/api/arm/pick_place" in call_args[0][0]
        body = call_args[1]["json"]
        assert body["target"] == "lego"
        assert body["destination"] == "bowl"

    def test_non_pick_intent_routes_to_api_command(self):
        bridge = _make_bridge(task_execution="automatic")

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"raw_text": "hello"}
            mock_resp.headers = {"content-type": "application/json"}
            mock_resp.raise_for_status = MagicMock()
            mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

            doc = {"scope": "chat", "instruction": "what do you see",
                   "issued_at": time.time(), "sender_type": "human"}
            bridge._dispatch_to_gateway("chat", "what do you see", doc)

        call_args = mock_client_cls.return_value.__enter__.return_value.post.call_args
        assert "/api/command" in call_args[0][0]

    def test_ask_mode_cancels_on_timeout(self):
        bridge = _make_bridge(task_execution="ask")

        # _wait_for_confirmation returns False (timeout)
        with patch.object(bridge, "_wait_for_confirmation", return_value=False):
            with patch.object(bridge, "_write_task_doc"):
                with patch.object(bridge, "_update_task_doc") as mock_update:
                    doc = {"scope": "chat", "instruction": "pick lego into bowl",
                           "issued_at": time.time(), "sender_type": "human"}
                    result = bridge._dispatch_to_gateway("chat", "pick lego into bowl", doc)

        assert result.get("status") == "cancelled"
        # Must update task doc to cancelled
        update_calls = [c[0][1] for c in mock_update.call_args_list]
        assert any(d.get("status") == "cancelled" for d in update_calls)


class TestDispatchPickPlaceFirestoreRead:
    def test_reads_task_execution_from_firestore(self):
        bridge = _make_bridge(task_execution="ask")  # config says ask

        # Firestore robot doc says automatic
        robot_doc = MagicMock()
        robot_doc.exists = True
        robot_doc.to_dict.return_value = {"task_execution": "automatic"}
        bridge._robot_ref().get.return_value = robot_doc

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"status": "complete", "log": []}
            mock_resp.headers = {"content-type": "application/json"}
            mock_resp.raise_for_status = MagicMock()
            mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

            # With automatic mode (from Firestore), should NOT call _wait_for_confirmation
            with patch.object(bridge, "_wait_for_confirmation") as mock_wait:
                bridge._dispatch_pick_place("lego", "bowl", {})
                mock_wait.assert_not_called()


# ── 5. Gateway phase writes ───────────────────────────────────────────────────

from castor.api import _write_task_phase


class TestGatewayPhaseWrites:
    def test_write_task_phase_calls_firestore_update(self):
        mock_db = MagicMock()
        task_ref = MagicMock()
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.document.return_value = task_ref

        _write_task_phase(mock_db, "RRN-001", "task-abc", "APPROACH", "running")

        task_ref.update.assert_called_once()
        call_args = task_ref.update.call_args[0][0]
        assert call_args["phase"] == "APPROACH"
        assert call_args["status"] == "running"

    def test_write_task_phase_swallows_error(self):
        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.document.return_value \
            .update.side_effect = Exception("network timeout")
        # Must not raise
        _write_task_phase(mock_db, "RRN-001", "task-abc", "GRASP", "running")

    def test_write_task_phase_no_op_when_no_task_id(self):
        mock_db = MagicMock()
        _write_task_phase(mock_db, "RRN-001", None, "APPROACH", "running")
        mock_db.collection.assert_not_called()
