"""tests/test_behavior_for_each.py — Tests for the ``for_each`` step type in BehaviorRunner."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner():
    from castor.behaviors import BehaviorRunner

    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, brain=None, speaker=None, config={})
    runner._running = True  # simulate being inside run()
    return runner


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_for_each_registered_in_step_handlers():
    """``for_each`` must appear in ``_step_handlers``."""
    runner = _make_runner()
    assert "for_each" in runner._step_handlers


def test_for_each_handler_is_callable():
    runner = _make_runner()
    assert callable(runner._step_handlers["for_each"])


# ---------------------------------------------------------------------------
# Empty / missing items
# ---------------------------------------------------------------------------


def test_for_each_empty_items_logs_warning_and_skips(caplog):
    """An empty ``items`` list should log a warning and return without error."""
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_for_each({"type": "for_each", "items": [], "inner_steps": []})
    assert any(
        "empty" in r.message.lower() or "missing" in r.message.lower() for r in caplog.records
    )


def test_for_each_missing_items_key_logs_warning(caplog):
    """A step dict with no ``items`` key should log a warning."""
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_for_each({"type": "for_each", "inner_steps": []})
    assert any(
        "empty" in r.message.lower() or "missing" in r.message.lower() for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Correct iteration count
# ---------------------------------------------------------------------------


def test_for_each_iterates_over_all_items():
    """Each item must produce exactly one call to ``_run_step_list``."""
    runner = _make_runner()
    call_log: list = []
    runner._run_step_list = lambda steps, ctx: call_log.append(("run", ctx))  # type: ignore[method-assign]

    runner._step_for_each(
        {"type": "for_each", "items": [1, 2, 3], "inner_steps": [{"type": "wait", "seconds": 0}]}
    )
    assert len(call_log) == 3


def test_for_each_single_item():
    runner = _make_runner()
    call_log: list = []
    runner._run_step_list = lambda steps, ctx: call_log.append(steps)  # type: ignore[method-assign]

    runner._step_for_each(
        {"type": "for_each", "items": [42], "inner_steps": [{"type": "wait", "seconds": "$item"}]}
    )
    assert len(call_log) == 1


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------


def test_for_each_substitutes_var_in_inner_steps():
    """String values equal to ``var`` in inner step dicts must be replaced by the item value."""
    runner = _make_runner()
    received_steps: list = []
    runner._run_step_list = lambda steps, ctx: received_steps.extend(steps)  # type: ignore[method-assign]

    runner._step_for_each(
        {
            "type": "for_each",
            "items": [10, 20],
            "var": "$item",
            "inner_steps": [{"type": "wait", "seconds": "$item"}],
        }
    )
    # First iteration → seconds=10, second → seconds=20
    assert received_steps[0]["seconds"] == 10
    assert received_steps[1]["seconds"] == 20


def test_for_each_custom_var_name():
    """Custom ``var`` names (e.g. ``$x``) must be substituted correctly."""
    runner = _make_runner()
    received_steps: list = []
    runner._run_step_list = lambda steps, ctx: received_steps.extend(steps)  # type: ignore[method-assign]

    runner._step_for_each(
        {
            "type": "for_each",
            "items": ["hello"],
            "var": "$x",
            "inner_steps": [{"type": "speak", "text": "$x"}],
        }
    )
    assert received_steps[0]["text"] == "hello"


def test_for_each_non_matching_values_unchanged():
    """Values in inner step dicts that do NOT equal ``var`` must be left as-is."""
    runner = _make_runner()
    received_steps: list = []
    runner._run_step_list = lambda steps, ctx: received_steps.extend(steps)  # type: ignore[method-assign]

    runner._step_for_each(
        {
            "type": "for_each",
            "items": [99],
            "var": "$item",
            "inner_steps": [{"type": "wait", "seconds": 1.0, "label": "fixed"}],
        }
    )
    assert received_steps[0]["seconds"] == 1.0
    assert received_steps[0]["label"] == "fixed"


# ---------------------------------------------------------------------------
# Mixed types
# ---------------------------------------------------------------------------


def test_for_each_mixed_item_types():
    """Items of mixed types (int, str, float) should all be iterated without error."""
    runner = _make_runner()
    call_log: list = []
    runner._run_step_list = lambda steps, ctx: call_log.append(steps[0]["seconds"])  # type: ignore[method-assign]

    runner._step_for_each(
        {
            "type": "for_each",
            "items": [1, "two", 3.0],
            "var": "$item",
            "inner_steps": [{"type": "wait", "seconds": "$item"}],
        }
    )
    assert call_log == [1, "two", 3.0]


# ---------------------------------------------------------------------------
# Stop propagation
# ---------------------------------------------------------------------------


def test_for_each_stop_breaks_loop():
    """Setting ``_running = False`` during iteration must break the loop early."""
    runner = _make_runner()
    iterations: list = []

    def _fake_run(steps, ctx):
        iterations.append(1)
        runner._running = False  # stop after first iteration

    runner._run_step_list = _fake_run  # type: ignore[method-assign]

    runner._step_for_each(
        {
            "type": "for_each",
            "items": [1, 2, 3, 4, 5],
            "inner_steps": [{"type": "wait", "seconds": 0}],
        }
    )
    assert len(iterations) == 1


# ---------------------------------------------------------------------------
# dwell_s
# ---------------------------------------------------------------------------


def test_for_each_dwell_s_respected(monkeypatch):
    """``dwell_s > 0`` must cause ``time.sleep`` to be called between iterations."""
    runner = _make_runner()
    sleep_calls: list = []
    monkeypatch.setattr("castor.behaviors.time.sleep", lambda s: sleep_calls.append(s))

    runner._step_for_each(
        {
            "type": "for_each",
            "items": [1, 2],
            "dwell_s": 0.1,
            "inner_steps": [{"type": "wait", "seconds": 0}],
        }
    )
    # At least one sleep call should have happened
    assert len(sleep_calls) > 0


def test_for_each_no_dwell_by_default(monkeypatch):
    """With ``dwell_s`` absent (default 0) no dwell sleep is inserted between iterations."""
    runner = _make_runner()
    sleep_calls: list = []
    monkeypatch.setattr("castor.behaviors.time.sleep", lambda s: sleep_calls.append(s))
    # Stub out _run_step_list so inner-step sleeps don't pollute the assertion.
    runner._run_step_list = lambda steps, ctx: None  # type: ignore[method-assign]

    runner._step_for_each(
        {
            "type": "for_each",
            "items": [1, 2],
            "inner_steps": [{"type": "wait", "seconds": 0}],
        }
    )
    assert sleep_calls == []


# ---------------------------------------------------------------------------
# Unknown inner step type
# ---------------------------------------------------------------------------


def test_for_each_unknown_inner_step_type_does_not_raise():
    """An unknown inner step type must be handled gracefully (no exception)."""
    runner = _make_runner()
    # Allow _run_step_list to run normally (handles unknown types with a warning).
    runner._step_for_each(
        {
            "type": "for_each",
            "items": [1],
            "inner_steps": [{"type": "nonexistent_step_xyz"}],
        }
    )  # Must not raise


def test_for_each_item_count_matches_iterations():
    """The number of ``_run_step_list`` calls must equal the number of items."""
    runner = _make_runner()
    count = 0

    def _counter(steps, ctx):
        nonlocal count
        count += 1

    runner._run_step_list = _counter  # type: ignore[method-assign]

    items = list(range(7))
    runner._step_for_each(
        {"type": "for_each", "items": items, "inner_steps": [{"type": "wait", "seconds": 0}]}
    )
    assert count == len(items)
