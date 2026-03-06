"""Integration test — validate sample RCAN config fixture against rcan-py CLI."""

from __future__ import annotations

import importlib.util
import subprocess
import pathlib

import pytest

HAS_RCAN = importlib.util.find_spec("rcan") is not None
HAS_RCAN_CLI = HAS_RCAN and importlib.util.find_spec("rcan") is not None and (
    subprocess.run(["which", "rcan-validate"], capture_output=True).returncode == 0
)

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "sample.rcan.yaml"
REPO_ROOT = pathlib.Path(__file__).parent.parent


@pytest.mark.skipif(not HAS_RCAN_CLI, reason="rcan-validate CLI not available")
def test_rcan_validate_sample_config():
    result = subprocess.run(
        ["rcan-validate", "config", str(FIXTURE_PATH)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"rcan-validate failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_sample_fixture_exists():
    """Fixture file exists and is non-empty YAML."""
    assert FIXTURE_PATH.exists(), f"Fixture missing: {FIXTURE_PATH}"
    content = FIXTURE_PATH.read_text()
    assert "rcan_version" in content
    assert "metadata" in content


def test_sample_fixture_is_valid_yaml():
    """Fixture parses as valid YAML."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        pytest.skip("pyyaml not installed")

    import yaml

    data = yaml.safe_load(FIXTURE_PATH.read_text())
    assert isinstance(data, dict)
    assert "rcan_version" in data
    assert "metadata" in data
    assert data["metadata"]["robot_name"] == "TestBot"
