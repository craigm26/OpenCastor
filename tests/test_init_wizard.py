"""
Tests for castor.init_wizard — interactive zero-to-fleet onboarding wizard.

At least 15 tests covering:
  - Config generation with all required fields
  - UUID format validation
  - RURI derivation from robot name
  - API token is 64-char hex
  - Provider model defaults
  - Non-interactive mode (--no-interactive)
  - Existing file: warns before overwriting
  - YAML parsability
  - cmd_quickstart wires init then gateway
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from castor.init_wizard import (
    PROVIDER_MODELS,
    _generate_ruri,
    _slugify,
    generate_wizard_config,
    run_wizard,
)

# ---------------------------------------------------------------------------
# Unit tests — generate_wizard_config()
# ---------------------------------------------------------------------------


def test_config_has_required_top_level_keys():
    config, rrn, filename = generate_wizard_config(
        robot_name="test-robot",
        provider="google",
    )
    for key in (
        "rcan_version",
        "metadata",
        "agent",
        "gateway",
        "rcan_protocol",
        "skills",
        "memory",
        "firebase",
    ):
        assert key in config, f"Missing top-level key: {key}"


def test_metadata_fields():
    config, rrn, filename = generate_wizard_config(robot_name="Zippy", provider="google")
    meta = config["metadata"]
    assert meta["robot_name"] == "Zippy"
    assert "robot_uuid" in meta
    assert "ruri" in meta
    assert "rrn" in meta


def test_robot_uuid_is_valid_uuid4():
    config, rrn, filename = generate_wizard_config(robot_name="Zippy", provider="google")
    robot_uuid = config["metadata"]["robot_uuid"]
    parsed = uuid.UUID(robot_uuid)
    assert parsed.version == 4, f"Expected UUID4, got version {parsed.version}"


def test_rrn_format():
    config, rrn, filename = generate_wizard_config(robot_name="Zippy", provider="google")
    # RRN must look like RRN-XXXXXXXXXXXX (12 hex uppercase chars)
    assert re.fullmatch(r"RRN-[0-9A-F]{12}", rrn), f"Bad RRN format: {rrn}"
    assert config["metadata"]["rrn"] == rrn


def test_ruri_derived_from_robot_name():
    config, rrn, filename = generate_wizard_config(robot_name="My Cool Bot", provider="google")
    ruri = config["metadata"]["ruri"]
    # slug of "My Cool Bot" → "my-cool-bot"
    assert ruri.startswith("rcan://my-cool-bot"), f"RURI mismatch: {ruri}"


def test_api_token_is_64_char_hex():
    config, rrn, filename = generate_wizard_config(robot_name="test", provider="google")
    token = config["gateway"]["api_token"]
    assert len(token) == 64, f"Expected 64-char token, got {len(token)}"
    assert re.fullmatch(r"[0-9a-f]{64}", token), f"Token is not hex: {token}"


def test_provider_default_google():
    config, _, _ = generate_wizard_config(robot_name="r", provider="google")
    assert config["agent"]["model"] == PROVIDER_MODELS["google"]
    assert config["agent"]["model"] == "gemini-2.5-flash"


def test_provider_default_anthropic():
    config, _, _ = generate_wizard_config(robot_name="r", provider="anthropic")
    assert config["agent"]["model"] == "claude-3-5-haiku-20241022"


def test_provider_default_openai():
    config, _, _ = generate_wizard_config(robot_name="r", provider="openai")
    assert config["agent"]["model"] == "gpt-4o-mini"


def test_provider_default_local():
    config, _, _ = generate_wizard_config(robot_name="r", provider="local")
    assert config["agent"]["model"] == "llama3.2:3b"


def test_skills_section():
    config, _, _ = generate_wizard_config(robot_name="r", provider="google")
    skills = config["skills"]
    assert skills["enabled"] is True
    assert "navigator" in skills["builtin_skills"]
    assert "vision" in skills["builtin_skills"]
    assert "code-reviewer" in skills["builtin_skills"]


def test_gateway_section():
    config, _, _ = generate_wizard_config(robot_name="r", provider="google", port=9090)
    gw = config["gateway"]
    assert gw["host"] == "0.0.0.0"
    assert gw["port"] == 9090
    assert "api_token" in gw


def test_firebase_enabled_when_non_default_project():
    config, _, _ = generate_wizard_config(
        robot_name="r", provider="google", firebase_project="my-real-project"
    )
    assert config["firebase"]["enabled"] is True
    assert config["firebase"]["project_id"] == "my-real-project"


def test_firebase_disabled_when_default():
    # "opencastor" is the sentinel default → disabled
    config, _, _ = generate_wizard_config(
        robot_name="r", provider="google", firebase_project="opencastor"
    )
    assert config["firebase"]["enabled"] is False


def test_rcan_protocol_section():
    config, _, _ = generate_wizard_config(robot_name="r", provider="google")
    rcan = config["rcan_protocol"]
    assert rcan["enable_mdns"] is True
    assert rcan["version"] == "1.6"


def test_memory_section():
    config, _, _ = generate_wizard_config(robot_name="r", provider="google")
    assert config["memory"]["enabled"] is True


def test_temperature_07():
    config, _, _ = generate_wizard_config(robot_name="r", provider="google")
    assert config["agent"]["temperature"] == 0.7


def test_api_key_included_in_agent_block():
    config, _, _ = generate_wizard_config(
        robot_name="r", provider="google", api_key="AIzaSy-test-key"
    )
    assert config["agent"].get("google_api_key") == "AIzaSy-test-key"


def test_api_key_not_set_when_blank():
    config, _, _ = generate_wizard_config(robot_name="r", provider="google", api_key="")
    assert "google_api_key" not in config["agent"]


# ---------------------------------------------------------------------------
# Slugify and RURI helpers
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert _slugify("My Robot") == "my-robot"
    assert _slugify("Bob") == "bob"
    assert _slugify("  Zippy 3000  ") == "zippy-3000"


def test_generate_ruri_contains_slug():
    ruri = _generate_ruri("My Robot", "abcd1234-0000-4000-8000-000000000001")
    assert "my-robot" in ruri
    assert ruri.startswith("rcan://")


def test_generate_ruri_contains_uuid_prefix():
    ruri = _generate_ruri("Bob", "abcd1234-0000-4000-8000-000000000001")
    # UUID prefix = first 8 chars of stripped uuid = "abcd1234"
    assert "abcd1234" in ruri


# ---------------------------------------------------------------------------
# Non-interactive mode (run_wizard with no_interactive=True)
# ---------------------------------------------------------------------------


def test_non_interactive_writes_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "bob.rcan.yaml")
        result = run_wizard(
            name="Bob",
            provider="google",
            port=8080,
            output=out,
            no_interactive=True,
        )
        assert os.path.exists(result)
        with open(result) as f:
            data = yaml.safe_load(f)
        assert data["metadata"]["robot_name"] == "Bob"


def test_non_interactive_uses_defaults():
    """When no args given, non-interactive should use 'my-robot' and google."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "my-robot.rcan.yaml")
        result = run_wizard(output=out, no_interactive=True)
        with open(result) as f:
            data = yaml.safe_load(f)
        assert data["metadata"]["robot_name"] == "my-robot"
        assert data["agent"]["provider"] == "google"


def test_non_interactive_provider_anthropic():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "r.rcan.yaml")
        run_wizard(name="r", provider="anthropic", output=out, no_interactive=True)
        with open(out) as f:
            data = yaml.safe_load(f)
        assert data["agent"]["provider"] == "anthropic"
        assert data["agent"]["model"] == "claude-3-5-haiku-20241022"


def test_non_interactive_custom_port():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "r.rcan.yaml")
        run_wizard(name="r", port=9999, output=out, no_interactive=True)
        with open(out) as f:
            data = yaml.safe_load(f)
        assert data["gateway"]["port"] == 9999


def test_non_interactive_overwrite_raises():
    """Without --overwrite, FileExistsError when file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "r.rcan.yaml")
        run_wizard(name="r", output=out, no_interactive=True)
        with pytest.raises(FileExistsError):
            run_wizard(name="r", output=out, no_interactive=True, overwrite=False)


def test_non_interactive_overwrite_succeeds():
    """With overwrite=True, wizard replaces existing file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "r.rcan.yaml")
        run_wizard(name="first", output=out, no_interactive=True)
        run_wizard(name="second", output=out, no_interactive=True, overwrite=True)
        with open(out) as f:
            data = yaml.safe_load(f)
        assert data["metadata"]["robot_name"] == "second"


def test_output_filename_derived_from_name():
    """When --output is not given, filename is <slug>.rcan.yaml."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_dir = os.getcwd()
        try:
            os.chdir(tmpdir)
            result = run_wizard(name="My Robot", no_interactive=True)
            assert Path(result).name == "my-robot.rcan.yaml"
        finally:
            os.chdir(orig_dir)


# ---------------------------------------------------------------------------
# YAML validity
# ---------------------------------------------------------------------------


def test_output_yaml_is_valid():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "r.rcan.yaml")
        run_wizard(name="test-bot", output=out, no_interactive=True)
        with open(out) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)


def test_all_required_sections_in_yaml():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "r.rcan.yaml")
        run_wizard(name="test-bot", output=out, no_interactive=True)
        with open(out) as f:
            data = yaml.safe_load(f)
        for section in (
            "rcan_version",
            "metadata",
            "agent",
            "gateway",
            "rcan_protocol",
            "skills",
            "memory",
            "firebase",
        ):
            assert section in data, f"Missing section: {section}"


# ---------------------------------------------------------------------------
# cmd_quickstart — calls init then starts gateway
# ---------------------------------------------------------------------------


def test_cmd_quickstart_calls_init_then_gateway():
    """cmd_quickstart should call run_wizard then exec the gateway."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "qs-bot.rcan.yaml")

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("castor.init_wizard.run_wizard", return_value=out) as mock_init,
            patch("castor.init_wizard.subprocess.run", return_value=mock_result) as mock_sub,
        ):
            from castor.init_wizard import cmd_quickstart

            args = MagicMock()
            args.name = "qs-bot"
            args.provider = "google"
            args.port = 8080
            args.api_key = None
            args.firebase_project = None
            args.output = out
            args.no_interactive = True
            args.overwrite = False

            with pytest.raises(SystemExit) as exc:
                cmd_quickstart(args)
            assert exc.value.code == 0

        mock_init.assert_called_once()
        # subprocess.run should have been called with gateway args
        gateway_call_args = mock_sub.call_args[0][0]
        assert "gateway" in gateway_call_args
        assert "--config" in gateway_call_args
        assert out in gateway_call_args


def test_cmd_init_delegates_to_wizard():
    """cmd_init should delegate to run_wizard."""
    with patch("castor.init_wizard.run_wizard") as mock_wizard:
        mock_wizard.return_value = "/tmp/bob.rcan.yaml"
        from castor.init_wizard import cmd_init

        args = MagicMock()
        args.print = False
        args.name = "Bob"
        args.provider = "google"
        args.port = 8080
        args.api_key = None
        args.firebase_project = None
        args.output = "/tmp/bob.rcan.yaml"
        args.no_interactive = True
        args.overwrite = False

        cmd_init(args)
        mock_wizard.assert_called_once()


def test_cmd_init_print_flag_uses_legacy_path():
    """--print flag should emit YAML to stdout without wizard."""
    from io import StringIO

    from castor.init_wizard import cmd_init

    args = MagicMock()
    args.print = True
    args.name = "print-test"

    with patch("sys.stdout", new_callable=StringIO) as mock_out:
        cmd_init(args)
        output = mock_out.getvalue()
    # Should contain YAML content
    assert "print-test" in output or "robot_name" in output


# ---------------------------------------------------------------------------
# Unique UUIDs — each call generates a fresh UUID
# ---------------------------------------------------------------------------


def test_unique_uuids_per_call():
    cfg1, _, _ = generate_wizard_config(robot_name="r", provider="google")
    cfg2, _, _ = generate_wizard_config(robot_name="r", provider="google")
    assert cfg1["metadata"]["robot_uuid"] != cfg2["metadata"]["robot_uuid"]


def test_unique_rrns_per_call():
    _, rrn1, _ = generate_wizard_config(robot_name="r", provider="google")
    _, rrn2, _ = generate_wizard_config(robot_name="r", provider="google")
    assert rrn1 != rrn2


# ---------------------------------------------------------------------------
# castor --help includes init and quickstart
# ---------------------------------------------------------------------------


def test_init_and_quickstart_in_cli_help():
    """castor --help output must mention init and quickstart."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "castor", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    combined = result.stdout + result.stderr
    assert "init" in combined, "'init' not found in castor --help"
    assert "quickstart" in combined, "'quickstart' not found in castor --help"
