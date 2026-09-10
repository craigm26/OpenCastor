"""Task registration. One scene per reference, one fixed prompt, one pinned reference.

``sacpaint/photo-v1`` is the built-in Sacramento task: the original photograph
on the ``reference`` camera, a traced landmark skeleton as the scoring rubric.
``sacpaint/line-v0``, the earlier line-drawing reference, stays registered for
comparison runs. Every other discoverable reference (package assets or
``~/.sacpaint/references/<name>.spec.json``) registers as ``sacpaint/<name>``
when this module is imported, which happens whenever Inspect Robots loads
either entry point.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from inspect_robots.registry import task
from inspect_robots.scene import Scene, Target
from inspect_robots.task import Task

from castor.bench.sacpaint.reference import (
    DEFAULT_REFERENCE,
    LINE_REFERENCE,
    available,
    get_spec,
    ink_sha256,
    reference_sha256,
)
from castor.bench.sacpaint.scorers import (
    composite,
    discipline,
    efficiency,
    landmark_geometry,
    structure,
)

INSTRUCTION = (
    "Draw the reference image on the canvas with the pen. You may look at the "
    "overhead camera to inspect your work and make corrections. Stop when you "
    "believe the drawing is complete."
)

TASK_NAME = "sacpaint/photo-v1"
LINE_TASK_NAME = "sacpaint/line-v0"
_BUILTIN_TASKS = {DEFAULT_REFERENCE: TASK_NAME, LINE_REFERENCE: LINE_TASK_NAME}
DEFAULT_MAX_STEPS = 3000
DEFAULT_EPOCHS = 3


def task_name_for(reference: str) -> str:
    """The registry name a reference runs under."""
    return _BUILTIN_TASKS.get(reference, f"sacpaint/{reference}")


def make_task(
    reference: str = DEFAULT_REFERENCE,
    max_steps: int = DEFAULT_MAX_STEPS,
    epochs: int = DEFAULT_EPOCHS,
) -> Task:
    """Build the benchmark task for a reference; the scene's target names the reference for the scorers."""
    spec = get_spec(reference)
    sha = reference_sha256(spec.name)
    ink = ink_sha256(spec.name)
    return Task(
        name=task_name_for(spec.name),
        scenes=[
            Scene(
                id=f"{spec.name}",
                instruction=spec.instruction or INSTRUCTION,
                target=Target(
                    kind="reference_drawing",
                    spec={
                        "reference": spec.name,
                        "kind": spec.kind,
                        "sha256": sha,
                        "ink_sha256": ink,
                        "rubric": spec.rubric(),
                    },
                ),
                metadata={"description": spec.description, "photo_credit": spec.photo_credit},
            )
        ],
        scorer=[
            composite(max_steps),
            landmark_geometry(),
            structure(),
            discipline(),
            efficiency(max_steps),
        ],
        max_steps=max_steps,
        epochs=epochs,
        metadata={
            "benchmark": "sacpaint",
            "reference": spec.name,
            "reference_kind": spec.kind,
            "prompt_fixed": spec.instruction is None,
            "reference_sha256": sha,
            "ink_sha256": ink,
            "track": "closed_loop",
        },
    )


@task(TASK_NAME)
def photo_v1(max_steps: int = DEFAULT_MAX_STEPS, epochs: int = DEFAULT_EPOCHS) -> Task:
    """Sacramento PaintBench V1: the original photograph, scored against its traced landmarks."""
    return make_task(DEFAULT_REFERENCE, max_steps=max_steps, epochs=epochs)


@task(LINE_TASK_NAME)
def line_v0(max_steps: int = DEFAULT_MAX_STEPS, epochs: int = DEFAULT_EPOCHS) -> Task:
    """Sacramento PaintBench V0: the line-drawing reference, rubric-scored. Kept for comparison runs."""
    return make_task(LINE_REFERENCE, max_steps=max_steps, epochs=epochs)


def _factory(reference: str) -> Callable[..., Task]:
    def build(max_steps: int = DEFAULT_MAX_STEPS, epochs: int = DEFAULT_EPOCHS, **_: Any) -> Task:
        return make_task(reference, max_steps=max_steps, epochs=epochs)

    build.__doc__ = f"PaintBench task for the {reference!r} reference."
    build.__name__ = f"task_{reference.replace('-', '_')}"
    return build


def register_discovered() -> list[str]:
    """Register every non-built-in reference as ``sacpaint/<name>``; returns the names registered."""
    from inspect_robots.registry import registered

    names = []
    already = registered("task")
    for name in available():
        if name in _BUILTIN_TASKS:
            continue
        key = task_name_for(name)
        if key not in already:
            task(key)(_factory(name))
        names.append(key)
    return names


register_discovered()
