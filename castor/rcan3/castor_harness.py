"""castor.rcan3.castor_harness — opencastor's native harness.

Think: LLM callable chooses the next action.
Do:    Executor callable runs the action (safety routing is the executor's job,
       inherited from ``castor.safety``; the harness does not re-implement it).

The two callables are dependency-injected so tests can swap them without
spinning up a real provider or driver.

This module lives in ``castor.rcan3`` (not ``castor.harness``) to avoid
collision with the existing production AgentHarness in ``castor/harness/``.
"""

from __future__ import annotations

from typing import Callable

from castor.rcan3.harness_protocol import ActionResult, Harness, Observation, Thought

LlmFn = Callable[[Observation], Thought]
ExecutorFn = Callable[[Thought], ActionResult]


class CastorDefaultHarness(Harness):
    """opencastor's reference harness. Wraps injected think/do callables."""

    def __init__(self, llm: LlmFn, executor: ExecutorFn) -> None:
        self._llm = llm
        self._executor = executor

    def think(self, obs: Observation) -> Thought:
        return self._llm(obs)

    def do(self, thought: Thought) -> ActionResult:
        return self._executor(thought)
