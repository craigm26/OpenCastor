"""Scorers: pure readers of a TrialRecord that grade the final canvas.

All scorers read the same final canvas (the parked observation's ``overhead``
frame when the embodiment offers one, else the last step's), rectify it unless
the embodiment marked it canonical, binarize ink, and compare against the
reference named in the scene's target (default ``sacramento-photo-v1``).
Nothing here calls a model; every number is reproducible offline from the
final frame. If ``SACPAINT_ARTIFACTS`` names a directory, the composite scorer
also writes the rectified final canvas and the full score breakdown there, so
``sacpaint export`` can publish them.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from inspect_robots.rollout import TrialRecord
from inspect_robots.scene import Target
from inspect_robots.scorer import Score

from castor.bench.sacpaint.reference import (
    DEFAULT_REFERENCE,
    INK_THRESHOLD,
    ReferenceSpec,
    get_spec,
    reference_ink,
)

OVERHEAD = "overhead"
CANONICAL_FLAG = "canonical_canvas"
CORNERS_KEY = "canvas_corners"
#: ``observation.extra["medium"]``: what the marks are made of; each medium is its own leaderboard category. ``pen`` is paper;
#: ``virtual`` is ink synthesised from arm telemetry (no paper); ``sim`` is the mock world.
MEDIUM_KEY = "medium"
DEFAULT_MEDIUM = "pen"
ARTIFACTS_ENV = "SACPAINT_ARTIFACTS"
WIRE_LABEL_ENV = "SACPAINT_WIRE_LABEL"  # e.g. "claude-code-cli" for subscription-shim runs


def spec_for(target: Target | None) -> ReferenceSpec:
    """The reference a scene scores against: ``target.spec["reference"]`` or the default."""
    name = DEFAULT_REFERENCE
    if target is not None and target.spec.get("reference"):
        name = str(target.spec["reference"])
    return get_spec(name)


# --- final frame -------------------------------------------------------------


def final_observation(record: TrialRecord):
    """The observation whose ``overhead`` frame is graded, or None.

    Prefers the parked observation, then the last step's in-memory frame, then
    the last frame a ``--store-frames`` run wrote to disk (the rollout strips
    images from step records in that mode and leaves ``FrameRef`` handles).
    """
    obs = record.parked_observation
    if obs is not None and OVERHEAD in obs.images:
        return obs
    for step in reversed(record.steps):
        for candidate, refs in (
            (step.result.observation, step.result_image_refs),
            (step.observation, step.image_refs),
        ):
            if OVERHEAD in candidate.images:
                return candidate
            if refs and OVERHEAD in refs:
                from inspect_robots.types import Observation

                return Observation(
                    images={OVERHEAD: refs[OVERHEAD].load()},
                    state=candidate.state,
                    instruction=candidate.instruction,
                    extra=candidate.extra,
                )
    return None


def final_canvas(record: TrialRecord, spec: ReferenceSpec | None = None) -> np.ndarray | None:
    """The canonical RGB canvas at the end of the trial, or None if no frame exists."""
    spec = spec or get_spec()
    obs = final_observation(record)
    if obs is None:
        return None
    frame = np.asarray(obs.images[OVERHEAD])
    w, h = spec.canonical_size()
    if obs.extra.get(CANONICAL_FLAG):
        if frame.shape[:2] != (h, w):
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        return frame
    from castor.bench.sacpaint.rectify import rectify

    return rectify(frame, corners=obs.extra.get(CORNERS_KEY), size=(w, h))


#: Pixels this close to the canvas edge are never ink. A rectified photograph of a sheet
#: carries the sheet's own edge, its shadow, and the warp's border there; a rubric whose
#: landmarks reach the frame (the photo's dome is cut by it) must not read those as marks.
EDGE_MARGIN_FRAC = 0.01  # of the shorter side: 1.5 mm on the 150 x 200 mm sheet


def ink_mask(
    rgb: np.ndarray, threshold: int = INK_THRESHOLD, edge_margin_frac: float = EDGE_MARGIN_FRAC
) -> np.ndarray:
    """Boolean mask of ink pixels (dark on white), blank within the edge margin."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mask = gray < threshold
    m = round(min(gray.shape[:2]) * edge_margin_frac)
    if m > 0:
        mask[:m, :] = False
        mask[-m:, :] = False
        mask[:, :m] = False
        mask[:, -m:] = False
    return mask


def _bbox_px(bbox: list[float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    w, h = size
    left, top, right, bottom = bbox
    return int(left * w), int(top * h), int(right * w), int(bottom * h)


def _centroid(mask: np.ndarray) -> tuple[float, float] | None:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return float(xs.mean()), float(ys.mean())


# --- landmark geometry --------------------------------------------------------


def _aligned_presence(
    can: np.ndarray, rf: np.ndarray, box: tuple[int, int, int, int], tol: float
) -> tuple[float, float, float, tuple[float, float] | None, tuple[float, float] | None]:
    """Presence of one landmark: precision x recall of ink inside its box after centroid alignment.

    Aligning the drawn ink to the reference ink by their centroids first means a
    landmark drawn in the wrong place still counts as present (the position
    term charges for the offset separately), while random ink inside the box,
    which no translation can make line up, does not.
    """
    x0, y0, x1, y1 = box
    pad = int(np.ceil(tol)) + 1
    ys0, ys1 = max(y0 - pad, 0), min(y1 + pad, rf.shape[0])
    xs0, xs1 = max(x0 - pad, 0), min(x1 + pad, rf.shape[1])
    r_crop, c_crop = rf[ys0:ys1, xs0:xs1], can[ys0:ys1, xs0:xs1]
    r_box, c_box = rf[y0:y1, x0:x1], can[y0:y1, x0:x1]
    c_ref, c_can = _centroid(r_box), _centroid(c_box)
    if c_ref is None or c_can is None:
        return 0.0, 0.0, 0.0, c_ref, c_can
    dx, dy = round(c_ref[0] - c_can[0]), round(c_ref[1] - c_can[1])
    shifted = np.zeros_like(c_crop)
    h, w = c_crop.shape
    src_y = slice(max(0, -dy), min(h, h - dy))
    src_x = slice(max(0, -dx), min(w, w - dx))
    dst_y = slice(max(0, dy), min(h, h + dy))
    dst_x = slice(max(0, dx), min(w, w + dx))
    shifted[dst_y, dst_x] = c_crop[src_y, src_x]
    # Only judge ink that lands inside the landmark box after alignment.
    inner = np.zeros_like(shifted)
    inner[y0 - ys0 : y1 - ys0, x0 - xs0 : x1 - xs0] = True
    shifted &= inner
    if not shifted.any():
        return 0.0, 0.0, 0.0, c_ref, c_can
    d_to_ref = cv2.distanceTransform((~r_crop).astype(np.uint8), cv2.DIST_L2, 3)
    d_to_can = cv2.distanceTransform((~shifted).astype(np.uint8), cv2.DIST_L2, 3)
    precision = float((d_to_ref[shifted] <= tol).mean())
    recall = float((d_to_can[r_crop & inner] <= tol).mean())
    return precision * recall, precision, recall, c_ref, c_can


def landmark_details(canvas: np.ndarray, rubric: dict[str, Any], ref: np.ndarray) -> dict[str, Any]:
    """Per-landmark presence/position terms and relation checks (the explanation payload)."""
    can, rf = ink_mask(canvas), ink_mask(ref)
    h, w = rf.shape[:2]
    size = (w, h)
    tol = rubric.get("landmark_tolerance_frac", 0.01) * float(np.hypot(w, h))
    out: dict[str, Any] = {"landmarks": {}, "relations": {}}
    centroids: dict[str, tuple[float, float] | None] = {}
    presences: dict[str, float] = {}
    total_w = 0.0
    acc = 0.0
    for name, lm in rubric["landmarks"].items():
        x0, y0, x1, y1 = _bbox_px(lm["bbox"], size)
        r_ink = int(rf[y0:y1, x0:x1].sum())
        c_ink = int(can[y0:y1, x0:x1].sum())
        if r_ink == 0:
            continue
        presence, precision, recall, c_ref, c_can = _aligned_presence(
            can, rf, (x0, y0, x1, y1), tol
        )
        if c_ink > 3 * r_ink:  # scribbling the box full is not drawing the landmark
            presence *= (3 * r_ink) / c_ink
        diag = float(np.hypot(x1 - x0, y1 - y0))
        if c_ref is None or c_can is None:
            position = 0.0
        else:
            d = float(np.hypot(c_ref[0] - c_can[0], c_ref[1] - c_can[1])) / diag
            position = max(0.0, 1.0 - d / 0.5)
        score = presence * (0.5 + 0.5 * position)
        centroids[name] = None if c_can is None else (c_can[0] + x0, c_can[1] + y0)
        presences[name] = presence
        out["landmarks"][name] = {
            "presence": round(presence, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "position": round(position, 4),
            "score": round(score, 4),
            "ink_px": c_ink,
            "reference_ink_px": r_ink,
        }
        acc += lm.get("weight", 1) * score
        total_w += lm.get("weight", 1)
    landmark_term = acc / total_w if total_w else 0.0

    rel_scores = []
    for rel in rubric.get("relations", []):
        a, b = centroids.get(rel["a"]), centroids.get(rel["b"])
        if a is None or b is None:
            val = 0.0
        elif rel["kind"] == "same_x":
            tol_x = rel.get("tolerance", 0.05) * w
            val = max(0.0, 1.0 - abs(a[0] - b[0]) / (2 * tol_x))
        elif rel["kind"] == "above":  # image rows grow downward
            val = 1.0 if a[1] < b[1] else 0.0
        elif rel["kind"] == "left_of":
            val = 1.0 if a[0] < b[0] else 0.0
        else:
            val = 0.0
        # A relation between two things that are not really there is not evidence.
        val *= min(presences.get(rel["a"], 0.0), presences.get(rel["b"], 0.0))
        out["relations"][f"{rel['kind']}({rel['a']},{rel['b']})"] = round(val, 4)
        rel_scores.append(val)
    relation_term = float(np.mean(rel_scores)) if rel_scores else 1.0
    out["landmark_term"] = round(landmark_term, 4)
    out["relation_term"] = round(relation_term, 4)
    out["value"] = round(0.8 * landmark_term + 0.2 * relation_term, 4)
    return out


def structure_pr(canvas: np.ndarray, ref: np.ndarray, tolerance_frac: float) -> dict[str, float]:
    """Chamfer-style precision x recall of ink within a distance tolerance."""
    can, rf = ink_mask(canvas), ink_mask(ref)
    h, w = rf.shape[:2]
    tol = tolerance_frac * float(np.hypot(w, h))
    if not can.any() or not rf.any():
        return {"precision": 0.0, "recall": 0.0, "value": 0.0, "tolerance_px": tol}
    # distanceTransform measures distance to the nearest zero pixel: invert masks.
    d_to_ref = cv2.distanceTransform((~rf).astype(np.uint8), cv2.DIST_L2, 3)
    d_to_can = cv2.distanceTransform((~can).astype(np.uint8), cv2.DIST_L2, 3)
    precision = float((d_to_ref[can] <= tol).mean())
    recall = float((d_to_can[rf] <= tol).mean())
    # Product, not F1: a dense scribble recalls every reference pixel, and F1
    # would let that perfect recall hide the poor precision.
    return {
        "precision": precision,
        "recall": recall,
        "value": precision * recall,
        "tolerance_px": tol,
    }


def discipline_details(canvas: np.ndarray, ref: np.ndarray, band_frac: float) -> dict[str, Any]:
    """Stray-ink fraction and over-inking, as the discipline scorer sees them."""
    can, rf = ink_mask(canvas), ink_mask(ref)
    total = int(can.sum())
    if total == 0:
        return {"value": 0.0, "ink_px": 0, "stray_px": 0, "ink_ratio": 0.0, "note": "no ink"}
    h, w = rf.shape[:2]
    band = max(1, int(band_frac * float(np.hypot(w, h))))
    near = cv2.dilate(rf.astype(np.uint8), np.ones((band, band), np.uint8)).astype(bool)
    stray = int((can & ~near).sum())
    value = 1.0 - stray / total
    ratio = total / max(int(rf.sum()), 1)
    if ratio > 4.0:  # far more ink than the drawing needs
        value *= 4.0 / ratio
    return {"value": value, "ink_px": total, "stray_px": stray, "ink_ratio": round(ratio, 3)}


def score_canvas(canvas: np.ndarray, spec: ReferenceSpec) -> dict[str, Any]:
    """Every image-only score for a canonical canvas (no trial needed). Used by ``sacpaint score``."""
    ref = reference_ink(spec.name)
    rubric = spec.rubric()
    lm = landmark_details(canvas, rubric, ref)
    st = structure_pr(canvas, ref, rubric["structure_tolerance_frac"])
    di = discipline_details(canvas, ref, rubric["discipline_band_frac"])
    weights = rubric["weights"]
    image_weights = {k: v for k, v in weights.items() if k != "efficiency"}
    total = sum(image_weights.values())
    parts = {"landmark_geometry": lm["value"], "structure": st["value"], "discipline": di["value"]}
    composite_photo = sum(image_weights[k] * parts[k] for k in parts) / total if total else 0.0
    return {
        "reference": spec.name,
        "composite_photo": round(composite_photo, 4),
        "parts": {k: round(v, 4) for k, v in parts.items()},
        "landmark_geometry": lm,
        "structure": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in st.items()},
        "discipline": di,
        "weights": weights,
    }


# --- scorer objects ------------------------------------------------------------


def _no_frame() -> Score:
    return Score(value=0.0, explanation="no final canvas frame")


@dataclass(frozen=True)
class _LandmarkGeometry:
    name: str = "landmark_geometry"

    def __call__(self, record: TrialRecord, target: Target | None) -> Score:
        spec = spec_for(target)
        canvas = final_canvas(record, spec)
        if canvas is None:
            return _no_frame()
        details = landmark_details(canvas, spec.rubric(), reference_ink(spec.name))
        return Score(
            value=details["value"],
            explanation="landmark presence x position, plus relations",
            metadata=details,
        )


def landmark_geometry() -> _LandmarkGeometry:
    """Each landmark present, in place, and in the right relation to the others."""
    return _LandmarkGeometry()


@dataclass(frozen=True)
class _Structure:
    name: str = "structure"

    def __call__(self, record: TrialRecord, target: Target | None) -> Score:
        spec = spec_for(target)
        canvas = final_canvas(record, spec)
        if canvas is None:
            return _no_frame()
        r = structure_pr(canvas, reference_ink(spec.name), spec.structure_tolerance_frac)
        return Score(
            value=r["value"],
            explanation="precision x recall of ink within tolerance of reference ink",
            metadata=r,
        )


def structure() -> _Structure:
    """Edge-level agreement: how much drawn ink is near reference ink, and vice versa."""
    return _Structure()


@dataclass(frozen=True)
class _Discipline:
    name: str = "discipline"

    def __call__(self, record: TrialRecord, target: Target | None) -> Score:
        spec = spec_for(target)
        canvas = final_canvas(record, spec)
        if canvas is None:
            return _no_frame()
        d = discipline_details(canvas, reference_ink(spec.name), spec.discipline_band_frac)
        return Score(
            value=d["value"],
            explanation="1 - stray ink fraction, scaled down when total ink exceeds 4x reference; 0 for a blank canvas",
            metadata=d,
        )


def discipline() -> _Discipline:
    """Penalize ink far from anything in the reference, scribbling, and drawing nothing."""
    return _Discipline()


@dataclass(frozen=True)
class _Efficiency:
    max_steps: int
    name: str = "efficiency"

    def __call__(self, record: TrialRecord, target: Target | None) -> Score:
        n = len(record.steps)
        if record.termination_reason == "max_steps" or n >= self.max_steps:
            return Score(value=0.0, explanation="ran out the horizon", metadata={"steps": n})
        return Score(
            value=max(0.0, 1.0 - n / self.max_steps),
            explanation="1 - steps/max_steps",
            metadata={"steps": n},
        )


def efficiency(max_steps: int) -> _Efficiency:
    """Fewer steps to a declared finish is better; running out the horizon scores zero."""
    return _Efficiency(max_steps=max_steps)


def _write_artifacts(
    record: TrialRecord, canvas: np.ndarray | None, payload: dict[str, Any]
) -> str | None:
    out_dir = os.environ.get(ARTIFACTS_ENV)
    if not out_dir:
        return None
    try:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = f"{time.strftime('%Y%m%d_%H%M%S')}_{record.scene_id}-e{record.epoch}"
        if canvas is not None:
            cv2.imwrite(str(out / f"{stem}.png"), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
        (out / f"{stem}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str)
        )
        return str(out / f"{stem}.json")
    except OSError:
        return None


@dataclass(frozen=True)
class _Composite:
    max_steps: int
    name: str = "composite"

    def __call__(self, record: TrialRecord, target: Target | None) -> Score:
        spec = spec_for(target)
        weights = spec.weights
        canvas = final_canvas(record, spec)
        obs = final_observation(record)
        medium = str(((getattr(obs, "extra", None) or {}).get(MEDIUM_KEY)) or DEFAULT_MEDIUM)
        if canvas is None:
            details: dict[str, Any] = {"error": "no final canvas frame"}
            parts = {k: 0.0 for k in weights}
        else:
            details = score_canvas(canvas, spec)
            parts = dict(details["parts"])
            eff = float(efficiency(self.max_steps)(record, target).value)
            # Finishing fast only counts when something was drawn: scale by structure.
            parts["efficiency"] = eff * parts["structure"]
        value = sum(weights[k] * float(parts.get(k, 0.0)) for k in weights)
        payload = {
            "scene_id": record.scene_id,
            "epoch": record.epoch,
            "steps": len(record.steps),
            "termination_reason": record.termination_reason,
            "composite": value,
            "parts": parts,
            "details": details,
            "reference": spec.name,
            "medium": medium,
            "wire": os.environ.get(WIRE_LABEL_ENV, "api"),
        }
        artifact = _write_artifacts(record, canvas, payload)
        meta: dict[str, Any] = {
            "parts": parts,
            "weights": weights,
            "details": details,
            "medium": medium,
        }
        if artifact:
            meta["artifact"] = artifact
        return Score(
            value=value,
            explanation="weighted sum per rubric (efficiency scaled by structure)",
            metadata=meta,
        )


def composite(max_steps: int) -> _Composite:
    """The leaderboard number: the rubric-weighted sum of the four component scores."""
    return _Composite(max_steps=max_steps)
