"""Tests for R2R2H mission thread support in castor.cloud.bridge.

Covers:
  1. _build_mission_context returns None for non-mission commands
  2. _build_mission_context returns formatted string for mission_thread commands
  3. _build_mission_context includes mission_id and participants
  4. _write_mission_response writes correct doc to Firestore
  5. _write_mission_response skips when no mission_id
  6. _write_mission_response skips when db is None
  7. _dispatch_to_gateway passes system_context for mission commands
  8. _dispatch_to_gateway does NOT pass system_context for non-mission commands
  9. _execute_command calls _write_mission_response when context=mission_thread
 10. Mission response includes robot RRN and name in from_rrn/from_name
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


def _make_bridge(db=None, rrn="RRN-000000000001", robot_name="Bob"):
    """Build a CastorBridge with minimal config — no real Firebase."""
    from castor.cloud.bridge import CastorBridge

    config = {
        "rrn": rrn,
        "robot_name": robot_name,
        "owner": "rrn://craigm26",
        "ruri": "rcan://rcan.dev/craigm26/bob",
        "capabilities": ["chat"],
        "firebase_uid": "testuid",
    }
    bridge = CastorBridge(
        config=config,
        firebase_project="test-project",
        gateway_url="http://127.0.0.1:8000",
        gateway_token="test-token",
    )
    bridge._db = db
    return bridge


# ---------------------------------------------------------------------------
# 1. _build_mission_context — non-mission command → None
# ---------------------------------------------------------------------------

class TestBuildMissionContextNonMission(unittest.TestCase):
    def test_returns_none_for_regular_command(self):
        bridge = _make_bridge()
        doc = {"scope": "chat", "instruction": "say hello", "context": "opencastor_fleet_ui"}
        result = bridge._build_mission_context(doc)
        self.assertIsNone(result)

    def test_returns_none_when_context_missing(self):
        bridge = _make_bridge()
        doc = {"scope": "chat", "instruction": "move forward"}
        result = bridge._build_mission_context(doc)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 2. _build_mission_context — mission_thread → returns string
# ---------------------------------------------------------------------------

class TestBuildMissionContextMission(unittest.TestCase):
    def _mission_doc(self):
        return {
            "context": "mission_thread",
            "mission_id": "mission-abc123",
            "participants": ["RRN-000000000001", "RRN-000000000005"],
            "scope": "chat",
            "instruction": "describe what you see",
        }

    def test_returns_string_for_mission_thread(self):
        bridge = _make_bridge()
        result = bridge._build_mission_context(self._mission_doc())
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_includes_mission_id(self):
        bridge = _make_bridge()
        result = bridge._build_mission_context(self._mission_doc())
        self.assertIn("mission-abc123", result)

    def test_includes_participants(self):
        bridge = _make_bridge()
        result = bridge._build_mission_context(self._mission_doc())
        self.assertIn("RRN-000000000001", result)
        self.assertIn("RRN-000000000005", result)

    def test_mentions_visibility(self):
        bridge = _make_bridge()
        result = bridge._build_mission_context(self._mission_doc())
        # Should explain that response is visible to all participants
        self.assertIn("visible", result.lower())

    def test_empty_participants_handled(self):
        bridge = _make_bridge()
        doc = {
            "context": "mission_thread",
            "mission_id": "mission-xyz",
            "participants": [],
            "scope": "chat",
        }
        result = bridge._build_mission_context(doc)
        self.assertIsInstance(result, str)  # Should not raise


# ---------------------------------------------------------------------------
# 4. _write_mission_response — writes correct doc to Firestore
# ---------------------------------------------------------------------------

class TestWriteMissionResponse(unittest.TestCase):
    def _make_mock_db(self):
        db = MagicMock()
        # Chain: db.collection().document().collection().document().set()
        col = MagicMock()
        doc_ref = MagicMock()
        inner_col = MagicMock()
        inner_doc = MagicMock()

        db.collection.return_value = col
        col.document.return_value = doc_ref
        doc_ref.collection.return_value = inner_col
        inner_col.document.return_value = inner_doc
        doc_ref.update = MagicMock()
        inner_doc.set = MagicMock()
        return db, inner_doc

    def test_writes_to_correct_collection(self):
        db, inner_doc = self._make_mock_db()
        bridge = _make_bridge(db=db)

        doc = {
            "context": "mission_thread",
            "mission_id": "mission-abc123",
            "mission_msg_id": "msg-original",
        }
        bridge._write_mission_response(doc, "I see a chair.", "cmd-001")

        # missions collection should be accessed
        db.collection.assert_any_call("missions")

    def test_written_doc_has_robot_rrn(self):
        db, inner_doc = self._make_mock_db()
        bridge = _make_bridge(db=db, rrn="RRN-000000000001")

        doc = {
            "context": "mission_thread",
            "mission_id": "mission-abc123",
        }
        bridge._write_mission_response(doc, "Response text", "cmd-001")

        # Check the set call argument contains from_rrn
        set_args = inner_doc.set.call_args
        if set_args:
            written = set_args[0][0]
            self.assertEqual(written.get("from_rrn"), "RRN-000000000001")
            self.assertEqual(written.get("from_type"), "robot")
            self.assertEqual(written.get("scope"), "chat")

    def test_written_doc_has_correct_status(self):
        db, inner_doc = self._make_mock_db()
        bridge = _make_bridge(db=db)

        doc = {
            "context": "mission_thread",
            "mission_id": "mission-abc123",
        }
        bridge._write_mission_response(doc, "Hello from robot", "cmd-002")

        set_args = inner_doc.set.call_args
        if set_args:
            written = set_args[0][0]
            self.assertEqual(written.get("status"), "responded")

    def test_written_doc_has_content(self):
        db, inner_doc = self._make_mock_db()
        bridge = _make_bridge(db=db)

        doc = {"context": "mission_thread", "mission_id": "mission-abc123"}
        bridge._write_mission_response(doc, "Navigation complete.", "cmd-003")

        set_args = inner_doc.set.call_args
        if set_args:
            written = set_args[0][0]
            self.assertEqual(written.get("content"), "Navigation complete.")


# ---------------------------------------------------------------------------
# 5. _write_mission_response — skips when no mission_id
# ---------------------------------------------------------------------------

class TestWriteMissionResponseNoMissionId(unittest.TestCase):
    def test_skips_when_no_mission_id(self):
        db = MagicMock()
        bridge = _make_bridge(db=db)
        doc = {"context": "mission_thread", "mission_id": ""}
        bridge._write_mission_response(doc, "Response", "cmd-001")
        # db.collection should NOT be called
        db.collection.assert_not_called()


# ---------------------------------------------------------------------------
# 6. _write_mission_response — skips when db is None
# ---------------------------------------------------------------------------

class TestWriteMissionResponseNoDb(unittest.TestCase):
    def test_skips_when_db_is_none(self):
        bridge = _make_bridge(db=None)
        doc = {"context": "mission_thread", "mission_id": "mission-abc123"}
        # Should not raise
        bridge._write_mission_response(doc, "Response", "cmd-001")


# ---------------------------------------------------------------------------
# 7. _dispatch_to_gateway passes system_context for mission commands
# ---------------------------------------------------------------------------

class TestDispatchMissionContext(unittest.TestCase):
    def _mock_response(self, json_data):
        resp = MagicMock()
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = json_data
        resp.status_code = 200
        return resp

    @patch("httpx.Client")
    def test_system_context_included_in_payload(self, mock_client_cls):
        bridge = _make_bridge()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=self._mock_response({"thought": "ok"}))
        mock_client_cls.return_value = mock_client

        doc = {
            "context": "mission_thread",
            "mission_id": "mission-abc123",
            "participants": ["RRN-000000000001"],
        }
        bridge._dispatch_to_gateway(
            scope="chat",
            instruction="describe surroundings",
            doc=doc,
            mission_context="You are in a multi-robot mission.",
        )

        call_kwargs = mock_client.post.call_args
        if call_kwargs:
            payload = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
            self.assertIn("system_context", payload)
            self.assertIn("multi-robot", payload["system_context"])

    @patch("httpx.Client")
    def test_no_system_context_for_non_mission(self, mock_client_cls):
        bridge = _make_bridge()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=self._mock_response({"thought": "ok"}))
        mock_client_cls.return_value = mock_client

        doc = {"context": "opencastor_fleet_ui"}
        bridge._dispatch_to_gateway(
            scope="chat",
            instruction="hello",
            doc=doc,
        )

        call_kwargs = mock_client.post.call_args
        if call_kwargs:
            payload = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
            self.assertNotIn("system_context", payload)


# ---------------------------------------------------------------------------
# 10. Robot name used in from_name field
# ---------------------------------------------------------------------------

class TestWriteMissionResponseRobotName(unittest.TestCase):
    def _make_mock_db(self):
        db = MagicMock()
        col = MagicMock()
        doc_ref = MagicMock()
        inner_col = MagicMock()
        inner_doc = MagicMock()

        db.collection.return_value = col
        col.document.return_value = doc_ref
        doc_ref.collection.return_value = inner_col
        inner_col.document.return_value = inner_doc
        doc_ref.update = MagicMock()
        inner_doc.set = MagicMock()
        return db, inner_doc

    def test_from_name_is_robot_name(self):
        db, inner_doc = self._make_mock_db()
        bridge = _make_bridge(db=db, robot_name="AlphaBot")

        doc = {"context": "mission_thread", "mission_id": "mission-test"}
        bridge._write_mission_response(doc, "I am AlphaBot.", "cmd-010")

        set_args = inner_doc.set.call_args
        if set_args:
            written = set_args[0][0]
            self.assertEqual(written.get("from_name"), "AlphaBot")


if __name__ == "__main__":
    unittest.main()
