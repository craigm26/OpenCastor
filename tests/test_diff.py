"""Tests for castor.diff -- compare two RCAN config files."""

import yaml

from castor.diff import diff_configs


# =====================================================================
# diff_configs -- identical configs
# =====================================================================
class TestDiffIdentical:
    def test_identical_configs_returns_empty_list(self, tmp_path):
        config = {
            "rcan_version": "1.0",
            "metadata": {"name": "test"},
            "agent": {"provider": "google"},
        }
        file_a = tmp_path / "a.rcan.yaml"
        file_b = tmp_path / "b.rcan.yaml"
        file_a.write_text(yaml.dump(config))
        file_b.write_text(yaml.dump(config))

        diffs = diff_configs(str(file_a), str(file_b))
        assert diffs == []


# =====================================================================
# diff_configs -- different values
# =====================================================================
class TestDiffDifferentValues:
    def test_different_values(self, tmp_path):
        config_a = {"rcan_version": "1.0", "metadata": {"name": "alpha"}}
        config_b = {"rcan_version": "1.0", "metadata": {"name": "beta"}}

        file_a = tmp_path / "a.rcan.yaml"
        file_b = tmp_path / "b.rcan.yaml"
        file_a.write_text(yaml.dump(config_a))
        file_b.write_text(yaml.dump(config_b))

        diffs = diff_configs(str(file_a), str(file_b))
        assert len(diffs) == 1
        key_path, val_a, val_b = diffs[0]
        assert key_path == "metadata.name"
        assert val_a == "alpha"
        assert val_b == "beta"


# =====================================================================
# diff_configs -- missing keys
# =====================================================================
class TestDiffMissingKeys:
    def test_missing_keys_in_b(self, tmp_path):
        config_a = {"rcan_version": "1.0", "extra_key": "value"}
        config_b = {"rcan_version": "1.0"}

        file_a = tmp_path / "a.rcan.yaml"
        file_b = tmp_path / "b.rcan.yaml"
        file_a.write_text(yaml.dump(config_a))
        file_b.write_text(yaml.dump(config_b))

        diffs = diff_configs(str(file_a), str(file_b))
        assert len(diffs) == 1
        key_path, val_a, val_b = diffs[0]
        assert key_path == "extra_key"
        assert val_a == "value"
        assert val_b == "<missing>"

    def test_missing_keys_in_a(self, tmp_path):
        config_a = {"rcan_version": "1.0"}
        config_b = {"rcan_version": "1.0", "new_key": "new_value"}

        file_a = tmp_path / "a.rcan.yaml"
        file_b = tmp_path / "b.rcan.yaml"
        file_a.write_text(yaml.dump(config_a))
        file_b.write_text(yaml.dump(config_b))

        diffs = diff_configs(str(file_a), str(file_b))
        assert len(diffs) == 1
        key_path, val_a, val_b = diffs[0]
        assert key_path == "new_key"
        assert val_a == "<missing>"
        assert val_b == "new_value"


# =====================================================================
# diff_configs -- nested dict differences
# =====================================================================
class TestDiffNestedDicts:
    def test_nested_dict_differences(self, tmp_path):
        config_a = {
            "agent": {
                "provider": "google",
                "model": "gemini-pro",
            }
        }
        config_b = {
            "agent": {
                "provider": "openai",
                "model": "gpt-4.1",
            }
        }

        file_a = tmp_path / "a.rcan.yaml"
        file_b = tmp_path / "b.rcan.yaml"
        file_a.write_text(yaml.dump(config_a))
        file_b.write_text(yaml.dump(config_b))

        diffs = diff_configs(str(file_a), str(file_b))
        assert len(diffs) == 2

        key_paths = [d[0] for d in diffs]
        assert "agent.model" in key_paths
        assert "agent.provider" in key_paths

    def test_deeply_nested_change(self, tmp_path):
        config_a = {"level1": {"level2": {"level3": "old"}}}
        config_b = {"level1": {"level2": {"level3": "new"}}}

        file_a = tmp_path / "a.rcan.yaml"
        file_b = tmp_path / "b.rcan.yaml"
        file_a.write_text(yaml.dump(config_a))
        file_b.write_text(yaml.dump(config_b))

        diffs = diff_configs(str(file_a), str(file_b))
        assert len(diffs) == 1
        assert diffs[0][0] == "level1.level2.level3"
        assert diffs[0][1] == "old"
        assert diffs[0][2] == "new"


# =====================================================================
# diff_configs -- list differences
# =====================================================================
class TestDiffLists:
    def test_list_differences(self, tmp_path):
        config_a = {"drivers": ["dynamixel", "pca9685"]}
        config_b = {"drivers": ["dynamixel", "servo"]}

        file_a = tmp_path / "a.rcan.yaml"
        file_b = tmp_path / "b.rcan.yaml"
        file_a.write_text(yaml.dump(config_a))
        file_b.write_text(yaml.dump(config_b))

        diffs = diff_configs(str(file_a), str(file_b))
        assert len(diffs) == 1
        key_path, val_a, val_b = diffs[0]
        assert key_path == "drivers[1]"
        assert val_a == "pca9685"
        assert val_b == "servo"

    def test_list_length_difference(self, tmp_path):
        config_a = {"items": ["a", "b"]}
        config_b = {"items": ["a", "b", "c"]}

        file_a = tmp_path / "a.rcan.yaml"
        file_b = tmp_path / "b.rcan.yaml"
        file_a.write_text(yaml.dump(config_a))
        file_b.write_text(yaml.dump(config_b))

        diffs = diff_configs(str(file_a), str(file_b))
        assert len(diffs) == 1
        key_path, val_a, val_b = diffs[0]
        assert key_path == "items[2]"
        assert val_a == "<missing>"
        assert val_b == "c"

    def test_empty_configs(self, tmp_path):
        file_a = tmp_path / "a.rcan.yaml"
        file_b = tmp_path / "b.rcan.yaml"
        file_a.write_text("")
        file_b.write_text("")

        diffs = diff_configs(str(file_a), str(file_b))
        assert diffs == []
