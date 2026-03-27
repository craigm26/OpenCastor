"""Tests for castor.harness.span_tracer."""

import time

import pytest

from castor.harness.span_tracer import SpanTracer


@pytest.fixture
def tracer(tmp_path):
    return SpanTracer({"export_path": str(tmp_path / "traces"), "max_trace_age_days": 7})


def test_start_trace_creates_root_span(tracer):
    span = tracer.start_trace("harness.run", {"scope": "chat"})
    assert span.trace_id
    assert span.span_id
    assert span.parent_span_id is None
    assert span.name == "harness.run"
    assert span.status == "in_progress"
    assert span.attributes["scope"] == "chat"


def test_start_span_child(tracer):
    root = tracer.start_trace("root")
    child = tracer.start_span("tool.move", parent=root)
    assert child.trace_id == root.trace_id
    assert child.parent_span_id == root.span_id
    assert child.span_id != root.span_id


def test_end_span_sets_fields(tracer):
    span = tracer.start_trace("test.span")
    tracer.end_span(span, status="ok")
    assert span.end_ns is not None
    assert span.status == "ok"


def test_end_span_error(tracer):
    span = tracer.start_trace("test.error")
    tracer.end_span(span, status="error", error="something failed")
    assert span.status == "error"
    assert span.attributes["error"] == "something failed"


def test_add_event(tracer):
    span = tracer.start_trace("test.events")
    tracer.add_event(span, "tool_result", {"result_len": 42})
    assert len(span.events) == 1
    assert span.events[0]["name"] == "tool_result"
    assert span.events[0]["attrs"]["result_len"] == 42


def test_export_and_reload(tracer):
    span = tracer.start_trace("export.test")
    tracer.add_event(span, "checkpoint", {"step": 1})
    tracer.end_span(span, status="ok")
    tracer.export_trace(span.trace_id)

    loaded = tracer.get_trace_from_disk(span.trace_id)
    assert len(loaded) == 1
    assert loaded[0]["name"] == "export.test"
    assert loaded[0]["status"] == "ok"


def test_get_trace_in_memory(tracer):
    root = tracer.start_trace("mem.test")
    tracer.start_span("child", parent=root)
    spans = tracer.get_trace(root.trace_id)
    assert len(spans) == 2


def test_list_traces(tracer):
    for i in range(3):
        s = tracer.start_trace(f"trace-{i}")
        tracer.end_span(s, status="ok")
        tracer.export_trace(s.trace_id)
    ids = tracer.list_traces()
    assert len(ids) == 3


def test_purge_old(tracer, tmp_path):
    # Export a trace then manually age it
    span = tracer.start_trace("old.trace")
    tracer.end_span(span)
    tracer.export_trace(span.trace_id)

    # Age the file
    import os
    import pathlib

    for f in pathlib.Path(str(tmp_path / "traces")).rglob("*.jsonl"):
        old_time = time.time() - 8 * 86400
        os.utime(f, (old_time, old_time))

    deleted = tracer.purge_old()
    assert deleted == 1


def test_sync_context_manager(tracer):
    root = tracer.start_trace("ctx.test")
    with tracer.span("child", parent=root) as s:
        assert s.status == "in_progress"
    assert s.status == "ok"
    assert s.end_ns is not None


def test_sync_context_manager_error(tracer):
    root = tracer.start_trace("ctx.error")
    with pytest.raises(ValueError):
        with tracer.span("child", parent=root) as s:
            raise ValueError("boom")
    assert s.status == "error"


@pytest.mark.asyncio
async def test_async_context_manager(tracer):
    root = tracer.start_trace("async.test")
    async with tracer.async_span("async_child", parent=root) as s:
        pass
    assert s.status == "ok"


def test_nested_spans_same_trace(tracer):
    root = tracer.start_trace("nested.test")
    child1 = tracer.start_span("child1", parent=root)
    tracer.start_span("child2", parent=root)
    grandchild = tracer.start_span("grandchild", parent=child1)

    assert child1.trace_id == root.trace_id
    assert grandchild.trace_id == root.trace_id
    assert grandchild.parent_span_id == child1.span_id

    spans = tracer.get_trace(root.trace_id)
    assert len(spans) == 4
