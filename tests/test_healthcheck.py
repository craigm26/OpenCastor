"""Tests for castor.healthcheck — startup health check module."""



from castor.healthcheck import (
    _check_config_valid,
    _check_dependencies,
    _check_package_version,
    _check_provider_auth,
    _check_python_version,
    print_health_report,
    run_startup_checks,
)


class TestIndividualChecks:
    def test_python_version_ok(self):
        result = _check_python_version()
        assert result["status"] in ("ok", "warn")
        assert "Python" in result["name"]

    def test_package_version(self):
        result = _check_package_version()
        assert result["status"] in ("ok", "warn")

    def test_dependencies_ok(self):
        result = _check_dependencies()
        assert result["name"] == "Dependencies"
        # yaml, fastapi, uvicorn should be installed in test env
        assert result["status"] in ("ok", "warn")

    def test_config_valid_empty(self):
        result = _check_config_valid({})
        assert result["status"] == "fail"

    def test_config_valid_good(self):
        config = {
            "metadata": {"robot_name": "TestBot"},
            "agent": {"provider": "anthropic", "model": "claude-opus-4-6"},
        }
        result = _check_config_valid(config)
        assert result["status"] == "ok"
        assert "TestBot" in result["detail"]

    def test_config_valid_missing_name(self):
        config = {"metadata": {}, "agent": {"provider": "anthropic"}}
        result = _check_config_valid(config)
        assert result["status"] == "warn"

    def test_provider_auth_env_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        result = _check_provider_auth({"agent": {"provider": "anthropic"}})
        assert result["status"] == "ok"

    def test_provider_auth_missing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path / "nonexistent"))
        monkeypatch.chdir(tmp_path)
        result = _check_provider_auth({"agent": {"provider": "anthropic"}})
        assert result["status"] == "fail"

    def test_provider_auth_ollama(self):
        result = _check_provider_auth({"agent": {"provider": "ollama"}})
        assert result["status"] == "ok"

    def test_provider_auth_stored_token(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        token_file = tmp_path / ".opencastor" / "anthropic-token"
        token_file.parent.mkdir(parents=True)
        token_file.write_text("sk-ant-oat01-test")
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: str(token_file) if "anthropic-token" in p else p,
        )
        monkeypatch.chdir(tmp_path)
        result = _check_provider_auth({"agent": {"provider": "anthropic"}})
        assert result["status"] == "ok"
        assert "setup-token" in result["detail"]


class TestRunStartupChecks:
    def test_returns_required_fields(self):
        config = {
            "metadata": {"robot_name": "TestBot"},
            "agent": {"provider": "ollama"},
        }
        result = run_startup_checks(config, simulate=True)
        assert "status" in result
        assert "checks" in result
        assert "summary" in result
        assert "duration_ms" in result
        assert result["status"] in ("healthy", "degraded", "critical")

    def test_simulate_skips_hardware(self):
        config = {
            "metadata": {"robot_name": "TestBot"},
            "agent": {"provider": "ollama"},
        }
        result = run_startup_checks(config, simulate=True)
        hw_checks = [c for c in result["checks"] if c["name"] == "Hardware"]
        assert len(hw_checks) == 1
        assert hw_checks[0]["status"] == "skip"

    def test_print_health_report_no_crash(self, capsys):
        result = {
            "status": "healthy",
            "checks": [
                {"name": "Test", "status": "ok", "detail": "Good"},
                {"name": "Test2", "status": "warn", "detail": "Meh"},
            ],
            "summary": "1 passed, 1 warnings, 0 failed, 0 skipped",
            "duration_ms": 5.2,
        }
        print_health_report(result)
        captured = capsys.readouterr()
        assert "SYSTEM HEALTH CHECK" in captured.out
        assert "HEALTHY" in captured.out
        assert "5.2ms" in captured.out


class TestWizardState:
    def test_save_and_load_state(self, tmp_path, monkeypatch):
        from castor.wizard import _load_previous_state, _save_wizard_state

        state_path = tmp_path / "wizard-state.yaml"
        monkeypatch.setattr("castor.wizard.WIZARD_STATE_PATH", str(state_path))

        _save_wizard_state({"robot_name": "TestBot", "provider": "anthropic"})
        loaded = _load_previous_state()
        assert loaded["robot_name"] == "TestBot"
        assert loaded["provider"] == "anthropic"

    def test_load_missing_state(self, tmp_path, monkeypatch):
        from castor.wizard import _load_previous_state

        monkeypatch.setattr("castor.wizard.WIZARD_STATE_PATH", str(tmp_path / "nonexistent.yaml"))
        assert _load_previous_state() == {}
