"""Colour: the palette, the colour reference, the colour metric, and what stays the same.

The load-bearing claim of this feature is a negative one: adding colour changes
nothing about the arm and nothing about the composite. Most of what follows is
there to hold that claim down.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "inspect_robots", reason="castor bench sacpaint needs pip install 'opencastor[paintbench]'"
)

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from inspect_robots.rollout import TrialRecord
from inspect_robots.types import Action, Observation

from castor.bench.sacpaint import cli, palette as pal, reference as refmod
from castor.bench.sacpaint.mock import (
    REFERENCE_COLOR_CAM,
    IdlePolicy,
    PlotterEmbodiment,
    TracePolicy,
    stroke_color,
)
from castor.bench.sacpaint.scorers import CANONICAL_FLAG, OVERHEAD, color_details, score_canvas
from castor.bench.sacpaint.tasks import make_task

CANVAS = "150x200"


# --- fixtures ------------------------------------------------------------------


def _synthetic_photo() -> np.ndarray:
    """Indigo on the left, gold on the right, a black bar between, white rules across.

    The rules give the auto-tracer edges to find; the two fields give the colour
    reference two large, unambiguous regions.
    """
    img = np.zeros((400, 300, 3), np.uint8)
    img[:, :150] = (40, 60, 170)
    img[:, 150:] = (200, 160, 20)
    cv2.rectangle(img, (140, 0), (160, 399), (0, 0, 0), -1)
    for y in range(0, 400, 60):
        cv2.line(img, (0, y), (299, y), (255, 255, 255), 3)
    return img


def _new(tmp_path: Path, name: str, *, color: bool) -> Path:
    photo = tmp_path / f"{name}-source.jpg"
    cv2.imwrite(str(photo), cv2.cvtColor(_synthetic_photo(), cv2.COLOR_RGB2BGR))
    cli.cmd_new(
        argparse.Namespace(
            name=name,
            from_reference="sacramento-photo-v1",
            canvas=CANVAS,
            description="",
            photo=str(photo),
            photo_credit="",
            auto_trace=True,
            color=color,
            force=True,
        )
    )
    refmod.refresh()
    return refmod.user_reference_dir() / f"{name}.spec.json"


@pytest.fixture
def refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SACPAINT_REFERENCES", str(tmp_path / "refs"))
    refmod.refresh()
    yield tmp_path
    refmod.refresh()


def _run(reference: str, *, force_color: int | None = None) -> PlotterEmbodiment:
    """Play the oracle through the mock plotter, optionally overriding every colour."""
    task = make_task(reference)
    scene = task.scenes[0]
    emb, pol = PlotterEmbodiment(reference=reference), TracePolicy(reference=reference)
    obs = emb.reset(scene)
    pol.reset(scene)
    steps = 0
    while steps < 8000:
        chunk = pol.act(obs)
        stop = False
        for action in chunk.actions:
            data = np.asarray(action.data, dtype=np.float64).copy()
            if force_color is not None and data.size == 4:
                data[3] = float(force_color)
            obs = emb.step(Action(data=data, meta=action.meta)).observation
            steps += 1
            stop = stop or bool(action.meta.get("request_stop"))
        if stop:
            break
    return emb


# --- the palette ----------------------------------------------------------------


def test_every_palette_colour_is_ink_to_the_line_scorers() -> None:
    """The composite binarises at INK_THRESHOLD, so a pale palette would erase colour runs."""
    lum = cv2.cvtColor(pal.RGB.astype(np.uint8).reshape(1, -1, 3), cv2.COLOR_RGB2GRAY).reshape(-1)
    assert lum.max() < refmod.INK_THRESHOLD, dict(zip(pal.NAMES, lum.tolist(), strict=True))
    assert 8 <= len(pal.NAMES) <= 12
    assert pal.NAMES[0] == "black" and pal.DEFAULT_INDEX == 0


def test_index_of_accepts_names_and_numbers_and_refuses_anything_else() -> None:
    assert pal.index_of(None) == 0
    assert pal.index_of("indigo") == pal.NAMES.index("indigo")
    assert pal.index_of(4.4) == 4 and pal.index_of("4") == 4
    with pytest.raises(ValueError, match="unknown pen colour"):
        pal.index_of("chartreuse")
    with pytest.raises(ValueError, match="outside"):
        pal.index_of(99)


# --- the mock plotter renders in the colour -------------------------------------


def test_a_coloured_stroke_lands_in_that_colour_and_the_default_is_black(refs: Path) -> None:
    _new(refs, "colourful", color=True)
    emb = PlotterEmbodiment(reference="colourful")
    assert emb.info.action_space.dim == 4
    indigo = float(pal.index_of("indigo"))
    emb.reset(make_task("colourful").scenes[0])
    emb.step(Action(data=np.array([0.02, 0.02, 0.0, indigo])))
    emb.step(Action(data=np.array([0.13, 0.02, 0.0, indigo])))
    canvas = emb.canvas()
    painted = np.unique(canvas.reshape(-1, 3), axis=0).tolist()
    assert list(pal.rgb_of("indigo")) in painted
    assert [0, 0, 0] not in painted  # nothing was laid down in black

    # A three-number action on the same colour canvas still means black.
    emb.reset(make_task("colourful").scenes[0])
    emb.step(Action(data=np.array([0.02, 0.05, 0.0])))
    emb.step(Action(data=np.array([0.13, 0.05, 0.0])))
    black = emb.canvas()
    assert [0, 0, 0] in np.unique(black.reshape(-1, 3), axis=0).tolist()


def test_a_mono_reference_keeps_the_three_dimension_contract(refs: Path) -> None:
    _new(refs, "plain", color=False)
    emb = PlotterEmbodiment(reference="plain")
    assert emb.info.action_space.dim == 3
    assert emb.info.action_space.semantics.dim_labels == ("x", "y", "z")
    assert [c.name for c in emb.info.observation_space.cameras] == ["overhead", "reference"]
    assert REFERENCE_COLOR_CAM not in emb.reset(make_task("plain").scenes[0]).images
    assert pal.COLOR_DIM_LABEL not in emb.info.docs
    assert IdlePolicy(reference="plain").info.action_space.dim == 3


# --- the colour reference -------------------------------------------------------


def test_new_color_builds_a_colour_reference_beside_an_unchanged_skeleton(refs: Path) -> None:
    mono_path = _new(refs, "mono", color=False)
    mono = json.loads(mono_path.read_text())
    colour_path = _new(refs, "colour", color=True)
    colour = json.loads(colour_path.read_text())

    # The skeleton the composite reads is untouched by --color.
    assert colour["strokes"] == mono["strokes"]
    assert colour["landmarks"] == mono["landmarks"]
    assert mono["color"] is None and mono["color_regions"] == []

    assert colour["color"] == "colour.color.png"
    assert colour["color_palette"] == pal.PALETTE_VERSION
    stored = colour_path.parent / colour["color"]
    assert stored.is_file()

    spec = refmod.get_spec("colour")
    assert spec.has_color and refmod.has_color("colour")
    assert len(spec.color_regions) == pal.COLOR_GRID[0] * pal.COLOR_GRID[1]
    # Downscaled: a colour target, not a second line drawing.
    small = spec.color_reference_image(canonical=False)
    assert small.shape[:2] == (200, 150) < spec.canonical_size()[::-1]
    # Only palette colours survive quantisation, and this picture's two fields are in it.
    used = {tuple(c) for c in np.unique(small.reshape(-1, 3), axis=0).tolist()}
    assert used <= {tuple(int(v) for v in rgb) for rgb in pal.RGB.tolist()}
    assert {r["color"] for r in spec.color_regions} >= {"indigo", "gold"}
    # Upscaling to the canvas invents no colour.
    canonical = spec.color_reference_image()
    assert canonical.shape[:2] == spec.canonical_size()[::-1]
    assert {tuple(c) for c in np.unique(canonical.reshape(-1, 3), axis=0).tolist()} <= used


def test_color_without_a_photo_is_refused(refs: Path) -> None:
    with pytest.raises(SystemExit, match="--color needs --photo"):
        cli.cmd_new(
            argparse.Namespace(
                name="nophoto",
                from_reference="sacramento-photo-v1",
                canvas=CANVAS,
                description="",
                photo=None,
                photo_credit="",
                auto_trace=False,
                color=True,
                force=True,
            )
        )


# --- the colour metric ----------------------------------------------------------


def test_color_fidelity_rewards_a_match_punishes_a_wrong_colour_and_zeroes_a_blank(
    refs: Path,
) -> None:
    _new(refs, "scored", color=True)
    spec = refmod.get_spec("scored")

    matched = color_details(_run("scored").canvas(), spec)
    wrong = color_details(_run("scored", force_color=pal.index_of("crimson")).canvas(), spec)
    blank = color_details(np.full((*spec.canonical_size()[::-1], 3), 255, np.uint8), spec)

    assert matched["value"] > 0.8
    assert matched["accuracy"] > 0.8 and matched["coverage"] > 0.6
    # One colour everywhere on a two-colour picture cannot be right about both.
    assert wrong["value"] < matched["value"] - 0.3
    assert blank == {
        **blank,
        "value": 0.0,
        "accuracy": 0.0,
        "coverage": 0.0,
        "painted_px": 0,
        "note": "nothing painted",
    }


def test_color_details_says_so_for_a_mono_reference(refs: Path) -> None:
    _new(refs, "monoscore", color=False)
    spec = refmod.get_spec("monoscore")
    details = color_details(np.full((800, 600, 3), 255, np.uint8), spec)
    assert details["value"] == 0.0 and "no colour target" in details["note"]


def test_the_composite_is_the_same_number_whatever_colour_the_run_used(refs: Path) -> None:
    """The whole point: colour must not move line fidelity, or past runs stop comparing."""
    _new(refs, "same", color=True)
    spec = refmod.get_spec("same")
    oracle = score_canvas(_run("same").canvas(), spec)
    black = score_canvas(_run("same", force_color=0).canvas(), spec)
    assert oracle["composite_photo"] == black["composite_photo"]
    assert oracle["parts"] == black["parts"]
    # ... and colour is the number that did move.
    assert oracle["color_fidelity"]["value"] > black["color_fidelity"]["value"] + 0.3
    assert "color_fidelity" not in oracle["parts"]
    assert set(oracle["weights"]) == {
        "landmark_geometry",
        "structure",
        "discipline",
        "efficiency",
    }


def test_a_colour_task_registers_the_colour_scorer_and_records_the_palette(refs: Path) -> None:
    _new(refs, "tasked", color=True)
    _new(refs, "untasked", color=False)
    colour, mono = make_task("tasked"), make_task("untasked")
    assert [s.name for s in colour.scorer][-1] == "color_fidelity"
    assert "color_fidelity" not in [s.name for s in mono.scorer]
    assert [s.name for s in mono.scorer] == [
        "composite",
        "landmark_geometry",
        "structure",
        "discipline",
        "efficiency",
    ]
    assert colour.metadata["color"] is True
    assert colour.metadata["color_palette"] == pal.PALETTE_VERSION
    assert len(colour.metadata["color_sha256"]) == 64
    assert mono.metadata["color"] is False and mono.metadata["color_sha256"] == ""


def test_the_colour_scorer_is_inapplicable_rather_than_zero_on_a_mono_task(refs: Path) -> None:
    from castor.bench.sacpaint.scorers import color_fidelity

    _new(refs, "monotask", color=False)
    task = make_task("monotask")
    record = TrialRecord(scene_id="s", epoch=0, seed=0)
    record.parked_observation = Observation(
        images={OVERHEAD: np.full((800, 600, 3), 255, np.uint8)}, extra={CANONICAL_FLAG: True}
    )
    score = color_fidelity()(record, task.scenes[0].target)
    assert score.metadata["applicable"] is False and "mono task" in score.explanation


# --- the prompt -----------------------------------------------------------------


def test_the_prompt_names_the_palette_for_a_colour_task_and_not_for_a_mono_one(
    refs: Path,
) -> None:
    _new(refs, "promptcolour", color=True)
    _new(refs, "promptmono", color=False)
    colour = PlotterEmbodiment(reference="promptcolour").info.docs
    mono = PlotterEmbodiment(reference="promptmono").info.docs

    for name in pal.NAMES:
        assert name in colour, name
        assert name not in mono, name
    assert REFERENCE_COLOR_CAM in colour and REFERENCE_COLOR_CAM not in mono
    assert "colour per stroke" in colour
    assert "changes nothing about how the arm moves" in colour
    assert "scored separately" in colour
    # The mono prompt is exactly the prompt it always was.
    assert mono.endswith("the sheet's edges.")


def test_the_colour_reference_is_offered_as_a_stream_like_the_line_reference(
    refs: Path,
) -> None:
    _new(refs, "streamed", color=True)
    emb = PlotterEmbodiment(reference="streamed")
    names = [c.name for c in emb.info.observation_space.cameras]
    assert names == ["overhead", "reference", REFERENCE_COLOR_CAM]
    obs = emb.reset(make_task("streamed").scenes[0])
    assert REFERENCE_COLOR_CAM in obs.images
    shown = obs.images[REFERENCE_COLOR_CAM]
    assert shown.shape[:2] == emb.spec.canonical_size()[::-1]
    assert obs.extra["palette"] == list(pal.NAMES)
    assert obs.extra["color"] == "black"


# --- the oracle -----------------------------------------------------------------


def test_the_oracle_reads_its_colour_off_the_colour_reference(refs: Path) -> None:
    _new(refs, "oracle", color=True)
    spec = refmod.get_spec("oracle")
    chosen = {pal.NAMES[stroke_color(spec, st)] for g in spec.strokes.values() for st in g}
    assert chosen & {"indigo", "gold"}
    assert stroke_color(refmod.get_spec("oracle"), [(0.0, 0.0), (1.0, 1.0)]) in range(
        len(pal.NAMES)
    )


def test_the_agent_tool_surface_gains_colour_and_nothing_else(refs: Path) -> None:
    """The framework builds the move tool from the action space; colour must fit through it."""
    tools = pytest.importorskip("inspect_robots_agent._tools")
    _new(refs, "toolcolour", color=True)
    _new(refs, "toolmono", color=False)

    def surface(name: str) -> tuple[list[str], dict]:
        emb = PlotterEmbodiment(reference=name)
        built = tools.build_toolset(
            emb.info.action_space,
            emb.info.observation_space,
            emb.info.control_hz,
            1.0,
            images="on_demand",
        )
        schemas = built.schemas()
        return [s["function"]["name"] for s in schemas], built

    colour_names, colour_built = surface("toolcolour")
    mono_names, mono_built = surface("toolmono")
    assert colour_names == mono_names  # same tools, no new ones
    assert colour_built.state_labels() == ("eef_pos", ("x", "y", "z", "color"))
    assert mono_built.state_labels() == ("eef_pos", ("x", "y", "z"))
    described = {
        s["function"]["name"]: s["function"]["parameters"] for s in colour_built.schemas()
    }
    move = next(k for k in described if k.startswith("move"))
    assert "color" in described[move]["properties"]["targets"]["description"]
