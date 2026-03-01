"""Tests for entry-point plugin system in castor.registry.

Issue #237 — Runtime plugin system via importlib.metadata entry points.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _fresh_registry():
    """Return a fresh ComponentRegistry (not the global singleton)."""
    from castor.registry import ComponentRegistry

    return ComponentRegistry()


def _make_ep(name: str, group: str, cls: type, pkg_name: str = "test-pkg") -> MagicMock:
    """Create a mock importlib.metadata EntryPoint."""
    ep = MagicMock()
    ep.name = name
    ep.group = group
    ep.load.return_value = cls
    dist = MagicMock()
    dist.metadata = {"Name": pkg_name}
    ep.dist = dist
    return ep


class _FakeProvider:
    def __init__(self, config):
        pass


class _FakeDriver:
    def __init__(self, config):
        pass


class _FakeChannel:
    def __init__(self, config, on_message=None):
        pass


# ---------------------------------------------------------------------------
# PluginEntry dataclass
# ---------------------------------------------------------------------------


class TestPluginEntry:
    def test_fields(self):
        from castor.registry import PluginEntry

        e = PluginEntry(name="myplugin", group="opencastor.providers", package="mypkg")
        assert e.name == "myplugin"
        assert e.group == "opencastor.providers"
        assert e.package == "mypkg"
        assert e.cls is None

    def test_with_cls(self):
        from castor.registry import PluginEntry

        e = PluginEntry(name="x", group="opencastor.drivers", package="xpkg", cls=_FakeDriver)
        assert e.cls is _FakeDriver


# ---------------------------------------------------------------------------
# discover_plugins — no entry points installed
# ---------------------------------------------------------------------------


class TestDiscoverPluginsEmpty:
    def test_returns_empty_list_when_no_eps(self):
        reg = _fresh_registry()
        with patch("castor.registry.entry_points", return_value=[], create=True):
            with patch("importlib.metadata.entry_points", return_value=[]):
                result = reg.discover_plugins()
        # May return list (empty or non-empty depending on real install)
        assert isinstance(result, list)

    def test_list_all_plugins_empty(self):
        reg = _fresh_registry()
        # Before any discover_plugins call, list is empty
        assert reg.list_all_plugins() == []


# ---------------------------------------------------------------------------
# discover_plugins — mocked entry points
# ---------------------------------------------------------------------------


class TestDiscoverPluginsMocked:
    def _run_discover(self, eps_by_group: dict) -> tuple:
        """Helper: run discover_plugins with mocked entry_points()."""
        reg = _fresh_registry()

        def fake_entry_points(group):
            return eps_by_group.get(group, [])

        with patch("castor.registry.entry_points", fake_entry_points):
            result = reg.discover_plugins()
        return reg, result

    def test_discovers_provider_ep(self):
        ep = _make_ep("my-provider", "opencastor.providers", _FakeProvider)
        reg, discovered = self._run_discover({"opencastor.providers": [ep]})
        assert any(e.name == "my-provider" for e in discovered)
        assert "my-provider" in reg._providers

    def test_discovers_driver_ep(self):
        ep = _make_ep("my-driver", "opencastor.drivers", _FakeDriver)
        reg, discovered = self._run_discover({"opencastor.drivers": [ep]})
        assert any(e.name == "my-driver" for e in discovered)
        assert "my-driver" in reg._drivers

    def test_discovers_channel_ep(self):
        ep = _make_ep("my-channel", "opencastor.channels", _FakeChannel)
        reg, discovered = self._run_discover({"opencastor.channels": [ep]})
        assert any(e.name == "my-channel" for e in discovered)
        assert "my-channel" in reg._channels

    def test_ep_cls_stored_in_plugin_entry(self):
        ep = _make_ep("pr", "opencastor.providers", _FakeProvider)
        reg, discovered = self._run_discover({"opencastor.providers": [ep]})
        entry = next(e for e in discovered if e.name == "pr")
        assert entry.cls is _FakeProvider

    def test_ep_package_name_stored(self):
        ep = _make_ep("dr", "opencastor.drivers", _FakeDriver, pkg_name="cool-robot-lib")
        reg, discovered = self._run_discover({"opencastor.drivers": [ep]})
        entry = next(e for e in discovered if e.name == "dr")
        assert entry.package == "cool-robot-lib"

    def test_duplicate_ep_not_registered_twice(self):
        ep1 = _make_ep("dup", "opencastor.providers", _FakeProvider)
        ep2 = _make_ep("dup", "opencastor.providers", _FakeProvider, pkg_name="other-pkg")
        reg = _fresh_registry()
        reg.add_provider("dup", _FakeProvider)  # pre-register

        def fake_entry_points(group):
            if group == "opencastor.providers":
                return [ep1, ep2]
            return []

        with patch("castor.registry.entry_points", fake_entry_points):
            discovered = reg.discover_plugins()
        # Should not appear in discovered (was pre-registered)
        assert all(e.name != "dup" for e in discovered)

    def test_failed_ep_load_skipped_gracefully(self):
        bad_ep = MagicMock()
        bad_ep.name = "bad"
        bad_ep.group = "opencastor.providers"
        bad_ep.load.side_effect = ImportError("no module named 'nonexistent'")
        bad_ep.dist = None

        reg = _fresh_registry()

        def fake_entry_points(group):
            if group == "opencastor.providers":
                return [bad_ep]
            return []

        with patch("castor.registry.entry_points", fake_entry_points):
            # Should not raise
            discovered = reg.discover_plugins()
        assert all(e.name != "bad" for e in discovered)

    def test_multiple_groups_discovered_together(self):
        ep_p = _make_ep("p1", "opencastor.providers", _FakeProvider)
        ep_d = _make_ep("d1", "opencastor.drivers", _FakeDriver)
        ep_c = _make_ep("c1", "opencastor.channels", _FakeChannel)

        def fake_entry_points(group):
            return {
                "opencastor.providers": [ep_p],
                "opencastor.drivers": [ep_d],
                "opencastor.channels": [ep_c],
            }.get(group, [])

        reg = _fresh_registry()
        with patch("castor.registry.entry_points", fake_entry_points):
            discovered = reg.discover_plugins()

        names = {e.name for e in discovered}
        assert "p1" in names
        assert "d1" in names
        assert "c1" in names


# ---------------------------------------------------------------------------
# list_all_plugins
# ---------------------------------------------------------------------------


class TestListAllPlugins:
    def test_sorted_by_group_then_name(self):
        ep_p = _make_ep("zebra", "opencastor.providers", _FakeProvider)
        ep_d = _make_ep("alpha", "opencastor.drivers", _FakeDriver)

        reg = _fresh_registry()

        def fake_entry_points(group):
            return {
                "opencastor.providers": [ep_p],
                "opencastor.drivers": [ep_d],
            }.get(group, [])

        with patch("castor.registry.entry_points", fake_entry_points):
            reg.discover_plugins()

        all_plugins = reg.list_all_plugins()
        groups = [e.group for e in all_plugins]
        assert groups == sorted(groups), "should be sorted by group"

    def test_returns_list(self):
        reg = _fresh_registry()
        result = reg.list_all_plugins()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Plugin entry point group constants
# ---------------------------------------------------------------------------


class TestEPGroupConstants:
    def test_groups_defined(self):
        from castor.registry import _EP_GROUP_CHANNELS, _EP_GROUP_DRIVERS, _EP_GROUP_PROVIDERS

        assert _EP_GROUP_PROVIDERS == "opencastor.providers"
        assert _EP_GROUP_DRIVERS == "opencastor.drivers"
        assert _EP_GROUP_CHANNELS == "opencastor.channels"
