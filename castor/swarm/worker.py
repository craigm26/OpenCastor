"""Swarm worker — subprocess-isolated perception pipeline.

Provides copy-on-write snapshot isolation: each WorkerTask runs in a fresh
Python interpreter with its own memory namespace.  Only the structured
result JSON is merged back to the parent; the parent brain's message
history is never contaminated.

Typical usage::

    coord = WorkerCoordinator(brain_provider=state.brain)
    result = await coord.dispatch_oak_session(session_path, session_id)
    print(result.summary)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("OpenCastor.SwarmWorker")

_OAK_WORKER_MODULE = "castor.swarm.oak_worker"
_OAK_WORKER_PROMPT = (
    "Analyze OAK-D session at {session_path}. "
    "Count frames, compute depth stats (min/median/max), identify anomalies. "
    "Return structured summary."
)


@dataclass
class WorkerConfig:
    """Configuration for an isolated worker subprocess."""

    name: str
    tool_permission: str = "read-only"  # "read-only" | "workspace-write"
    timeout_s: int = 300
    max_turns: int = 10


@dataclass
class WorkerResult:
    """Structured result merged back from a completed worker."""

    success: bool
    summary: str
    error: str = ""
    duration_s: float = 0.0


@dataclass
class WorkerTask:
    """A unit of work dispatched to an isolated subprocess worker."""

    task_id: str
    worker_config: WorkerConfig
    prompt: str
    context: dict = field(default_factory=dict)


class WorkerCoordinator:
    """Dispatches perception tasks to isolated subprocess workers.

    Each worker runs in a fresh Python interpreter with its own memory
    namespace.  Only the structured result JSON is merged back — the
    parent brain's message history is never touched.

    Args:
        brain_provider: Reference to the parent brain (not mutated during
            dispatch; kept only for context snapshot in future extensions).
    """

    def __init__(self, brain_provider=None) -> None:
        self._brain = brain_provider

    async def dispatch(self, task: WorkerTask) -> WorkerResult:
        """Run *task* in an isolated subprocess and return its result.

        The subprocess receives the WorkerTask serialised as JSON on stdin.
        It must write a single JSON object to stdout::

            {"summary": "...", "error": ""}

        A non-zero exit code or a timeout is treated as failure; the parent
        brain state is never modified.

        Args:
            task: The task to execute in isolation.

        Returns:
            WorkerResult with ``success=False`` and a descriptive ``error``
            string if the subprocess fails or times out.
        """
        payload = json.dumps(
            {
                "task_id": task.task_id,
                "prompt": task.prompt,
                "context": task.context,
                "tool_permission": task.worker_config.tool_permission,
            }
        ).encode()

        start = time.monotonic()
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                _OAK_WORKER_MODULE,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(payload),
                    timeout=task.worker_config.timeout_s,
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                duration = time.monotonic() - start
                logger.warning(
                    "Swarm worker %s timed out after %ss",
                    task.task_id,
                    task.worker_config.timeout_s,
                )
                return WorkerResult(
                    success=False,
                    summary="",
                    error=f"timeout after {task.worker_config.timeout_s}s",
                    duration_s=duration,
                )

            duration = time.monotonic() - start

            if proc.returncode != 0:
                err_text = stderr.decode(errors="replace").strip() if stderr else "non-zero exit"
                logger.warning(
                    "Swarm worker %s exited %s: %s",
                    task.task_id,
                    proc.returncode,
                    err_text,
                )
                return WorkerResult(
                    success=False,
                    summary="",
                    error=err_text,
                    duration_s=duration,
                )

            try:
                result = json.loads(stdout.decode())
            except json.JSONDecodeError as exc:
                return WorkerResult(
                    success=False,
                    summary="",
                    error=f"invalid JSON from worker: {exc}",
                    duration_s=duration,
                )

            return WorkerResult(
                success=not bool(result.get("error")),
                summary=result.get("summary", ""),
                error=result.get("error", ""),
                duration_s=duration,
            )

        except Exception as exc:
            duration = time.monotonic() - start
            logger.exception("Swarm worker dispatch failed: %s", exc)
            return WorkerResult(
                success=False,
                summary="",
                error=str(exc),
                duration_s=duration,
            )

    async def dispatch_oak_session(self, session_path: str, session_id: str) -> WorkerResult:
        """Dispatch a standard OAK-D depth/RGB analysis session.

        Convenience wrapper that builds the canonical WorkerTask for an
        OAK-D perception session and calls :meth:`dispatch`.

        Args:
            session_path: Filesystem path to the recorded session directory.
            session_id: Unique identifier for the session (used in summary).

        Returns:
            WorkerResult containing depth stats and anomaly counts.
        """
        task = WorkerTask(
            task_id=str(uuid.uuid4()),
            worker_config=WorkerConfig(
                name="oak-d-analysis",
                tool_permission="read-only",
                timeout_s=300,
                max_turns=10,
            ),
            prompt=_OAK_WORKER_PROMPT.format(session_path=session_path),
            context={"session_path": session_path, "session_id": session_id},
        )
        return await self.dispatch(task)
