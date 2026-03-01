"""tests/test_behavior_chain.py — Tests for the ``chain`` step type in BehaviorRunner."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner():
    from castor.behaviors import BehaviorRunner

    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, brain=None, speaker=None, config={})
    runner._running = True  # simulate being inside run()
    return runner


def _write_behavior_yaml(path: Path, name: str, steps: list | None = None) -> str:
    """Write a minimal behavior YAML file and return its path as a string."""
    import yaml  # noqa: PLC0415

    steps = steps or [{"type": "wait", "seconds": 0}]
    data = {"name": name, "steps": steps}
    path.write_text(yaml.dump(data))
    return str(path)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_chain_registered_in_step_handlers():
    """``chain`` must appear in ``_step_handlers``."""
    runner = _make_runner()
    assert "chain" in runner._step_handlers


def test_chain_handler_is_callable():
    runner = _make_runner()
    assert callable(runner._step_handlers["chain"])


# ---------------------------------------------------------------------------
# Missing keys
# ---------------------------------------------------------------------------


def test_chain_missing_behavior_file_warns_and_skips(caplog):
    """Missing ``behavior_file`` must log a warning and not raise."""
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_chain({"type": "chain", "behavior_name": "foo"})
    assert any("behavior_file" in r.message for r in caplog.records)


def test_chain_missing_behavior_name_warns_and_skips(caplog):
    """Missing ``behavior_name`` must log a warning and not raise."""
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_chain({"type": "chain", "behavior_file": "some.yaml"})
    assert any("behavior_name" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# File / behavior not found
# ---------------------------------------------------------------------------


def test_chain_nonexistent_file_warns_and_skips(caplog, tmp_path):
    """A non-existent ``behavior_file`` must log a warning and return without executing steps."""
    runner = _make_runner()
    executed: list = []
    runner._run_step_list = lambda steps, ctx: executed.extend(steps)  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_chain(
            {
                "type": "chain",
                "behavior_file": str(tmp_path / "does_not_exist.yaml"),
                "behavior_name": "patrol",
            }
        )
    assert executed == []
    assert any(
        "failed to load" in r.message.lower() or "not found" in r.message.lower()
        for r in caplog.records
    )


def test_chain_behavior_name_not_found_warns_and_skips(caplog, tmp_path):
    """When ``behavior_name`` is not in the loaded file, log a warning and skip."""
    runner = _make_runner()
    ypath = _write_behavior_yaml(tmp_path / "patrol.yaml", name="other_name")

    executed: list = []
    runner._run_step_list = lambda steps, ctx: executed.extend(steps)  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_chain(
            {"type": "chain", "behavior_file": ypath, "behavior_name": "patrol_loop"}
        )
    assert executed == []
    assert any("not found" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Correct execution
# ---------------------------------------------------------------------------


def test_chain_executes_steps_from_named_behavior(tmp_path):
    """Steps from the chained behavior must actually be executed."""
    runner = _make_runner()
    ypath = _write_behavior_yaml(
        tmp_path / "patrol.yaml",
        name="patrol_loop",
        steps=[{"type": "wait", "seconds": 0}],
    )

    executed_contexts: list = []
    runner._run_step_list = lambda steps, ctx: executed_contexts.append(ctx)  # type: ignore[method-assign]

    runner._step_chain({"type": "chain", "behavior_file": ypath, "behavior_name": "patrol_loop"})
    assert any("chain:patrol_loop" in ctx for ctx in executed_contexts)


def test_chain_passes_correct_steps_to_run_step_list(tmp_path):
    """The steps passed to ``_run_step_list`` must match those in the YAML."""

    steps = [{"type": "wait", "seconds": 1}, {"type": "wait", "seconds": 2}]
    ypath = _write_behavior_yaml(tmp_path / "b.yaml", name="my_behavior", steps=steps)

    runner = _make_runner()
    received: list = []
    runner._run_step_list = lambda s, ctx: received.extend(s)  # type: ignore[method-assign]

    runner._step_chain({"type": "chain", "behavior_file": ypath, "behavior_name": "my_behavior"})
    assert received == steps


# ---------------------------------------------------------------------------
# Depth counter
# ---------------------------------------------------------------------------


def test_chain_depth_increments_and_decrements(tmp_path):
    """``_chain_depth`` must be 0 before and after a chain step."""
    runner = _make_runner()
    assert runner._chain_depth == 0

    ypath = _write_behavior_yaml(tmp_path / "b.yaml", name="foo")
    runner._run_step_list = lambda steps, ctx: None  # type: ignore[method-assign]

    runner._step_chain({"type": "chain", "behavior_file": ypath, "behavior_name": "foo"})
    assert runner._chain_depth == 0


def test_chain_depth_is_nonzero_during_execution(tmp_path):
    """While executing, ``_chain_depth`` must be >= 1."""
    runner = _make_runner()
    depth_during: list = []

    def _capture(steps, ctx):
        depth_during.append(runner._chain_depth)

    runner._run_step_list = _capture  # type: ignore[method-assign]
    ypath = _write_behavior_yaml(tmp_path / "b.yaml", name="foo")
    runner._step_chain({"type": "chain", "behavior_file": ypath, "behavior_name": "foo"})
    assert depth_during == [1]


def test_chain_max_depth_logs_warning_and_skips(caplog, tmp_path):
    """When ``_chain_depth >= _CHAIN_MAX_DEPTH``, a warning is logged and steps are skipped."""
    runner = _make_runner()
    runner._chain_depth = runner._CHAIN_MAX_DEPTH  # already at limit

    executed: list = []
    runner._run_step_list = lambda steps, ctx: executed.extend(steps)  # type: ignore[method-assign]

    ypath = _write_behavior_yaml(tmp_path / "b.yaml", name="foo")
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_chain({"type": "chain", "behavior_file": ypath, "behavior_name": "foo"})
    assert executed == []
    assert any("max chain depth" in r.message.lower() for r in caplog.records)


def test_chain_max_depth_does_not_decrement_below_limit(tmp_path):
    """When max depth is hit the depth counter must remain unchanged (not decremented)."""
    runner = _make_runner()
    runner._chain_depth = runner._CHAIN_MAX_DEPTH

    ypath = _write_behavior_yaml(tmp_path / "b.yaml", name="foo")
    runner._step_chain({"type": "chain", "behavior_file": ypath, "behavior_name": "foo"})
    # Depth should still equal _CHAIN_MAX_DEPTH — the handler returned before the
    # try/finally block, so no increment/decrement occurred.
    assert runner._chain_depth == runner._CHAIN_MAX_DEPTH


# ---------------------------------------------------------------------------
# stop() propagation
# ---------------------------------------------------------------------------


def test_chain_stop_propagates_into_chained_behavior(tmp_path):
    """If ``_running`` is False, the chain step returns immediately without executing steps."""
    runner = _make_runner()
    runner._running = False  # already stopped

    executed: list = []
    runner._run_step_list = lambda steps, ctx: executed.extend(steps)  # type: ignore[method-assign]

    ypath = _write_behavior_yaml(tmp_path / "b.yaml", name="foo")
    runner._step_chain({"type": "chain", "behavior_file": ypath, "behavior_name": "foo"})
    assert executed == []


def test_chain_depth_restored_after_exception(tmp_path):
    """``_chain_depth`` must return to its original value even if ``_run_step_list`` raises."""
    runner = _make_runner()

    def _raise(steps, ctx):
        raise RuntimeError("inner failure")

    runner._run_step_list = _raise  # type: ignore[method-assign]
    ypath = _write_behavior_yaml(tmp_path / "b.yaml", name="foo")

    initial_depth = runner._chain_depth
    with pytest.raises(RuntimeError):
        runner._step_chain({"type": "chain", "behavior_file": ypath, "behavior_name": "foo"})
    assert runner._chain_depth == initial_depth
