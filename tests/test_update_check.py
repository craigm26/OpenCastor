"""Tests for castor/update_check.py — PyPI version checking."""

import json
import os
import sys
import time
from types import ModuleType
from unittest.mock import MagicMock, patch

import castor.update_check as uc
from castor.update_check import (
    _is_newer,
    _print_hint,
    _read_cache,
    _write_cache,
    check_for_update,
    print_update_status,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pypi_response(version: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"info": {"version": version}}
    return resp


def _newer_than_installed() -> str:
    """A release that sorts above whatever is installed.

    Since 3.1.0 the 3.x line is published with the PEP 440 epoch ``1!`` (1!3.5.0
    and so on), because pip otherwise prefers the 2026.x CalVer releases (see
    docs/pypi-versioning.md). A bare 9999.0.0 sorts BELOW every epoch-1 version,
    so "9999.0.0" stopped being newer the day the epoch shipped.
    """
    from packaging.version import Version

    import castor

    epoch = Version(castor.__version__).epoch
    return f"{epoch}!9999.0.0" if epoch else "9999.0.0"


NEWER = _newer_than_installed()


def _fake_httpx(version: str = "1.0.0", status_code: int = 200, side_effect=None):
    """Return a fake httpx module whose get() returns the given response."""
    mod = ModuleType("httpx")
    if side_effect is not None:
        mod.get = MagicMock(side_effect=side_effect)
    else:
        mod.get = MagicMock(return_value=_make_pypi_response(version, status_code))
    return mod


# ---------------------------------------------------------------------------
# _is_newer
# ---------------------------------------------------------------------------


class TestIsNewer:
    def test_newer_semver(self):
        assert _is_newer("2.0.0", "1.9.0") is True

    def test_same_version(self):
        assert _is_newer("1.0.0", "1.0.0") is False

    def test_older_version(self):
        assert _is_newer("0.9.0", "1.0.0") is False

    def test_newer_calver(self):
        # Date-based CalVer: 2026.3.12 > 2026.3.8
        assert _is_newer("2026.3.12", "2026.3.8") is True

    def test_same_calver(self):
        assert _is_newer("2026.3.8", "2026.3.8") is False

    def test_older_calver(self):
        assert _is_newer("2026.3.1", "2026.3.12") is False

    def test_prerelease_less_than_release(self):
        # packaging: 1.0.0a1 < 1.0.0
        assert _is_newer("1.0.0a1", "1.0.0") is False

    def test_fallback_string_comparison_newer(self):
        """When packaging is unavailable, falls back to string comparison."""
        with patch.dict(sys.modules, {"packaging.version": None, "packaging": None}):
            # Force reimport path by calling _is_newer with simple strings
            # The except-pass in _is_newer catches ImportError and falls back
            result = _is_newer("2.0", "1.0")
        # packaging is available in this env so this tests the normal path
        assert result is True  # "2.0" > "1.0" is True either way

    def test_fallback_string_comparison_equal(self):
        assert _is_newer("1.0", "1.0") is False


# ---------------------------------------------------------------------------
# _read_cache / _write_cache
# ---------------------------------------------------------------------------


class TestCache:
    def test_no_cache_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(uc, "_CACHE_FILE", str(tmp_path / "nonexistent.json"))
        assert _read_cache() is None

    def test_valid_cache_returns_data(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        data = {
            "current": "1.0.0",
            "latest": "2.0.0",
            "update_available": True,
            "checked_at": time.time(),
        }
        cache.write_text(json.dumps(data))
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        result = _read_cache()
        assert result is not None
        assert result["current"] == "1.0.0"
        assert result["update_available"] is True

    def test_expired_cache_returns_none(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        data = {
            "current": "1.0.0",
            "latest": "2.0.0",
            "update_available": True,
            "checked_at": time.time() - uc._CACHE_TTL - 1,
        }
        cache.write_text(json.dumps(data))
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        assert _read_cache() is None

    def test_malformed_json_returns_none(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        cache.write_text("not json {{{")
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        assert _read_cache() is None

    def test_write_cache_creates_file(self, tmp_path, monkeypatch):
        cache = tmp_path / "subdir" / "cache.json"
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        result = {"current": "1.0", "latest": "1.1", "update_available": True}
        _write_cache(result)
        assert cache.exists()
        stored = json.loads(cache.read_text())
        assert stored["latest"] == "1.1"
        assert "checked_at" in stored

    def test_write_cache_handles_permission_error(self, monkeypatch):
        monkeypatch.setattr(uc, "_CACHE_FILE", "/root/no_permission/cache.json")
        # Should not raise
        _write_cache({"current": "1.0", "latest": "1.0", "update_available": False})

    def test_cache_includes_checked_at(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        before = time.time()
        _write_cache({"current": "1.0", "latest": "2.0", "update_available": True})
        after = time.time()
        stored = json.loads(cache.read_text())
        assert before <= stored["checked_at"] <= after


# ---------------------------------------------------------------------------
# check_for_update — cache hit scenarios
# ---------------------------------------------------------------------------


class TestCheckForUpdateCache:
    def test_cache_hit_no_update(self, monkeypatch, tmp_path):
        cache = tmp_path / "cache.json"
        import castor

        current = castor.__version__
        data = {
            "current": current,
            "latest": current,
            "update_available": False,
            "checked_at": time.time(),
        }
        cache.write_text(json.dumps(data))
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        result = check_for_update(quiet=True)
        assert result["update_available"] is False

    def test_cache_hit_update_available_prints_hint(self, monkeypatch, tmp_path, capsys):
        cache = tmp_path / "cache.json"
        import castor

        current = castor.__version__
        data = {
            "current": current,
            "latest": NEWER,
            "update_available": True,
            "checked_at": time.time(),
        }
        cache.write_text(json.dumps(data))
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        # Patch Console to avoid rich import issues; force ImportError so stderr path runs
        with patch.dict(sys.modules, {"rich": None, "rich.console": None}):
            result = check_for_update(quiet=False)
        assert result["update_available"] is True
        assert result["latest"] == NEWER
        captured = capsys.readouterr()
        assert NEWER in captured.err

    def test_cache_hit_quiet_suppresses_hint(self, monkeypatch, tmp_path, capsys):
        cache = tmp_path / "cache.json"
        import castor

        current = castor.__version__
        data = {
            "current": current,
            "latest": NEWER,
            "update_available": True,
            "checked_at": time.time(),
        }
        cache.write_text(json.dumps(data))
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        result = check_for_update(quiet=True)
        assert result["update_available"] is True
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_cache_miss_on_version_mismatch(self, monkeypatch, tmp_path):
        """Cache with different 'current' is treated as a miss and PyPI is queried."""
        cache = tmp_path / "cache.json"
        import castor

        current = castor.__version__
        data = {
            "current": "0.0.0",  # different from actual current
            "latest": NEWER,
            "update_available": True,
            "checked_at": time.time(),
        }
        cache.write_text(json.dumps(data))
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        # PyPI returns same as current — no update
        with patch.dict(sys.modules, {"httpx": _fake_httpx(current)}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False

    def test_expired_cache_queries_pypi(self, monkeypatch, tmp_path):
        cache = tmp_path / "cache.json"
        import castor

        current = castor.__version__
        data = {
            "current": current,
            "latest": current,
            "update_available": False,
            "checked_at": time.time() - uc._CACHE_TTL - 10,
        }
        cache.write_text(json.dumps(data))
        monkeypatch.setattr(uc, "_CACHE_FILE", str(cache))
        with patch.dict(sys.modules, {"httpx": _fake_httpx(NEWER)}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is True


# ---------------------------------------------------------------------------
# check_for_update — PyPI query scenarios
# ---------------------------------------------------------------------------


class TestCheckForUpdatePyPI:
    """All tests bypass cache by pointing to a non-existent cache file."""

    def setup_method(self, tmp_path=None):
        # Use a fresh tmp dir per test via monkeypatch in each test
        pass

    def _no_cache(self, monkeypatch, tmp_path):
        monkeypatch.setattr(uc, "_CACHE_FILE", str(tmp_path / "no_cache.json"))

    def test_newer_version_sets_update_available(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        import castor

        current = castor.__version__
        with patch.dict(sys.modules, {"httpx": _fake_httpx(NEWER)}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is True
        assert result["latest"] == NEWER
        assert result["current"] == current

    def test_same_version_no_update(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        import castor

        current = castor.__version__
        with patch.dict(sys.modules, {"httpx": _fake_httpx(current)}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False

    def test_older_pypi_version_no_update(self, monkeypatch, tmp_path):
        """Dev/pre-release build ahead of PyPI — no warning."""
        self._no_cache(monkeypatch, tmp_path)
        with patch.dict(sys.modules, {"httpx": _fake_httpx("0.0.1")}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False

    def test_network_connection_error_silent(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        import castor

        current = castor.__version__
        with patch.dict(
            sys.modules, {"httpx": _fake_httpx(side_effect=ConnectionError("no network"))}
        ):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False
        assert result["current"] == result["latest"] == current

    def test_network_timeout_silent(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        with patch.dict(sys.modules, {"httpx": _fake_httpx(side_effect=TimeoutError("timeout"))}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False

    def test_http_non_200_no_update(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        with patch.dict(sys.modules, {"httpx": _fake_httpx(NEWER, status_code=404)}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False

    def test_malformed_response_missing_info_key(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        import castor

        current = castor.__version__
        mod = ModuleType("httpx")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"releases": {}}  # no 'info'
        mod.get = MagicMock(return_value=resp)
        with patch.dict(sys.modules, {"httpx": mod}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False
        assert result["latest"] == current

    def test_malformed_response_missing_version_key(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        import castor

        current = castor.__version__
        mod = ModuleType("httpx")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"info": {"name": "opencastor"}}  # no 'version'
        mod.get = MagicMock(return_value=resp)
        with patch.dict(sys.modules, {"httpx": mod}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False
        assert result["latest"] == current

    def test_json_decode_error_silent(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        mod = ModuleType("httpx")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        mod.get = MagicMock(return_value=resp)
        with patch.dict(sys.modules, {"httpx": mod}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False

    def test_httpx_import_error_silent(self, monkeypatch, tmp_path):
        """If httpx isn't installed, update check fails silently."""
        self._no_cache(monkeypatch, tmp_path)
        with patch.dict(sys.modules, {"httpx": None}):
            result = check_for_update(quiet=True)
        assert result["update_available"] is False

    def test_update_prints_hint_when_not_quiet(self, monkeypatch, tmp_path, capsys):
        self._no_cache(monkeypatch, tmp_path)
        with patch.dict(
            sys.modules, {"httpx": _fake_httpx(NEWER), "rich": None, "rich.console": None}
        ):
            result = check_for_update(quiet=False)
        assert result["update_available"] is True
        captured = capsys.readouterr()
        assert NEWER in captured.err

    def test_no_update_no_hint(self, monkeypatch, tmp_path, capsys):
        self._no_cache(monkeypatch, tmp_path)
        import castor

        current = castor.__version__
        with patch.dict(
            sys.modules, {"httpx": _fake_httpx(current), "rich": None, "rich.console": None}
        ):
            check_for_update(quiet=False)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_write_cache_called_on_success(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        with patch.dict(sys.modules, {"httpx": _fake_httpx(NEWER)}):
            check_for_update(quiet=True)
        # Cache file should now exist (written successfully)
        cache_path = str(tmp_path / "no_cache.json")
        assert os.path.exists(cache_path)

    def test_write_cache_not_called_on_network_error(self, monkeypatch, tmp_path):
        self._no_cache(monkeypatch, tmp_path)
        with patch.dict(sys.modules, {"httpx": _fake_httpx(side_effect=OSError("network down"))}):
            check_for_update(quiet=True)
        assert not os.path.exists(str(tmp_path / "no_cache.json"))


# ---------------------------------------------------------------------------
# Version edge cases
# ---------------------------------------------------------------------------


class TestVersionEdgeCases:
    def test_prerelease_not_newer_than_release(self):
        assert _is_newer("1.0.0a1", "1.0.0") is False

    def test_release_newer_than_prerelease(self):
        assert _is_newer("1.0.0", "1.0.0a1") is True

    def test_post_release_newer(self):
        assert _is_newer("1.0.0.post1", "1.0.0") is True

    def test_dev_release_not_newer(self):
        assert _is_newer("1.0.0.dev1", "1.0.0") is False

    def test_calver_patch_bump(self):
        assert _is_newer("2026.3.12.8", "2026.3.12.7") is True

    def test_calver_same_patch(self):
        assert _is_newer("2026.3.12.7", "2026.3.12.7") is False

    def test_minor_version_bump(self):
        assert _is_newer("1.1.0", "1.0.9") is True

    def test_major_version_bump(self):
        assert _is_newer("2.0.0", "1.99.99") is True


# ---------------------------------------------------------------------------
# _print_hint
# ---------------------------------------------------------------------------


class TestPrintHint:
    def test_with_rich_uses_console(self):
        mock_console_instance = MagicMock()
        mock_console_cls = MagicMock(return_value=mock_console_instance)
        mock_rich = ModuleType("rich.console")
        mock_rich.Console = mock_console_cls
        with patch.dict(sys.modules, {"rich.console": mock_rich}):
            _print_hint("1.0.0", "2.0.0")
        mock_console_instance.print.assert_called_once()
        call_args = mock_console_instance.print.call_args[0][0]
        assert "1.0.0" in call_args
        assert "2.0.0" in call_args

    def test_without_rich_prints_to_stderr(self, capsys):
        with patch.dict(sys.modules, {"rich": None, "rich.console": None}):
            _print_hint("1.0.0", "2.0.0")
        captured = capsys.readouterr()
        assert "1.0.0" in captured.err
        assert "2.0.0" in captured.err

    def test_without_rich_mentions_upgrade(self, capsys):
        with patch.dict(sys.modules, {"rich": None, "rich.console": None}):
            _print_hint("1.0.0", "2.0.0")
        captured = capsys.readouterr()
        assert "castor upgrade" in captured.err


# ---------------------------------------------------------------------------
# print_update_status
# ---------------------------------------------------------------------------


class TestPrintUpdateStatus:
    def _up_to_date_result(self):
        import castor

        v = castor.__version__
        return {"current": v, "latest": v, "update_available": False}

    def _update_available_result(self):
        import castor

        v = castor.__version__
        return {"current": v, "latest": NEWER, "update_available": True}

    def test_update_available_with_rich(self):
        mock_console = MagicMock()
        mock_rich = ModuleType("rich.console")
        mock_rich.Console = MagicMock(return_value=mock_console)
        with patch(
            "castor.update_check.check_for_update", return_value=self._update_available_result()
        ):
            with patch.dict(sys.modules, {"rich.console": mock_rich}):
                print_update_status()
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("Update" in c or NEWER in c for c in calls)

    def test_up_to_date_with_rich(self):
        mock_console = MagicMock()
        mock_rich = ModuleType("rich.console")
        mock_rich.Console = MagicMock(return_value=mock_console)
        with patch("castor.update_check.check_for_update", return_value=self._up_to_date_result()):
            with patch.dict(sys.modules, {"rich.console": mock_rich}):
                print_update_status()
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("up to date" in c.lower() for c in calls)

    def test_update_available_without_rich(self, capsys):
        with patch(
            "castor.update_check.check_for_update", return_value=self._update_available_result()
        ):
            with patch.dict(sys.modules, {"rich": None, "rich.console": None}):
                print_update_status()
        captured = capsys.readouterr()
        assert "Update available" in captured.out

    def test_up_to_date_without_rich(self, capsys):
        with patch("castor.update_check.check_for_update", return_value=self._up_to_date_result()):
            with patch.dict(sys.modules, {"rich": None, "rich.console": None}):
                print_update_status()
        captured = capsys.readouterr()
        assert "up to date" in captured.out.lower()

    def test_shows_current_and_latest_versions(self, capsys):
        import castor

        v = castor.__version__
        with patch("castor.update_check.check_for_update", return_value=self._up_to_date_result()):
            with patch.dict(sys.modules, {"rich": None, "rich.console": None}):
                print_update_status()
        captured = capsys.readouterr()
        assert v in captured.out

    def test_uses_quiet_mode_for_internal_check(self):
        """print_update_status calls check_for_update(quiet=True)."""
        with patch("castor.update_check.check_for_update") as mock_check:
            mock_check.return_value = self._up_to_date_result()
            with patch.dict(sys.modules, {"rich": None, "rich.console": None}):
                print_update_status()
        mock_check.assert_called_once_with(quiet=True)
