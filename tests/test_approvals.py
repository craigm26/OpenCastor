"""Tests for castor.approvals -- safety gate for dangerous hardware commands."""

from unittest.mock import patch

from castor.approvals import ApprovalGate


# =====================================================================
# ApprovalGate.__init__
# =====================================================================
class TestApprovalGateInit:
    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_default_config(self, mock_exists):
        gate = ApprovalGate({})
        assert gate.max_safe_linear == 0.5
        assert gate.max_safe_angular == 1.5
        assert gate.require_approval is False

    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_custom_thresholds(self, mock_exists):
        config = {
            "physics": {
                "max_speed_ms": 1.0,
                "max_angular_speed": 3.0,
            },
            "agent": {
                "require_approval": True,
            },
        }
        gate = ApprovalGate(config)
        assert gate.max_safe_linear == 1.0
        assert gate.max_safe_angular == 3.0
        assert gate.require_approval is True


# =====================================================================
# ApprovalGate.check -- safe action
# =====================================================================
class TestApprovalGateCheckSafe:
    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_safe_action_passes_through(self, mock_exists):
        config = {
            "physics": {"max_speed_ms": 1.0, "max_angular_speed": 2.0},
            "agent": {"require_approval": True},
        }
        gate = ApprovalGate(config)
        action = {"type": "move", "linear": 0.5, "angular": 0.3}
        result = gate.check(action)
        assert result == action

    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_non_move_action_passes_through(self, mock_exists):
        config = {"agent": {"require_approval": True}}
        gate = ApprovalGate(config)
        action = {"type": "stop"}
        result = gate.check(action)
        assert result == action


# =====================================================================
# ApprovalGate.check -- dangerous action
# =====================================================================
class TestApprovalGateCheckDangerous:
    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_high_linear_speed_returns_pending(self, mock_exists):
        config = {
            "physics": {"max_speed_ms": 0.5},
            "agent": {"require_approval": True},
        }
        gate = ApprovalGate(config)
        action = {"type": "move", "linear": 2.0, "angular": 0.0}
        result = gate.check(action)
        assert result["status"] == "pending"
        assert "approval_id" in result
        assert len(result["reasons"]) > 0

    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_high_angular_speed_returns_pending(self, mock_exists):
        config = {
            "physics": {"max_angular_speed": 1.0},
            "agent": {"require_approval": True},
        }
        gate = ApprovalGate(config)
        action = {"type": "move", "linear": 0.0, "angular": 5.0}
        result = gate.check(action)
        assert result["status"] == "pending"
        assert "approval_id" in result


# =====================================================================
# ApprovalGate.approve
# =====================================================================
class TestApprovalGateApprove:
    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_approve_returns_queued_action(self, mock_exists):
        config = {
            "physics": {"max_speed_ms": 0.5},
            "agent": {"require_approval": True},
        }
        gate = ApprovalGate(config)
        action = {"type": "move", "linear": 2.0, "angular": 0.0}
        pending = gate.check(action)
        approval_id = pending["approval_id"]

        result = gate.approve(approval_id)
        assert result == action

    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_approve_nonexistent_returns_none(self, mock_exists):
        gate = ApprovalGate({"agent": {"require_approval": True}})
        assert gate.approve(999) is None


# =====================================================================
# ApprovalGate.deny
# =====================================================================
class TestApprovalGateDeny:
    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_deny_removes_queued_action(self, mock_exists):
        config = {
            "physics": {"max_speed_ms": 0.5},
            "agent": {"require_approval": True},
        }
        gate = ApprovalGate(config)
        action = {"type": "move", "linear": 2.0, "angular": 0.0}
        pending = gate.check(action)
        approval_id = pending["approval_id"]

        result = gate.deny(approval_id)
        assert result is True
        # Should no longer be in pending list
        assert len(gate.list_pending()) == 0

    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_deny_nonexistent_returns_false(self, mock_exists):
        gate = ApprovalGate({"agent": {"require_approval": True}})
        assert gate.deny(999) is False


# =====================================================================
# ApprovalGate.list_pending
# =====================================================================
class TestApprovalGateListPending:
    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_list_pending_returns_only_pending_items(self, mock_exists):
        config = {
            "physics": {"max_speed_ms": 0.5},
            "agent": {"require_approval": True},
        }
        gate = ApprovalGate(config)

        # Queue two actions
        action1 = {"type": "move", "linear": 2.0, "angular": 0.0}
        action2 = {"type": "move", "linear": 3.0, "angular": 0.0}
        p1 = gate.check(action1)
        p2 = gate.check(action2)

        # Approve first
        gate.approve(p1["approval_id"])

        pending = gate.list_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == p2["approval_id"]


# =====================================================================
# ApprovalGate.clear
# =====================================================================
class TestApprovalGateClear:
    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_clear_removes_resolved_items(self, mock_exists):
        config = {
            "physics": {"max_speed_ms": 0.5},
            "agent": {"require_approval": True},
        }
        gate = ApprovalGate(config)

        action1 = {"type": "move", "linear": 2.0, "angular": 0.0}
        action2 = {"type": "move", "linear": 3.0, "angular": 0.0}
        p1 = gate.check(action1)
        p2 = gate.check(action2)

        # Approve first, deny second -- both resolved
        gate.approve(p1["approval_id"])
        gate.deny(p2["approval_id"])

        gate.clear()
        # All resolved items should be gone; no pending items remain
        assert len(gate._queue) == 0


# =====================================================================
# ApprovalGate -- disabled gate
# =====================================================================
class TestApprovalGateDisabled:
    @patch("castor.approvals.os.path.exists", return_value=False)
    def test_disabled_gate_passes_everything_through(self, mock_exists):
        gate = ApprovalGate({})  # require_approval defaults to False
        action = {"type": "move", "linear": 100.0, "angular": 100.0}
        result = gate.check(action)
        assert result == action
