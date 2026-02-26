"""Setup catalog consistency tests."""

from __future__ import annotations

import json

from castor.auth import PROVIDER_AUTH_MAP
from castor.conformance import KNOWN_PROVIDERS
from castor.setup_catalog import get_hardware_preset_map, get_hardware_presets, get_provider_specs
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


def test_setup_catalog_includes_stem_hardware_presets():
    """Setup catalog should expose ESP32 and LEGO presets for beginner flows."""
    ids = {item.id for item in get_hardware_presets()}
    assert {"esp32_generic", "lego_mindstorms_ev3", "lego_spike_prime"}.issubset(ids)


def test_hardware_preset_numeric_map_includes_stem_options():
    """Wizard numeric map should expose ESP32 + LEGO choices."""
    preset_map = get_hardware_preset_map()
    assert preset_map["7"] == "esp32_generic"
    assert preset_map["8"] == "lego_mindstorms_ev3"
    assert preset_map["9"] == "lego_spike_prime"
