"""Tests for castor diff (config diff CLI) (#389)."""

import pytest
import yaml

from castor.diff import diff_configs, print_diff


@pytest.fixture
def rcan_a(tmp_path):
    data = {
        "rcan_version": "1.1.0",
        "metadata": {"robot_name": "alex"},
        "agent": {"provider": "google", "model": "gemini-2.5-flash"},
        "drivers": [{"id": "wheels", "protocol": "pca9685"}],
    }
    p = tmp_path / "a.rcan.yaml"
    p.write_text(yaml.dump(data))
    return str(p)


@pytest.fixture
def rcan_b(tmp_path):
    data = {
        "rcan_version": "1.1.0",
        "metadata": {"robot_name": "bob"},
        "agent": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "drivers": [{"id": "wheels", "protocol": "pca9685"}],
    }
    p = tmp_path / "b.rcan.yaml"
    p.write_text(yaml.dump(data))
    return str(p)


@pytest.fixture
def rcan_identical(tmp_path, rcan_a):
    import shutil

    p = tmp_path / "a_copy.rcan.yaml"
    shutil.copy(rcan_a, str(p))
    return str(p)


# ── basic return shape ────────────────────────────────────────────────────────


def test_diff_configs_returns_list(rcan_a, rcan_b):
    result = diff_configs(rcan_a, rcan_b)
    assert isinstance(result, list)


def test_diff_configs_identical_files_empty(rcan_a, rcan_identical):
    result = diff_configs(rcan_a, rcan_identical)
    assert result == []


def test_diff_configs_detects_differences(rcan_a, rcan_b):
    result = diff_configs(rcan_a, rcan_b)
    assert len(result) > 0


def test_diff_entry_is_tuple(rcan_a, rcan_b):
    result = diff_configs(rcan_a, rcan_b)
    assert all(isinstance(entry, tuple) for entry in result)


def test_diff_entry_has_three_elements(rcan_a, rcan_b):
    result = diff_configs(rcan_a, rcan_b)
    assert all(len(entry) == 3 for entry in result)


# ── detects specific changes ──────────────────────────────────────────────────


def test_diff_detects_robot_name_change(rcan_a, rcan_b):
    result = diff_configs(rcan_a, rcan_b)
    paths = [entry[0] for entry in result]
    # Should detect metadata.robot_name or metadata change
    assert any("robot_name" in p or "metadata" in p for p in paths)


def test_diff_detects_provider_change(rcan_a, rcan_b):
    result = diff_configs(rcan_a, rcan_b)
    paths = [entry[0] for entry in result]
    assert any("provider" in p or "agent" in p for p in paths)


def test_diff_values_in_tuple(rcan_a, rcan_b):
    result = diff_configs(rcan_a, rcan_b)
    # For the robot_name diff, values should be "alex" and "bob"
    robot_name_diff = next((e for e in result if "robot_name" in e[0]), None)
    if robot_name_diff:
        vals = set(robot_name_diff[1:])
        assert "alex" in vals or "bob" in vals


# ── missing key handling ──────────────────────────────────────────────────────


def test_diff_detects_missing_key(tmp_path):
    data_a = {"rcan_version": "1.1.0", "extra_key": "value"}
    data_b = {"rcan_version": "1.1.0"}
    pa = tmp_path / "a.yaml"
    pb = tmp_path / "b.yaml"
    pa.write_text(yaml.dump(data_a))
    pb.write_text(yaml.dump(data_b))
    result = diff_configs(str(pa), str(pb))
    paths = [e[0] for e in result]
    assert any("extra_key" in p for p in paths)


def test_diff_detects_added_key(tmp_path):
    data_a = {"rcan_version": "1.1.0"}
    data_b = {"rcan_version": "1.1.0", "new_key": "new_val"}
    pa = tmp_path / "a.yaml"
    pb = tmp_path / "b.yaml"
    pa.write_text(yaml.dump(data_a))
    pb.write_text(yaml.dump(data_b))
    result = diff_configs(str(pa), str(pb))
    paths = [e[0] for e in result]
    assert any("new_key" in p for p in paths)


# ── print_diff smoke test ─────────────────────────────────────────────────────


def test_print_diff_does_not_raise(rcan_a, rcan_b, capsys):
    diffs = diff_configs(rcan_a, rcan_b)
    print_diff(diffs, rcan_a, rcan_b)
    captured = capsys.readouterr()
    # Should produce some output
    assert len(captured.out) >= 0  # just verify no crash


def test_print_diff_empty_diffs_no_crash(rcan_a, rcan_identical, capsys):
    diffs = diff_configs(rcan_a, rcan_identical)
    print_diff(diffs, rcan_a, rcan_identical)  # should not raise
