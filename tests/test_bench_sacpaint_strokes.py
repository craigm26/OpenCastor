"""The stroke primitive: one call, many segments, and nothing else different.

The load-bearing claims here are negative ones. A stroke draws the same
segments the per-target action drew, in the same colours; a stroke that would
leave the sheet draws nothing at all and leaves the pen where it was; the
oracle playing strokes back scores what the oracle playing targets back scores;
and with the primitive off the prompt is the prompt it always was.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "inspect_robots", reason="castor bench sacpaint needs pip install 'opencastor[paintbench]'"
)

from pathlib import Path

import numpy as np
import pytest
from inspect_robots import eval as ir_eval
from inspect_robots.scene import Scene

from castor.bench.sacpaint import palette as pal
from castor.bench.sacpaint import strokes as stroke_lib
from castor.bench.sacpaint.mock import (
    IdlePolicy,
    PlotterEmbodiment,
    TracePolicy,
    stroke_action_groups,
    stroke_actions,
)
from castor.bench.sacpaint.reference import get_spec
from castor.bench.sacpaint.tasks import make_task

COLOR_REFERENCE = "starry-night"
MONO_REFERENCE = "sacramento-line-v0"
SCENE = Scene(id="strokes", instruction="Draw the reference image.")


def _plotter(reference: str, **kwargs) -> PlotterEmbodiment:
    body = PlotterEmbodiment(reference=reference, strokes=True, **kwargs)
    body.reset(SCENE)
    return body


def _pixel(body: PlotterEmbodiment, x_m: float, y_m: float) -> tuple[int, int, int]:
    col, row = body.spec.mm_to_px((x_m * 1000.0, y_m * 1000.0))
    return tuple(int(v) for v in body.canvas()[row, col])


# --- the primitive -------------------------------------------------------------


def test_a_stroke_plans_the_targets_the_body_already_sends() -> None:
    targets = stroke_lib.plan(
        [(0.01, 0.02), (0.05, 0.02), (0.05, 0.06)],
        low=[0.0, 0.0, 0.0],
        high=[0.15, 0.12, 0.05],
        pen_down_z=0.002,
        travel_z=0.005,
    )
    assert [t.tolist() for t in targets] == [
        [0.01, 0.02, 0.005],  # travel over the first point
        [0.01, 0.02, 0.002],  # down
        [0.05, 0.02, 0.002],
        [0.05, 0.06, 0.002],
        [0.05, 0.06, 0.005],  # up again after the last
    ]
    colored = stroke_lib.plan(
        [(0.01, 0.02), (0.05, 0.02)],
        low=[0.0, 0.0, 0.0, 0.0],
        high=[0.15, 0.12, 0.05, 11.0],
        pen_down_z=0.002,
        travel_z=0.005,
        color="gold",
    )
    assert all(t.shape == (4,) for t in colored)
    assert {t[3] for t in colored} == {float(pal.index_of("gold"))}


def test_a_stroke_on_the_mock_lands_every_segment_in_its_colour() -> None:
    body = _plotter(COLOR_REFERENCE)
    width_m, height_m = (v / 1000.0 for v in body.spec.canvas_mm)
    points = [
        (0.2 * width_m, 0.2 * height_m),
        (0.8 * width_m, 0.2 * height_m),
        (0.8 * width_m, 0.8 * height_m),
        (0.2 * width_m, 0.8 * height_m),
    ]
    gold = pal.rgb_of("gold")
    result = body.stroke(points, color="gold")

    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        assert _pixel(body, (x0 + x1) / 2, (y0 + y1) / 2) == gold
    # The travel legs marked nothing: outside the polyline the sheet is still white.
    assert _pixel(body, 0.5 * width_m, 0.5 * height_m) == (255, 255, 255)
    # Every target is recorded, in order, under one stroke id.
    assert len(body.stroke_log) == len(points) + 2 == body.num_steps
    assert {entry["stroke"] for entry in body.stroke_log} == {1}
    assert [entry["stroke_point"] for entry in body.stroke_log] == list(range(len(points) + 2))
    assert [entry["stroke_final"] for entry in body.stroke_log][-1] is True
    # One observation, at the end, with the pen lifted off the last point.
    assert result.observation.state["eef_pos"][:3] == pytest.approx([*points[-1], 0.005])
    assert result.observation.extra["color"] == "gold"


def test_a_stroke_that_leaves_the_sheet_is_refused_without_moving() -> None:
    body = _plotter(COLOR_REFERENCE)
    width_m, height_m = (v / 1000.0 for v in body.spec.canvas_mm)
    before_canvas = body.canvas()
    before_eef = body._eef.copy()

    with pytest.raises(stroke_lib.StrokeError, match="off the sheet"):
        body.stroke([(0.1 * width_m, 0.1 * height_m), (width_m * 3.0, 0.1 * height_m)])

    assert body.num_steps == 0
    assert body.stroke_log == []
    assert np.array_equal(body.canvas(), before_canvas)
    assert np.array_equal(body._eef, before_eef)
    # And the same refusal for the other malformed shapes, equally motionless.
    for bad, match in (
        ([], "at least one point"),
        ([[0.01, 0.01, 0.002]], r"\[x, y\] pairs"),
        ([[0.01, 0.01]] * (stroke_lib.MAX_POINTS + 1), "at most"),
        ([[0.01, float("nan")]], "finite"),
    ):
        with pytest.raises(stroke_lib.StrokeError, match=match):
            body.stroke(bad)
    assert body.num_steps == 0
    assert np.array_equal(body.canvas(), before_canvas)


def test_a_stroke_is_looked_at_once_and_every_target_is_still_a_step() -> None:
    """Mid-stroke targets get the standing view; the last one gets a fresh look."""
    from inspect_robots.types import Action

    body = _plotter(MONO_REFERENCE)
    targets = stroke_lib.plan(
        [(0.02, 0.02), (0.06, 0.02)],
        low=body.info.action_space.low,
        high=body.info.action_space.high,
        pen_down_z=body.pen_down_z,
        travel_z=0.005,
    )
    seen = [
        body.step(Action(data=t, meta=stroke_lib.meta_for(7, i, len(targets)))).observation
        for i, t in enumerate(targets)
    ]
    assert body.num_steps == len(targets)  # every target is its own step
    assert all(o is seen[0] for o in seen[:-1])  # one view for the whole stroke
    assert seen[-1] is not seen[0]  # and a fresh one at the end


# --- the oracle ----------------------------------------------------------------


def test_the_stroke_oracle_draws_the_same_targets_in_stroke_shaped_calls() -> None:
    spec = get_spec(MONO_REFERENCE)
    groups = stroke_action_groups(spec)
    flat = stroke_actions(spec)
    assert sum(len(g) for g in groups) == len(flat)
    assert all(np.array_equal(a, b) for a, b in zip([a for g in groups for a in g], flat))

    policy = TracePolicy(reference=MONO_REFERENCE, strokes=True)
    policy.reset(SCENE)
    chunk = policy.act(_observation(policy))
    assert len(chunk.actions) == len(groups[0]) > 1  # one whole stroke in one call
    assert {a.meta[stroke_lib.STROKE_KEY] for a in chunk.actions} == {1}
    assert chunk.actions[-1].meta[stroke_lib.FINAL_KEY] is True
    assert all(a.meta[stroke_lib.FINAL_KEY] is False for a in chunk.actions[:-1])


def _observation(policy: TracePolicy):
    from inspect_robots.types import Observation

    dim = policy.info.action_space.dim
    return Observation(images={}, state={"eef_pos": np.zeros(dim)})


@pytest.mark.parametrize("reference", [MONO_REFERENCE, COLOR_REFERENCE])
def test_the_stroke_oracle_scores_at_least_the_per_target_oracle(
    tmp_path: Path, reference: str
) -> None:
    task = make_task(reference, max_steps=3000, epochs=1)

    def run(strokes: bool) -> dict[str, float]:
        (log,) = ir_eval(
            task,
            TracePolicy(reference=reference, strokes=strokes),
            PlotterEmbodiment(reference=reference, strokes=strokes),
            log_dir=str(tmp_path / ("stroke" if strokes else "target")),
        )
        return {k: float(v) for k, v in log.results.metrics.items()}

    per_target, stroke = run(False), run(True)
    assert stroke["composite"] >= per_target["composite"] > 0.9
    assert stroke["efficiency"] >= per_target["efficiency"]
    if "color_fidelity" in per_target:
        assert stroke["color_fidelity"] >= per_target["color_fidelity"]


def test_the_packaged_task_still_orders_the_oracle_above_the_floor(tmp_path: Path) -> None:
    task = make_task("sacramento-photo-v1", max_steps=3000, epochs=1)
    (trace,) = ir_eval(
        task,
        TracePolicy(reference="sacramento-photo-v1", strokes=True),
        PlotterEmbodiment(reference="sacramento-photo-v1", strokes=True),
        log_dir=str(tmp_path / "trace"),
    )
    (idle,) = ir_eval(
        task,
        IdlePolicy(reference="sacramento-photo-v1"),
        PlotterEmbodiment(reference="sacramento-photo-v1", strokes=True),
        log_dir=str(tmp_path / "idle"),
    )
    assert trace.results.metrics["composite"] > 0.9 > idle.results.metrics["composite"]


# --- the prompt ----------------------------------------------------------------


def test_the_prompt_mentions_strokes_only_when_they_are_on() -> None:
    off = PlotterEmbodiment(reference=MONO_REFERENCE)
    on = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    assert "stroke'" not in off.info.docs and stroke_lib.TOOL_NAME not in off.info.docs
    assert on.info.docs.startswith(off.info.docs)  # byte-identical, then the new paragraph
    assert f"'{stroke_lib.TOOL_NAME}' tool" in on.info.docs
    assert str(stroke_lib.MAX_POINTS) in on.info.docs

    colour_off = PlotterEmbodiment(reference=COLOR_REFERENCE)
    colour_on = PlotterEmbodiment(reference=COLOR_REFERENCE, strokes=True)
    assert colour_on.info.docs.startswith(colour_off.info.docs)
    assert "one 'color' for the whole line" in colour_on.info.docs


def test_the_stroke_tool_schema_declares_the_cap_and_the_sheet() -> None:
    schema = stroke_lib.tool_schema(canvas_mm=(150.0, 200.0), colored=True)
    function = schema["function"]
    assert function["name"] == "stroke"
    points = function["parameters"]["properties"]["points"]
    assert points["maxItems"] == stroke_lib.MAX_POINTS
    assert "0.15" in points["description"] and "0.20" in points["description"]
    assert function["parameters"]["properties"]["color"]["maximum"] == len(pal.NAMES) - 1
    mono = stroke_lib.tool_schema(canvas_mm=(150.0, 200.0))
    assert "color" not in mono["function"]["parameters"]["properties"]


def test_the_registry_factory_coerces_the_flag_the_cli_passes() -> None:
    from castor.bench.sacpaint.mock import plotter_embodiment, trace_policy

    assert plotter_embodiment(reference=MONO_REFERENCE, strokes="true").strokes is True
    assert plotter_embodiment(reference=MONO_REFERENCE, strokes="false").strokes is False
    assert trace_policy(reference=MONO_REFERENCE, strokes="yes").strokes is True
    with pytest.raises(ValueError, match="true or false"):
        plotter_embodiment(reference=MONO_REFERENCE, strokes="maybe")
