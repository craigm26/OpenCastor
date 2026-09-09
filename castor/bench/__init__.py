"""``castor bench`` — benchmarks that emit a JSON file rather than a claim.

Today there is one: ``ten-minutes``, the OpenCastor ten-minute goal made
runnable. See ``docs/benchmarks/ten-minutes.md`` and the review it comes from,
``docs/reviews/microduck-ten-minutes-2026-09-08.md``.
"""

from castor.bench.record import BENCHMARK, DEFAULT_BUDGET_S, Checkpoint, Record

__all__ = ["BENCHMARK", "DEFAULT_BUDGET_S", "Checkpoint", "Record"]
