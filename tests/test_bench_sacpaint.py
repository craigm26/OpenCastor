"""Scorer discrimination, rectification without a fixture, spec-driven references, and the CLI."""

from __future__ import annotations

import pytest

pytest.importorskip(
    "inspect_robots", reason="castor bench sacpaint needs pip install 'opencastor[paintbench]'"
)

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from inspect_robots import eval as ir_eval
from inspect_robots.rollout import TrialRecord
from inspect_robots.types import Observation

from castor.bench.sacpaint import cli, reference
from castor.bench.sacpaint.mock import IdlePolicy, PlotterEmbodiment, TracePolicy
from castor.bench.sacpaint.rectify import (
    RectifyError,
    compose_fixture_view,
    compose_sheet_view,
    find_canvas_corners,
    rectify,
)
from castor.bench.sacpaint.scorers import (
    CANONICAL_FLAG,
    CORNERS_KEY,
    OVERHEAD,
    discipline,
    landmark_geometry,
    structure,
)
from castor.bench.sacpaint.tasks import line_v0, make_task, photo_v1, register_discovered

ASSETS = Path(reference.__file__).parent / "assets"


def _record_with(canvas: np.ndarray, **extra) -> TrialRecord:
    rec = TrialRecord(scene_id="s", epoch=0, seed=0)
    rec.parked_observation = Observation(
        images={OVERHEAD: canvas}, extra=extra or {CANONICAL_FLAG: True}
    )
    return rec


def _scribble(n_lines: int, seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    img = np.full((800, 600, 3), 255, dtype=np.uint8)
    for _ in range(n_lines):
        p0 = tuple(int(v) for v in rng.uniform(0, [600, 800]))
        p1 = tuple(int(v) for v in rng.uniform(0, [600, 800]))
        cv2.line(img, p0, p1, (0, 0, 0), 3)
    return img


def _perspective(sheet: np.ndarray) -> np.ndarray:
    h, w = sheet.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    dst = np.array([[60, 40], [w - 30, 70], [w - 80, h - 20], [20, h - 60]], dtype=np.float32)
    return cv2.warpPerspective(
        sheet, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=(96, 96, 96)
    )


# --- assets and spec ---------------------------------------------------------------


def test_assets_match_generator() -> None:
    line = reference.sacramento_spec()
    assert hashlib.sha256(line.png_bytes()).hexdigest() == reference.reference_sha256(
        reference.LINE_REFERENCE
    )
    assert reference.reference_sha256(reference.LINE_REFERENCE).startswith(
        "ce081e13"
    )  # the V0 identity, unchanged
    assert json.loads((ASSETS / f"{reference.LINE_REFERENCE}.json").read_text()) == line.rubric()
    photo = reference.sacramento_photo_spec()
    assert hashlib.sha256(photo.png_bytes()).hexdigest() == reference.ink_sha256()
    assert json.loads((ASSETS / reference.RUBRIC_JSON).read_text()) == photo.rubric()
    for spec in (line, photo):
        on_disk = json.loads((ASSETS / f"{spec.name}{reference.SPEC_SUFFIX}").read_text())
        assert reference.ReferenceSpec.from_dict(on_disk, base_dir=ASSETS).rubric() == spec.rubric()


def test_the_model_sees_the_photograph_and_the_scorers_see_the_ink() -> None:
    assert reference.DEFAULT_REFERENCE == "sacramento-photo-v1"
    assert reference.reference_kind() == "photo"
    seen = reference.reference_image()
    assert seen.shape == (2000, 1499, 3)  # the original, native size, 3:4 like the sheet
    assert (seen.std(axis=2) > 8).mean() > 0.3  # a colour photograph, not a black-and-white render
    ink = reference.reference_ink()
    assert ink.shape == (800, 600, 3)
    assert set(np.unique(ink)) <= {0, 255}
    photo_sha = hashlib.sha256((ASSETS / reference.PHOTO_FILE).read_bytes()).hexdigest()
    assert reference.reference_sha256() == photo_sha != reference.ink_sha256()
    task = make_task()
    assert task.name == "sacpaint/photo-v1"
    assert task.metadata["reference_kind"] == "photo"
    assert task.metadata["reference_sha256"] == photo_sha
    assert task.scenes[0].target.spec["ink_sha256"] == reference.ink_sha256()
    # the line reference still answers as before
    assert reference.reference_image(reference.LINE_REFERENCE).shape == (800, 600, 3)
    assert reference.reference_kind(reference.LINE_REFERENCE) == "line"


def test_photo_rubric_landmarks_sit_where_the_photo_has_them() -> None:
    rub = reference.load_rubric()
    lm = rub["landmarks"]
    assert set(lm) == {"horizon", "tower_bridge", "road", "cupola", "capitol_dome", "buildings"}
    assert lm["tower_bridge"]["weight"] == lm["capitol_dome"]["weight"] == 3
    # normalised boxes, y down: the tower is in the upper middle, the dome along the bottom edge
    x0, y0, x1, y1 = lm["tower_bridge"]["bbox"]
    assert 0.35 < x0 < x1 < 0.65 and 0.25 < y0 < y1 < 0.6
    x0, y0, x1, y1 = lm["capitol_dome"]["bbox"]
    assert y1 == 1.0 and 0.1 < x0 < 0.2 and 0.8 < x1 < 0.9
    assert {r["kind"] for r in rub["relations"]} == {"same_x", "above"}


def test_spec_round_trip_and_auto_bbox() -> None:
    spec = reference.ReferenceSpec(
        name="tri",
        strokes={"t": [[(10.0, 10.0), (90.0, 10.0), (50.0, 80.0), (10.0, 10.0)]]},
        canvas_mm=(100.0, 100.0),
    )
    back = reference.ReferenceSpec.from_dict(json.loads(json.dumps(spec.to_dict())))
    assert back.rubric() == spec.rubric()
    assert back.rubric()["landmarks"]["t"]["bbox"] == [0.04, 0.14, 0.96, 0.96]
    with pytest.raises(ValueError):
        reference.ReferenceSpec.from_dict({"strokes": {}})


# --- scorers ------------------------------------------------------------------------


def test_perfect_trace_scores_near_one_and_blank_zero() -> None:
    ref = reference.reference_ink()
    blank = np.full_like(ref, 255)
    for scorer in (landmark_geometry(), structure()):
        # 2e-3: the photo rubric's same_x relations read the ink centroids, and banker's rounding
        # in rasterisation leaves them a third of a pixel apart on the reference itself.
        assert scorer(_record_with(ref), None).value == pytest.approx(1.0, abs=2e-3)
        assert scorer(_record_with(blank), None).value == 0.0
    assert discipline()(_record_with(ref), None).value == pytest.approx(1.0)
    assert discipline()(_record_with(blank), None).value == 0.0


@pytest.mark.parametrize("n_lines", [30, 60, 400])
def test_scribble_is_punished(n_lines: int) -> None:
    scribble = _scribble(n_lines)
    # Measured 2026-09-09 on the photo rubric: 30/60/400 lines composite 0.33/0.47/0.44. The photo's
    # skeleton covers more of the sheet than the V0 line drawing, so a dense scribble collects more
    # structure precision; the oracle still sits 0.5 above it and the landmark term stays low.
    assert landmark_geometry()(_record_with(scribble), None).value < 0.55
    assert structure()(_record_with(scribble), None).value < 0.6
    assert discipline()(_record_with(scribble), None).value < 0.6


def test_shifted_drawing_is_charged_for_placement() -> None:
    ref = reference.reference_ink()
    shifted = np.full_like(ref, 255)
    shifted[:, 20:] = ref[
        :, :-20
    ]  # 5 mm to the right (20 px): a placement error, not a missing landmark
    d = landmark_geometry()(_record_with(shifted), None)
    assert 0.4 < d.value < 0.95
    tower = d.metadata["landmarks"]["tower_bridge"]
    assert tower["presence"] > 0.7 and tower["position"] < 1.0


def test_wobbly_hand_is_forgiven() -> None:
    rng = np.random.RandomState(3)
    wobbly = reference.render(
        {
            k: [[(x + rng.normal(0, 1.0), y + rng.normal(0, 1.0)) for x, y in st] for st in v]
            for k, v in reference.strokes().items()
        }
    )
    assert landmark_geometry()(_record_with(wobbly), None).value > 0.9
    assert structure()(_record_with(wobbly), None).value > 0.9


# --- rectification without a fixture -----------------------------------------------


def test_rectify_from_markers_under_perspective() -> None:
    ref = reference.reference_ink()
    warped = _perspective(compose_fixture_view(ref))
    _, how = find_canvas_corners(warped)
    assert how == "markers"
    back = rectify(warped)
    assert structure()(_record_with(back), None).value > 0.9
    assert landmark_geometry()(_record_with(back), None).value > 0.9


def test_rectify_from_plain_sheet_under_perspective() -> None:
    ref = reference.reference_ink()
    warped = _perspective(compose_sheet_view(ref))
    _, how = find_canvas_corners(warped)
    assert how == "sheet"
    back = rectify(warped)
    assert structure()(_record_with(back), None).value > 0.85
    assert landmark_geometry()(_record_with(back), None).value > 0.85


def test_rectify_from_given_corners_in_observation_extra() -> None:
    ref = reference.reference_ink()
    sheet = compose_sheet_view(ref, margin_px=90)
    h, w = sheet.shape[:2]
    corners = [
        [90 / w, 90 / h],
        [(w - 90) / w, 90 / h],
        [(w - 90) / w, (h - 90) / h],
        [90 / w, (h - 90) / h],
    ]
    rec = _record_with(sheet, **{CORNERS_KEY: corners})
    assert structure()(rec, None).value > 0.95
    _, how = find_canvas_corners(sheet, corners)
    assert how == "given"


def test_rectify_fails_loudly_on_nothing() -> None:
    with pytest.raises(RectifyError):
        rectify(np.full((300, 300, 3), 40, dtype=np.uint8))


# --- end to end in the mock -----------------------------------------------------------


@pytest.mark.parametrize("build", [photo_v1, line_v0])
def test_end_to_end_mock_eval_orders_policies(tmp_path: Path, build) -> None:
    task = build(max_steps=3000, epochs=1)
    ref = task.metadata["reference"]
    (trace,) = ir_eval(
        task, TracePolicy(reference=ref), PlotterEmbodiment(reference=ref), log_dir=str(tmp_path)
    )
    (idle,) = ir_eval(task, IdlePolicy(), PlotterEmbodiment(reference=ref), log_dir=str(tmp_path))
    m_trace, m_idle = trace.results.metrics, idle.results.metrics
    assert m_trace["composite"] > 0.9 > m_idle["composite"]
    assert m_trace["landmark_geometry"] > 0.95
    assert m_idle["landmark_geometry"] == 0.0
    # the mock declares its medium, and the reference camera carried what the model should see
    assert (
        trace.results.scores[0].metadata["medium"] == "sim"
        if hasattr(trace.results, "scores")
        else True
    )


@pytest.mark.parametrize("mode", ["markers", "sheet"])
def test_photo_modes_go_through_rectification(tmp_path: Path, mode: str) -> None:
    task = line_v0(max_steps=3000, epochs=1)
    (log,) = ir_eval(task, TracePolicy(), PlotterEmbodiment(photo_mode=mode), log_dir=str(tmp_path))
    assert log.results.metrics["structure"] > 0.85


def test_store_frames_run_still_scores_from_disk(tmp_path: Path) -> None:
    task = line_v0(max_steps=3000, epochs=1)
    (log,) = ir_eval(
        task, TracePolicy(), PlotterEmbodiment(), log_dir=str(tmp_path), store_frames=True
    )
    assert log.results.metrics["structure"] > 0.95


def test_artifacts_are_written_when_env_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    art = tmp_path / "art"
    monkeypatch.setenv("SACPAINT_ARTIFACTS", str(art))
    task = line_v0(max_steps=3000, epochs=1)
    ir_eval(task, IdlePolicy(), PlotterEmbodiment(), log_dir=str(tmp_path / "logs"))
    jsons = list(art.glob("*.json"))
    assert len(jsons) == 1 and jsons[0].with_suffix(".png").exists()
    payload = json.loads(jsons[0].read_text())
    assert payload["composite"] == 0.0 and payload["reference"] == reference.LINE_REFERENCE
    assert payload["medium"] == "sim"


# --- new references become tasks ----------------------------------------------------------


def test_user_spec_registers_as_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SACPAINT_REFERENCES", str(tmp_path))
    reference.refresh()
    spec = reference.ReferenceSpec(
        name="box",
        canvas_mm=(200.0, 200.0),
        strokes={
            "box": [[(40.0, 40.0), (160.0, 40.0), (160.0, 160.0), (40.0, 160.0), (40.0, 40.0)]],
            "dot": [[(100.0, 100.0), (101.0, 100.0)]],
        },
        landmarks={"box": {"weight": 2}},
        relations=[{"kind": "above", "a": "dot", "b": "box"}],
    )
    spec.save(tmp_path / "box.spec.json")
    reference.refresh()
    assert "box" in reference.available()
    assert "sacpaint/box" in register_discovered()
    task = make_task("box", max_steps=500, epochs=1)
    assert task.name == "sacpaint/box"
    (log,) = ir_eval(
        task,
        TracePolicy(reference="box"),
        PlotterEmbodiment(reference="box"),
        log_dir=str(tmp_path / "logs"),
    )
    assert log.results.metrics["structure"] > 0.9
    reference.refresh()


# --- CLI ---------------------------------------------------------------------------------


def test_cli_score_photo_of_sheet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    photo = tmp_path / "photo.png"
    cv2.imwrite(
        str(photo),
        cv2.cvtColor(
            _perspective(compose_sheet_view(reference.reference_ink())), cv2.COLOR_RGB2BGR
        ),
    )
    assert cli.main(["score", str(photo)]) == 0
    out = capsys.readouterr().out
    assert "corners     sheet" in out
    result = json.loads((tmp_path / "photo-score.json").read_text())
    assert result["composite_photo"] > 0.85
    assert (tmp_path / "photo-overlay.png").exists()


def test_cli_new_then_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SACPAINT_REFERENCES", str(tmp_path))
    reference.refresh()
    assert cli.main(["new", "mytown", "--canvas", "210x297"]) == 0
    assert (tmp_path / "mytown.spec.json").exists() and (tmp_path / "mytown.png").exists()
    assert cli.main(["list"]) == 0
    assert "sacpaint/mytown" in capsys.readouterr().out
    reference.refresh()


def test_cli_export_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("SACPAINT_ARTIFACTS", str(logs / "sacpaint-artifacts"))
    ir_eval(
        line_v0(max_steps=3000, epochs=1), TracePolicy(), PlotterEmbodiment(), log_dir=str(logs)
    )
    out = tmp_path / "bundle"
    assert (
        cli.main(
            [
                "export",
                str(logs),
                "--out",
                str(out),
                "--label",
                "oracle",
                "--no-video",
                "--no-frames",
            ]
        )
        == 0
    )
    assert (out / "runs" / "oracle-1.md").exists() and (out / "runs" / "README.md").exists()
    assert (out / "reference.sha256").read_text().strip() == reference.reference_sha256()
    assert list((out / "canvases").glob("*.png"))
    page = (out / "runs" / "oracle-1.md").read_text()
    assert "composite" in page and "tower_bridge" in page


def test_run_command_builder_for_api_and_subscription(tmp_path: Path) -> None:
    import argparse

    args = argparse.Namespace(
        task="sacpaint/line-v0",
        policy="agent",
        embodiment="sacpaint_plotter",
        model="haiku",
        max_llm_calls=12,
        no_rerun=True,
        no_prompt=True,
        log_dir=str(tmp_path),
        extra=["-T", "epochs=1"],
    )
    cmd, env = cli.build_run_command(args, None)
    assert cmd[-2:] == ["-T", "epochs=1"] and "--store-frames" in cmd and "model=haiku" in cmd
    assert (
        env["SACPAINT_ARTIFACTS"].endswith("sacpaint-artifacts")
        and "SACPAINT_WIRE_LABEL" not in env
    )
    cmd, env = cli.build_run_command(args, 8931)
    assert "base_url=http://127.0.0.1:8931/v1" in cmd and "api_key_env=SACPAINT_SHIM_KEY" in cmd
    assert env["SACPAINT_WIRE_LABEL"] == "claude-code-cli" and "max_llm_calls=12" in cmd
