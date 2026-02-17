"""Tests for the Community Hub."""

import json
from pathlib import Path

import pytest


class TestPIIScrubbing:
    def test_scrub_api_keys(self):
        from castor.hub import scrub_pii

        assert "[REDACTED_API_KEY]" in scrub_pii("key: sk-ant-abc123def456ghi789jkl012")
        assert "[REDACTED_HF_TOKEN]" in scrub_pii("token: hf_abcdefghijklmnopqrstuvwxyz")
        assert "[REDACTED_GOOGLE_KEY]" in scrub_pii("key: AIzaSyAbcDefGhiJklMnoPqrStUvWxYz0123456")

    def test_scrub_email(self):
        from castor.hub import scrub_pii

        assert "[REDACTED_EMAIL]" in scrub_pii("contact: user@example.com")

    def test_scrub_phone(self):
        from castor.hub import scrub_pii

        assert "[REDACTED_PHONE]" in scrub_pii("call me: +1-555-123-4567")

    def test_scrub_home_path(self):
        from castor.hub import scrub_pii

        assert "/home/user" in scrub_pii("/home/johndoe/robots/config.yaml")
        assert "johndoe" not in scrub_pii("/home/johndoe/robots/config.yaml")

    def test_preserves_private_ips(self):
        from castor.hub import scrub_pii

        assert "192.168.1.100" in scrub_pii("host: 192.168.1.100")
        assert "10.0.0.1" in scrub_pii("host: 10.0.0.1")

    def test_scrubs_public_ips(self):
        from castor.hub import scrub_pii

        assert "[REDACTED_IP]" in scrub_pii("server: 203.0.113.50")

    def test_scrub_passwords(self):
        from castor.hub import scrub_pii

        result = scrub_pii("password: mysecretpass123")
        assert "mysecretpass123" not in result
        assert "[REDACTED]" in result


class TestRecipeManifest:
    def test_create_manifest(self):
        from castor.hub import create_recipe_manifest

        m = create_recipe_manifest(
            name="Test Bot",
            description="A test",
            author="tester",
            category="home",
            difficulty="beginner",
            hardware=["RPi 4"],
            ai_provider="google",
            ai_model="gemini-2.5-flash",
        )
        assert m["name"] == "Test Bot"
        assert m["category"] == "home"
        assert m["ai"]["provider"] == "google"
        assert "id" in m
        assert "created" in m

    def test_recipe_id_is_slug(self):
        from castor.hub import generate_recipe_id

        rid = generate_recipe_id("My Cool Robot!!")
        assert " " not in rid
        assert "!" not in rid
        assert rid.startswith("my-cool-robot")


class TestRecipePackaging:
    def test_package_creates_files(self, tmp_path):
        from castor.hub import create_recipe_manifest, package_recipe

        config = tmp_path / "test.rcan.yaml"
        config.write_text("agent:\n  provider: google\n  api_key: AIzaSyA12345\n")

        doc = tmp_path / "notes.md"
        doc.write_text("# Notes\nMy email is test@example.com\n")

        manifest = create_recipe_manifest(
            name="test-bot",
            description="test",
            author="anon",
            category="home",
            difficulty="beginner",
            hardware=["RPi"],
            ai_provider="google",
            ai_model="gemini",
        )

        result = package_recipe(
            config_path=str(config),
            output_dir=str(tmp_path),
            docs=[str(doc)],
            manifest=manifest,
        )

        assert result.exists()
        assert (result / "config.rcan.yaml").exists()
        assert (result / "recipe.json").exists()
        assert (result / "README.md").exists()
        assert (result / "notes.md").exists()

        # Verify PII was scrubbed
        scrubbed_config = (result / "config.rcan.yaml").read_text()
        assert "AIzaSyA12345" not in scrubbed_config

        scrubbed_doc = (result / "notes.md").read_text()
        assert "test@example.com" not in scrubbed_doc

    def test_dry_run_no_files(self, tmp_path):
        from castor.hub import create_recipe_manifest, package_recipe

        config = tmp_path / "test.rcan.yaml"
        config.write_text("agent:\n  provider: google\n")

        manifest = create_recipe_manifest(
            name="dry",
            description="dry",
            author="anon",
            category="custom",
            difficulty="beginner",
            hardware=[],
            ai_provider="google",
            ai_model="gemini",
        )

        result = package_recipe(
            config_path=str(config),
            output_dir=str(tmp_path / "out"),
            manifest=manifest,
            dry_run=True,
        )

        assert not result.exists()


class TestRecipeListing:
    def test_list_seed_recipes(self):
        from castor.hub import list_recipes

        recipes = list_recipes()
        assert len(recipes) >= 2

    def test_filter_by_category(self):
        from castor.hub import list_recipes

        home = list_recipes(category="home")
        assert all(r["category"] == "home" for r in home)

    def test_filter_by_provider(self):
        from castor.hub import list_recipes

        hf = list_recipes(provider="huggingface")
        assert all(r["ai"]["provider"] == "huggingface" for r in hf)

    def test_search(self):
        from castor.hub import list_recipes

        results = list_recipes(search="patrol")
        assert len(results) >= 1

    def test_get_recipe(self):
        from castor.hub import get_recipe

        r = get_recipe("picar-home-patrol-e7f3a1")
        assert r is not None
        assert r["name"] == "PiCar-X Home Patrol Bot"

    def test_get_recipe_not_found(self):
        from castor.hub import get_recipe

        assert get_recipe("nonexistent-abc123") is None
