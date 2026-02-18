"""Tests for castor.doctor -- system health checks."""

import os
from unittest.mock import MagicMock, patch

from castor.doctor import (
    check_camera,
    check_env_file,
    check_hardware_sdks,
    check_provider_keys,
    check_python_version,
    check_rcan_config,
    print_report,
    run_all_checks,
    run_post_wizard_checks,
)


# =====================================================================
# check_python_version
# =====================================================================
class TestCheckPythonVersion:
    def test_passes_on_current_python(self):
        ok, name, detail = check_python_version()
        # We're running on 3.10+ in dev, so this should pass
        assert ok is True
        assert "Python" in name


# =====================================================================
# check_env_file
# =====================================================================
class TestCheckEnvFile:
    @patch("castor.doctor.os.path.exists", return_value=True)
    def test_passes_when_env_exists(self, mock_exists):
        ok, name, detail = check_env_file()
        assert ok is True
        assert "found" in detail

    @patch("castor.doctor.os.path.exists", return_value=False)
    def test_fails_when_env_missing(self, mock_exists):
        ok, name, detail = check_env_file()
        assert ok is False
        assert "missing" in detail


# =====================================================================
# check_provider_keys
# =====================================================================
class TestCheckProviderKeys:
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=True)
    def test_reports_available_provider(self):
        results = check_provider_keys()
        google_result = [r for r in results if "google" in r[1]]
        assert len(google_result) == 1
        assert google_result[0][0] is True

    @patch.dict(os.environ, {}, clear=True)
    def test_reports_missing_provider(self):
        results = check_provider_keys()
        google_result = [r for r in results if "google" in r[1]]
        assert len(google_result) == 1
        assert google_result[0][0] is False

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key"}, clear=True)
    def test_config_scoped_check(self):
        config = {"agent": {"provider": "anthropic"}}
        results = check_provider_keys(config)
        assert len(results) == 1
        assert results[0][0] is True

    @patch.dict(os.environ, {}, clear=True)
    @patch("castor.auth.os.path.expanduser", return_value="/tmp/nonexistent/.opencastor/anthropic-token")
    def test_config_scoped_missing(self, mock_expand):
        config = {"agent": {"provider": "anthropic"}}
        results = check_provider_keys(config)
        assert len(results) == 1
        assert results[0][0] is False


# =====================================================================
# check_rcan_config
# =====================================================================
class TestCheckRcanConfig:
    def test_no_path(self):
        ok, name, detail = check_rcan_config(None)
        assert ok is False

    def test_missing_file(self):
        ok, name, detail = check_rcan_config("/nonexistent/file.yaml")
        assert ok is False
        assert "not found" in detail

    def test_valid_preset(self):
        preset = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "presets",
            "rpi_rc_car.rcan.yaml",
        )
        if os.path.exists(preset):
            ok, name, detail = check_rcan_config(preset)
            # Result depends on schema match; just ensure no crash
            assert isinstance(ok, bool)


# =====================================================================
# check_hardware_sdks
# =====================================================================
class TestCheckHardwareSDKs:
    def test_returns_list(self):
        results = check_hardware_sdks()
        assert isinstance(results, list)
        assert len(results) == 4  # dynamixel, pca9685, picamera2, cv2

    def test_each_result_is_tuple(self):
        for ok, name, detail in check_hardware_sdks():
            assert isinstance(ok, bool)
            assert isinstance(name, str)
            assert isinstance(detail, str)


# =====================================================================
# check_camera
# =====================================================================
class TestCheckCamera:
    def test_camera_accessible(self):
        mock_cv2 = MagicMock()
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cv2.VideoCapture.return_value = mock_cap
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            ok, name, detail = check_camera()
        assert ok is True
        assert "accessible" in detail
        mock_cap.release.assert_called_once()

    def test_camera_not_accessible(self):
        mock_cv2 = MagicMock()
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cv2.VideoCapture.return_value = mock_cap
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            ok, name, detail = check_camera()
        assert ok is False

    def test_camera_no_opencv(self):
        # When cv2 can't be imported, should report not installed
        import sys as _sys

        saved = _sys.modules.get("cv2")
        _sys.modules["cv2"] = None  # Force ImportError on import
        try:
            ok, name, detail = check_camera()
            assert ok is False
            assert "not installed" in detail
        finally:
            if saved is not None:
                _sys.modules["cv2"] = saved
            else:
                _sys.modules.pop("cv2", None)


# =====================================================================
# run_all_checks
# =====================================================================
class TestRunAllChecks:
    def test_returns_list_of_tuples(self):
        results = run_all_checks()
        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, tuple)
            assert len(item) == 3


# =====================================================================
# run_post_wizard_checks
# =====================================================================
class TestRunPostWizardChecks:
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key"}, clear=True)
    def test_checks_provider_and_config(self):
        results = run_post_wizard_checks("/nonexistent.yaml", {}, "anthropic")
        # Should have RCAN config check (fail) + provider check (pass)
        assert len(results) == 2
        # First is RCAN (fails because file doesn't exist)
        assert results[0][0] is False
        # Second is provider key
        assert results[1][0] is True


# =====================================================================
# print_report
# =====================================================================
class TestPrintReport:
    def test_all_pass(self, capsys):
        results = [(True, "Test", "ok")]
        assert print_report(results) is True
        out = capsys.readouterr().out
        assert "1 passed, 0 failed" in out

    def test_with_failure(self, capsys):
        results = [(True, "A", "ok"), (False, "B", "bad")]
        assert print_report(results) is False
        out = capsys.readouterr().out
        assert "1 passed, 1 failed" in out
