"""Tests for AgentRegistry — spawn, list, stop, health check."""

import asyncio

import pytest

from castor.agents.base import AgentStatus, BaseAgent
from castor.agents.navigator import NavigatorAgent
from castor.agents.observer import ObserverAgent
from castor.agents.registry import AgentRegistry


# ---------------------------------------------------------------------------
# Minimal test agents
# ---------------------------------------------------------------------------


class DummyAgent(BaseAgent):
    name = "dummy"

    async def observe(self, sensor_data):
        return {}

    async def act(self, context):
        return {"action": "noop"}


class AnotherAgent(BaseAgent):
    name = "another"

    async def observe(self, sensor_data):
        return {}

    async def act(self, context):
        return {}


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestAgentRegistryRegister:
    def test_register_adds_class(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        assert "dummy" in registry._classes

    def test_register_multiple_classes(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.register(AnotherAgent)
        assert "dummy" in registry._classes
        assert "another" in registry._classes

    def test_register_observer(self):
        registry = AgentRegistry()
        registry.register(ObserverAgent)
        assert "observer" in registry._classes

    def test_register_navigator(self):
        registry = AgentRegistry()
        registry.register(NavigatorAgent)
        assert "navigator" in registry._classes

    def test_reregister_overwrites(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.register(DummyAgent)
        assert registry._classes["dummy"] is DummyAgent


# ---------------------------------------------------------------------------
# spawn()
# ---------------------------------------------------------------------------


class TestAgentRegistrySpawn:
    def test_spawn_returns_instance(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        agent = registry.spawn("dummy")
        assert isinstance(agent, DummyAgent)

    def test_spawn_unknown_raises_key_error(self):
        registry = AgentRegistry()
        with pytest.raises(KeyError):
            registry.spawn("nonexistent")

    def test_spawn_with_config(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        agent = registry.spawn("dummy", config={"key": "val"})
        assert agent.config["key"] == "val"

    def test_spawn_without_config_uses_empty_dict(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        agent = registry.spawn("dummy")
        assert agent.config == {}

    def test_spawn_stores_agent(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        spawned = registry.spawn("dummy")
        assert registry.get("dummy") is spawned

    def test_spawn_observer_agent(self):
        registry = AgentRegistry()
        registry.register(ObserverAgent)
        agent = registry.spawn("observer")
        assert isinstance(agent, ObserverAgent)

    def test_spawn_navigator_agent(self):
        registry = AgentRegistry()
        registry.register(NavigatorAgent)
        agent = registry.spawn("navigator")
        assert isinstance(agent, NavigatorAgent)

    def test_spawn_records_spawn_time(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        assert "dummy" in registry._spawn_times

    def test_spawn_sets_idle_status(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        agent = registry.spawn("dummy")
        assert agent.status == AgentStatus.IDLE


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


class TestAgentRegistryGet:
    def test_get_returns_spawned_agent(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        spawned = registry.spawn("dummy")
        assert registry.get("dummy") is spawned

    def test_get_unknown_returns_none(self):
        registry = AgentRegistry()
        assert registry.get("unknown") is None

    def test_get_unspawned_registered_returns_none(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)  # registered but not yet spawned
        assert registry.get("dummy") is None


# ---------------------------------------------------------------------------
# list_agents()
# ---------------------------------------------------------------------------


class TestAgentRegistryList:
    def test_list_empty_initially(self):
        registry = AgentRegistry()
        assert registry.list_agents() == []

    def test_list_has_entry_after_spawn(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        entries = registry.list_agents()
        assert len(entries) == 1

    def test_list_entry_has_name(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        assert registry.list_agents()[0]["name"] == "dummy"

    def test_list_entry_has_status(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        assert "status" in registry.list_agents()[0]

    def test_list_entry_has_uptime_s(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        entry = registry.list_agents()[0]
        assert "uptime_s" in entry
        assert entry["uptime_s"] >= 0.0

    def test_list_multiple_agents(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.register(AnotherAgent)
        registry.spawn("dummy")
        registry.spawn("another")
        names = {e["name"] for e in registry.list_agents()}
        assert names == {"dummy", "another"}

    def test_list_status_idle_before_start(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        assert registry.list_agents()[0]["status"] == "idle"


# ---------------------------------------------------------------------------
# stop_all()
# ---------------------------------------------------------------------------


class TestAgentRegistryStopAll:
    def test_stop_all_empty_no_error(self):
        registry = AgentRegistry()
        asyncio.run(registry.stop_all())  # must not raise

    def test_stop_all_stops_running_agents(self):
        async def _test():
            registry = AgentRegistry()
            registry.register(DummyAgent)
            agent = registry.spawn("dummy")
            await agent.start()
            assert agent.status == AgentStatus.RUNNING
            await registry.stop_all()
            assert agent.status == AgentStatus.STOPPED

        asyncio.run(_test())

    def test_stop_all_multiple_agents(self):
        async def _test():
            registry = AgentRegistry()
            registry.register(DummyAgent)
            registry.register(AnotherAgent)
            a1 = registry.spawn("dummy")
            a2 = registry.spawn("another")
            await a1.start()
            await a2.start()
            await registry.stop_all()
            assert a1.status == AgentStatus.STOPPED
            assert a2.status == AgentStatus.STOPPED

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# health_report()
# ---------------------------------------------------------------------------


class TestAgentRegistryHealthReport:
    def test_health_report_empty(self):
        registry = AgentRegistry()
        assert registry.health_report() == {}

    def test_health_report_has_agent_entry(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        assert "dummy" in registry.health_report()

    def test_health_report_entry_has_status(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        assert "status" in registry.health_report()["dummy"]

    def test_health_report_entry_has_uptime(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        assert "uptime_s" in registry.health_report()["dummy"]

    def test_health_report_entry_has_errors(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.spawn("dummy")
        assert "errors" in registry.health_report()["dummy"]

    def test_health_report_reflects_running_status(self):
        async def _test():
            registry = AgentRegistry()
            registry.register(DummyAgent)
            agent = registry.spawn("dummy")
            await agent.start()
            report = registry.health_report()
            assert report["dummy"]["status"] == "running"
            await agent.stop()

        asyncio.run(_test())

    def test_health_report_multiple_agents(self):
        registry = AgentRegistry()
        registry.register(DummyAgent)
        registry.register(AnotherAgent)
        registry.spawn("dummy")
        registry.spawn("another")
        report = registry.health_report()
        assert "dummy" in report
        assert "another" in report
