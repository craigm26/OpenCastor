"""Tests for BehaviorRunner condition step (issue #269)."""

from __future__ import annotations


def _make_runner():
    from castor.behaviors import BehaviorRunner

    return BehaviorRunner(driver=None, brain=None, speaker=None, config={})


# ── Registration ──────────────────────────────────────────────────────────────


def test_condition_registered_in_handlers():
    runner = _make_runner()
    assert "condition" in runner._step_handlers


# ── Condition evaluation with sensor=none ─────────────────────────────────────


def test_condition_none_sensor_no_field_runs_else_steps():
    """sensor=none with field=None → actual is None → runs else_steps silently."""
    results = []
    runner = _make_runner()

    def record_else(step):
        results.append("else")

    runner._step_handlers["stop"] = record_else

    behavior = {
        "name": "cond_none",
        "steps": [
            {
                "type": "condition",
                "sensor": "none",
                "op": "lt",
                "value": 100,
                "then_steps": [],
                "else_steps": [{"type": "stop"}],
            }
        ],
    }
    runner.run(behavior)
    assert results == ["else"]


def test_condition_registered():
    """'condition' is in step_handlers."""
    runner = _make_runner()
    assert "condition" in runner._step_handlers


def test_condition_false_runs_else_steps():
    """When condition evaluates False, else_steps are run."""
    results = []
    runner = _make_runner()

    def record_then(step):
        results.append("then")

    def record_else(step):
        results.append("else")

    runner._step_handlers["wait"] = record_then
    runner._step_handlers["stop"] = record_else

    # Patch sensor to return a value that will be > 300 (condition False for lt)
    import unittest.mock as mock

    with mock.patch("castor.drivers.lidar_driver.get_lidar") as mock_lidar:
        mock_lidar.return_value.obstacles.return_value = {"center_cm": 500}
        behavior = {
            "name": "cond_false",
            "steps": [
                {
                    "type": "condition",
                    "sensor": "lidar",
                    "field": "center_cm",
                    "op": "lt",
                    "value": 300,
                    "then_steps": [{"type": "wait"}],
                    "else_steps": [{"type": "stop"}],
                }
            ],
        }
        runner.run(behavior)
    assert "else" in results
    assert "then" not in results


def test_condition_true_runs_then_steps():
    """When condition evaluates True, then_steps are run."""
    results = []
    runner = _make_runner()

    def record_then(step):
        results.append("then")

    def record_else(step):
        results.append("else")

    runner._step_handlers["wait"] = record_then
    runner._step_handlers["stop"] = record_else

    import unittest.mock as mock

    with mock.patch("castor.drivers.lidar_driver.get_lidar") as mock_lidar:
        mock_lidar.return_value.obstacles.return_value = {"center_cm": 100}
        behavior = {
            "name": "cond_true",
            "steps": [
                {
                    "type": "condition",
                    "sensor": "lidar",
                    "field": "center_cm",
                    "op": "lt",
                    "value": 300,
                    "then_steps": [{"type": "wait"}],
                    "else_steps": [{"type": "stop"}],
                }
            ],
        }
        runner.run(behavior)
    assert "then" in results
    assert "else" not in results


def test_condition_missing_field_runs_else(caplog):
    """When the sensor field is missing, log warning and run else_steps."""
    import logging
    import unittest.mock as mock

    results = []
    runner = _make_runner()

    def record_else(step):
        results.append("else")

    runner._step_handlers["stop"] = record_else

    with mock.patch("castor.drivers.lidar_driver.get_lidar") as mock_lidar:
        mock_lidar.return_value.obstacles.return_value = {}  # no 'center_cm'
        with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
            behavior = {
                "name": "cond_missing",
                "steps": [
                    {
                        "type": "condition",
                        "sensor": "lidar",
                        "field": "center_cm",
                        "op": "lt",
                        "value": 300,
                        "then_steps": [],
                        "else_steps": [{"type": "stop"}],
                    }
                ],
            }
            runner.run(behavior)
    assert "else" in results


def test_condition_unknown_step_type_skipped():
    """Unknown inner step types are skipped without raising."""
    runner = _make_runner()
    behavior = {
        "name": "cond_unknown",
        "steps": [
            {
                "type": "condition",
                "sensor": "none",
                "field": "x",
                "op": "eq",
                "value": 0,
                "then_steps": [{"type": "does_not_exist"}],
                "else_steps": [],
            }
        ],
    }
    runner.run(behavior)  # must not raise


def test_condition_ops_all_supported():
    """All six operators (lt/gt/lte/gte/eq/neq) should work."""
    import unittest.mock as mock

    ops_and_expected = [
        ("lt", 50, 100, True),
        ("gt", 150, 100, True),
        ("lte", 100, 100, True),
        ("gte", 100, 100, True),
        ("eq", 100, 100, True),
        ("neq", 50, 100, True),
    ]

    for op, actual, threshold, expected_then in ops_and_expected:
        results = []
        runner = _make_runner()
        runner._step_handlers["wait"] = lambda step, label="then", _r=results: _r.append(label)
        runner._step_handlers["stop"] = lambda step, label="else", _r=results: _r.append(label)

        with mock.patch("castor.drivers.lidar_driver.get_lidar") as mock_lidar:
            mock_lidar.return_value.obstacles.return_value = {"val": actual}
            behavior = {
                "name": f"op_{op}",
                "steps": [
                    {
                        "type": "condition",
                        "sensor": "lidar",
                        "field": "val",
                        "op": op,
                        "value": threshold,
                        "then_steps": [{"type": "wait"}],
                        "else_steps": [{"type": "stop"}],
                    }
                ],
            }
            runner.run(behavior)

        if expected_then:
            assert "then" in results, (
                f"Expected 'then' for op={op} actual={actual} threshold={threshold}"
            )
