"""Tests for the redesigned wizard: provider/model separation and secondary models."""

from unittest.mock import patch

import pytest

from castor.wizard import (
    MODELS,
    PROVIDER_AUTH,
    PROVIDER_ORDER,
    SECONDARY_MODELS,
    _build_agent_config,
    choose_model,
    choose_provider_step,
    choose_secondary_models,
    generate_preset_config,
)


class TestProviderAuth:
    """PROVIDER_AUTH data structure tests."""

    def test_all_providers_have_required_keys(self):
        for key, info in PROVIDER_AUTH.items():
            assert "env_var" in info, f"{key} missing env_var"
            assert "label" in info, f"{key} missing label"
            assert "desc" in info, f"{key} missing desc"

    def test_provider_order_matches_auth(self):
        for p in PROVIDER_ORDER:
            assert p in PROVIDER_AUTH, f"{p} in PROVIDER_ORDER but not PROVIDER_AUTH"

    def test_ollama_no_api_key(self):
        assert PROVIDER_AUTH["ollama"]["env_var"] is None


class TestModels:
    """MODELS data structure tests."""

    def test_all_providers_have_models(self):
        for p in PROVIDER_ORDER:
            assert p in MODELS, f"{p} missing from MODELS"

    def test_each_model_has_required_fields(self):
        for provider, model_list in MODELS.items():
            for m in model_list:
                assert "id" in m, f"{provider} model missing id"
                assert "label" in m, f"{provider} model missing label"
                assert "desc" in m, f"{provider} model missing desc"
                assert "tags" in m, f"{provider} model missing tags"

    def test_each_provider_has_one_recommended(self):
        for provider, model_list in MODELS.items():
            if not model_list:
                continue  # ollama is dynamic
            recs = [m for m in model_list if m.get("recommended")]
            assert len(recs) == 1, f"{provider} should have exactly 1 recommended model"


class TestChooseProviderStep:
    """Test choose_provider_step menu."""

    @patch("builtins.input", return_value="")
    def test_default_is_anthropic(self, _):
        assert choose_provider_step() == "anthropic"

    @patch("builtins.input", return_value="2")
    def test_select_google(self, _):
        assert choose_provider_step() == "google"

    @patch("builtins.input", return_value="5")
    def test_select_ollama(self, _):
        assert choose_provider_step() == "ollama"

    @patch("builtins.input", return_value="99")
    def test_invalid_defaults_anthropic(self, _):
        assert choose_provider_step() == "anthropic"


class TestChooseModel:
    """Test choose_model menu."""

    @patch("builtins.input", return_value="")
    def test_default_anthropic_model(self, _):
        m = choose_model("anthropic")
        assert m["id"] == "claude-opus-4-6"

    @patch("builtins.input", return_value="2")
    def test_select_second_model(self, _):
        m = choose_model("google")
        assert m["id"] == "gemini-2.5-pro"

    @patch("builtins.input", return_value="3")
    def test_select_third_openai(self, _):
        m = choose_model("openai")
        assert m["id"] == "gpt-4o"

    @patch("builtins.input", return_value="99")
    def test_invalid_defaults_to_first(self, _):
        m = choose_model("anthropic")
        assert m["id"] == "claude-opus-4-6"


class TestBuildAgentConfig:
    """Test _build_agent_config backward compat."""

    def test_has_required_keys(self):
        model = MODELS["anthropic"][0]
        cfg = _build_agent_config("anthropic", model)
        assert cfg["provider"] == "anthropic"
        assert cfg["model"] == "claude-opus-4-6"
        assert "label" in cfg
        assert cfg["env_var"] == "ANTHROPIC_API_KEY"

    def test_google_config(self):
        model = MODELS["google"][1]
        cfg = _build_agent_config("google", model)
        assert cfg["provider"] == "google"
        assert cfg["model"] == "gemini-2.5-pro"
        assert cfg["env_var"] == "GOOGLE_API_KEY"


class TestSecondaryModels:
    """Test choose_secondary_models."""

    @patch("builtins.input", return_value="")
    def test_skip(self, _):
        result = choose_secondary_models("anthropic", {"anthropic"})
        assert result == []

    @patch("builtins.input", return_value="1")
    def test_select_one(self, mock_input):
        # The secondary model is google, so auth will be called
        with patch("castor.wizard.authenticate_provider"):
            result = choose_secondary_models("anthropic", {"anthropic"})
        assert len(result) == 1
        assert result[0]["provider"] == "google"
        assert result[0]["model"] == "gemini-er-1.5"

    @patch("builtins.input", return_value="1,3")
    def test_select_multiple(self, mock_input):
        with patch("castor.wizard.authenticate_provider"):
            result = choose_secondary_models("anthropic", {"anthropic"})
        assert len(result) == 2


class TestGeneratePresetWithSecondary:
    """Test that secondary models appear in generated config."""

    def test_no_secondary(self):
        cfg = {"provider": "anthropic", "model": "claude-opus-4-6"}
        config = generate_preset_config("rpi_rc_car", "TestBot", cfg)
        assert "secondary_models" not in config.get("agent", {})

    def test_with_secondary(self):
        cfg = {"provider": "anthropic", "model": "claude-opus-4-6"}
        sec = [{"provider": "google", "model": "gemini-er-1.5", "tags": ["robotics"]}]
        config = generate_preset_config("rpi_rc_car", "TestBot", cfg, secondary_models=sec)
        assert "secondary_models" in config["agent"]
        assert len(config["agent"]["secondary_models"]) == 1
        assert config["agent"]["secondary_models"][0]["model"] == "gemini-er-1.5"
