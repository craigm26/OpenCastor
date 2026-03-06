"""Tests for castor.updater — self-update checks."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest
from castor.updater import get_version_info, check_latest_pypi, VersionInfo


def test_version_info_up_to_date():
    with patch("castor.updater._current_version", return_value="1.0.0"), \
         patch("castor.updater.check_latest_pypi", return_value="1.0.0"):
        info = get_version_info()
        assert info.up_to_date is True
        assert info.current == "1.0.0"
        assert info.latest == "1.0.0"


def test_version_info_update_available():
    with patch("castor.updater._current_version", return_value="1.0.0"), \
         patch("castor.updater.check_latest_pypi", return_value="2.0.0"):
        info = get_version_info()
        assert info.up_to_date is False
        assert info.latest == "2.0.0"
        assert "2.0.0" in info.release_url


def test_version_info_pypi_unavailable():
    with patch("castor.updater._current_version", return_value="1.0.0"), \
         patch("castor.updater.check_latest_pypi", return_value=None):
        info = get_version_info()
        assert info.up_to_date is True  # fallback: current == latest


def test_version_info_unknown_version():
    with patch("castor.updater._current_version", return_value="unknown"), \
         patch("castor.updater.check_latest_pypi", return_value="2.0.0"):
        info = get_version_info()
        # When current is unknown, string comparison: "unknown" != "2.0.0"
        assert isinstance(info.up_to_date, bool)


def test_check_latest_pypi_network_error():
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        result = check_latest_pypi("nonexistent-pkg-xyz")
        assert result is None


def test_version_info_dataclass():
    info = VersionInfo(current="1.0", latest="2.0", up_to_date=False, release_url="https://example.com")
    assert info.current == "1.0"
    assert not info.up_to_date


def test_do_upgrade_aborted(monkeypatch):
    from castor.updater import do_upgrade
    monkeypatch.setattr("builtins.input", lambda _: "n")
    rc = do_upgrade(yes=False)
    assert rc == 0


def test_do_upgrade_yes_flag():
    from castor.updater import do_upgrade
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        rc = do_upgrade(yes=True)
        assert rc == 0
        mock_run.assert_called_once()
        assert "--upgrade" in mock_run.call_args[0][0]
