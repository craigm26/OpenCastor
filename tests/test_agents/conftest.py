"""Pytest configuration for the test_agents package."""

import pytest


# Set asyncio mode to auto for this directory so every async test
# function/method runs without needing explicit @pytest.mark.asyncio
def pytest_configure(config):
    pass


# Override asyncio_mode to "auto" for this subdirectory
# by adding pytestmark to each module at collection time.
def pytest_collection_modifyitems(session, config, items):
    for item in items:
        if "test_agents" not in str(getattr(item, "fspath", "")):
            continue
        # For async test functions/methods, ensure asyncio mark is present
        import asyncio
        import inspect

        fn = getattr(item, "function", None)
        if fn is not None and inspect.iscoroutinefunction(fn):
            # Remove any existing asyncio mark first to avoid duplicates
            item.own_markers = [
                m for m in item.own_markers if m.name != "asyncio"
            ]
            item.add_marker(pytest.mark.asyncio(loop_scope="function"), append=False)
