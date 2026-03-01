"""Tests for doctor check improvements (Issue #280)."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

from castor.doctor import (
    check_ble_driver,
    check_memory_db_size,
    check_signal_channel,
    run_all_checks,
)

# ── check_memory_db_size ──────────────────────────────────────────────────────


def test_memory_db_check_returns_tuple():
    result = check_memory_db_size()
    assert isinstance(result, tuple)
    assert len(result) == 3


def test_memory_db_check_ok_is_bool():
    ok, name, detail = check_memory_db_size()
    assert isinstance(ok, bool)


def test_memory_db_check_name():
    ok, name, detail = check_memory_db_size()
    assert "Memory DB" in name


def test_memory_db_check_missing_file():
    with patch.dict(os.environ, {"CASTOR_MEMORY_DB": "/tmp/opencastor_does_not_exist.db"}):
        ok, name, detail = check_memory_db_size()
    assert ok is True
    assert "not found" in detail


def test_memory_db_check_small_file():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        f.write(b"x" * 100)
        path = f.name
    try:
        with patch.dict(os.environ, {"CASTOR_MEMORY_DB": path}):
            ok, name, detail = check_memory_db_size()
        assert ok is True
    finally:
        os.unlink(path)


def test_memory_db_check_large_file():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        # Write 110 MB of data
        f.write(b"x" * (110 * 1024 * 1024))
        path = f.name
    try:
        with patch.dict(os.environ, {"CASTOR_MEMORY_DB": path}):
            ok, name, detail = check_memory_db_size()
        assert ok is False
        assert "MB" in detail
    finally:
        os.unlink(path)


def test_memory_db_check_detail_is_string():
    ok, name, detail = check_memory_db_size()
    assert isinstance(detail, str)


# ── check_ble_driver ──────────────────────────────────────────────────────────


def test_ble_driver_check_returns_tuple():
    result = check_ble_driver()
    assert isinstance(result, tuple)
    assert len(result) == 3


def test_ble_driver_check_ok_is_bool():
    ok, name, detail = check_ble_driver()
    assert isinstance(ok, bool)


def test_ble_driver_check_name_contains_bleak():
    ok, name, detail = check_ble_driver()
    assert "bleak" in name.lower() or "BLE" in name


def test_ble_driver_check_detail_is_string():
    ok, name, detail = check_ble_driver()
    assert isinstance(detail, str)


def test_ble_driver_check_when_bleak_missing():
    import sys

    original = sys.modules.get("bleak")
    sys.modules["bleak"] = None  # type: ignore[assignment]
    try:
        ok, name, detail = check_ble_driver()
        # ok should still be True (optional dep)
        assert ok is True
        assert "not installed" in detail.lower() or "optional" in detail.lower()
    finally:
        if original is None:
            sys.modules.pop("bleak", None)
        else:
            sys.modules["bleak"] = original


# ── check_signal_channel ──────────────────────────────────────────────────────


def test_signal_channel_check_returns_tuple():
    result = check_signal_channel()
    assert isinstance(result, tuple)
    assert len(result) == 3


def test_signal_channel_check_name():
    ok, name, detail = check_signal_channel()
    assert "Signal" in name


def test_signal_channel_check_ok():
    ok, name, detail = check_signal_channel()
    # The signal channel should be importable
    assert ok is True


def test_signal_channel_check_detail_is_string():
    ok, name, detail = check_signal_channel()
    assert isinstance(detail, str)


# ── run_all_checks integration ────────────────────────────────────────────────


def test_run_all_checks_includes_memory_db():
    results = run_all_checks()
    names = [r[1] for r in results]
    assert any("Memory DB" in n for n in names)


def test_run_all_checks_includes_ble():
    results = run_all_checks()
    names = [r[1] for r in results]
    assert any("BLE" in n or "bleak" in n.lower() for n in names)


def test_run_all_checks_includes_signal():
    results = run_all_checks()
    names = [r[1] for r in results]
    assert any("Signal" in n for n in names)
