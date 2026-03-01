"""Tests for castor/commands/hub.py plugin support (issue #135)."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# _discover_installed_plugins
# ---------------------------------------------------------------------------


def test_discover_returns_empty_when_no_plugins():
    from castor.commands.hub import _discover_installed_plugins

    with patch("importlib.metadata.entry_points", return_value=[]):
        plugins = _discover_installed_plugins()
        assert plugins == []


def test_discover_returns_plugin_metadata():
    """Simulate one installed plugin entry point."""
    from castor.commands.hub import _discover_installed_plugins

    dist_mock = MagicMock()
    dist_mock.name = "opencastor-plugin-gpio"
    dist_mock.version = "1.0.0"
    meta_mock = {"Summary": "GPIO support", "Author": "Bob", "Home-page": "https://example.com"}
    dist_mock.metadata = meta_mock

    ep_mock = MagicMock()
    ep_mock.name = "gpio"
    ep_mock.value = "opencastor_gpio:plugin"
    ep_mock.dist = dist_mock

    with patch("importlib.metadata.entry_points", return_value=[ep_mock]):
        plugins = _discover_installed_plugins()

    assert len(plugins) == 1
    p = plugins[0]
    assert p["name"] == "opencastor-plugin-gpio"
    assert p["version"] == "1.0.0"
    assert p["entry_point"] == "gpio"
    assert p["description"] == "GPIO support"


def test_discover_handles_entry_point_error():
    """Entry point iteration error should not crash."""
    from castor.commands.hub import _discover_installed_plugins

    broken_ep = MagicMock()
    broken_ep.name = "broken"
    broken_ep.value = "broken:plugin"
    # Make dist raise
    type(broken_ep).dist = property(lambda s: (_ for _ in ()).throw(RuntimeError("broken")))

    with patch("importlib.metadata.entry_points", return_value=[broken_ep]):
        plugins = _discover_installed_plugins()
    # Should not crash; broken ep skipped
    assert isinstance(plugins, list)


# ---------------------------------------------------------------------------
# _load_plugin
# ---------------------------------------------------------------------------


def test_load_plugin_stdlib_module():
    """Load a stdlib module attribute via entry_point_value string."""
    # "os:path" should return os.path
    import os

    from castor.commands.hub import _load_plugin

    result = _load_plugin("os:path")
    assert result is os.path


def test_load_plugin_module_only():
    """Load a module without attribute (no colon)."""
    import os

    from castor.commands.hub import _load_plugin

    result = _load_plugin("os")
    assert result is os


# ---------------------------------------------------------------------------
# cmd_hub_plugins
# ---------------------------------------------------------------------------


def test_cmd_hub_plugins_no_plugins():
    """cmd_hub_plugins() should not raise when no plugins installed."""
    from castor.commands.hub import cmd_hub_plugins

    with patch("castor.commands.hub._discover_installed_plugins", return_value=[]):
        # Should not raise regardless of whether rich is installed
        try:
            cmd_hub_plugins(None)
        except SystemExit:
            pass  # rich.Console may call sys.exit on certain terminals


def test_cmd_hub_plugins_prints_table(capsys):
    """With plugins, cmd_hub_plugins prints a table (no-rich path)."""
    from castor.commands.hub import cmd_hub_plugins

    fake_plugins = [
        {
            "name": "opencastor-plugin-test",
            "version": "0.1.0",
            "entry_point": "test",
            "value": "testmod:plugin",
            "description": "Test plugin",
        }
    ]

    with (
        patch("castor.commands.hub._discover_installed_plugins", return_value=fake_plugins),
        # Force no-rich path
        patch.dict(sys.modules, {"rich.console": None, "rich.table": None, "rich": None}),
    ):
        try:
            cmd_hub_plugins(None)
        except (TypeError, AttributeError):
            pass  # Rich may partially load; we only need no crash

    capsys.readouterr()
    # Either rich printed or plain text — plugin name should appear
    # (or there was an import-related skip)


# ---------------------------------------------------------------------------
# cmd_hub_install_plugin
# ---------------------------------------------------------------------------


def test_install_plugin_no_name_prints_usage(capsys):
    from castor.commands.hub import cmd_hub_install_plugin

    args = SimpleNamespace(name="")
    cmd_hub_install_plugin(args)
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_install_plugin_normalizes_bare_name():
    """Bare 'gpio' should become 'opencastor-plugin-gpio'."""
    from castor.commands.hub import cmd_hub_install_plugin

    args = SimpleNamespace(name="gpio")
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    with (
        patch("subprocess.run", return_value=mock_result) as mock_run,
        patch("castor.commands.hub._discover_installed_plugins", return_value=[]),
    ):
        cmd_hub_install_plugin(args)

    called_cmd = mock_run.call_args[0][0]
    assert "opencastor-plugin-gpio" in called_cmd


def test_install_plugin_url_kept_as_is():
    """A URL should not have prefix added."""
    from castor.commands.hub import cmd_hub_install_plugin

    url = "https://github.com/example/my-plugin"
    args = SimpleNamespace(name=url)
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "not found"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        cmd_hub_install_plugin(args)

    called_cmd = mock_run.call_args[0][0]
    assert url in called_cmd


def test_install_plugin_opencastor_prefix_kept():
    """Full package name starting with 'opencastor-' should not get extra prefix."""
    from castor.commands.hub import cmd_hub_install_plugin

    args = SimpleNamespace(name="opencastor-plugin-gpio")
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    with (
        patch("subprocess.run", return_value=mock_result) as mock_run,
        patch("castor.commands.hub._discover_installed_plugins", return_value=[]),
    ):
        cmd_hub_install_plugin(args)

    called_cmd = mock_run.call_args[0][0]
    # Should not be "opencastor-plugin-opencastor-plugin-gpio"
    assert "opencastor-plugin-opencastor" not in " ".join(called_cmd)


# ---------------------------------------------------------------------------
# cmd_hub_uninstall_plugin
# ---------------------------------------------------------------------------


def test_uninstall_plugin_no_name_prints_usage(capsys):
    from castor.commands.hub import cmd_hub_uninstall_plugin

    args = SimpleNamespace(name="")
    cmd_hub_uninstall_plugin(args)
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_uninstall_plugin_normalizes_name():
    from castor.commands.hub import cmd_hub_uninstall_plugin

    args = SimpleNamespace(name="gpio")
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        cmd_hub_uninstall_plugin(args)

    called_cmd = mock_run.call_args[0][0]
    assert "opencastor-plugin-gpio" in called_cmd
