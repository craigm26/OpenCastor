"""Tests for castor.profiles -- manage named config profiles."""

import os
from unittest.mock import patch

import pytest

from castor import profiles
from castor.profiles import (
    get_active_profile,
    list_profiles,
    remove_profile,
    save_profile,
    use_profile,
)


# =====================================================================
# save_profile
# =====================================================================
class TestSaveProfile:
    def test_save_profile_copies_file_to_profiles_dir(self, tmp_path):
        profiles_dir = str(tmp_path / "profiles")
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text("rcan_version: '1.0'\n")

        with patch.object(profiles, "_PROFILES_DIR", profiles_dir):
            dest = save_profile("test_profile", str(config_file))

        assert os.path.exists(dest)
        assert dest == os.path.join(profiles_dir, "test_profile.rcan.yaml")
        with open(dest) as f:
            assert "rcan_version" in f.read()

    def test_save_profile_nonexistent_config_raises(self, tmp_path):
        profiles_dir = str(tmp_path / "profiles")
        with patch.object(profiles, "_PROFILES_DIR", profiles_dir):
            with pytest.raises(FileNotFoundError):
                save_profile("bad", "/nonexistent/config.yaml")


# =====================================================================
# use_profile
# =====================================================================
class TestUseProfile:
    def test_use_profile_copies_to_cwd(self, tmp_path):
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        profile_file = profiles_dir / "indoor.rcan.yaml"
        profile_file.write_text("rcan_version: '1.0'\nmetadata:\n  name: indoor\n")

        active_file = str(tmp_path / "active-profile")

        with (
            patch.object(profiles, "_PROFILES_DIR", str(profiles_dir)),
            patch.object(profiles, "_ACTIVE_FILE", active_file),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            result = use_profile("indoor")

        assert result == str(profile_file)
        dest = tmp_path / "robot.rcan.yaml"
        assert dest.exists()
        assert "indoor" in dest.read_text()

    def test_use_profile_nonexistent_raises(self, tmp_path):
        profiles_dir = str(tmp_path / "profiles")
        os.makedirs(profiles_dir, exist_ok=True)

        with patch.object(profiles, "_PROFILES_DIR", profiles_dir):
            with pytest.raises(FileNotFoundError, match="Profile not found"):
                use_profile("nonexistent_profile")


# =====================================================================
# list_profiles
# =====================================================================
class TestListProfiles:
    def test_list_profiles_returns_correct_format(self, tmp_path):
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "indoor.rcan.yaml").write_text("test: true")
        (profiles_dir / "outdoor.rcan.yaml").write_text("test: true")
        (profiles_dir / "readme.txt").write_text("ignore me")

        active_file = str(tmp_path / "active-profile")
        with open(active_file, "w") as f:
            f.write("indoor")

        with (
            patch.object(profiles, "_PROFILES_DIR", str(profiles_dir)),
            patch.object(profiles, "_ACTIVE_FILE", active_file),
        ):
            result = list_profiles()

        assert len(result) == 2
        names = [p["name"] for p in result]
        assert "indoor" in names
        assert "outdoor" in names

        indoor = [p for p in result if p["name"] == "indoor"][0]
        assert indoor["active"] is True

        outdoor = [p for p in result if p["name"] == "outdoor"][0]
        assert outdoor["active"] is False

    def test_list_profiles_empty_dir(self, tmp_path):
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()

        with (
            patch.object(profiles, "_PROFILES_DIR", str(profiles_dir)),
            patch.object(profiles, "_ACTIVE_FILE", str(tmp_path / "active-profile")),
        ):
            result = list_profiles()

        assert result == []


# =====================================================================
# remove_profile
# =====================================================================
class TestRemoveProfile:
    def test_remove_profile_deletes_file(self, tmp_path):
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        profile_file = profiles_dir / "old.rcan.yaml"
        profile_file.write_text("test: true")

        with (
            patch.object(profiles, "_PROFILES_DIR", str(profiles_dir)),
            patch.object(profiles, "_ACTIVE_FILE", str(tmp_path / "active-profile")),
        ):
            result = remove_profile("old")

        assert result is True
        assert not profile_file.exists()

    def test_remove_nonexistent_profile_returns_false(self, tmp_path):
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()

        with (
            patch.object(profiles, "_PROFILES_DIR", str(profiles_dir)),
            patch.object(profiles, "_ACTIVE_FILE", str(tmp_path / "active-profile")),
        ):
            result = remove_profile("does_not_exist")

        assert result is False


# =====================================================================
# get_active_profile
# =====================================================================
class TestGetActiveProfile:
    def test_get_active_profile_reads_file(self, tmp_path):
        active_file = tmp_path / "active-profile"
        active_file.write_text("my_robot")

        with patch.object(profiles, "_ACTIVE_FILE", str(active_file)):
            assert get_active_profile() == "my_robot"

    def test_get_active_profile_no_file_returns_none(self, tmp_path):
        with patch.object(profiles, "_ACTIVE_FILE", str(tmp_path / "nonexistent")):
            assert get_active_profile() is None
