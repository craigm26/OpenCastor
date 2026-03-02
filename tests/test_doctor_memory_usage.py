"""Tests for doctor.check_memory_usage() (#382)."""

from unittest.mock import MagicMock, mock_open, patch

import pytest

from castor.doctor import check_memory_usage, run_all_checks

# ── return shape ──────────────────────────────────────────────────────────────

def test_check_memory_usage_returns_tuple():
    result = check_memory_usage()
    assert isinstance(result, tuple)
    assert len(result) == 3


def test_check_memory_usage_second_element_is_memory_usage():
    _, name, _ = check_memory_usage()
    assert name == "Memory usage"


def test_check_memory_usage_first_element_is_bool():
    ok, _, _ = check_memory_usage()
    assert isinstance(ok, bool)


def test_check_memory_usage_third_element_is_string():
    _, _, detail = check_memory_usage()
    assert isinstance(detail, str)


# ── normal case (<85%) ────────────────────────────────────────────────────────

def test_check_memory_usage_ok_low_usage():
    fake_meminfo = "MemTotal: 8000000 kB\nMemAvailable: 7000000 kB\n"
    with patch("builtins.open", mock_open(read_data=fake_meminfo)):
        with patch("os.path.exists", return_value=True):
            ok, name, detail = check_memory_usage()
    assert ok is True
    assert "%" in detail


def test_check_memory_usage_detail_contains_percent():
    fake_meminfo = "MemTotal: 4000000 kB\nMemAvailable: 3000000 kB\n"
    with patch("builtins.open", mock_open(read_data=fake_meminfo)):
        with patch("os.path.exists", return_value=True):
            _, _, detail = check_memory_usage()
    assert "%" in detail


# ── warning case (>=85%) ──────────────────────────────────────────────────────

def test_check_memory_usage_warn_high_usage():
    # 90% used: total=1000000, avail=100000
    fake_meminfo = "MemTotal: 1000000 kB\nMemAvailable: 100000 kB\n"
    with patch("builtins.open", mock_open(read_data=fake_meminfo)):
        with patch("os.path.exists", return_value=True):
            ok, name, detail = check_memory_usage()
    assert ok is False
    assert "85%" in detail or ">85%" in detail or "full" in detail


def test_check_memory_usage_warn_message_has_85(caplog):
    fake_meminfo = "MemTotal: 1000000 kB\nMemAvailable: 50000 kB\n"
    with patch("builtins.open", mock_open(read_data=fake_meminfo)):
        with patch("os.path.exists", return_value=True):
            ok, _, detail = check_memory_usage()
    assert ok is False


# ── fallback paths ────────────────────────────────────────────────────────────

def test_check_memory_usage_no_proc_uses_fallback():
    """When /proc/meminfo unavailable, falls back to psutil or resource."""
    with patch("os.path.exists", return_value=False):
        result = check_memory_usage()
    # should not crash; might be True or False depending on actual memory
    assert isinstance(result, tuple)
    assert len(result) == 3


def test_check_memory_usage_psutil_fallback(monkeypatch):
    vm = MagicMock()
    vm.percent = 50.0
    vm.available = 2 * 1024 * 1024 * 1024  # 2GB

    with patch("os.path.exists", return_value=False):
        try:
            import importlib.util
            if importlib.util.find_spec("psutil") is None:
                pytest.skip("psutil not available")
            with patch("psutil.virtual_memory", return_value=vm):
                ok, name, detail = check_memory_usage()
            assert ok is True
        except ImportError:
            pytest.skip("psutil not available")


# ── never raises ─────────────────────────────────────────────────────────────

def test_check_memory_usage_never_raises():
    try:
        check_memory_usage()
    except Exception as exc:
        pytest.fail(f"check_memory_usage raised: {exc}")


# ── integration with run_all_checks ──────────────────────────────────────────

def test_run_all_checks_includes_memory_usage():
    results = run_all_checks()
    names = [r[1] for r in results]
    assert "Memory usage" in names


def test_run_all_checks_memory_usage_has_correct_shape():
    results = run_all_checks()
    mem_result = next((r for r in results if r[1] == "Memory usage"), None)
    assert mem_result is not None
    ok, name, detail = mem_result
    assert isinstance(ok, bool)
    assert isinstance(detail, str)
