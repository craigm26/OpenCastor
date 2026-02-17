"""Tests for RURI (RCAN Uniform Resource Identifier)."""

import pytest

from castor.rcan.ruri import RURI


class TestRURIParse:
    """Parsing RURI strings."""

    def test_basic_ruri(self):
        r = RURI.parse("rcan://opencastor.rover.abc12345")
        assert r.manufacturer == "opencastor"
        assert r.model == "rover"
        assert r.instance == "abc12345"
        assert r.capability is None

    def test_ruri_with_capability(self):
        r = RURI.parse("rcan://opencastor.arm.def456/teleop")
        assert r.manufacturer == "opencastor"
        assert r.model == "arm"
        assert r.instance == "def456"
        assert r.capability == "teleop"

    def test_ruri_with_nested_capability(self):
        r = RURI.parse("rcan://acme.bot.xyz/nav/path")
        assert r.capability == "nav/path"

    def test_ruri_with_wildcards(self):
        r = RURI.parse("rcan://*.*.*/status")
        assert r.manufacturer == "*"
        assert r.model == "*"
        assert r.instance == "*"
        assert r.capability == "status"

    def test_invalid_ruri_no_scheme(self):
        with pytest.raises(ValueError, match="Invalid RURI"):
            RURI.parse("http://opencastor.rover.abc")

    def test_invalid_ruri_missing_parts(self):
        with pytest.raises(ValueError, match="Invalid RURI"):
            RURI.parse("rcan://opencastor.rover")

    def test_invalid_ruri_empty(self):
        with pytest.raises(ValueError, match="Invalid RURI"):
            RURI.parse("")

    def test_whitespace_stripped(self):
        r = RURI.parse("  rcan://opencastor.rover.abc123  ")
        assert r.manufacturer == "opencastor"


class TestRURIFromConfig:
    """Generating RURI from RCAN config."""

    def test_explicit_ruri_in_config(self):
        config = {"metadata": {"ruri": "rcan://acme.bot.xyz123"}}
        r = RURI.from_config(config)
        assert r.manufacturer == "acme"
        assert r.model == "bot"
        assert r.instance == "xyz123"

    def test_from_structured_fields(self):
        config = {
            "metadata": {
                "manufacturer": "waveshare",
                "model": "alphabot",
                "robot_uuid": "11111111-2222-3333-4444-555555555555",
            }
        }
        r = RURI.from_config(config)
        assert r.manufacturer == "waveshare"
        assert r.model == "alphabot"
        assert r.instance == "11111111"

    def test_fallback_to_robot_name(self):
        config = {
            "metadata": {
                "robot_name": "Castor Rover One",
                "robot_uuid": "abcdef01-0000-0000-0000-000000000000",
            }
        }
        r = RURI.from_config(config)
        assert r.manufacturer == "opencastor"
        assert r.model == "castor_rover_one"
        assert r.instance == "abcdef01"

    def test_empty_config(self):
        r = RURI.from_config({})
        assert r.manufacturer == "opencastor"
        assert r.model == "robot"
        assert len(r.instance) == 8


class TestRURIStr:
    """String representation."""

    def test_str_without_capability(self):
        r = RURI("opencastor", "rover", "abc12345")
        assert str(r) == "rcan://opencastor.rover.abc12345"

    def test_str_with_capability(self):
        r = RURI("opencastor", "rover", "abc12345", capability="nav")
        assert str(r) == "rcan://opencastor.rover.abc12345/nav"

    def test_base_property(self):
        r = RURI("opencastor", "rover", "abc12345", capability="nav")
        assert r.base == "rcan://opencastor.rover.abc12345"

    def test_roundtrip(self):
        original = "rcan://opencastor.rover.abc12345/teleop"
        r = RURI.parse(original)
        assert str(r) == original


class TestRURIWithCapability:
    """Creating derived RURIs."""

    def test_with_capability(self):
        r = RURI("opencastor", "rover", "abc12345")
        r2 = r.with_capability("vision")
        assert r.capability is None
        assert r2.capability == "vision"
        assert r2.manufacturer == "opencastor"


class TestRURIMatches:
    """Pattern matching."""

    def test_exact_match(self):
        r = RURI("opencastor", "rover", "abc12345")
        p = RURI("opencastor", "rover", "abc12345")
        assert r.matches(p)

    def test_wildcard_all(self):
        r = RURI("opencastor", "rover", "abc12345")
        p = RURI("*", "*", "*")
        assert r.matches(p)

    def test_wildcard_manufacturer(self):
        r = RURI("opencastor", "rover", "abc12345")
        p = RURI("*", "rover", "abc12345")
        assert r.matches(p)

    def test_no_match_different_model(self):
        r = RURI("opencastor", "rover", "abc12345")
        p = RURI("opencastor", "arm", "abc12345")
        assert not r.matches(p)

    def test_pattern_without_capability_matches_any(self):
        r = RURI("opencastor", "rover", "abc12345", capability="nav")
        p = RURI("opencastor", "rover", "abc12345")  # no cap
        assert r.matches(p)

    def test_pattern_with_capability_must_match(self):
        r = RURI("opencastor", "rover", "abc12345", capability="nav")
        p = RURI("opencastor", "rover", "abc12345", capability="vision")
        assert not r.matches(p)

    def test_pattern_capability_wildcard(self):
        r = RURI("opencastor", "rover", "abc12345", capability="nav")
        p = RURI("opencastor", "rover", "abc12345", capability="*")
        assert r.matches(p)
