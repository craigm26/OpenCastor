"""Tests for RCAN §19 INVOKE/INVOKE_RESULT message types."""

import time
from concurrent.futures import ThreadPoolExecutor

from castor.rcan.invoke import InvokeRequest, InvokeResult, SkillRegistry
from castor.rcan.message import MessageType


def test_message_type_enum_invoke_values():
    """MessageType enum must define INVOKE=11, INVOKE_RESULT=12, INVOKE_CANCEL=13 (§3.2, §19)."""
    assert MessageType.INVOKE == 11
    assert MessageType.INVOKE_RESULT == 12
    assert MessageType.INVOKE_CANCEL == 13
    assert MessageType["INVOKE"] is MessageType.INVOKE
    assert MessageType["INVOKE_RESULT"] is MessageType.INVOKE_RESULT
    assert MessageType["INVOKE_CANCEL"] is MessageType.INVOKE_CANCEL


def test_invoke_request_to_message():
    req = InvokeRequest(skill="nav.go_to", params={"x": 1.0, "y": 2.0}, invoke_id="test-123")
    msg = req.to_message("rcan://localhost/test/bot/1", "rcan://localhost/test/bot/1")
    assert msg["type"] == MessageType.INVOKE
    assert msg["payload"]["skill"] == "nav.go_to"
    assert msg["msg_id"] == "test-123"  # §19.3 — wire field is msg_id


def test_invoke_result_success():
    result = InvokeResult(invoke_id="test-123", status="success", result={"reached": True})
    msg = result.to_message("rcan://localhost/test/bot/1", "rcan://localhost/test/controller/1")
    assert msg["type"] == MessageType.INVOKE_RESULT
    assert msg["payload"]["status"] == "success"


def test_skill_registry_register_and_invoke():
    registry = SkillRegistry()

    @registry.register("test.ping")
    def ping(params):
        return {"pong": True, "echo": params.get("msg")}

    req = InvokeRequest(skill="test.ping", params={"msg": "hello"})
    result = registry.invoke(req)
    assert result.status == "success"
    assert result.result["pong"] is True
    assert result.result["echo"] == "hello"


def test_skill_not_found():
    registry = SkillRegistry()
    req = InvokeRequest(skill="nonexistent.skill", params={})
    result = registry.invoke(req)
    assert result.status == "not_found"
    assert "nonexistent.skill" in result.error


def test_skill_error_handling():
    registry = SkillRegistry()

    @registry.register("test.failing")
    def failing(params):
        raise ValueError("Something went wrong")

    req = InvokeRequest(skill="test.failing", params={})
    result = registry.invoke(req)
    assert result.status == "failure"
    assert "Something went wrong" in result.error


def test_list_skills():
    registry = SkillRegistry()
    registry.register_fn("a.skill", lambda p: {})
    registry.register_fn("b.skill", lambda p: {})
    skills = registry.list_skills()
    assert "a.skill" in skills
    assert "b.skill" in skills


def test_invoke_result_not_found():
    result = InvokeResult(invoke_id="test-456", status="not_found", error="skill not found")
    msg = result.to_message("rcan://a", "rcan://b")
    assert msg["payload"]["status"] == "not_found"


class TestInvokeTimeout:
    def test_invoke_timeout_field_in_message(self):
        """InvokeRequest with timeout_ms=5000 should include it in payload."""
        req = InvokeRequest(skill="nav.go_to", params={}, timeout_ms=5000)
        msg = req.to_message("rcan://src", "rcan://dst")
        assert msg["payload"]["timeout_ms"] == 5000

    def test_invoke_timeout_defaults_to_none(self):
        """Omitting timeout_ms should not include it in payload."""
        req = InvokeRequest(skill="nav.go_to", params={})
        assert req.timeout_ms is None
        msg = req.to_message("rcan://src", "rcan://dst")
        assert "timeout_ms" not in msg["payload"]


class TestInvokeConcurrent:
    def test_concurrent_invokes_have_unique_msg_ids(self):
        """Create 10 InvokeRequests — all msg_ids must be unique UUIDs."""
        requests = [InvokeRequest(skill="test.skill", params={}) for _ in range(10)]
        ids = [r.invoke_id for r in requests]
        assert len(set(ids)) == 10, "All invoke_ids must be unique"

    def test_invoke_result_correlates_msg_id(self):
        """InvokeResult.msg_id must match paired InvokeRequest.msg_id."""
        req = InvokeRequest(skill="test.skill", params={}, invoke_id="corr-999")
        result = InvokeResult(invoke_id=req.invoke_id, status="success", result={"ok": True})
        msg = result.to_message("rcan://src", "rcan://dst")
        # reply_to on INVOKE_RESULT echoes the originating INVOKE's msg_id (§19.3)
        assert msg["reply_to"] == req.invoke_id


class TestInvokeDurationMs:
    def test_duration_ms_in_result(self):
        """InvokeResult with duration_ms=42 should include it in payload."""
        result = InvokeResult(invoke_id="dur-001", status="success", duration_ms=42)
        msg = result.to_message("rcan://src", "rcan://dst")
        assert msg["payload"]["duration_ms"] == 42

    def test_duration_ms_optional(self):
        """InvokeResult without duration_ms should not crash."""
        result = InvokeResult(invoke_id="dur-002", status="success")
        assert result.duration_ms is None
        msg = result.to_message("rcan://src", "rcan://dst")
        assert msg["payload"]["duration_ms"] is None


class TestTimeoutEnforcement:
    """#608 — SkillRegistry.invoke() must enforce InvokeRequest.timeout_ms."""

    def test_blocking_skill_times_out(self):
        """A skill that sleeps longer than timeout_ms must return status='timeout'."""
        registry = SkillRegistry()

        @registry.register("test.slow")
        def slow(params):
            time.sleep(10)  # intentionally longer than timeout
            return {"done": True}

        req = InvokeRequest(skill="test.slow", params={}, timeout_ms=100)
        result = registry.invoke(req)

        assert result.status == "timeout"
        assert result.duration_ms is not None
        # Should have returned well under 1 second despite the 10-second sleep
        assert result.duration_ms < 1000

    def test_fast_skill_completes_within_timeout(self):
        """A skill that finishes quickly must return status='success' even with timeout set."""
        registry = SkillRegistry()

        @registry.register("test.fast")
        def fast(params):
            return {"ok": True}

        req = InvokeRequest(skill="test.fast", params={}, timeout_ms=5000)
        result = registry.invoke(req)

        assert result.status == "success"
        assert result.result["ok"] is True

    def test_no_timeout_blocks_until_done(self):
        """Without timeout_ms, a quick skill should complete normally."""
        registry = SkillRegistry()

        @registry.register("test.quick")
        def quick(params):
            return {"value": params.get("x", 0) * 2}

        req = InvokeRequest(skill="test.quick", params={"x": 21})
        result = registry.invoke(req)

        assert result.status == "success"
        assert result.result["value"] == 42

    def test_timeout_error_message_includes_ms(self):
        """Timeout error message should mention the timeout duration."""
        registry = SkillRegistry()

        @registry.register("test.frozen")
        def frozen(params):
            time.sleep(10)
            return {}

        req = InvokeRequest(skill="test.frozen", params={}, timeout_ms=50)
        result = registry.invoke(req)

        assert result.status == "timeout"
        assert result.error is not None
        assert "50" in result.error  # timeout_ms value should appear in error


class TestConcurrentInvoke:
    """#605 — concurrent INVOKE execution tests."""

    def test_concurrent_invokes_all_complete(self):
        """Multiple concurrent invocations on separate registries must all succeed."""
        registry = SkillRegistry()

        @registry.register("math.double")
        def double(params):
            return {"result": params["n"] * 2}

        def run_invoke(n: int) -> InvokeResult:
            req = InvokeRequest(skill="math.double", params={"n": n})
            return registry.invoke(req)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(run_invoke, i) for i in range(5)]
            results = [f.result() for f in futures]

        assert all(r.status == "success" for r in results)
        result_values = {r.result["result"] for r in results}
        assert result_values == {0, 2, 4, 6, 8}

    def test_concurrent_timeouts_are_independent(self):
        """Concurrent timed-out invocations should each return 'timeout' independently."""
        registry = SkillRegistry()

        @registry.register("test.block")
        def block(params):
            time.sleep(10)
            return {}

        def run_invoke() -> InvokeResult:
            req = InvokeRequest(skill="test.block", params={}, timeout_ms=100)
            return registry.invoke(req)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(run_invoke) for _ in range(3)]
            results = [f.result() for f in futures]

        assert all(r.status == "timeout" for r in results)
        # All should have completed in well under 2 seconds total
        assert all(r.duration_ms is not None and r.duration_ms < 1000 for r in results)
