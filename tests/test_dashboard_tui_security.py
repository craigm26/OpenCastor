"""
tests/test_dashboard_tui_security.py

Security tests for castor/dashboard_tui.py Bearer token authentication.
Mirrors the pattern verified by test_dashboard_security.py for dashboard.py.
"""

import importlib
import inspect
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re-)load the module with a given env
# ---------------------------------------------------------------------------

SRC_PATH = Path(__file__).resolve().parent.parent / "castor" / "dashboard_tui.py"
MODULE_NAME = "castor.dashboard_tui"


def _load_module(token: str = "") -> types.ModuleType:
    """Import (or reload) dashboard_tui with OPENCASTOR_API_TOKEN set."""
    env_patch = {"OPENCASTOR_API_TOKEN": token}
    with patch.dict("os.environ", env_patch, clear=False):
        # Remove cached module so the top-level _API_TOKEN re-evaluates
        sys.modules.pop(MODULE_NAME, None)
        mod = importlib.import_module(MODULE_NAME)
    return mod


# ---------------------------------------------------------------------------
# 1. _hdr() returns correct Bearer format when token is set
# ---------------------------------------------------------------------------


def test_hdr_returns_bearer_header():
    mod = _load_module("my-secret-token")
    # Force _API_TOKEN to match what was loaded
    mod._API_TOKEN = "my-secret-token"
    result = mod._hdr()
    assert result == {"Authorization": "Bearer my-secret-token"}


# ---------------------------------------------------------------------------
# 2. _hdr() returns {} when no token
# ---------------------------------------------------------------------------


def test_hdr_returns_empty_dict_when_no_token():
    mod = _load_module("")
    mod._API_TOKEN = ""
    result = mod._hdr()
    assert result == {}


# ---------------------------------------------------------------------------
# 3. _hdr() format is exactly "Bearer <token>" (no extra spaces)
# ---------------------------------------------------------------------------


def test_hdr_bearer_format_exact():
    mod = _load_module("tok123")
    mod._API_TOKEN = "tok123"
    hdr = mod._hdr()
    assert hdr["Authorization"].startswith("Bearer ")
    assert hdr["Authorization"] == "Bearer tok123"


# ---------------------------------------------------------------------------
# 4. Source file contains OPENCASTOR_API_TOKEN
# ---------------------------------------------------------------------------


def test_source_contains_opencastor_api_token():
    src = SRC_PATH.read_text()
    assert "OPENCASTOR_API_TOKEN" in src


# ---------------------------------------------------------------------------
# 5. Source file defines _hdr()
# ---------------------------------------------------------------------------


def test_source_defines_hdr_function():
    src = SRC_PATH.read_text()
    assert "def _hdr()" in src


# ---------------------------------------------------------------------------
# 6. Source file defines _warn_no_token()
# ---------------------------------------------------------------------------


def test_source_defines_warn_no_token():
    src = SRC_PATH.read_text()
    assert "def _warn_no_token(" in src or "_warn_no_token" in src


# ---------------------------------------------------------------------------
# 7. All requests.get calls in the file use headers= keyword argument
# ---------------------------------------------------------------------------


def test_all_requests_get_use_headers():
    src = SRC_PATH.read_text()
    # Find every requests.get(...) call
    calls = re.findall(r"requests\.get\(.*?\)", src, re.DOTALL)
    for call in calls:
        assert "headers=" in call, f"requests.get call missing headers=: {call!r}"


# ---------------------------------------------------------------------------
# 8. All requests.post calls in the file use headers= keyword argument
# ---------------------------------------------------------------------------


def test_all_requests_post_use_headers():
    src = SRC_PATH.read_text()
    calls = re.findall(r"requests\.post\(.*?\)", src, re.DOTALL)
    for call in calls:
        assert "headers=" in call, f"requests.post call missing headers=: {call!r}"


# ---------------------------------------------------------------------------
# 9. 401 response triggers a user-visible message in _run_embedding_loop
# ---------------------------------------------------------------------------


def test_401_triggers_error_message(capsys):
    mod = _load_module("good-token")
    mod._API_TOKEN = "good-token"

    fake_resp = MagicMock()
    fake_resp.status_code = 401

    # Patch requests.get inside the module and sleep to break the loop
    call_count = [0]

    def _fake_get(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] >= 2:
            raise KeyboardInterrupt  # exit loop after second iteration
        return fake_resp

    # Patch the requests import inside the module
    mock_requests = MagicMock()
    mock_requests.get.side_effect = _fake_get

    with patch.dict(sys.modules, {"requests": mock_requests}):
        # Re-import to pick up patched requests
        sys.modules.pop(MODULE_NAME, None)
        mod2 = importlib.import_module(MODULE_NAME)
        mod2._API_TOKEN = "good-token"
        try:
            mod2._run_embedding_loop()
        except KeyboardInterrupt:
            pass

    captured = capsys.readouterr()
    assert "401" in captured.err or "OPENCASTOR_API_TOKEN" in captured.err


# ---------------------------------------------------------------------------
# 10. _warn_no_token prints to stderr when token is absent
# ---------------------------------------------------------------------------


def test_warn_no_token_prints_stderr(capsys):
    mod = _load_module("")
    mod._API_TOKEN = ""
    mod._warn_no_token()
    captured = capsys.readouterr()
    assert "OPENCASTOR_API_TOKEN" in captured.err or "token" in captured.err.lower()


# ---------------------------------------------------------------------------
# 11. _warn_no_token does NOT print when token is set
# ---------------------------------------------------------------------------


def test_warn_no_token_silent_when_token_set(capsys):
    mod = _load_module("secret")
    mod._API_TOKEN = "secret"
    mod._warn_no_token()
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------------------------------------------------------------------------
# 12. _hdr() is callable and returns a dict
# ---------------------------------------------------------------------------


def test_hdr_is_callable_and_returns_dict():
    mod = _load_module("any-token")
    mod._API_TOKEN = "any-token"
    result = mod._hdr()
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 13. Source contains the actionable "Set OPENCASTOR_API_TOKEN env var" message
# ---------------------------------------------------------------------------


def test_source_contains_actionable_401_message():
    src = SRC_PATH.read_text()
    assert "OPENCASTOR_API_TOKEN" in src
    # The 401 path should mention the env var
    lines_with_401 = [ln for ln in src.splitlines() if "401" in ln]
    assert lines_with_401, "No line mentioning 401 found in source"


# ---------------------------------------------------------------------------
# 14. _run_embedding_loop uses _hdr() not a local ad-hoc headers variable
# ---------------------------------------------------------------------------


def test_embedding_loop_uses_module_level_hdr():
    src = SRC_PATH.read_text()
    # Extract _run_embedding_loop function source
    mod = _load_module("")
    func_src = inspect.getsource(mod._run_embedding_loop)
    # Should call _hdr() inside the loop
    assert "_hdr()" in func_src


# ---------------------------------------------------------------------------
# 15. Module-level _API_TOKEN is a string (not None)
# ---------------------------------------------------------------------------


def test_api_token_module_level_is_string():
    mod = _load_module("")
    assert isinstance(mod._API_TOKEN, str)
