"""
OpenCastor Plugins -- extensible hook system for custom commands and drivers.

Users drop Python files into ``~/.opencastor/plugins/`` and they are
auto-loaded at CLI startup. Each plugin **must** include a ``plugin.json``
manifest alongside the ``.py`` file. Plugins can register:
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

Manifest format (``plugin.json`` in the same directory)::

    {
        "name": "my_plugin",
        "version": "1.0.0",
        "author": "Your Name",
        "hooks": ["on_startup"],
        "commands": ["my-cmd"],
        "sha256": "<hex digest of my_plugin.py -- optional>"
    }

Install a plugin (records provenance in ``~/.opencastor/plugins.lock``)::

    castor plugin install https://example.com/my_plugin.py
    castor plugin install /local/path/my_plugin.py
"""

import hashlib
import importlib.util
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger("OpenCastor.Plugins")

_PLUGINS_DIR = os.path.expanduser("~/.opencastor/plugins")
_PLUGINS_LOCK = os.path.expanduser("~/.opencastor/plugins.lock")

# Required keys in every plugin.json manifest
_MANIFEST_REQUIRED_FIELDS = {"name", "version", "author", "hooks", "commands"}


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

    def add_provider(self, name: str, cls: type) -> None:
        """Register a custom AI provider with the component registry."""
        from castor.registry import get_registry

        get_registry().add_provider(name, cls)
        logger.debug(f"Plugin provider registered: {name}")

    def add_driver(self, name: str, cls: type) -> None:
        """Register a custom hardware driver with the component registry."""
        from castor.registry import get_registry

        get_registry().add_driver(name, cls)
        logger.debug(f"Plugin driver registered: {name}")

    def add_channel(self, name: str, cls: type) -> None:
        """Register a custom messaging channel with the component registry."""
        from castor.registry import get_registry

        get_registry().add_channel(name, cls)
        logger.debug(f"Plugin channel registered: {name}")

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


def _sha256_file(filepath: str) -> str:
    """Return the hex-encoded SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_manifest(filepath: str) -> dict | None:
    """Validate the ``plugin.json`` manifest that must accompany *filepath*.

    Returns the parsed manifest dict on success, or ``None`` when the plugin
    should be skipped.  Validation steps:

    1. The manifest file (same stem, ``.json`` extension) must exist.
    2. It must be valid JSON and contain all required fields.
    3. If a ``sha256`` key is present its value must match the ``.py`` file.
    """
    manifest_path = os.path.splitext(filepath)[0] + ".json"

    if not os.path.isfile(manifest_path):
        logger.warning(
            "Plugin '%s' has no manifest (%s). Skipping. "
            "Run 'castor plugin install' to install plugins securely.",
            os.path.basename(filepath),
            os.path.basename(manifest_path),
        )
        return None

    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Plugin manifest '%s' is invalid JSON: %s. Skipping.", manifest_path, exc)
        return None

    missing = _MANIFEST_REQUIRED_FIELDS - manifest.keys()
    if missing:
        logger.warning(
            "Plugin manifest '%s' is missing required fields: %s. Skipping.",
            manifest_path,
            sorted(missing),
        )
        return None

    # Optional SHA-256 integrity check
    if "sha256" in manifest:
        actual = _sha256_file(filepath)
        if actual != manifest["sha256"]:
            logger.warning(
                "Plugin '%s' SHA-256 mismatch (expected %s, got %s). Skipping.",
                os.path.basename(filepath),
                manifest["sha256"],
                actual,
            )
            return None

    return manifest


def _read_lock() -> dict:
    """Return the parsed plugins.lock, or an empty dict if it does not exist."""
    if os.path.isfile(_PLUGINS_LOCK):
        try:
            with open(_PLUGINS_LOCK, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_lock(data: dict) -> None:
    """Persist *data* to the plugins.lock file."""
    os.makedirs(os.path.dirname(_PLUGINS_LOCK), exist_ok=True)
    with open(_PLUGINS_LOCK, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def install_plugin(source: str) -> bool:
    """Install a plugin from a URL or local file path.

    Steps:

    1. Fetch the ``.py`` file (and the accompanying ``plugin.json`` manifest).
    2. Validate the manifest before writing anything to disk.
    3. Copy both files into ``~/.opencastor/plugins/``.
    4. Record provenance (source, timestamp, sha256) in ``plugins.lock``.

    Returns ``True`` on success, ``False`` on failure.
    """
    os.makedirs(_PLUGINS_DIR, exist_ok=True)

    is_url = source.startswith(("http://", "https://"))
    # Derive the manifest URL/path from the plugin source
    if source.endswith(".py"):
        manifest_source = source[:-3] + ".json"
    else:
        logger.error("Plugin source must be a .py file: %s", source)
        return False

    plugin_name = os.path.splitext(os.path.basename(source))[0]
    dest_py = os.path.join(_PLUGINS_DIR, os.path.basename(source))
    dest_json = os.path.join(_PLUGINS_DIR, plugin_name + ".json")

    # ------------------------------------------------------------------
    # Fetch plugin .py
    # ------------------------------------------------------------------
    try:
        if is_url:
            logger.info("Downloading plugin from %s …", source)
            with urllib.request.urlopen(source, timeout=30) as resp:  # noqa: S310
                plugin_code = resp.read()
        else:
            with open(source, "rb") as fh:
                plugin_code = fh.read()
    except Exception as exc:
        logger.error("Failed to fetch plugin '%s': %s", source, exc)
        return False

    # ------------------------------------------------------------------
    # Fetch manifest .json
    # ------------------------------------------------------------------
    try:
        if is_url:
            logger.info("Downloading manifest from %s …", manifest_source)
            with urllib.request.urlopen(manifest_source, timeout=30) as resp:  # noqa: S310
                manifest_raw = resp.read()
        else:
            with open(manifest_source, "rb") as fh:
                manifest_raw = fh.read()
    except Exception as exc:
        logger.error(
            "Failed to fetch plugin manifest '%s': %s. "
            "Every plugin must ship a plugin.json manifest.",
            manifest_source,
            exc,
        )
        return False

    # ------------------------------------------------------------------
    # Validate manifest content before writing to disk
    # ------------------------------------------------------------------
    try:
        manifest = json.loads(manifest_raw)
    except json.JSONDecodeError as exc:
        logger.error("Plugin manifest is not valid JSON: %s", exc)
        return False

    missing = _MANIFEST_REQUIRED_FIELDS - manifest.keys()
    if missing:
        logger.error(
            "Plugin manifest is missing required fields: %s. Aborting install.",
            sorted(missing),
        )
        return False

    # Compute SHA-256 of the plugin code and validate against manifest if present
    sha256 = hashlib.sha256(plugin_code).hexdigest()
    if "sha256" in manifest and manifest["sha256"] != sha256:
        logger.error(
            "Plugin SHA-256 mismatch (manifest says %s, downloaded file is %s). "
            "Aborting install.",
            manifest["sha256"],
            sha256,
        )
        return False

    # ------------------------------------------------------------------
    # Write files to plugins directory
    # ------------------------------------------------------------------
    with open(dest_py, "wb") as fh:
        fh.write(plugin_code)
    with open(dest_json, "wb") as fh:
        fh.write(manifest_raw)

    # ------------------------------------------------------------------
    # Record provenance in plugins.lock
    # ------------------------------------------------------------------
    lock = _read_lock()
    lock[plugin_name] = {
        "source": source,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "sha256": sha256,
        "manifest": {k: manifest.get(k) for k in ("name", "version", "author")},
    }
    _write_lock(lock)

    logger.info("Plugin '%s' installed successfully.", plugin_name)
    return True


def load_plugins() -> PluginRegistry:
    """Load all plugins from the plugins directory.

    Each plugin must have an accompanying ``plugin.json`` manifest.  Plugins
    without a valid manifest are skipped.  If the manifest contains a
    ``sha256`` field the plugin file is verified against it before execution.

    Returns the populated PluginRegistry.
    """
    if not os.path.isdir(_PLUGINS_DIR):
        return _registry

    for filename in sorted(os.listdir(_PLUGINS_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        filepath = os.path.join(_PLUGINS_DIR, filename)
        plugin_name = filename[:-3]  # strip .py

        # Validate manifest before executing any code
        manifest = _validate_manifest(filepath)
        if manifest is None:
            continue

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

    lock = _read_lock()

    for filename in sorted(os.listdir(_PLUGINS_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        name = filename[:-3]
        filepath = os.path.join(_PLUGINS_DIR, filename)
        manifest_path = os.path.splitext(filepath)[0] + ".json"

        manifest = None
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as fh:
                    manifest = json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass

        plugins.append(
            {
                "name": name,
                "path": filepath,
                "loaded": name in _registry._loaded,
                "has_manifest": manifest is not None,
                "manifest": manifest,
                "provenance": lock.get(name),
            }
        )

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
        msg = "  No plugins found.\n  Install a plugin: castor plugin install <url-or-path>\n"
        if has_rich:
            console.print(f"  [dim]{msg}[/]")
        else:
            print(msg)
        return

    if has_rich:
        table = Table(show_header=True, box=None)
        table.add_column("Plugin", style="bold")
        table.add_column("Status")
        table.add_column("Manifest")
        table.add_column("Version", style="dim")
        table.add_column("Source", style="dim")

        for p in plugins:
            status = "[green]loaded[/]" if p["loaded"] else "[dim]available[/]"
            manifest_status = "[green]✓[/]" if p["has_manifest"] else "[red]missing[/]"
            version = (p["manifest"] or {}).get("version", "")
            source = (p.get("provenance") or {}).get("source", "")
            table.add_row(p["name"], status, manifest_status, version, source)

        console.print(table)
    else:
        for p in plugins:
            status = "loaded" if p["loaded"] else "available"
            manifest_status = "manifest:ok" if p["has_manifest"] else "manifest:MISSING"
            version = (p["manifest"] or {}).get("version", "")
            print(f"    {p['name']:20s} {status:10s} {manifest_status}  {version}")

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
