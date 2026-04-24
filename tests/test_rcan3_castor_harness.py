"""Tests for castor.rcan3.castor_harness — opencastor's native harness."""

from __future__ import annotations


def _fake_llm(obs):
    from castor.rcan3.harness_protocol import Thought

    return Thought(action="move_forward", params={"dx": 0.5}, confidence=0.9)


def _fake_executor(thought):
    from castor.rcan3.harness_protocol import ActionResult

    return ActionResult(ok=True, data={"executed": thought.action}, error=None)


def test_castor_default_think_delegates_to_llm():
    from castor.rcan3.castor_harness import CastorDefaultHarness
    from castor.rcan3.harness_protocol import Observation

    h = CastorDefaultHarness(llm=_fake_llm, executor=_fake_executor)
    t = h.think(Observation(state={}, context={}))
    assert t.action == "move_forward"
    assert t.confidence == 0.9


def test_castor_default_do_delegates_to_executor():
    from castor.rcan3.castor_harness import CastorDefaultHarness
    from castor.rcan3.harness_protocol import Thought

    h = CastorDefaultHarness(llm=_fake_llm, executor=_fake_executor)
    r = h.do(Thought(action="stop", params={}, confidence=1.0))
    assert r.ok is True
    assert r.data["executed"] == "stop"


def test_castor_default_satisfies_harness_protocol():
    from castor.rcan3.castor_harness import CastorDefaultHarness
    from castor.rcan3.harness_protocol import Harness

    h = CastorDefaultHarness(llm=_fake_llm, executor=_fake_executor)
    assert isinstance(h, Harness)
