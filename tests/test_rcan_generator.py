"""Tests for castor.rcan_generator."""

import pytest

from castor.rcan_generator import (
    BUILT_IN_TEMPLATES,
    extract_config_fields,
    generate_from_template,
    generate_rcan_config,
    list_templates,
)


class TestExtractConfigFields:
    def test_returns_dict_with_required_keys(self):
        fields = extract_config_fields("a simple rover with pca9685")
        for key in (
            "robot_name",
            "uuid",
            "description",
            "provider",
            "model",
            "vision_enabled",
            "camera_type",
            "driver_protocol",
            "capabilities",
        ):
            assert key in fields

    def test_detects_oakd_camera(self):
        fields = extract_config_fields("rover with OAK-D depth camera")
        assert fields["camera_type"] == "oakd"

    def test_detects_oak4_pro(self):
        fields = extract_config_fields("robot with OAK-4 Pro camera and IMU")
        assert fields["camera_type"] == "oakd"

    def test_detects_picamera(self):
        fields = extract_config_fields("RPi rover with Picamera2")
        assert fields["camera_type"] == "picamera2"

    def test_detects_pca9685_driver(self):
        fields = extract_config_fields("4WD kit with PCA9685 motor hat")
        assert fields["driver_protocol"] == "pca9685"

    def test_detects_stepper_driver(self):
        fields = extract_config_fields("arm with NEMA 17 stepper motors and DRV8825")
        assert fields["driver_protocol"] == "stepper"

    def test_detects_odrive(self):
        fields = extract_config_fields("rover with ODrive brushless motors")
        assert fields["driver_protocol"] == "odrive"

    def test_detects_groq_provider(self):
        fields = extract_config_fields("fast bot using Groq inference")
        assert fields["provider"] == "groq"
        assert "llama" in fields["model"].lower()

    def test_detects_anthropic_provider(self):
        fields = extract_config_fields("robot powered by Claude Anthropic")
        assert fields["provider"] == "anthropic"

    def test_detects_ollama_local(self):
        fields = extract_config_fields("fully local offline robot with Ollama")
        assert fields["provider"] == "ollama"

    def test_depth_adds_slam_capability(self):
        fields = extract_config_fields("rover with OAK-D depth camera")
        assert "slam" in fields["capabilities"]
        assert "depth" in fields["capabilities"]

    def test_voice_adds_voice_capability(self):
        fields = extract_config_fields("robot that can speak and listen to voice commands")
        assert "voice" in fields["capabilities"]

    def test_uuid_is_unique(self):
        f1 = extract_config_fields("test robot")
        f2 = extract_config_fields("test robot")
        assert f1["uuid"] != f2["uuid"]


class TestGenerateRcanConfig:
    def test_returns_yaml_string(self):
        yaml = generate_rcan_config("a simple test rover")
        assert isinstance(yaml, str)
        assert len(yaml) > 100

    def test_contains_required_fields(self):
        yaml = generate_rcan_config("RPi rover with pca9685 and Gemini")
        assert "rcan_version" in yaml
        assert "metadata" in yaml
        assert "drivers" in yaml
        assert "agent" in yaml

    def test_llm_fallback_on_bad_output(self):
        brain = type(
            "B", (), {"think": lambda self, *a, **k: type("T", (), {"raw_text": "bad yaml"})()}
        )()
        yaml = generate_rcan_config("test rover", brain=brain)
        assert "rcan_version" in yaml

    def test_llm_used_when_valid(self):
        good_yaml = (
            'rcan_version: "1.1.0"\n'
            "metadata:\n  robot_name: LLM Robot\n"
            "agent:\n  provider: google\n  model: gemini-2.0-flash-exp\n"
            "drivers:\n  - id: wheels\n    protocol: pca9685\n"
        )
        brain = type(
            "B",
            (),
            {"think": lambda self, *a, **k: type("T", (), {"raw_text": good_yaml})()},
        )()
        yaml = generate_rcan_config("test rover", brain=brain)
        assert "LLM Robot" in yaml

    def test_llm_exception_falls_back(self):
        class BadBrain:
            def think(self, *a, **k):
                raise RuntimeError("LLM down")

        yaml = generate_rcan_config("simple test rover", brain=BadBrain())
        assert "rcan_version" in yaml

    def test_no_brain_rule_based(self):
        yaml = generate_rcan_config("stepper arm with NEMA 17 motors and Claude")
        assert "stepper" in yaml
        assert "anthropic" in yaml


class TestTemplates:
    def test_list_templates_returns_dict(self):
        templates = list_templates()
        assert isinstance(templates, dict)
        assert len(templates) >= 5

    def test_all_template_keys_in_builtin(self):
        for key in list_templates():
            assert key in BUILT_IN_TEMPLATES

    def test_generate_from_template_valid(self):
        yaml = generate_from_template("rpi_rover_gemini")
        assert "rcan_version" in yaml

    def test_generate_from_template_invalid(self):
        with pytest.raises(ValueError, match="Unknown template"):
            generate_from_template("nonexistent_template")

    def test_private_local_template_uses_ollama(self):
        yaml = generate_from_template("private_local")
        assert "ollama" in yaml.lower()
