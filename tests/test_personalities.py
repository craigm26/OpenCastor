"""
tests/test_personalities.py — Unit + API tests for castor/personalities.py.

Covers:
  - PersonalityProfile dataclass
  - PersonalityRegistry: built-ins, set_active, register, init_from_config
  - Module-level singleton helpers
  - API: GET /api/personality/list, /current, POST /api/personality/set
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# PersonalityProfile tests
# ---------------------------------------------------------------------------


def test_builtin_profiles_present():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    names = {p["name"] for p in reg.list_profiles()}
    assert {"assistant", "explorer", "guardian", "scientist", "companion", "minimal"} <= names


def test_default_active_is_assistant():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    assert reg.active_name == "assistant"


def test_profile_to_dict_keys():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    d = reg.current.to_dict()
    for key in ("name", "description", "emoji_mode", "response_style", "greeting", "tags"):
        assert key in d


def test_set_active_valid():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    profile = reg.set_active("explorer")
    assert profile.name == "explorer"
    assert reg.active_name == "explorer"
    assert reg.current.name == "explorer"


def test_set_active_case_insensitive():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    reg.set_active("GUARDIAN")
    assert reg.active_name == "guardian"


def test_set_active_unknown_raises():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    with pytest.raises(ValueError, match="Unknown personality"):
        reg.set_active("nonexistent_profile")


def test_register_custom_profile():
    from castor.personalities import PersonalityProfile, PersonalityRegistry

    reg = PersonalityRegistry()
    custom = PersonalityProfile(
        name="ninja",
        description="Silent and deadly efficient",
        system_prompt="Be brief. Act fast.",
        emoji_mode=False,
        response_style="terse",
        greeting="...",
        tags=["custom"],
    )
    reg.register(custom)
    assert reg.get("ninja") is not None
    assert reg.get("ninja").description == "Silent and deadly efficient"


def test_register_from_dict():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    reg.register_from_dict(
        {
            "name": "tester",
            "description": "Test persona",
            "system_prompt": "You test things.",
            "emoji_mode": True,
            "response_style": "verbose",
            "greeting": "Testing!",
            "tags": ["test"],
        }
    )
    p = reg.get("tester")
    assert p is not None
    assert p.emoji_mode is True


def test_list_profiles_has_active_flag():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    reg.set_active("companion")
    profiles = reg.list_profiles()
    active = [p for p in profiles if p["active"]]
    assert len(active) == 1
    assert active[0]["name"] == "companion"


def test_init_from_config_custom():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    config = {
        "personalities": {
            "default": "explorer",
            "custom": [
                {
                    "name": "custom_bot",
                    "description": "Custom bot",
                    "system_prompt": "Do stuff.",
                }
            ],
        }
    }
    reg.init_from_config(config)
    assert reg.active_name == "explorer"
    assert reg.get("custom_bot") is not None


def test_init_from_config_unknown_default_fallback():
    """Unknown default name in config should not crash — stays on current active."""
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    reg.init_from_config({"personalities": {"default": "does_not_exist", "custom": []}})
    # Should still be a valid profile
    assert reg.active_name in {p["name"] for p in reg.list_profiles()}


def test_init_from_config_empty():
    from castor.personalities import PersonalityRegistry

    reg = PersonalityRegistry()
    reg.init_from_config({})  # No personalities block — should not crash
    assert reg.active_name == "assistant"


def test_singleton_get_registry():
    import castor.personalities as m

    m._registry = None  # reset
    reg1 = m.get_registry()
    reg2 = m.get_registry()
    assert reg1 is reg2


def test_module_set_active():
    import castor.personalities as m

    m._registry = None
    m.set_active("scientist")
    assert m.get_registry().active_name == "scientist"
    m._registry = None  # cleanup


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    """Return a TestClient with a fresh AppState."""
    from fastapi.testclient import TestClient

    from castor.api import app, state

    state.personality_registry = None  # reset to lazy-init
    return TestClient(app)


def test_api_personality_list(api_client):
    resp = api_client.get("/api/personality/list")
    assert resp.status_code == 200
    data = resp.json()
    assert "personalities" in data
    names = [p["name"] for p in data["personalities"]]
    assert "assistant" in names
    assert "explorer" in names


def test_api_personality_current(api_client):
    resp = api_client.get("/api/personality/current")
    assert resp.status_code == 200
    data = resp.json()
    assert "name" in data
    assert "personality" in data
    assert data["name"] == data["personality"]["name"]


def test_api_personality_set_valid(api_client):
    resp = api_client.post("/api/personality/set", json={"name": "guardian"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "guardian"
    assert "greeting" in data


def test_api_personality_set_unknown(api_client):
    resp = api_client.post("/api/personality/set", json={"name": "zzz_unknown"})
    assert resp.status_code == 404


def test_api_personality_current_reflects_set(api_client):
    api_client.post("/api/personality/set", json={"name": "minimal"})
    resp = api_client.get("/api/personality/current")
    assert resp.json()["name"] == "minimal"
