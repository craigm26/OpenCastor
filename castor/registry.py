"""
OpenCastor Component Registry -- unified factory for providers, drivers, and channels.

All three core factories are accessible through a single ``ComponentRegistry``
instance.  The global registry is available via :func:`get_registry`.

Plugins can extend the registry by calling::

    def register(registry):
        registry.add_provider("my-provider", MyProvider)
        registry.add_driver("my-protocol", MyDriver)
        registry.add_channel("my-channel", MyChannel)

Built-in implementations are still resolved through the existing factory
functions in their respective modules (``castor.providers``, ``castor.main``,
``castor.channels``) so that existing test patches continue to work.
"""

import logging
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Registry")

# Built-in names used for introspection (list_* methods).
_BUILTIN_PROVIDER_NAMES: List[str] = [
    "google",
    "openai",
    "anthropic",
    "huggingface",
    "hf",
    "ollama",
    "llamacpp",
    "llama.cpp",
    "llama-cpp",
    "mlx",
    "mlx-lm",
    "vllm-mlx",
]
_BUILTIN_DRIVER_NAMES: List[str] = ["pca9685_rc", "pca9685", "dynamixel"]


class ComponentRegistry:
    """Unified registry for AI providers, hardware drivers, and messaging channels.

    Plugin-registered implementations are stored in ``_providers``, ``_drivers``,
    and ``_channels`` dicts and take precedence over built-ins.  Built-in
    implementations are resolved by delegating to the existing per-module factory
    functions (thin wrappers around ``_builtin_*`` helpers).

    Usage::

        from castor.registry import get_registry

        registry = get_registry()
        provider = registry.get_provider({"provider": "google", ...})
        driver   = registry.get_driver(config)
        channel  = registry.create_channel("telegram", on_message=cb)
    """

    def __init__(self) -> None:
        # Plugin-registered implementations only; built-ins are not stored here.
        self._providers: Dict[str, type] = {}
        self._drivers: Dict[str, type] = {}
        self._channels: Dict[str, type] = {}

    # ------------------------------------------------------------------
    # Registration (used by plugins and other external callers)
    # ------------------------------------------------------------------

    def add_provider(self, name: str, cls: type) -> None:
        """Register a provider class under *name*.

        Replaces any previously registered provider with the same name.
        """
        self._providers[name.lower()] = cls
        logger.debug("Provider registered: %s -> %s", name, getattr(cls, "__name__", cls))

    def add_driver(self, name: str, cls: type) -> None:
        """Register a driver class under *name* (protocol string).

        *name* is stored lowercased; lookups are also lowercased, so
        registration is case-insensitive.  Replaces any previously registered
        driver with the same name.
        """
        self._drivers[name.lower()] = cls
        logger.debug("Driver registered: %s -> %s", name, getattr(cls, "__name__", cls))

    def add_channel(self, name: str, cls: type) -> None:
        """Register a channel class under *name*.

        *name* is stored lowercased; lookups are also lowercased, so
        registration is case-insensitive.  Replaces any previously registered
        channel with the same name.
        """
        self._channels[name.lower()] = cls
        logger.debug("Channel registered: %s -> %s", name, getattr(cls, "__name__", cls))

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    def get_provider(self, config: dict):
        """Instantiate the correct AI provider from *config*.

        Plugin-registered providers are tried first; built-in implementations
        are resolved via ``castor.providers._builtin_get_provider``.

        Args:
            config: RCAN agent config block.  The ``provider`` key selects the
                    implementation (default: ``"google"``).

        Returns:
            An initialised :class:`~castor.providers.base.BaseProvider` instance.

        Raises:
            ValueError: If the provider name is not recognised.
        """
        name = config.get("provider", "google").lower()
        if name in self._providers:
            logger.debug("Using plugin provider: %s", name)
            return self._providers[name](config)
        # Delegate to the built-in factory which uses module-level class names
        # so that test patches on castor.providers.* still work correctly.
        from castor.providers import _builtin_get_provider

        return _builtin_get_provider(config)

    def get_driver(self, config: dict):
        """Instantiate the appropriate hardware driver from *config*.

        Plugin-registered drivers are matched by exact protocol name first.
        Built-in implementations (pca9685, dynamixel, …) are resolved via
        ``castor.main._builtin_get_driver``.

        Args:
            config: Full RCAN config dict.  The ``drivers`` list is inspected;
                    the first entry's ``protocol`` key selects the driver.

        Returns:
            An initialised :class:`~castor.drivers.base.DriverBase` instance,
            or ``None`` if no drivers are configured.
        """
        if not config.get("drivers"):
            return None
        driver_config = config["drivers"][0]
        protocol = driver_config.get("protocol", "").lower()
        if protocol in self._drivers:
            logger.debug("Using plugin driver: %s", protocol)
            return self._drivers[protocol](driver_config)
        # Delegate to the built-in factory.
        from castor.main import _builtin_get_driver

        return _builtin_get_driver(config)

    def create_channel(
        self,
        name: str,
        config: Optional[dict] = None,
        on_message: Optional[Callable] = None,
    ):
        """Instantiate a messaging channel by *name*.

        Plugin-registered channels are tried first; built-in channels are
        resolved via ``castor.channels._builtin_create_channel``.

        Args:
            name:       Channel name (e.g. ``"telegram"``, ``"discord"``).
            config:     Optional extra config dict.  Credentials are
                        auto-resolved from environment variables and merged.
            on_message: Callback ``(channel_name, chat_id, text) -> reply_str``.

        Returns:
            An initialised :class:`~castor.channels.base.BaseChannel` instance.

        Raises:
            ValueError: If the channel name is not recognised.
        """
        name_lower = name.lower()
        if name_lower in self._channels:
            logger.debug("Using plugin channel: %s", name_lower)
            from castor.auth import resolve_channel_credentials

            merged = dict(config or {})
            merged.update(resolve_channel_credentials(name))
            return self._channels[name_lower](merged, on_message=on_message)
        # Delegate to the built-in factory so that test patches on
        # castor.channels._CHANNEL_CLASSES still work correctly.
        from castor.channels import _builtin_create_channel

        return _builtin_create_channel(name, config, on_message)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_providers(self) -> List[str]:
        """Return sorted list of all known provider names (built-ins + plugins)."""
        return sorted(set(_BUILTIN_PROVIDER_NAMES) | set(self._providers.keys()))

    def list_drivers(self) -> List[str]:
        """Return sorted list of all known driver protocol names (built-ins + plugins)."""
        return sorted(set(_BUILTIN_DRIVER_NAMES) | set(self._drivers.keys()))

    def list_channels(self) -> List[str]:
        """Return sorted list of all known channel names (available built-ins + plugins)."""
        try:
            from castor.channels import get_available_channels

            built_ins: set = set(get_available_channels())
        except Exception:
            built_ins = set()
        return sorted(built_ins | set(self._channels.keys()))

    def list_plugin_providers(self) -> List[str]:
        """Return sorted list of plugin-registered provider names."""
        return sorted(self._providers.keys())

    def list_plugin_drivers(self) -> List[str]:
        """Return sorted list of plugin-registered driver names."""
        return sorted(self._drivers.keys())

    def list_plugin_channels(self) -> List[str]:
        """Return sorted list of plugin-registered channel names."""
        return sorted(self._channels.keys())


# Global singleton -- shared across the process.
_registry = ComponentRegistry()


def get_registry() -> ComponentRegistry:
    """Return the global :class:`ComponentRegistry` singleton."""
    return _registry
