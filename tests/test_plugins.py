"""Tests for castor.plugins -- extensible hook system."""

import hashlib
import json
from unittest.mock import MagicMock, patch

from castor.plugins import PluginRegistry, list_plugins, load_plugins


def _make_manifest(plugins_dir, name, extra=None):
    """Write a minimal valid plugin.json manifest."""
    manifest = {
        "name": name,
        "version": "1.0.0",
        "author": "Test Author",
        "hooks": [],
        "commands": [],
    }
    if extra:
        manifest.update(extra)
    (plugins_dir / f"{name}.json").write_text(json.dumps(manifest))
    return manifest


# =====================================================================
# PluginRegistry.add_command
# =====================================================================
class TestPluginRegistryAddCommand:
    def test_add_command_registers_command(self):
        registry = PluginRegistry()
        handler = MagicMock()
        registry.add_command("my-cmd", handler, help="My command")

        assert "my-cmd" in registry.commands
        stored_handler, stored_help = registry.commands["my-cmd"]
        assert stored_handler is handler
        assert stored_help == "My command"

    def test_add_multiple_commands(self):
        registry = PluginRegistry()
        handler_a = MagicMock()
        handler_b = MagicMock()
        registry.add_command("cmd-a", handler_a, help="A")
        registry.add_command("cmd-b", handler_b, help="B")

        assert len(registry.commands) == 2


# =====================================================================
# PluginRegistry.add_hook
# =====================================================================
class TestPluginRegistryAddHook:
    def test_add_hook_registers_hook(self):
        registry = PluginRegistry()
        fn = MagicMock()
        registry.add_hook("on_startup", fn)

        assert fn in registry.hooks["on_startup"]

    def test_add_hook_unknown_event_does_not_register(self):
        registry = PluginRegistry()
        fn = MagicMock()
        registry.add_hook("on_nonexistent_event", fn)

        # Should not appear in any known hook lists
        for event_fns in registry.hooks.values():
            assert fn not in event_fns


# =====================================================================
# PluginRegistry.fire
# =====================================================================
class TestPluginRegistryFire:
    def test_fire_calls_hook_functions(self):
        registry = PluginRegistry()
        fn1 = MagicMock()
        fn2 = MagicMock()
        registry.add_hook("on_startup", fn1)
        registry.add_hook("on_startup", fn2)

        config = {"test": True}
        registry.fire("on_startup", config)

        fn1.assert_called_once_with(config)
        fn2.assert_called_once_with(config)

    def test_fire_catches_and_logs_hook_exception(self):
        registry = PluginRegistry()
        bad_fn = MagicMock(side_effect=RuntimeError("boom"))
        good_fn = MagicMock()

        registry.add_hook("on_startup", bad_fn)
        registry.add_hook("on_startup", good_fn)

        # Should not raise
        registry.fire("on_startup")

        bad_fn.assert_called_once()
        good_fn.assert_called_once()

    def test_fire_unknown_event_does_nothing(self):
        registry = PluginRegistry()
        # Should not raise
        registry.fire("nonexistent_event", "arg1", key="val")

    def test_fire_with_kwargs(self):
        registry = PluginRegistry()
        fn = MagicMock()
        registry.add_hook("on_action", fn)

        registry.fire("on_action", {"type": "move"}, source="brain")
        fn.assert_called_once_with({"type": "move"}, source="brain")


# =====================================================================
# load_plugins -- no plugins dir
# =====================================================================
class TestLoadPlugins:
    def test_load_plugins_no_dir_returns_registry(self, tmp_path):
        nonexistent = str(tmp_path / "no_plugins_here")
        with patch("castor.plugins._PLUGINS_DIR", nonexistent):
            result = load_plugins()
        assert isinstance(result, PluginRegistry)

    def test_load_plugins_with_valid_plugin(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        # Create a simple plugin file
        plugin_code = (
            "def register(registry):\n"
            "    registry.add_command('hello', lambda args: None, help='Say hello')\n"
        )
        (plugins_dir / "hello_plugin.py").write_text(plugin_code)
        _make_manifest(plugins_dir, "hello_plugin", {"commands": ["hello"]})

        # Use a fresh registry for this test
        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
        ):
            load_plugins()

        assert "hello" in fresh_registry.commands

    def test_load_plugins_skips_plugin_without_manifest(self, tmp_path):
        """A plugin with no plugin.json must be skipped (security requirement)."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        plugin_code = (
            "def register(registry):\n"
            "    registry.add_command('secret', lambda args: None)\n"
        )
        (plugins_dir / "no_manifest.py").write_text(plugin_code)
        # Deliberately no .json manifest

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
        ):
            load_plugins()

        # Plugin must NOT be loaded without a manifest
        assert "secret" not in fresh_registry.commands
        assert "no_manifest" not in fresh_registry._loaded

    def test_load_plugins_skips_plugin_with_missing_manifest_fields(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "bad_manifest.py").write_text("def register(r): pass")
        # Manifest missing required fields
        (plugins_dir / "bad_manifest.json").write_text(json.dumps({"name": "bad"}))

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
        ):
            load_plugins()

        assert "bad_manifest" not in fresh_registry._loaded

    def test_load_plugins_skips_plugin_with_invalid_json_manifest(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "bad_json.py").write_text("def register(r): pass")
        (plugins_dir / "bad_json.json").write_text("{not valid json")

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
        ):
            load_plugins()

        assert "bad_json" not in fresh_registry._loaded

    def test_load_plugins_sha256_match_loads_plugin(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        plugin_code = "def register(r): r.add_command('sha_ok', None)\n"
        py_path = plugins_dir / "sha_ok.py"
        py_path.write_text(plugin_code)
        sha = hashlib.sha256(plugin_code.encode()).hexdigest()
        _make_manifest(plugins_dir, "sha_ok", {"sha256": sha, "commands": ["sha_ok"]})

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
        ):
            load_plugins()

        assert "sha_ok" in fresh_registry.commands

    def test_load_plugins_sha256_mismatch_skips_plugin(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        plugin_code = "def register(r): r.add_command('bad_sha', None)\n"
        py_path = plugins_dir / "sha_mismatch.py"
        py_path.write_text(plugin_code)
        _make_manifest(plugins_dir, "sha_mismatch", {"sha256": "deadbeef" * 8})

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
        ):
            load_plugins()

        assert "sha_mismatch" not in fresh_registry._loaded

    def test_load_plugins_skips_underscore_files(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "_internal.py").write_text("def register(r): r.add_command('bad', None)")
        (plugins_dir / "__init__.py").write_text("")

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
        ):
            load_plugins()

        assert len(fresh_registry.commands) == 0

    def test_load_plugins_handles_broken_plugin(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "broken.py").write_text("raise RuntimeError('broken plugin')")
        _make_manifest(plugins_dir, "broken")

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
        ):
            # Should not raise
            result = load_plugins()

        assert isinstance(result, PluginRegistry)


# =====================================================================
# list_plugins
# =====================================================================
class TestListPlugins:
    def test_list_plugins_returns_correct_format(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "motor_helper.py").write_text("def register(r): pass")
        _make_manifest(plugins_dir, "motor_helper")
        (plugins_dir / "sensor_log.py").write_text("def register(r): pass")
        _make_manifest(plugins_dir, "sensor_log")
        (plugins_dir / "readme.txt").write_text("not a plugin")
        (plugins_dir / "_hidden.py").write_text("def register(r): pass")

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
            patch("castor.plugins._PLUGINS_LOCK", str(tmp_path / "plugins.lock")),
        ):
            result = list_plugins()

        assert len(result) == 2
        names = [p["name"] for p in result]
        assert "motor_helper" in names
        assert "sensor_log" in names

        for p in result:
            assert "name" in p
            assert "path" in p
            assert "loaded" in p
            assert "has_manifest" in p
            assert "manifest" in p
            assert "provenance" in p
            assert p["loaded"] is False  # Not loaded yet
            assert p["has_manifest"] is True

    def test_list_plugins_no_dir_returns_empty(self, tmp_path):
        with patch("castor.plugins._PLUGINS_DIR", str(tmp_path / "nonexistent")):
            result = list_plugins()
        assert result == []

    def test_list_plugins_missing_manifest_flagged(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "no_manifest.py").write_text("def register(r): pass")
        # No .json manifest

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
            patch("castor.plugins._PLUGINS_LOCK", str(tmp_path / "plugins.lock")),
        ):
            result = list_plugins()

        assert len(result) == 1
        assert result[0]["has_manifest"] is False
        assert result[0]["manifest"] is None


# =====================================================================
# install_plugin
# =====================================================================
class TestInstallPlugin:
    def _make_plugin_files(self, tmp_path, name="my_plugin", with_sha=False):
        """Return (py_bytes, json_bytes) for a minimal plugin."""
        code = b"def register(r): r.add_command('hello', None)\n"
        sha = hashlib.sha256(code).hexdigest()
        manifest = {
            "name": name,
            "version": "1.0.0",
            "author": "Test",
            "hooks": [],
            "commands": ["hello"],
        }
        if with_sha:
            manifest["sha256"] = sha
        return code, json.dumps(manifest).encode(), sha

    def test_install_from_local_path(self, tmp_path):
        from castor.plugins import install_plugin

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        code, manifest_bytes, _ = self._make_plugin_files(tmp_path)
        (src_dir / "my_plugin.py").write_bytes(code)
        (src_dir / "my_plugin.json").write_bytes(manifest_bytes)

        plugins_dir = tmp_path / "plugins"
        lock_path = tmp_path / "plugins.lock"

        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._PLUGINS_LOCK", str(lock_path)),
        ):
            result = install_plugin(str(src_dir / "my_plugin.py"))

        assert result is True
        assert (plugins_dir / "my_plugin.py").exists()
        assert (plugins_dir / "my_plugin.json").exists()
        assert lock_path.exists()
        lock = json.loads(lock_path.read_text())
        assert "my_plugin" in lock
        assert "source" in lock["my_plugin"]
        assert "sha256" in lock["my_plugin"]
        assert "installed_at" in lock["my_plugin"]

    def test_install_rejects_missing_manifest(self, tmp_path):
        from castor.plugins import install_plugin

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "my_plugin.py").write_bytes(b"def register(r): pass\n")
        # No manifest file

        plugins_dir = tmp_path / "plugins"
        lock_path = tmp_path / "plugins.lock"

        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._PLUGINS_LOCK", str(lock_path)),
        ):
            result = install_plugin(str(src_dir / "my_plugin.py"))

        assert result is False
        assert not (plugins_dir / "my_plugin.py").exists()

    def test_install_rejects_manifest_missing_fields(self, tmp_path):
        from castor.plugins import install_plugin

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "my_plugin.py").write_bytes(b"def register(r): pass\n")
        (src_dir / "my_plugin.json").write_text(json.dumps({"name": "incomplete"}))

        plugins_dir = tmp_path / "plugins"
        lock_path = tmp_path / "plugins.lock"

        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._PLUGINS_LOCK", str(lock_path)),
        ):
            result = install_plugin(str(src_dir / "my_plugin.py"))

        assert result is False
        assert not (plugins_dir / "my_plugin.py").exists()

    def test_install_rejects_sha256_mismatch(self, tmp_path):
        from castor.plugins import install_plugin

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        code = b"def register(r): pass\n"
        (src_dir / "my_plugin.py").write_bytes(code)
        manifest = {
            "name": "my_plugin",
            "version": "1.0.0",
            "author": "Attacker",
            "hooks": [],
            "commands": [],
            "sha256": "deadbeef" * 8,  # wrong hash
        }
        (src_dir / "my_plugin.json").write_text(json.dumps(manifest))

        plugins_dir = tmp_path / "plugins"
        lock_path = tmp_path / "plugins.lock"

        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._PLUGINS_LOCK", str(lock_path)),
        ):
            result = install_plugin(str(src_dir / "my_plugin.py"))

        assert result is False
        assert not (plugins_dir / "my_plugin.py").exists()

    def test_install_with_correct_sha256_succeeds(self, tmp_path):
        from castor.plugins import install_plugin

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        code, manifest_bytes, sha = self._make_plugin_files(tmp_path, with_sha=True)
        (src_dir / "my_plugin.py").write_bytes(code)
        (src_dir / "my_plugin.json").write_bytes(manifest_bytes)

        plugins_dir = tmp_path / "plugins"
        lock_path = tmp_path / "plugins.lock"

        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._PLUGINS_LOCK", str(lock_path)),
        ):
            result = install_plugin(str(src_dir / "my_plugin.py"))

        assert result is True
        lock = json.loads(lock_path.read_text())
        assert lock["my_plugin"]["sha256"] == sha

    def test_install_rejects_non_py_source(self, tmp_path):
        from castor.plugins import install_plugin

        plugins_dir = tmp_path / "plugins"
        lock_path = tmp_path / "plugins.lock"

        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._PLUGINS_LOCK", str(lock_path)),
        ):
            result = install_plugin("/some/path/plugin.txt")

        assert result is False

