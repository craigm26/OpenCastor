"""Tests for castor.plugins -- extensible hook system."""

from unittest.mock import MagicMock, patch

from castor.plugins import PluginRegistry, list_plugins, load_plugins


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

        # Use a fresh registry for this test
        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
        ):
            load_plugins()

        assert "hello" in fresh_registry.commands

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
        (plugins_dir / "sensor_log.py").write_text("def register(r): pass")
        (plugins_dir / "readme.txt").write_text("not a plugin")
        (plugins_dir / "_hidden.py").write_text("def register(r): pass")

        fresh_registry = PluginRegistry()
        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_registry),
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
            assert p["loaded"] is False  # Not loaded yet

    def test_list_plugins_no_dir_returns_empty(self, tmp_path):
        with patch("castor.plugins._PLUGINS_DIR", str(tmp_path / "nonexistent")):
            result = list_plugins()
        assert result == []
