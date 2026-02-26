"""Setup catalog consistency tests."""

from __future__ import annotations

import json

from castor.auth import PROVIDER_AUTH_MAP
from castor.conformance import KNOWN_PROVIDERS
from castor.setup_catalog import get_provider_specs
from castor.wizard import PROVIDER_ORDER


def test_provider_catalog_consistency_across_layers():
    """Setup-visible providers should be consistently recognized everywhere."""
    setup_providers = set(get_provider_specs(include_hidden=False).keys())

    with open("config/rcan.schema.json", encoding="utf-8") as f:
        schema = json.load(f)

    schema_providers = set(schema["properties"]["agent"]["properties"]["provider"]["enum"])
    wizard_providers = set(PROVIDER_ORDER)
    auth_providers = set(PROVIDER_AUTH_MAP)

    assert "apple" in setup_providers
    assert "apple" in schema_providers
    assert "apple" in wizard_providers
    assert "apple" in auth_providers
    assert "apple" in KNOWN_PROVIDERS

    # Every setup-visible provider should exist in wizard menus and conformance known list.
    assert setup_providers.issubset(wizard_providers)
    assert setup_providers.issubset(KNOWN_PROVIDERS)

    # Providers declared in schema enum should be accepted by conformance.
    assert schema_providers.issubset(KNOWN_PROVIDERS)
