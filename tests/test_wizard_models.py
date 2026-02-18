"""Tests for the redesigned wizard: provider/model separation and secondary models."""

from unittest.mock import patch

from castor.wizard import (
    MODELS,
    PROVIDER_AUTH,
    PROVIDER_ORDER,
    _build_agent_config,
    _check_google_adc,
    _check_huggingface_token,
    _google_auth_flow,
    _huggingface_auth_flow,
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


class TestGoogleAuthFlow:
    """Test Google ADC/OAuth auth flow."""

    def test_check_google_adc_exists(self, tmp_path):
        adc = tmp_path / "application_default_credentials.json"
        adc.write_text("{}")
        with patch.dict("os.environ", {"GOOGLE_APPLICATION_CREDENTIALS": str(adc)}):
            assert _check_google_adc() is True

    def test_check_google_adc_missing(self, tmp_path):
        with patch("os.path.expanduser", return_value=str(tmp_path / "nope")):
            with patch.dict("os.environ", {}, clear=True):
                assert _check_google_adc() is False

    @patch("builtins.input", return_value="1")
    def test_google_adc_already_present(self, _, tmp_path):
        with patch("castor.wizard._check_google_adc", return_value=True):
            with patch("castor.wizard._write_env_var") as mock_write:
                result = _google_auth_flow("GOOGLE_API_KEY")
        assert result is True
        mock_write.assert_called_with("GOOGLE_AUTH_MODE", "adc")

    @patch("builtins.input", side_effect=["2", "fake-key"])
    def test_google_api_key_fallback(self, _):
        with patch("castor.wizard._validate_api_key", return_value=True):
            with patch("castor.wizard._write_env_var") as mock_write:
                result = _google_auth_flow("GOOGLE_API_KEY")
        assert result is True
        mock_write.assert_any_call("GOOGLE_API_KEY", "fake-key")


class TestHuggingFaceAuthFlow:
    """Test HuggingFace CLI login / token auth flow."""

    def test_check_hf_token_exists(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("hf_abc123")
        with patch(
            "os.path.expanduser",
            side_effect=lambda p: (
                str(tmp_path / "token") if "cache" in p else str(tmp_path / "nope")
            ),
        ):
            assert _check_huggingface_token() is True

    @patch("builtins.input", return_value="1")
    def test_hf_cli_already_authed(self, _):
        with patch("castor.wizard._check_huggingface_token", return_value=True):
            with patch("castor.wizard._write_env_var") as mock_write:
                result = _huggingface_auth_flow("HF_TOKEN")
        assert result is True
        mock_write.assert_called_with("HF_AUTH_MODE", "cli")

    @patch("builtins.input", side_effect=["2", "hf_fake_token"])
    def test_hf_paste_token(self, _):
        with patch("castor.wizard._write_env_var") as mock_write:
            result = _huggingface_auth_flow("HF_TOKEN")
        assert result is True
        mock_write.assert_any_call("HF_TOKEN", "hf_fake_token")

    @patch("builtins.input", side_effect=["2", ""])
    def test_hf_skip_token(self, _):
        result = _huggingface_auth_flow("HF_TOKEN")
        assert result is False


class TestProviderAuthFlags:
    """Verify PROVIDER_AUTH has correct flags for OAuth/CLI login."""

    def test_anthropic_has_oauth(self):
        assert PROVIDER_AUTH["anthropic"].get("has_oauth") is True

    def test_google_has_oauth(self):
        assert PROVIDER_AUTH["google"].get("has_oauth") is True

    def test_huggingface_has_cli_login(self):
        assert PROVIDER_AUTH["huggingface"].get("has_cli_login") is True

    def test_openai_no_oauth(self):
        assert PROVIDER_AUTH["openai"].get("has_oauth") is None

    def test_ollama_no_oauth(self):
        assert PROVIDER_AUTH["ollama"].get("has_oauth") is None


class TestDynamicModelFetch:
    """Tests for dynamic model fetching from Anthropic/OpenAI APIs."""

    def test_fetch_anthropic_models_parses_docs_page(self):
        """Should parse model IDs from Anthropic docs HTML."""
        from unittest.mock import MagicMock

        from castor.wizard import _fetch_anthropic_models

        # Simulate HTML with model IDs embedded
        mock_html = b"""
        <p>claude-opus-4-6 is our most capable model.</p>
        <p>claude-sonnet-4-5-20250929 offers great balance.</p>
        <p>claude-haiku-3-5-20241022 is the fastest.</p>
        """

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_html
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("castor.wizard.urlopen", return_value=mock_resp):
            models = _fetch_anthropic_models()

        ids = [m["id"] for m in models]
        assert "claude-opus-4-6" in ids
        assert "claude-sonnet-4-5-20250929" in ids
        assert "claude-haiku-3-5-20241022" in ids

    def test_fetch_anthropic_models_returns_empty_on_failure(self):
        """Should return empty list when docs page is unreachable."""
        from castor.wizard import _fetch_anthropic_models

        with patch("castor.wizard.urlopen", side_effect=Exception("timeout")):
            models = _fetch_anthropic_models()
        assert models == []

    def test_fetch_openai_models_filters_chat_models(self):
        """Should only return chat-relevant models, not embeddings/tts/etc."""
        import json
        from unittest.mock import MagicMock

        from castor.wizard import _fetch_openai_models

        mock_data = json.dumps(
            {
                "data": [
                    {"id": "gpt-4.1", "created": 1700000003},
                    {"id": "gpt-4.1-mini", "created": 1700000002},
                    {"id": "text-embedding-3-large", "created": 1700000001},
                    {"id": "tts-1", "created": 1700000000},
                    {"id": "dall-e-3", "created": 1699999999},
                    {"id": "o3-mini", "created": 1700000004},
                ]
            }
        ).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("castor.wizard.urlopen", return_value=mock_resp):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
                models = _fetch_openai_models()

        ids = [m["id"] for m in models]
        assert "gpt-4.1" in ids
        assert "gpt-4.1-mini" in ids
        assert "o3-mini" in ids
        assert "text-embedding-3-large" not in ids
        assert "tts-1" not in ids
        assert "dall-e-3" not in ids

    def test_fetch_openai_models_sorted_newest_first(self):
        """Models should be sorted by creation date, newest first."""
        import json
        from unittest.mock import MagicMock

        from castor.wizard import _fetch_openai_models

        mock_data = json.dumps(
            {
                "data": [
                    {"id": "gpt-4.1-mini", "created": 100},
                    {"id": "gpt-4.1", "created": 300},
                    {"id": "gpt-4o", "created": 200},
                ]
            }
        ).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("castor.wizard.urlopen", return_value=mock_resp):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
                models = _fetch_openai_models()

        assert models[0]["id"] == "gpt-4.1"
        assert models[1]["id"] == "gpt-4o"
        assert models[2]["id"] == "gpt-4.1-mini"

    def test_choose_model_falls_back_on_api_failure(self):
        """Should fall back to static MODELS list when API fetch fails."""
        with patch("castor.wizard._fetch_anthropic_models", side_effect=Exception("timeout")):
            with patch("builtins.input", return_value="1"):
                model = choose_model("anthropic")
        assert model["id"] in [m["id"] for m in MODELS["anthropic"]]

    def test_fetch_openai_models_no_key(self):
        """Should return empty list when no API key available."""
        from castor.wizard import _fetch_openai_models

        with patch.dict("os.environ", {}, clear=True):
            models = _fetch_openai_models()
        assert models == []


class TestBrainPresets:
    def test_all_presets_have_required_fields(self):
        from castor.wizard import BRAIN_PRESETS

        for preset in BRAIN_PRESETS:
            assert "name" in preset
            assert "desc" in preset
            assert "primary" in preset
            assert "provider" in preset["primary"]
            assert "model" in preset["primary"]
            assert "cost" in preset

    def test_preset_count(self):
        from castor.wizard import BRAIN_PRESETS

        assert len(BRAIN_PRESETS) >= 5

    def test_free_preset_exists(self):
        from castor.wizard import BRAIN_PRESETS

        free = [p for p in BRAIN_PRESETS if p["cost"] == "free"]
        assert len(free) >= 1
        assert free[0]["primary"]["provider"] == "huggingface"
