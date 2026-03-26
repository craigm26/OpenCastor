"""Tests for personal vs community run_type field on WorkUnit (#741)."""

from __future__ import annotations

import pytest

from castor.contribute.work_unit import (
    RUN_TYPE_COMMUNITY,
    RUN_TYPE_PERSONAL,
    WorkUnit,
    _VALID_RUN_TYPES,
)


def _make_unit(**kwargs: object) -> WorkUnit:
    defaults: dict = {
        "work_unit_id": "wu-test-001",
        "project": "harness_research",
        "coordinator_url": "http://localhost:8000",
        "model_format": "gguf",
        "input_data": {"prompt": "hello"},
    }
    defaults.update(kwargs)
    return WorkUnit(**defaults)  # type: ignore[arg-type]


def test_default_run_type_is_personal() -> None:
    wu = _make_unit()
    assert wu.run_type == RUN_TYPE_PERSONAL
    assert wu.run_type == "personal"


def test_community_run_type_accepted() -> None:
    wu = _make_unit(run_type=RUN_TYPE_COMMUNITY)
    assert wu.run_type == "community"


def test_personal_run_type_explicit() -> None:
    wu = _make_unit(run_type="personal")
    assert wu.run_type == "personal"


def test_invalid_run_type_raises() -> None:
    with pytest.raises(ValueError, match="run_type must be one of"):
        _make_unit(run_type="public")


def test_invalid_run_type_empty_raises() -> None:
    with pytest.raises(ValueError, match="run_type must be one of"):
        _make_unit(run_type="")


def test_valid_run_types_set() -> None:
    assert "personal" in _VALID_RUN_TYPES
    assert "community" in _VALID_RUN_TYPES
    assert len(_VALID_RUN_TYPES) == 2
