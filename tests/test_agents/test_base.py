"""Tests for BaseAgent ABC and AgentStatus lifecycle."""

import asyncio
import time

from castor.agents.base import AgentStatus, BaseAgent


# ---------------------------------------------------------------------------
# Minimal concrete implementations used across tests
# ---------------------------------------------------------------------------


class ConcreteAgent(BaseAgent):
    """Minimal concrete implementation for unit testing."""

    name = "concrete"

    async def observe(self, sensor_data):
        return {"received": sensor_data}

    async def act(self, context):
        return {"action": "noop"}


class SlowAgent(BaseAgent):
    """Agent whose loop sleeps — useful for cancellation tests."""

    name = "slow"

    async def _run_loop(self):
        await asyncio.sleep(10)

    async def observe(self, sensor_data):
        return {}

    async def act(self, context):
        return {}


# ---------------------------------------------------------------------------
# AgentStatus enum
# ---------------------------------------------------------------------------


class TestAgentStatus:
    def test_idle_value(self):
        assert AgentStatus.IDLE.value == "idle"

    def test_running_value(self):
        assert AgentStatus.RUNNING.value == "running"

    def test_stopped_value(self):
        assert AgentStatus.STOPPED.value == "stopped"

    def test_error_value(self):
        assert AgentStatus.ERROR.value == "error"

    def test_all_four_statuses_present(self):
        assert {s.value for s in AgentStatus} == {"idle", "running", "stopped", "error"}

    def test_status_is_comparable(self):
        assert AgentStatus.IDLE != AgentStatus.RUNNING


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestBaseAgentInit:
    def test_default_status_is_idle(self):
        agent = ConcreteAgent()
        assert agent.status == AgentStatus.IDLE

    def test_default_config_is_empty_dict(self):
        agent = ConcreteAgent()
        assert agent.config == {}

    def test_custom_config_stored(self):
        agent = ConcreteAgent(config={"speed": 0.5})
        assert agent.config["speed"] == 0.5

    def test_none_config_becomes_empty_dict(self):
        agent = ConcreteAgent(config=None)
        assert agent.config == {}

    def test_name_attribute(self):
        agent = ConcreteAgent()
        assert agent.name == "concrete"

    def test_no_start_time_initially(self):
        agent = ConcreteAgent()
        assert agent._start_time is None

    def test_errors_list_empty_initially(self):
        agent = ConcreteAgent()
        assert agent._errors == []

    def test_task_is_none_initially(self):
        agent = ConcreteAgent()
        assert agent._task is None


# ---------------------------------------------------------------------------
# Lifecycle — start / stop
# ---------------------------------------------------------------------------


class TestBaseAgentLifecycle:
    def test_start_sets_running(self):
        async def _test():
            agent = ConcreteAgent()
            await agent.start()
            assert agent.status == AgentStatus.RUNNING
            await agent.stop()

        asyncio.run(_test())

    def test_stop_sets_stopped(self):
        async def _test():
            agent = ConcreteAgent()
            await agent.start()
            await agent.stop()
            assert agent.status == AgentStatus.STOPPED

        asyncio.run(_test())

    def test_start_idempotent_when_already_running(self):
        async def _test():
            agent = ConcreteAgent()
            await agent.start()
            first_task = agent._task
            await agent.start()  # second call should be no-op
            assert agent._task is first_task
            assert agent.status == AgentStatus.RUNNING
            await agent.stop()

        asyncio.run(_test())

    def test_start_records_start_time(self):
        async def _test():
            agent = ConcreteAgent()
            before = time.monotonic()
            await agent.start()
            after = time.monotonic()
            assert before <= agent._start_time <= after
            await agent.stop()

        asyncio.run(_test())

    def test_stop_cancels_background_task(self):
        async def _test():
            agent = SlowAgent()
            await agent.start()
            task = agent._task
            await agent.stop()
            assert task.done()

        asyncio.run(_test())

    def test_stop_on_idle_agent_is_safe(self):
        async def _test():
            agent = ConcreteAgent()
            await agent.stop()  # should not raise
            assert agent.status == AgentStatus.STOPPED

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestBaseAgentHealth:
    def test_health_returns_dict(self):
        agent = ConcreteAgent()
        h = agent.health()
        assert isinstance(h, dict)

    def test_health_has_required_keys(self):
        agent = ConcreteAgent()
        h = agent.health()
        assert "status" in h
        assert "uptime_s" in h
        assert "errors" in h

    def test_health_idle_status(self):
        agent = ConcreteAgent()
        assert agent.health()["status"] == "idle"

    def test_health_uptime_zero_before_start(self):
        agent = ConcreteAgent()
        assert agent.health()["uptime_s"] == 0.0

    def test_health_errors_empty_initially(self):
        agent = ConcreteAgent()
        assert agent.health()["errors"] == []

    def test_health_running_status(self):
        async def _test():
            agent = ConcreteAgent()
            await agent.start()
            assert agent.health()["status"] == "running"
            await agent.stop()

        asyncio.run(_test())

    def test_health_uptime_positive_after_start(self):
        async def _test():
            agent = ConcreteAgent()
            await agent.start()
            h = agent.health()
            assert h["uptime_s"] >= 0.0
            await agent.stop()

        asyncio.run(_test())

    def test_health_stopped_status(self):
        async def _test():
            agent = ConcreteAgent()
            await agent.start()
            await agent.stop()
            assert agent.health()["status"] == "stopped"

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# observe / act interface
# ---------------------------------------------------------------------------


class TestBaseAgentObserveAct:
    def test_observe_returns_value(self):
        agent = ConcreteAgent()
        result = asyncio.run(agent.observe({"x": 1}))
        assert result is not None

    def test_observe_passthrough_data(self):
        agent = ConcreteAgent()
        result = asyncio.run(agent.observe({"sensor": 42}))
        assert result["received"]["sensor"] == 42

    def test_act_returns_dict(self):
        agent = ConcreteAgent()
        result = asyncio.run(agent.act({}))
        assert isinstance(result, dict)

    def test_act_has_action_key(self):
        agent = ConcreteAgent()
        result = asyncio.run(agent.act({}))
        assert "action" in result

    def test_observe_with_empty_dict(self):
        agent = ConcreteAgent()
        result = asyncio.run(agent.observe({}))
        assert result is not None


# ---------------------------------------------------------------------------
# Error recording helper
# ---------------------------------------------------------------------------


class TestRecordError:
    def test_record_error_appends_message(self):
        agent = ConcreteAgent()
        agent._record_error("something went wrong")
        assert "something went wrong" in agent._errors

    def test_record_error_sets_error_status(self):
        agent = ConcreteAgent()
        agent._record_error("boom")
        assert agent.status == AgentStatus.ERROR

    def test_multiple_errors_accumulated(self):
        agent = ConcreteAgent()
        agent._record_error("err1")
        agent._record_error("err2")
        assert len(agent._errors) == 2
