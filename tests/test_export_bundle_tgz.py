"""Tests for export_bundle_tgz -- issue #335."""

from __future__ import annotations

import json
import os
import tarfile
import tempfile

import yaml


def _write_rcan(path: str, robot_name: str = "test-bot") -> str:
    """Write a minimal RCAN yaml for testing."""
    config = {
        "rcan_version": "1.1.0",
        "metadata": {"robot_name": robot_name},
        "agent": {"provider": "mock", "model": "test-model", "api_key": "secret123"},
        "drivers": [{"id": "wheels", "protocol": "mock"}],
    }
    with open(path, "w") as f:
        yaml.dump(config, f)
    return path


# ── basic output ──────────────────────────────────────────────────────────────


def test_returns_output_path():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "bundle.tar.gz")
        result = export_bundle_tgz(cfg, output_path=out)
    assert result == out


def test_file_exists_after_export():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "bundle.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        assert os.path.exists(out)


def test_auto_output_path_created():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        orig_cwd = os.getcwd()
        os.chdir(d)
        try:
            out = export_bundle_tgz(cfg)
            assert os.path.exists(out)
        finally:
            os.chdir(orig_cwd)
            if os.path.exists(out):
                os.unlink(out)


# ── archive members ───────────────────────────────────────────────────────────


def _open_bundle(path):
    return tarfile.open(path, "r:gz")


def test_archive_has_manifest():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "bundle.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        with _open_bundle(out) as tf:
            names = tf.getnames()
    assert "manifest.json" in names


def test_archive_has_config_yaml():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "bundle.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        with _open_bundle(out) as tf:
            names = tf.getnames()
    assert "config.rcan.yaml" in names


def test_archive_has_episodes_jsonl():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "bundle.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        with _open_bundle(out) as tf:
            names = tf.getnames()
    assert "episodes.jsonl" in names


def test_archive_has_env_vars():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "bundle.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        with _open_bundle(out) as tf:
            names = tf.getnames()
    assert "env_vars.json" in names


# ── manifest content ──────────────────────────────────────────────────────────


def _read_member(bundle_path: str, member: str) -> bytes:
    with tarfile.open(bundle_path, "r:gz") as tf:
        f = tf.extractfile(member)
        return f.read()


def test_manifest_has_robot_name():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"), robot_name="zippy")
        out = os.path.join(d, "b.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        m = json.loads(_read_member(out, "manifest.json"))
    assert m["robot_name"] == "zippy"


def test_manifest_has_checksums():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "b.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        m = json.loads(_read_member(out, "manifest.json"))
    assert "checksums" in m
    assert "config.rcan.yaml" in m["checksums"]


def test_manifest_has_exported_at():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "b.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        m = json.loads(_read_member(out, "manifest.json"))
    assert "exported_at" in m


def test_manifest_has_rcan_version():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "b.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        m = json.loads(_read_member(out, "manifest.json"))
    assert m.get("rcan_version") == "1.1.0"


# ── config sanitisation ───────────────────────────────────────────────────────


def test_config_api_key_redacted():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "b.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        raw = _read_member(out, "config.rcan.yaml").decode()
    assert "secret123" not in raw
    assert "<REDACTED>" in raw


def test_config_robot_name_preserved():
    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"), robot_name="speedy")
        out = os.path.join(d, "b.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        raw = _read_member(out, "config.rcan.yaml").decode()
    assert "speedy" in raw


# ── env_vars.json ─────────────────────────────────────────────────────────────


def test_env_vars_no_values(monkeypatch):
    from castor.export import export_bundle_tgz

    monkeypatch.setenv("CASTOR_TEST_KEY", "should_not_appear")
    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "b.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        ev = json.loads(_read_member(out, "env_vars.json"))
    assert "env_var_names" in ev
    assert "should_not_appear" not in str(ev)


def test_env_vars_includes_castor_names(monkeypatch):
    from castor.export import export_bundle_tgz

    monkeypatch.setenv("CASTOR_MY_VAR", "hidden_value")
    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "b.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        ev = json.loads(_read_member(out, "env_vars.json"))
    assert "CASTOR_MY_VAR" in ev["env_var_names"]


# ── checksums integrity ───────────────────────────────────────────────────────


def test_checksums_match_actual_content():
    import hashlib

    from castor.export import export_bundle_tgz

    with tempfile.TemporaryDirectory() as d:
        cfg = _write_rcan(os.path.join(d, "r.rcan.yaml"))
        out = os.path.join(d, "b.tar.gz")
        export_bundle_tgz(cfg, output_path=out)
        manifest = json.loads(_read_member(out, "manifest.json"))
        config_bytes = _read_member(out, "config.rcan.yaml")

    expected = hashlib.sha256(config_bytes).hexdigest()
    assert manifest["checksums"]["config.rcan.yaml"] == expected


# ── cli integration ───────────────────────────────────────────────────────────


def test_cli_export_tgz_choice():
    """Verify --format tgz is a valid argparse choice."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "castor.cli", "export", "--help"],
        capture_output=True,
        text=True,
    )
    assert "tgz" in result.stdout


def test_cli_export_episodes_flag():
    """Verify --episodes flag is present in help."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "castor.cli", "export", "--help"],
        capture_output=True,
        text=True,
    )
    assert "--episodes" in result.stdout


def test_manifest_carries_withheld_naming_api_key_by_path(tmp_path):
    """OC-13: a redaction that leaves no record is invisible; name every one."""
    import json
    import tarfile

    import yaml

    from castor.export import export_bundle_tgz

    cfg = {
        "metadata": {"robot_name": "withheld-bot"},
        "agent": {"provider": "anthropic", "model": "m", "api_key": "sk-secret"},
        "channels": [{"type": "telegram", "bot_token": "tok", "webhook_secret": "s"}],
    }
    cfg_path = tmp_path / "robot.rcan.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    out = tmp_path / "bundle.tar.gz"

    export_bundle_tgz(str(cfg_path), str(out))

    with tarfile.open(out, "r:gz") as tf:
        manifest = json.loads(tf.extractfile("manifest.json").read().decode())
        sanitized = yaml.safe_load(tf.extractfile("config.rcan.yaml").read().decode())

    paths = {w["path"] for w in manifest["withheld"]}
    assert "agent.api_key" in paths
    assert "channels[0].bot_token" in paths
    assert "channels[0].webhook_secret" in paths
    by_path = {w["path"]: w["class"] for w in manifest["withheld"]}
    assert by_path["agent.api_key"] == "api_key"
    assert by_path["channels[0].bot_token"] == "token"
    # The values really are gone from the bundle.
    assert sanitized["agent"]["api_key"] == "<REDACTED>"
    # An empty episodes list carries a marker rather than standing alone.
    assert "truncated" in manifest["episodes_note"]
    assert "unavailable" in manifest["episodes_note"]


def test_withheld_is_empty_list_when_nothing_was_removed(tmp_path):
    import json
    import tarfile

    import yaml

    from castor.export import export_bundle_tgz

    cfg = {"metadata": {"robot_name": "clean-bot"}, "agent": {"provider": "anthropic"}}
    cfg_path = tmp_path / "robot.rcan.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    out = tmp_path / "bundle.tar.gz"
    export_bundle_tgz(str(cfg_path), str(out))
    with tarfile.open(out, "r:gz") as tf:
        manifest = json.loads(tf.extractfile("manifest.json").read().decode())
    assert manifest["withheld"] == []
