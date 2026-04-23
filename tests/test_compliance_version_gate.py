"""Regression tests for the RCAN 3.0 hard-cut version gate.

Release C (2026.4.23.0) hard-cuts 2.x acceptance. These tests lock the
invariant that is_accepted_version() rejects 2.x and does not give a free
pass to future majors.
"""

from __future__ import annotations

from castor.compliance import ACCEPTED_RCAN_VERSIONS, is_accepted_version


def test_accepted_versions_is_3_0_only():
    assert ACCEPTED_RCAN_VERSIONS == ("3.0",)


def test_accepts_3_0():
    assert is_accepted_version("3.0") is True


def test_accepts_3_x_forward_compat():
    assert is_accepted_version("3.1") is True
    assert is_accepted_version("3.2.1") is True


def test_rejects_2_x_hard_cut():
    for v in ("2.1", "2.1.0", "2.2", "2.2.0", "2.2.1"):
        assert is_accepted_version(v) is False, f"{v!r} should be rejected"


def test_rejects_1_x():
    for v in ("1.6", "1.9.0"):
        assert is_accepted_version(v) is False, f"{v!r} should be rejected"


def test_rejects_future_major_no_free_pass():
    """4.x and above are NOT forward-compatible — explicit bump required."""
    assert is_accepted_version("4.0") is False
    assert is_accepted_version("10.0") is False


def test_rejects_malformed():
    assert is_accepted_version("") is False
    assert is_accepted_version("not-a-version") is False
    assert is_accepted_version("3") is False  # no minor
