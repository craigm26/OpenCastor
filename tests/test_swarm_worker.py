"""Tests for castor.swarm.worker — subprocess-isolated perception pipeline.

Covers WorkerCoordinator.dispatch(), dispatch_oak_session(), WorkerResult /
WorkerTask / WorkerConfig dataclass contracts, and isolation guarantees (the
parent brain's state must not be mutated by dispatch).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from castor.swarm.worker import (
    WorkerConfig,
    WorkerCoordinator,
    WorkerResult,
    WorkerTask,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    timeout_s: int = 5,
    prompt: str = "test prompt",
    context: dict | None = None,
) -> WorkerTask:
    return WorkerTask(
        task_id="test-task-id",
        worker_config=WorkerConfig(name="test-worker", timeout_s=timeout_s),
        prompt=prompt,
        context=context or {},
    )


def _mock_process(*, stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    """Return a mock asyncio.Process whose communicate() returns immediately."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# test_worker_result_fields
# ---------------------------------------------------------------------------


def test_worker_result_fields():
    """WorkerResult exposes all required fields with correct types."""
    result = WorkerResult(success=True, summary="ok", error="", duration_s=1.23)
    assert result.success is True
    assert result.summary == "ok"
    assert result.error == ""
    assert result.duration_s == pytest.approx(1.23)


def test_worker_result_defaults():
    result = WorkerResult(success=False, summary="")
    assert result.error == ""
    assert result.duration_s == 0.0


def test_worker_config_defaults():
    cfg = WorkerConfig(name="my-worker")
    assert cfg.tool_permission == "read-only"
    assert cfg.timeout_s == 300
    assert cfg.max_turns == 10


# ---------------------------------------------------------------------------
# test_dispatch_returns_result
# ---------------------------------------------------------------------------


async def test_dispatch_returns_result():
    """A subprocess that returns valid JSON produces WorkerResult.success=True."""
    payload = json.dumps({"summary": "10 frames analyzed. Depth OK.", "error": ""}).encode()
    mock_proc = _mock_process(stdout=payload)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        coord = WorkerCoordinator()
        result = await coord.dispatch(_make_task())

    assert result.success is True
    assert result.summary == "10 frames analyzed. Depth OK."
    assert result.error == ""
    assert result.duration_s >= 0.0


async def test_dispatch_nonzero_exit_returns_failure():
    """A subprocess that exits non-zero returns success=False."""
    mock_proc = _mock_process(stdout=b"", returncode=1, stderr=b"something went wrong")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        coord = WorkerCoordinator()
        result = await coord.dispatch(_make_task())

    assert result.success is False
    assert "something went wrong" in result.error


async def test_dispatch_invalid_json_returns_failure():
    """A subprocess that writes non-JSON stdout returns success=False."""
    mock_proc = _mock_process(stdout=b"not-json")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        coord = WorkerCoordinator()
        result = await coord.dispatch(_make_task())

    assert result.success is False
    assert "invalid JSON" in result.error


# ---------------------------------------------------------------------------
# test_dispatch_timeout
# ---------------------------------------------------------------------------


async def test_dispatch_timeout():
    """A subprocess that hangs returns WorkerResult with timeout error."""

    async def _hang(input):  # noqa: A002
        await asyncio.sleep(999)

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.communicate = _hang
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        coord = WorkerCoordinator()
        task = _make_task(timeout_s=1)
        result = await coord.dispatch(task)

    assert result.success is False
    assert "timeout" in result.error
    mock_proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# test_dispatch_oak_session_creates_task
# ---------------------------------------------------------------------------


async def test_dispatch_oak_session_creates_task():
    """dispatch_oak_session builds the correct prompt and context."""
    captured: list[WorkerTask] = []

    async def _fake_dispatch(task: WorkerTask) -> WorkerResult:
        captured.append(task)
        return WorkerResult(success=True, summary="ok")

    coord = WorkerCoordinator()
    coord.dispatch = _fake_dispatch  # type: ignore[method-assign]

    await coord.dispatch_oak_session("/data/sessions/s1", "s1")

    assert len(captured) == 1
    task = captured[0]
    assert "/data/sessions/s1" in task.prompt
    assert task.context["session_path"] == "/data/sessions/s1"
    assert task.context["session_id"] == "s1"
    assert task.worker_config.tool_permission == "read-only"
    assert task.worker_config.timeout_s == 300
    assert task.task_id  # non-empty uuid


async def test_dispatch_oak_session_prompt_contains_keywords():
    """The OAK-D prompt includes all required analysis keywords."""
    captured: list[WorkerTask] = []

    async def _fake_dispatch(task: WorkerTask) -> WorkerResult:
        captured.append(task)
        return WorkerResult(success=True, summary="ok")

    coord = WorkerCoordinator()
    coord.dispatch = _fake_dispatch  # type: ignore[method-assign]

    await coord.dispatch_oak_session("/tmp/session", "sess-42")

    prompt = captured[0].prompt.lower()
    assert "oak-d" in prompt
    assert "depth" in prompt
    assert "anomal" in prompt


# ---------------------------------------------------------------------------
# test_coordinator_does_not_mutate_parent
# ---------------------------------------------------------------------------


async def test_coordinator_does_not_mutate_parent():
    """dispatch() must not modify the parent brain's message history."""
    brain = MagicMock()
    brain.messages = []
    brain.thought_history = []

    payload = json.dumps({"summary": "clean", "error": ""}).encode()
    mock_proc = _mock_process(stdout=payload)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        coord = WorkerCoordinator(brain_provider=brain)
        await coord.dispatch(_make_task())

    assert brain.messages == []
    assert brain.thought_history == []
    brain.think.assert_not_called()
    brain.think_stream.assert_not_called()


def test_coordinator_stores_brain_reference():
    """WorkerCoordinator holds a reference to the brain without mutating it."""
    brain = MagicMock()
    coord = WorkerCoordinator(brain_provider=brain)
    assert coord._brain is brain
    brain.think.assert_not_called()
