"""Tests for castor.registry -- ComponentRegistry unified factory."""

from unittest.mock import MagicMock, patch

import pytest

from castor.registry import ComponentRegistry, get_registry


# =====================================================================
# ComponentRegistry.add_provider / add_driver / add_channel
# =====================================================================
class TestComponentRegistryRegistration:
    def test_add_provider_stores_class(self):
        registry = ComponentRegistry()
        cls = MagicMock()
        registry.add_provider("my-ai", cls)
        assert registry._providers["my-ai"] is cls

    def test_add_provider_lowercases_name(self):
        registry = ComponentRegistry()
        cls = MagicMock()
        registry.add_provider("MyAI", cls)
        assert "myai" in registry._providers
        assert "MyAI" not in registry._providers

    def test_add_driver_stores_class(self):
        registry = ComponentRegistry()
        cls = MagicMock()
        registry.add_driver("my-protocol", cls)
        assert registry._drivers["my-protocol"] is cls

    def test_add_channel_stores_class(self):
        registry = ComponentRegistry()
        cls = MagicMock()
        registry.add_channel("my-channel", cls)
        assert registry._channels["my-channel"] is cls

    def test_add_provider_replaces_existing(self):
        registry = ComponentRegistry()
        cls1 = MagicMock()
        cls2 = MagicMock()
        registry.add_provider("p", cls1)
        registry.add_provider("p", cls2)
        assert registry._providers["p"] is cls2


# =====================================================================
# ComponentRegistry.get_provider
# =====================================================================
class TestComponentRegistryGetProvider:
    def test_plugin_provider_takes_precedence(self):
        registry = ComponentRegistry()
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        registry.add_provider("custom", mock_cls)

        config = {"provider": "custom"}
        result = registry.get_provider(config)

        mock_cls.assert_called_once_with(config)
        assert result is mock_instance

    def test_builtin_provider_fallback(self):
        """get_provider falls back to _builtin_get_provider for unknown plugin names."""
        registry = ComponentRegistry()
        mock_builtin = MagicMock()
        mock_builtin.return_value = MagicMock()

        with patch("castor.providers._builtin_get_provider", mock_builtin):
            config = {"provider": "google"}
            registry.get_provider(config)

        mock_builtin.assert_called_once_with(config)

    def test_unknown_provider_raises_via_builtin(self):
        """Unknown provider names propagate ValueError from the built-in factory."""
        registry = ComponentRegistry()
        with pytest.raises(ValueError, match="Unknown AI provider"):
            registry.get_provider({"provider": "nonexistent_xyz"})

    def test_default_provider_resolved(self):
        """When no 'provider' key is set, google is used (default)."""
        registry = ComponentRegistry()
        mock_builtin = MagicMock()
        mock_builtin.return_value = MagicMock()

        with patch("castor.providers._builtin_get_provider", mock_builtin):
            registry.get_provider({})

        mock_builtin.assert_called_once_with({})


# =====================================================================
# ComponentRegistry.get_driver
# =====================================================================
class TestComponentRegistryGetDriver:
    def test_returns_none_when_no_drivers(self):
        registry = ComponentRegistry()
        result = registry.get_driver({})
        assert result is None

    def test_returns_none_when_drivers_empty_list(self):
        registry = ComponentRegistry()
        result = registry.get_driver({"drivers": []})
        assert result is None

    def test_plugin_driver_takes_precedence(self):
        registry = ComponentRegistry()
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        registry.add_driver("my-protocol", mock_cls)

        config = {"drivers": [{"protocol": "my-protocol"}]}
        result = registry.get_driver(config)

        mock_cls.assert_called_once_with({"protocol": "my-protocol"})
        assert result is mock_instance

    def test_builtin_driver_fallback(self):
        """get_driver falls back to _builtin_get_driver for unknown protocols."""
        registry = ComponentRegistry()
        mock_builtin = MagicMock()
        mock_builtin.return_value = None

        with patch("castor.main._builtin_get_driver", mock_builtin):
            config = {"drivers": [{"protocol": "pca9685"}]}
            registry.get_driver(config)

        mock_builtin.assert_called_once_with(config)


# =====================================================================
# ComponentRegistry.create_channel
# =====================================================================
class TestComponentRegistryCreateChannel:
    def test_plugin_channel_takes_precedence(self):
        registry = ComponentRegistry()

        class FakeChannel:
            def __init__(self, config, on_message=None):
                self.config = config
                self._on_message = on_message

        registry.add_channel("fakechan", FakeChannel)

        with patch("castor.auth.resolve_channel_credentials", return_value={"tok": "abc"}):
            ch = registry.create_channel("fakechan", config={"extra": "val"})

        assert isinstance(ch, FakeChannel)
        assert ch.config["tok"] == "abc"
        assert ch.config["extra"] == "val"

    def test_builtin_channel_fallback(self):
        """create_channel falls back to _builtin_create_channel for built-ins."""
        registry = ComponentRegistry()
        mock_builtin = MagicMock()
        mock_builtin.return_value = MagicMock()

        with patch("castor.channels._builtin_create_channel", mock_builtin):
            registry.create_channel("telegram", config={"x": 1})

        mock_builtin.assert_called_once_with("telegram", {"x": 1}, None)

    def test_unknown_channel_raises(self):
        registry = ComponentRegistry()
        with pytest.raises(ValueError, match="Unknown channel"):
            registry.create_channel("nonexistent_xyz_channel")

    def test_on_message_passed_to_plugin_channel(self):
        registry = ComponentRegistry()

        class FakeChannel:
            def __init__(self, config, on_message=None):
                self._cb = on_message

        registry.add_channel("fakechan2", FakeChannel)
        cb = MagicMock()

        with patch("castor.auth.resolve_channel_credentials", return_value={}):
            ch = registry.create_channel("fakechan2", on_message=cb)

        assert ch._cb is cb


# =====================================================================
# ComponentRegistry introspection (list_*)
# =====================================================================
class TestComponentRegistryListMethods:
    def test_list_providers_includes_builtins(self):
        registry = ComponentRegistry()
        providers = registry.list_providers()
        assert "google" in providers
        assert "openai" in providers
        assert "anthropic" in providers
        assert "ollama" in providers

    def test_list_providers_includes_plugin(self):
        registry = ComponentRegistry()
        registry.add_provider("my-custom-ai", MagicMock())
        assert "my-custom-ai" in registry.list_providers()

    def test_list_drivers_includes_builtins(self):
        registry = ComponentRegistry()
        drivers = registry.list_drivers()
        assert "pca9685" in drivers
        assert "dynamixel" in drivers

    def test_list_drivers_includes_plugin(self):
        registry = ComponentRegistry()
        registry.add_driver("my-servo", MagicMock())
        assert "my-servo" in registry.list_drivers()

    def test_list_channels_includes_plugin(self):
        registry = ComponentRegistry()
        registry.add_channel("my-chat", MagicMock())
        with patch("castor.channels.get_available_channels", return_value=["telegram"]):
            channels = registry.list_channels()
        assert "my-chat" in channels
        assert "telegram" in channels

    def test_list_plugin_providers_empty_initially(self):
        registry = ComponentRegistry()
        assert registry.list_plugin_providers() == []

    def test_list_plugin_drivers_empty_initially(self):
        registry = ComponentRegistry()
        assert registry.list_plugin_drivers() == []

    def test_list_plugin_channels_empty_initially(self):
        registry = ComponentRegistry()
        assert registry.list_plugin_channels() == []

    def test_list_plugin_providers_after_add(self):
        registry = ComponentRegistry()
        registry.add_provider("alpha", MagicMock())
        registry.add_provider("beta", MagicMock())
        assert sorted(registry.list_plugin_providers()) == ["alpha", "beta"]


# =====================================================================
# get_registry singleton
# =====================================================================
class TestGetRegistry:
    def test_returns_component_registry_instance(self):
        reg = get_registry()
        assert isinstance(reg, ComponentRegistry)

    def test_returns_same_instance(self):
        assert get_registry() is get_registry()


# =====================================================================
# PluginRegistry delegation to ComponentRegistry
# =====================================================================
class TestPluginRegistryDelegation:
    def test_plugin_add_provider_delegates_to_component_registry(self):
        from castor.plugins import PluginRegistry

        plugin_reg = PluginRegistry()
        mock_cls = MagicMock()

        fresh_component_reg = ComponentRegistry()
        with patch("castor.registry.get_registry", return_value=fresh_component_reg):
            plugin_reg.add_provider("test-ai", mock_cls)
            assert "test-ai" in fresh_component_reg._providers
            assert fresh_component_reg._providers["test-ai"] is mock_cls

    def test_plugin_add_driver_delegates_to_component_registry(self):
        from castor.plugins import PluginRegistry

        plugin_reg = PluginRegistry()
        mock_cls = MagicMock()

        fresh_component_reg = ComponentRegistry()
        with patch("castor.registry.get_registry", return_value=fresh_component_reg):
            plugin_reg.add_driver("test-protocol", mock_cls)
            assert "test-protocol" in fresh_component_reg._drivers
            assert fresh_component_reg._drivers["test-protocol"] is mock_cls

    def test_plugin_add_channel_delegates_to_component_registry(self):
        from castor.plugins import PluginRegistry

        plugin_reg = PluginRegistry()
        mock_cls = MagicMock()

        fresh_component_reg = ComponentRegistry()
        with patch("castor.registry.get_registry", return_value=fresh_component_reg):
            plugin_reg.add_channel("test-chan", mock_cls)
            assert "test-chan" in fresh_component_reg._channels
            assert fresh_component_reg._channels["test-chan"] is mock_cls

    def test_plugin_register_callback_can_use_all_methods(self, tmp_path):
        """A plugin's register() function can call add_provider, add_driver, add_channel."""
        import hashlib
        import json

        from castor.plugins import PluginRegistry, load_plugins

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        plugin_code = (
            "from unittest.mock import MagicMock\n"
            "def register(registry):\n"
            "    registry.add_provider('my-provider', MagicMock)\n"
            "    registry.add_driver('my-driver', MagicMock)\n"
            "    registry.add_channel('my-channel', MagicMock)\n"
        )
        py_path = plugins_dir / "multi_register.py"
        py_path.write_text(plugin_code)
        sha = hashlib.sha256(plugin_code.encode()).hexdigest()
        manifest = {
            "name": "multi_register",
            "version": "1.0.0",
            "author": "Test",
            "hooks": [],
            "commands": [],
            "sha256": sha,
        }
        (plugins_dir / "multi_register.json").write_text(json.dumps(manifest))

        fresh_plugin_reg = PluginRegistry()
        fresh_component_reg = ComponentRegistry()

        with (
            patch("castor.plugins._PLUGINS_DIR", str(plugins_dir)),
            patch("castor.plugins._registry", fresh_plugin_reg),
            patch("castor.registry.get_registry", return_value=fresh_component_reg),
        ):
            load_plugins()

        assert "my-provider" in fresh_component_reg._providers
        assert "my-driver" in fresh_component_reg._drivers
        assert "my-channel" in fresh_component_reg._channels


# =====================================================================
# Existing factory thin-wrapper contracts
# =====================================================================
class TestExistingFactoryThinWrappers:
    def test_get_provider_delegates_to_registry(self):
        """castor.providers.get_provider calls registry.get_provider."""
        from castor.providers import get_provider

        mock_registry = MagicMock()
        mock_registry.get_provider.return_value = MagicMock()

        with patch("castor.registry.get_registry", return_value=mock_registry):
            config = {"provider": "google"}
            get_provider(config)

        mock_registry.get_provider.assert_called_once_with(config)

    def test_get_driver_delegates_to_registry(self):
        """castor.main.get_driver calls registry.get_driver."""
        from castor.main import get_driver

        mock_registry = MagicMock()
        mock_registry.get_driver.return_value = None

        with patch("castor.registry.get_registry", return_value=mock_registry):
            config = {"drivers": [{"protocol": "pca9685"}]}
            get_driver(config)

        mock_registry.get_driver.assert_called_once_with(config)

    def test_create_channel_delegates_to_registry(self):
        """castor.channels.create_channel calls registry.create_channel."""
        from castor.channels import create_channel

        mock_registry = MagicMock()
        mock_registry.create_channel.return_value = MagicMock()

        with patch("castor.registry.get_registry", return_value=mock_registry):
            create_channel("telegram", config={"x": 1})

        mock_registry.create_channel.assert_called_once_with("telegram", {"x": 1}, None)
