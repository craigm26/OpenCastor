"""Tests for castor.harness.circuit_breaker."""

import time

import pytest

from castor.harness.circuit_breaker import CircuitBreaker


@pytest.fixture
def cb():
    return CircuitBreaker({"failure_threshold": 3, "cooldown_s": 5, "half_open_probe": True})


def test_starts_closed(cb):
    assert cb.state("skill_a") == "closed"
    assert not cb.is_open("skill_a")


def test_opens_after_threshold(cb):
    for _ in range(3):
        cb.record_failure("skill_a")
    assert cb.state("skill_a") == "open"
    assert cb.is_open("skill_a")


def test_not_open_below_threshold(cb):
    cb.record_failure("skill_a")
    cb.record_failure("skill_a")
    assert cb.state("skill_a") == "closed"
    assert not cb.is_open("skill_a")


def test_resets_on_success(cb):
    for _ in range(3):
        cb.record_failure("skill_a")
    assert cb.state("skill_a") == "open"
    cb.record_success("skill_a")
    assert cb.state("skill_a") == "closed"
    assert not cb.is_open("skill_a")


def test_half_open_probe_after_cooldown():
    cb = CircuitBreaker({"failure_threshold": 2, "cooldown_s": 0.1, "half_open_probe": True})
    cb.record_failure("skill_a")
    cb.record_failure("skill_a")
    assert cb.state("skill_a") == "open"

    # Wait for cooldown
    time.sleep(0.15)
    assert cb.state("skill_a") == "half_open"
    # First is_open call in half_open should return False (probe allowed)
    assert not cb.is_open("skill_a")
    # Second call should be blocked again
    assert cb.is_open("skill_a")


def test_reset_manually(cb):
    for _ in range(3):
        cb.record_failure("skill_a")
    cb.reset("skill_a")
    assert cb.state("skill_a") == "closed"


def test_status_all(cb):
    cb.record_failure("skill_a")
    cb.record_failure("skill_b")
    cb.record_failure("skill_b")
    cb.record_failure("skill_b")
    status = cb.status_all()
    assert status["skill_a"] == "closed"
    assert status["skill_b"] == "open"


def test_independent_skills(cb):
    for _ in range(3):
        cb.record_failure("skill_a")
    cb.record_failure("skill_b")
    assert cb.is_open("skill_a")
    assert not cb.is_open("skill_b")


def test_no_half_open_when_disabled():
    cb = CircuitBreaker({"failure_threshold": 2, "cooldown_s": 0.1, "half_open_probe": False})
    cb.record_failure("x")
    cb.record_failure("x")
    time.sleep(0.15)
    # cooldown expired and half_open_probe=False → auto-closed
    assert cb.state("x") == "closed"
