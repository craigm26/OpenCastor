"""Tests for castor.rcan3.harness_protocol Protocol + dataclasses."""

from __future__ import annotations

from dataclasses import is_dataclass


def test_observation_is_frozen_dataclass():
    from castor.rcan3.harness_protocol import Observation

    obs = Observation(state={"camera": "frame"}, context={})
    assert is_dataclass(obs)
    # Confirm it's immutable:
    import pytest

    with pytest.raises((AttributeError, Exception)):
        obs.state = {}  # type: ignore[misc]


def test_thought_fields():
    from castor.rcan3.harness_protocol import Thought

    t = Thought(action="move", params={"dx": 1.0}, confidence=0.92)
    assert t.action == "move"
    assert t.params["dx"] == 1.0
    assert t.confidence == 0.92


def test_action_result_fields():
    from castor.rcan3.harness_protocol import ActionResult

    r = ActionResult(ok=True, data={"moved": 1.0}, error=None)
    assert r.ok is True


def test_harness_protocol_runtime_check():
    """A class with matching signatures satisfies Harness at runtime."""
    from castor.rcan3.harness_protocol import ActionResult, Harness, Observation, Thought

    class Noop:
        def think(self, obs: Observation) -> Thought:
            return Thought(action="noop", params={}, confidence=1.0)

        def do(self, thought: Thought) -> ActionResult:
            return ActionResult(ok=True, data={}, error=None)

    assert isinstance(Noop(), Harness)


def test_harness_rejects_missing_method():
    from castor.rcan3.harness_protocol import Harness

    class MissingDo:
        def think(self, obs):
            return None

    assert not isinstance(MissingDo(), Harness)
