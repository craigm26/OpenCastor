"""Tests for the Community Hub."""

import subprocess
from unittest.mock import MagicMock, patch

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


class TestRecipeVersion:
    def test_manifest_uses_current_version(self):
        """opencastor_version in manifest should match installed version."""
        from castor import __version__
        from castor.hub import create_recipe_manifest

        m = create_recipe_manifest(
            name="v",
            description="d",
            author="a",
            category="custom",
            difficulty="beginner",
            hardware=[],
            ai_provider="google",
            ai_model="gemini",
        )
        assert m["opencastor_version"] == __version__
        assert m["opencastor_version"] != "2026.2.17.7"


class TestSubmitRecipePR:
    """Tests for the auto-PR submission feature."""

    def test_submit_error_when_gh_not_installed(self, tmp_path):
        from castor.hub import SubmitError, _run_gh

        with patch("castor.hub.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(SubmitError, match="not installed"):
                _run_gh(["auth", "status"])

    def test_submit_error_when_not_authenticated(self):
        from castor.hub import SubmitError, _check_gh_auth

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "not logged in"

        with patch("castor.hub._run_gh", return_value=mock_result):
            with pytest.raises(SubmitError, match="Not authenticated"):
                _check_gh_auth()

    def test_check_gh_auth_returns_username(self):
        from castor.hub import _check_gh_auth

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "✓ Logged in to github.com account testuser (oauth_token)\n"
        mock_result.stderr = ""

        with patch("castor.hub._run_gh", return_value=mock_result):
            assert _check_gh_auth() == "testuser"

    def test_check_gh_auth_unknown_user(self):
        from castor.hub import _check_gh_auth

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "✓ Some other format\n"
        mock_result.stderr = ""

        with patch("castor.hub._run_gh", return_value=mock_result):
            assert _check_gh_auth() == "unknown"

    def test_build_pr_description(self):
        from castor.hub import _build_pr_description

        manifest = {
            "name": "Test Bot",
            "description": "A test bot",
            "category": "home",
            "difficulty": "beginner",
            "ai": {"provider": "google", "model": "gemini-2.5-flash"},
            "hardware": ["RPi 4", "Camera"],
            "tags": ["home", "patrol"],
            "budget": "$100",
            "use_case": "Home patrol",
        }
        desc = _build_pr_description(manifest)
        assert "Test Bot" in desc
        assert "Home & Indoor" in desc
        assert "google" in desc
        assert "RPi 4, Camera" in desc
        assert "Home patrol" in desc
        assert "$100" in desc

    def test_build_pr_description_minimal(self):
        from castor.hub import _build_pr_description

        manifest = {
            "name": "Minimal",
            "description": "Bare minimum",
            "ai": {},
            "hardware": [],
            "tags": [],
        }
        desc = _build_pr_description(manifest)
        assert "Minimal" in desc
        assert "Not specified" in desc

    def test_submit_timeout_error(self):
        from castor.hub import SubmitError, _run_gh

        with patch(
            "castor.hub.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=60),
        ):
            with pytest.raises(SubmitError, match="timed out"):
                _run_gh(["auth", "status"])

    def test_submit_generic_gh_error(self):
        from castor.hub import SubmitError, _run_gh

        with patch(
            "castor.hub.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh", stderr="something went wrong"),
        ):
            with pytest.raises(SubmitError, match="something went wrong"):
                _run_gh(["auth", "status"])

    def test_ensure_fork_already_exists(self):
        from castor.hub import _ensure_fork

        fork_result = MagicMock()
        fork_result.returncode = 1
        fork_result.stderr = "already exists"

        view_result = MagicMock()
        view_result.stdout = "testuser/OpenCastor\n"

        with patch("castor.hub._run_gh", side_effect=[fork_result, view_result]):
            name = _ensure_fork()
            assert name == "testuser/OpenCastor"

    def test_ensure_fork_failure(self):
        from castor.hub import SubmitError, _ensure_fork

        fork_result = MagicMock()
        fork_result.returncode = 1
        fork_result.stderr = "permission denied"

        with patch("castor.hub._run_gh", return_value=fork_result):
            with pytest.raises(SubmitError, match="Failed to fork"):
                _ensure_fork()


# ===========================================================================
# Hub Index tests (Issue #123)
# ===========================================================================

SAMPLE_INDEX = {
    "version": 1,
    "presets": [
        {
            "name": "waveshare_alpha",
            "url": "https://raw.githubusercontent.com/craigm26/OpenCastor/main/config/presets/waveshare_alpha.rcan.yaml",
            "tags": ["mobile", "rover", "waveshare"],
            "author": "OpenCastor Default",
            "description": "Waveshare AlphaBot preset",
        },
        {
            "name": "dynamixel_arm",
            "url": "https://raw.githubusercontent.com/craigm26/OpenCastor/main/config/presets/dynamixel_arm.rcan.yaml",
            "tags": ["arm", "manipulator", "dynamixel"],
            "author": "OpenCastor Default",
            "description": "Dynamixel 6DOF arm preset",
        },
    ],
    "behaviors": [
        {
            "name": "patrol",
            "url": "https://example.com/patrol.behavior.yaml",
            "tags": ["navigation", "patrol"],
            "author": "community",
            "description": "Simple patrol loop",
        }
    ],
}


class TestFetchIndex:
    def test_fetch_index_returns_dict(self):
        """fetch_index should return a dict with presets and behaviors keys."""
        from castor.commands.hub import fetch_index

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_INDEX
        mock_response.raise_for_status = MagicMock()

        with patch("castor.commands.hub.requests.get", return_value=mock_response):
            index = fetch_index("https://example.com/hub.json")

        assert isinstance(index, dict)
        assert "presets" in index
        assert "behaviors" in index
        assert index["version"] == 1

    def test_fetch_index_network_error(self):
        """fetch_index should raise RuntimeError with a clear message on network errors."""
        import requests as req_lib

        from castor.commands.hub import fetch_index

        with patch(
            "castor.commands.hub.requests.get",
            side_effect=req_lib.exceptions.ConnectionError("unreachable"),
        ):
            with pytest.raises(RuntimeError, match="Network error"):
                fetch_index("https://example.com/hub.json")


class TestHubList:
    def test_hub_list_prints_table(self, capsys):
        """cmd_hub_list should print a table containing preset names."""
        from castor.commands.hub import cmd_hub_list

        args = MagicMock()
        args.hub_url = None

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_INDEX
        mock_response.raise_for_status = MagicMock()

        with patch("castor.commands.hub.requests.get", return_value=mock_response):
            cmd_hub_list(args)

        captured = capsys.readouterr()
        assert "waveshare_alpha" in captured.out or "waveshare_alpha" in captured.err


class TestHubSearch:
    def test_hub_search_filters_by_name(self, capsys):
        """Searching for 'waveshare' should return only matching rows."""
        from castor.commands.hub import cmd_hub_search

        args = MagicMock()
        args.hub_url = None
        args.query = "waveshare"

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_INDEX
        mock_response.raise_for_status = MagicMock()

        with patch("castor.commands.hub.requests.get", return_value=mock_response):
            cmd_hub_search(args)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "waveshare_alpha" in output
        assert "dynamixel_arm" not in output

    def test_hub_search_case_insensitive(self, capsys):
        """Searching 'WAVESHARE' should match 'waveshare'."""
        from castor.commands.hub import cmd_hub_search

        args = MagicMock()
        args.hub_url = None
        args.query = "WAVESHARE"

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_INDEX
        mock_response.raise_for_status = MagicMock()

        with patch("castor.commands.hub.requests.get", return_value=mock_response):
            cmd_hub_search(args)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "waveshare_alpha" in output


class TestHubInstall:
    def test_hub_install_downloads_file(self, tmp_path, monkeypatch):
        """cmd_hub_install should download and save the preset file."""
        from castor.commands.hub import cmd_hub_install
        import castor.commands.hub as hub_mod

        monkeypatch.setattr(hub_mod, "_REPO_ROOT", tmp_path)

        args = MagicMock()
        args.hub_url = None
        args.name = "waveshare_alpha"

        index_response = MagicMock()
        index_response.json.return_value = SAMPLE_INDEX
        index_response.raise_for_status = MagicMock()

        preset_content = "rcan_version: '1.1.0'\nmetadata:\n  robot_name: Test\n"
        file_response = MagicMock()
        file_response.text = preset_content
        file_response.raise_for_status = MagicMock()

        with patch("castor.commands.hub.requests.get", side_effect=[index_response, file_response]):
            cmd_hub_install(args)

        output_file = tmp_path / "config" / "presets" / "waveshare_alpha.rcan.yaml"
        assert output_file.exists()
        assert output_file.read_text() == preset_content

    def test_hub_install_unknown_name(self, capsys):
        """Installing an unknown preset should print a clear error."""
        from castor.commands.hub import cmd_hub_install

        args = MagicMock()
        args.hub_url = None
        args.name = "nonexistent_preset"

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_INDEX
        mock_response.raise_for_status = MagicMock()

        with patch("castor.commands.hub.requests.get", return_value=mock_response):
            cmd_hub_install(args)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "not found" in output.lower()


class TestHubIndexJson:
    def test_hub_index_json_valid(self):
        """The real config/hub_index.json should have valid structure."""
        import json
        from pathlib import Path

        index_path = Path(__file__).parent.parent / "config" / "hub_index.json"
        assert index_path.exists(), f"hub_index.json not found at {index_path}"

        with open(index_path) as f:
            index = json.load(f)

        assert isinstance(index, dict)
        assert "version" in index
        assert "presets" in index
        assert "behaviors" in index
        assert isinstance(index["presets"], list)
        assert isinstance(index["behaviors"], list)
        assert len(index["presets"]) > 0

        # Validate each preset has required fields
        required_fields = {"name", "url", "tags", "author", "description"}
        for preset in index["presets"]:
            missing = required_fields - set(preset.keys())
            assert not missing, f"Preset {preset.get('name')} missing fields: {missing}"
