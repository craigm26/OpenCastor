"""The duck's LLM tools, and the gate that kept them dark.

`docs/hardware/microduck.md` said `duck_vocabulary` and `duck_perform` are
registered "automatically whenever a Microduck is the attached robot". They are
registered at exactly one call site, inside a block gated on
`agent.harness.enabled` — which no shipped profile set. The claim was true of
the function and false of the product.

These tests ask the product's question, not the function's: does a MICRODUCK
CONFIG yield a registry containing `duck_perform`?
"""

from __future__ import annotations

from pathlib import Path

import yaml

import castor
from castor.api import build_tool_registry, harness_enabled

PROFILE = Path(castor.__file__).resolve().parent / "profiles" / "pollen" / "microduck.yaml"
PRESET = (
    Path(castor.__file__).resolve().parent.parent
    / "config"
    / "presets"
    / "pollen_microduck.rcan.yaml"
)


class FakeDuckDriver:
    """Named MicroduckDriver because that is what api.py matches on."""

    def __init__(self):
        self.calls = []

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<FakeDuckDriver>"


FakeDuckDriver.__name__ = "MicroduckDriver"


class FakeCarDriver:
    pass


class FakeState:
    def __init__(self, config, driver=None):
        self.config = config
        self.driver = driver
        self.tool_registry = None


def load(path):
    return yaml.safe_load(path.read_text())


# ── The gate ─────────────────────────────────────────────────────────────────


def test_the_shipped_profile_turns_the_harness_on():
    assert harness_enabled(load(PROFILE)) is True


def test_the_shipped_preset_turns_the_harness_on():
    assert harness_enabled(load(PRESET)) is True


def test_profile_and_preset_agree_about_the_harness():
    """They are kept in sync by hand; a drift here is a duck whose tools vanish."""
    assert load(PROFILE)["agent"]["harness"] == load(PRESET)["agent"]["harness"]


def test_harness_is_still_opt_in_for_everyone_else():
    assert harness_enabled({}) is False
    assert harness_enabled({"agent": {}}) is False
    assert harness_enabled({"agent": {"harness": {}}}) is False
    assert harness_enabled(None) is False


# ── The registry ─────────────────────────────────────────────────────────────


def test_a_microduck_config_yields_a_registry_containing_duck_perform():
    """The one assertion the hardware guide's claim rests on."""
    state = FakeState(load(PROFILE), driver=FakeDuckDriver())
    registry = build_tool_registry(state)
    names = registry.list_tools()
    assert "duck_perform" in names
    assert "duck_vocabulary" in names


def test_the_preset_yields_the_same_registry():
    state = FakeState(load(PRESET), driver=FakeDuckDriver())
    assert "duck_perform" in build_tool_registry(state).list_tools()


def test_duck_perform_is_callable_and_refuses_a_bad_plan():
    """Registered is not the same as usable. Invoke it."""
    state = FakeState(load(PROFILE), driver=FakeDuckDriver())
    registry = build_tool_registry(state)
    result = registry.call("duck_perform", plan=[{"move": "pirouette"}])
    assert result.error is None, result.error
    assert isinstance(result.result, dict)
    assert result.result.get("ok") is False
    # Refused whole, before anything moved, and it names the step it choked on.
    assert "pirouette" in str(result.result)


def test_a_car_gets_no_duck_tools():
    state = FakeState({"agent": {"harness": {"enabled": True}}}, driver=FakeCarDriver())
    assert "duck_perform" not in build_tool_registry(state).list_tools()


def test_no_driver_at_all_is_not_an_error():
    state = FakeState({"agent": {}}, driver=None)
    assert "duck_perform" not in build_tool_registry(state).list_tools()


def test_an_existing_registry_is_extended_not_replaced():
    """api.py reuses state.tool_registry when there is one."""
    from castor.tools import ToolRegistry

    existing = ToolRegistry({})
    existing.register(name="already_here", fn=lambda: None, description="x")
    state = FakeState(load(PROFILE), driver=FakeDuckDriver())
    state.tool_registry = existing
    registry = build_tool_registry(state)
    assert registry is existing
    assert {"already_here", "duck_perform"} <= set(registry.list_tools())
