"""Parallel skill execution with fan-out and configurable aggregation."""

from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from castor.rcan.invoke import InvokeRequest, InvokeResult, SkillRegistry

logger = logging.getLogger("OpenCastor.RCAN.ParallelInvoke")


@dataclass
class ParallelInvokeResult:
    """Aggregated result from a parallel fan-out of skill invocations."""

    results: dict[str, InvokeResult] = field(default_factory=dict)
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    timed_out: list[str] = field(default_factory=list)
    wall_time_ms: int = 0


class ParallelSkillExecutor:
    """Fan-out skill invocations across a SkillRegistry concurrently.

    Example::

        executor = ParallelSkillExecutor(registry)
        result = executor.invoke_all(
            [InvokeRequest("nav.go"), InvokeRequest("arm.pick")],
            timeout_ms=5000,
        )
        print(result.succeeded)  # ["nav.go", "arm.pick"]
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def invoke_all(
        self,
        requests: list[InvokeRequest],
        timeout_ms: Optional[int] = None,
        wait_for: Optional[int] = None,
    ) -> ParallelInvokeResult:
        """Fan out requests to the registry concurrently.

        Args:
            requests:   List of InvokeRequest objects to execute in parallel.
            timeout_ms: Wall-clock deadline in milliseconds.  Individual
                        requests that don't finish in time appear in
                        ``timed_out``.  ``None`` means no deadline.
            wait_for:   Return early once this many invocations complete.
                        Remaining futures are cancelled.  ``None`` (default)
                        waits for all requests.

        Returns:
            :class:`ParallelInvokeResult` with per-skill results.
        """
        if not requests:
            return ParallelInvokeResult()

        start = time.monotonic()
        timeout_s = timeout_ms / 1000.0 if timeout_ms is not None else None
        target = wait_for if wait_for is not None else len(requests)
        agg = ParallelInvokeResult()

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(requests)) as ex:
            future_to_req: dict[concurrent.futures.Future[InvokeResult], InvokeRequest] = {
                ex.submit(self._registry.invoke, r): r for r in requests
            }
            done_count = 0
            try:
                for fut in concurrent.futures.as_completed(future_to_req, timeout=timeout_s):
                    req = future_to_req[fut]
                    try:
                        res: InvokeResult = fut.result()
                        agg.results[req.skill] = res
                        if res.status in ("ok", "success", "cancelled"):
                            agg.succeeded.append(req.skill)
                        else:
                            agg.failed.append(req.skill)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Parallel invoke failed for %s: %s", req.skill, e)
                        agg.failed.append(req.skill)
                    done_count += 1
                    if done_count >= target:
                        # Cancel remaining futures and signal registry.
                        for f, r in future_to_req.items():
                            if not f.done():
                                f.cancel()
                                self._registry.cancel(r.invoke_id)
                        break
            except concurrent.futures.TimeoutError:
                # Wall-clock deadline exceeded — mark undone futures as timed_out.
                for f, r in future_to_req.items():
                    if not f.done():
                        f.cancel()
                        self._registry.cancel(r.invoke_id)
                        agg.timed_out.append(r.skill)

        agg.wall_time_ms = int((time.monotonic() - start) * 1000)
        return agg

    def invoke_race(
        self,
        requests: list[InvokeRequest],
        timeout_ms: Optional[int] = None,
    ) -> Optional[InvokeResult]:
        """Return the first successful result and cancel the rest.

        Args:
            requests:   Candidate InvokeRequest objects to race.
            timeout_ms: Overall deadline in milliseconds.

        Returns:
            The first successful :class:`InvokeResult`, or ``None`` if all
            failed or the deadline elapsed before any succeeded.
        """
        result = self.invoke_all(requests, timeout_ms=timeout_ms, wait_for=1)
        if result.succeeded:
            return result.results.get(result.succeeded[0])
        return None
