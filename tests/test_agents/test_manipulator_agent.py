"""Tests for ManipulatorAgent."""

import asyncio

import pytest

from castor.agents.shared_state import SharedState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def make_agent(state=None):
    from castor.agents.manipulator_agent import ManipulatorAgent

    return ManipulatorAgent(config={}, shared_state=state or SharedState())


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestManipulatorAgentInit:
    def test_name(self):
        a = make_agent()
        assert a.name == "manipulator"

    def test_specialist_gracefully_absent(self):
        """If ManipulatorSpecialist is unavailable the agent still constructs."""
        import sys
        from unittest.mock import patch

        # Patch the import to fail
        with patch.dict(sys.modules, {"castor.specialists.manipulator": None}):
            from castor.agents.manipulator_agent import ManipulatorAgent

            a = ManipulatorAgent(config={}, shared_state=SharedState())
        # specialist may or may not be set — no crash is the only requirement
        assert a is not None


# ---------------------------------------------------------------------------
# observe()
# ---------------------------------------------------------------------------


class TestManipulatorAgentObserve:
    def test_observe_reads_shared_state(self):
        state = SharedState()
        state.set("swarm.manipulation_task", {"type": "grasp", "goal": "cup"})
        a = make_agent(state)
        ctx = run(a.observe({}))
        assert ctx["pending_task"]["type"] == "grasp"

    def test_observe_reads_sensor_data_fallback(self):
        a = make_agent()
        ctx = run(a.observe({"manipulation_task": {"type": "place", "goal": "shelf"}}))
        assert ctx["pending_task"]["type"] == "place"

    def test_observe_none_when_no_task(self):
        a = make_agent()
        ctx = run(a.observe({}))
        assert ctx["pending_task"] is None


# ---------------------------------------------------------------------------
# act()
# ---------------------------------------------------------------------------


class TestManipulatorAgentAct:
    def test_act_idle_when_no_task(self):
        a = make_agent()
        result = run(a.act({"pending_task": None}))
        assert result["action"] == "idle"

    def test_act_error_when_specialist_unavailable(self):
        a = make_agent()
        a._specialist = None
        result = run(a.act({"pending_task": {"type": "grasp", "goal": "cup"}}))
        assert result["action"] == "manipulate"
        assert result["result"]["status"] == "error"

    def test_act_publishes_result_to_shared_state(self):
        from unittest.mock import AsyncMock, MagicMock

        from castor.specialists.base_specialist import TaskResult, TaskStatus

        state = SharedState()
        a = make_agent(state)
        mock_specialist = MagicMock()
        mock_specialist.execute = AsyncMock(
            return_value=TaskResult(
                task_id="t1",
                status=TaskStatus.SUCCESS,
                output={"joints": [0.0] * 6},
                duration_s=0.5,
            )
        )
        a._specialist = mock_specialist

        run(a.act({"pending_task": {"type": "grasp", "goal": "the red cube"}}))

        published = state.get("swarm.manipulation_result")
        assert published is not None
        assert published["status"] == "success"

    def test_act_handles_specialist_exception(self):
        from unittest.mock import AsyncMock, MagicMock

        state = SharedState()
        a = make_agent(state)
        mock_specialist = MagicMock()
        mock_specialist.execute = AsyncMock(side_effect=RuntimeError("motor fault"))
        a._specialist = mock_specialist

        result = run(a.act({"pending_task": {"type": "grasp", "goal": "cup"}}))

        assert result["result"]["status"] == "error"
        assert "motor fault" in result["result"]["error"]
