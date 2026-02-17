"""
OpenCastor Plugins -- extensible hook system for custom commands and drivers.

Users drop Python files into ``~/.opencastor/plugins/`` and they are
auto-loaded at CLI startup. Plugins can register:
  - Custom CLI commands
  - Custom drivers
  - Custom providers
  - Startup/shutdown hooks

Plugin file format::

    # ~/.opencastor/plugins/my_plugin.py

    def register(registry):
        registry.add_command("my-cmd", my_handler, help="My custom command")
        registry.add_hook("on_startup", my_startup_fn)

    def my_handler(args):
        print("Hello from my plugin!")

    def my_startup_fn(config):
        print("Robot booting up!")
"""

import importlib.util
import logging
import os

logger = logging.getLogger("OpenCastor.Plugins")

_PLUGINS_DIR = os.path.expanduser("~/.opencastor/plugins")


class PluginRegistry:
    """Registry for plugin-provided commands and hooks."""

    def __init__(self):
        self.commands = {}  # name -> (handler, help_text)
        self.hooks = {
            "on_startup": [],
            "on_shutdown": [],
            "on_action": [],
            "on_error": [],
        }
        self._loaded = []

    def add_command(self, name: str, handler, help: str = ""):
        """Register a custom CLI command."""
        self.commands[name] = (handler, help)
        logger.debug(f"Plugin command registered: {name}")

    def add_hook(self, event: str, fn):
        """Register a hook function for an event."""
        if event in self.hooks:
            self.hooks[event].append(fn)
            logger.debug(f"Plugin hook registered: {event}")
        else:
            logger.warning(f"Unknown hook event: {event}")

    def fire(self, event: str, *args, **kwargs):
        """Fire all hooks for an event."""
        for fn in self.hooks.get(event, []):
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                logger.warning(f"Plugin hook error ({event}): {exc}")


# Global registry instance
_registry = PluginRegistry()


def get_registry() -> PluginRegistry:
    """Get the global plugin registry."""
    return _registry


def load_plugins() -> PluginRegistry:
    """Load all plugins from the plugins directory.

    Returns the populated PluginRegistry.
    """
    if not os.path.isdir(_PLUGINS_DIR):
        return _registry

    for filename in sorted(os.listdir(_PLUGINS_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        filepath = os.path.join(_PLUGINS_DIR, filename)
        plugin_name = filename[:-3]  # strip .py

        try:
            spec = importlib.util.spec_from_file_location(
                f"opencastor_plugin_{plugin_name}", filepath
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Call register() if it exists
            if hasattr(module, "register"):
                module.register(_registry)
                _registry._loaded.append(plugin_name)
                logger.info(f"Plugin loaded: {plugin_name}")
            else:
                logger.debug(f"Plugin {plugin_name} has no register() function")

        except Exception as exc:
            logger.warning(f"Failed to load plugin {plugin_name}: {exc}")

    return _registry


def list_plugins() -> list:
    """List all available and loaded plugins."""
    plugins = []

    if not os.path.isdir(_PLUGINS_DIR):
        return plugins

    for filename in sorted(os.listdir(_PLUGINS_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        name = filename[:-3]
        plugins.append({
            "name": name,
            "path": os.path.join(_PLUGINS_DIR, filename),
            "loaded": name in _registry._loaded,
        })

    return plugins


def print_plugins(plugins: list):
    """Print plugin list."""
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if has_rich:
        console.print("\n[bold cyan]  OpenCastor Plugins[/]")
        console.print(f"  Directory: [dim]{_PLUGINS_DIR}[/]\n")
    else:
        print("\n  OpenCastor Plugins")
        print(f"  Directory: {_PLUGINS_DIR}\n")

    if not plugins:
        msg = (
            "  No plugins found.\n"
            f"  Create a plugin: {_PLUGINS_DIR}/my_plugin.py\n"
        )
        if has_rich:
            console.print(f"  [dim]{msg}[/]")
        else:
            print(msg)
        return

    if has_rich:
        table = Table(show_header=True, box=None)
        table.add_column("Plugin", style="bold")
        table.add_column("Status")
        table.add_column("Path", style="dim")

        for p in plugins:
            status = "[green]loaded[/]" if p["loaded"] else "[dim]available[/]"
            table.add_row(p["name"], status, p["path"])

        console.print(table)
    else:
        for p in plugins:
            status = "loaded" if p["loaded"] else "available"
            print(f"    {p['name']:20s} {status:10s} {p['path']}")

    # Show registered commands
    if _registry.commands:
        if has_rich:
            console.print("\n  Plugin commands:")
            for name, (_, help_text) in _registry.commands.items():
                console.print(f"    [cyan]{name}[/]  {help_text}")
        else:
            print("\n  Plugin commands:")
            for name, (_, help_text) in _registry.commands.items():
                print(f"    {name}  {help_text}")

    print()
