"""Tests for ParallelSkillExecutor (#616) and InvokeCancelRequest (#609 #610)."""

from __future__ import annotations

import time

from castor.rcan.invoke import InvokeCancelRequest, InvokeRequest, InvokeResult, SkillRegistry
from castor.rcan.message import MessageType
from castor.rcan.parallel_invoke import ParallelInvokeResult, ParallelSkillExecutor

# ---------------------------------------------------------------------------
# InvokeCancelRequest tests (#609)
# ---------------------------------------------------------------------------


def test_invoke_cancel_request_fields():
    req = InvokeCancelRequest(msg_id="abc-123", reason="user abort")
    assert req.msg_id == "abc-123"
    assert req.reason == "user abort"


def test_invoke_cancel_request_optional_reason():
    req = InvokeCancelRequest(msg_id="abc-456")
    assert req.reason is None


def test_invoke_cancel_request_to_message():
    req = InvokeCancelRequest(msg_id="abc-789", reason="timeout")
    msg = req.to_message("rcan://src", "rcan://dst")
    assert msg["type"] == MessageType.INVOKE_CANCEL
    assert msg["payload"]["msg_id"] == "abc-789"
    assert msg["payload"]["reason"] == "timeout"
    assert "msg_id" in msg  # envelope msg_id for the cancel message itself


def test_invoke_cancel_request_to_message_no_reason():
    req = InvokeCancelRequest(msg_id="no-reason")
    msg = req.to_message("rcan://src", "rcan://dst")
    assert "reason" not in msg["payload"]


# ---------------------------------------------------------------------------
# InvokeRequest.msg_id property (#609)
# ---------------------------------------------------------------------------


def test_invoke_request_msg_id_property():
    req = InvokeRequest(skill="test.skill", invoke_id="fixed-id")
    assert req.msg_id == "fixed-id"
    assert req.msg_id == req.invoke_id


# ---------------------------------------------------------------------------
# SkillRegistry.cancel() (#610)
# ---------------------------------------------------------------------------


def test_cancel_unknown_invoke_id_returns_false():
    registry = SkillRegistry()
    assert registry.cancel("nonexistent-id") is False


def test_cancel_in_flight_invocation():
    """cancel() should signal the in-flight invocation and return True."""
    registry = SkillRegistry()
    barrier = __import__("threading").Event()

    @registry.register("test.blocking")
    def blocking(params):
        barrier.wait(timeout=5)  # blocks until cancelled or barrier set
        return {"done": True}

    req = InvokeRequest(skill="test.blocking", params={}, invoke_id="cancel-test-id")

    results: list[InvokeResult] = []

    def run():
        results.append(registry.invoke(req))

    t = __import__("threading").Thread(target=run)
    t.start()

    # Give the skill a moment to start.
    time.sleep(0.1)

    found = registry.cancel("cancel-test-id")
    barrier.set()  # unblock so thread exits cleanly
    t.join(timeout=2)

    assert found is True
    # The result should be cancelled (or success if race — both are acceptable).
    assert results[0].status in ("cancelled", "success")


def test_cancel_completed_invoke_returns_false():
    """cancel() called after completion should return False (event cleaned up)."""
    registry = SkillRegistry()

    @registry.register("test.instant")
    def instant(params):
        return {"ok": True}

    req = InvokeRequest(skill="test.instant", params={}, invoke_id="done-id")
    result = registry.invoke(req)
    assert result.status == "success"

    # After completion the cancel event is removed.
    assert registry.cancel("done-id") is False


# ---------------------------------------------------------------------------
# InvokeResult status includes "cancelled" and "not_found" (#609)
# ---------------------------------------------------------------------------


def test_invoke_result_cancelled_status():
    res = InvokeResult(invoke_id="x", status="cancelled", error="user abort")
    msg = res.to_message("rcan://a", "rcan://b")
    assert msg["payload"]["status"] == "cancelled"


def test_invoke_result_not_found_status():
    res = InvokeResult(invoke_id="y", status="not_found", error="no such skill")
    msg = res.to_message("rcan://a", "rcan://b")
    assert msg["payload"]["status"] == "not_found"


# ---------------------------------------------------------------------------
# ParallelSkillExecutor tests (#616)
# ---------------------------------------------------------------------------


def _make_registry(*skill_names: str, delay: float = 0.0) -> SkillRegistry:
    """Build a registry where each skill returns its own name after ``delay`` seconds."""
    registry = SkillRegistry()
    for name in skill_names:
        captured = name  # closure capture

        def handler(params, _n=captured, _d=delay):
            if _d:
                time.sleep(_d)
            return {"skill": _n}

        registry.register_fn(name, handler)
    return registry


class TestInvokeAllSuccess:
    def test_all_skills_succeed(self):
        registry = _make_registry("a.skill", "b.skill", "c.skill")
        executor = ParallelSkillExecutor(registry)
        requests = [InvokeRequest(skill=s) for s in ("a.skill", "b.skill", "c.skill")]

        result = executor.invoke_all(requests)

        assert isinstance(result, ParallelInvokeResult)
        assert set(result.succeeded) == {"a.skill", "b.skill", "c.skill"}
        assert result.failed == []
        assert result.timed_out == []
        assert result.wall_time_ms >= 0

    def test_results_dict_populated(self):
        registry = _make_registry("x.skill", "y.skill")
        executor = ParallelSkillExecutor(registry)
        requests = [InvokeRequest(skill=s) for s in ("x.skill", "y.skill")]

        result = executor.invoke_all(requests)

        assert "x.skill" in result.results
        assert "y.skill" in result.results
        assert result.results["x.skill"].result["skill"] == "x.skill"

    def test_empty_requests(self):
        registry = _make_registry()
        executor = ParallelSkillExecutor(registry)
        result = executor.invoke_all([])
        assert result.succeeded == []
        assert result.failed == []
        assert result.wall_time_ms == 0


class TestInvokeAllPartialTimeout:
    def test_slow_skill_appears_in_timed_out(self):
        """One slow skill should end up in timed_out; fast ones in succeeded."""
        registry = _make_registry("fast.a", "fast.b")
        # Register a slow skill manually.
        registry.register_fn("slow.c", lambda p: time.sleep(10) or {"done": True})

        executor = ParallelSkillExecutor(registry)
        requests = [
            InvokeRequest(skill="fast.a"),
            InvokeRequest(skill="fast.b"),
            InvokeRequest(skill="slow.c"),
        ]

        result = executor.invoke_all(requests, timeout_ms=300)

        assert "fast.a" in result.succeeded
        assert "fast.b" in result.succeeded
        assert "slow.c" in result.timed_out

    def test_wall_time_is_bounded_by_timeout(self):
        registry = _make_registry()
        registry.register_fn("forever", lambda p: time.sleep(30) or {})
        executor = ParallelSkillExecutor(registry)

        start = time.monotonic()
        result = executor.invoke_all([InvokeRequest(skill="forever")], timeout_ms=200)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert result.wall_time_ms < 1000
        assert elapsed_ms < 1500  # generous bound


class TestWaitForN:
    def test_wait_for_2_returns_when_2_complete(self):
        """With wait_for=2, executor returns after 2 skills finish and cancels the third."""
        registry = _make_registry("quick.a", "quick.b")
        # Third skill blocks.
        registry.register_fn("slow.c", lambda p: time.sleep(10) or {"done": True})

        executor = ParallelSkillExecutor(registry)
        requests = [
            InvokeRequest(skill="quick.a"),
            InvokeRequest(skill="quick.b"),
            InvokeRequest(skill="slow.c"),
        ]

        result = executor.invoke_all(requests, timeout_ms=3000, wait_for=2)

        # Two quick skills must have succeeded.
        assert len(result.succeeded) >= 2
        # slow.c should NOT be in succeeded.
        assert "slow.c" not in result.succeeded

    def test_wait_for_1_returns_after_first(self):
        registry = _make_registry("first", "second", "third")
        executor = ParallelSkillExecutor(registry)
        requests = [InvokeRequest(skill=s) for s in ("first", "second", "third")]

        result = executor.invoke_all(requests, wait_for=1)

        assert len(result.succeeded) >= 1

    def test_wait_for_equals_total_waits_for_all(self):
        registry = _make_registry("a", "b", "c")
        executor = ParallelSkillExecutor(registry)
        requests = [InvokeRequest(skill=s) for s in ("a", "b", "c")]

        result = executor.invoke_all(requests, wait_for=3)

        assert set(result.succeeded) == {"a", "b", "c"}


class TestInvokeRace:
    def test_race_returns_first_success(self):
        """invoke_race should return an InvokeResult from the winning skill."""
        registry = _make_registry("racer.a", "racer.b", "racer.c")
        executor = ParallelSkillExecutor(registry)
        requests = [InvokeRequest(skill=s) for s in ("racer.a", "racer.b", "racer.c")]

        winner = executor.invoke_race(requests, timeout_ms=3000)

        assert winner is not None
        assert winner.status == "success"

    def test_race_returns_none_on_all_failure(self):
        """If all skills fail, invoke_race should return None."""
        registry = SkillRegistry()
        registry.register_fn("bad.a", lambda p: (_ for _ in ()).throw(RuntimeError("fail")))  # noqa: E731

        executor = ParallelSkillExecutor(registry)
        # Use not_found skills so all fail.
        requests = [InvokeRequest(skill="nonexistent.x"), InvokeRequest(skill="nonexistent.y")]

        winner = executor.invoke_race(requests, timeout_ms=500)

        assert winner is None

    def test_race_returns_none_on_timeout(self):
        registry = SkillRegistry()
        registry.register_fn("slow", lambda p: time.sleep(10) or {})
        executor = ParallelSkillExecutor(registry)

        winner = executor.invoke_race([InvokeRequest(skill="slow")], timeout_ms=100)

        assert winner is None


class TestParallelInvokeResultFields:
    def test_parallel_result_defaults(self):
        res = ParallelInvokeResult()
        assert res.results == {}
        assert res.succeeded == []
        assert res.failed == []
        assert res.timed_out == []
        assert res.wall_time_ms == 0

    def test_not_found_skills_go_to_failed(self):
        """Skills that return status='not_found' should appear in failed, not succeeded."""
        registry = SkillRegistry()  # empty — all invocations will be not_found
        executor = ParallelSkillExecutor(registry)
        requests = [InvokeRequest(skill="ghost.skill")]

        result = executor.invoke_all(requests)

        assert "ghost.skill" in result.failed
        assert result.succeeded == []
