"""Tests for check_swap_usage() — Issue #412."""
from __future__ import annotations

import types
from unittest.mock import MagicMock, mock_open, patch

import pytest

from castor.doctor import check_swap_usage, run_all_checks


# ── 1. function is callable ───────────────────────────────────────────────────

def test_function_is_callable():
    assert callable(check_swap_usage)


# ── 2. returns tuple of length 3 ─────────────────────────────────────────────

def test_returns_tuple_of_length_3():
    result = check_swap_usage()
    assert isinstance(result, tuple)
    assert len(result) == 3


# ── 3. second element is "Swap usage" ────────────────────────────────────────

def test_second_element_is_swap_usage():
    _, name, _ = check_swap_usage()
    assert name == "Swap usage"


# ── 4. first element is bool ─────────────────────────────────────────────────

def test_first_element_is_bool():
    ok, _, _ = check_swap_usage()
    assert isinstance(ok, bool)


# ── 5. third element is non-empty str ────────────────────────────────────────

def test_third_element_is_nonempty_str():
    _, _, detail = check_swap_usage()
    assert isinstance(detail, str)
    assert len(detail) > 0


# ── 6. no-swap path returns True (psutil: total == 0) ───────────────────────

def test_no_swap_returns_true_psutil():
    mock_sw = MagicMock()
    mock_sw.total = 0

    mock_psutil = types.ModuleType("psutil")
    mock_psutil.swap_memory = lambda: mock_sw

    # Force /proc/swaps to not exist so we fall through to psutil
    with patch("os.path.exists", return_value=False):
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            ok, name, detail = check_swap_usage()

    assert ok is True
    assert name == "Swap usage"
    assert "no swap" in detail


# ── 7. over 50% swap returns False (psutil path) ─────────────────────────────

def test_over_50_pct_returns_false_psutil():
    mock_sw = MagicMock()
    mock_sw.total = 2 * 1024 * 1024 * 1024   # 2 GB
    mock_sw.used = 1.5 * 1024 * 1024 * 1024  # 75%
    mock_sw.percent = 75.0

    mock_psutil = types.ModuleType("psutil")
    mock_psutil.swap_memory = lambda: mock_sw

    with patch("os.path.exists", return_value=False):
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            ok, name, detail = check_swap_usage()

    assert ok is False
    assert name == "Swap usage"
    assert "swap >50% full" in detail


# ── 8. /proc/swaps no-swap path (file has no swap entries) ──────────────────

def test_proc_swaps_no_entries_returns_true():
    proc_content = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"

    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=proc_content)):
            ok, name, detail = check_swap_usage()

    assert ok is True
    assert name == "Swap usage"
    assert "no swap" in detail


# ── 9. /proc/swaps with usage under threshold returns True ───────────────────

def test_proc_swaps_low_usage_returns_true():
    # Size=2097148 (2GB), Used=209714 (~10%)
    proc_content = (
        "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
        "/dev/sda2                               partition\t2097148\t209714\t-2\n"
    )

    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=proc_content)):
            ok, name, detail = check_swap_usage()

    assert ok is True
    assert name == "Swap usage"


# ── 10. /proc/swaps with usage over threshold returns False ──────────────────

def test_proc_swaps_high_usage_returns_false():
    # Size=2097148, Used=1572861 (~75%)
    proc_content = (
        "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
        "/dev/sda2                               partition\t2097148\t1572861\t-2\n"
    )

    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=proc_content)):
            ok, name, detail = check_swap_usage()

    assert ok is False
    assert name == "Swap usage"
    assert "swap >50% full" in detail


# ── 11. included in run_all_checks() results ─────────────────────────────────

def test_included_in_run_all_checks():
    results = run_all_checks()
    names = [name for _, name, _ in results]
    assert "Swap usage" in names


# ── 12. never raises ─────────────────────────────────────────────────────────

def test_never_raises():
    try:
        check_swap_usage()
    except Exception as exc:
        pytest.fail(f"check_swap_usage() raised unexpectedly: {exc}")
