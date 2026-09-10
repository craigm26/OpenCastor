"""References: what the model sees, and the stroke rubric the scorers read.

A reference is a ``ReferenceSpec``: the canvas size, polylines grouped by
landmark name (canvas millimetres, origin bottom-left, x right, y up), the
landmark weights and boxes, the relations between landmarks, the scorer
tolerances, and optionally a **photograph**. Two images come out of it:

* ``reference_image(name)`` is what the model sees on the ``reference`` camera:
  the photograph when the spec has one, otherwise the rendered strokes;
* ``reference_ink(name)`` is what every scorer reads: the strokes rendered on
  the canonical canvas. Scoring is geometric, so the rubric is always ink.

The built-in reference is ``sacramento-photo-v1``: the original photograph of
Sacramento (Tower Bridge above the Capitol dome, the Mall between them) and a
stroke rubric traced over its landmarks. ``sacramento-line-v0``, the earlier
line-drawing reference, stays available as ``sacpaint/line-v0``. Any
``<name>.spec.json`` in the package assets or in ``$SACPAINT_REFERENCES``
(default ``~/.sacpaint/references``) becomes a task named ``sacpaint/<name>`` as
soon as the package is imported; ``sacpaint new`` writes one for you.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import cv2
import numpy as np

DEFAULT_REFERENCE = "sacramento-photo-v1"
LINE_REFERENCE = "sacramento-line-v0"
#: References defined in code. Their spec files on disk are copies for humans.
BUILTIN_REFERENCES: tuple[str, ...] = (DEFAULT_REFERENCE, LINE_REFERENCE)
#: The original photograph, byte-for-byte as supplied; its SHA-256 is the benchmark's identity.
PHOTO_FILE = f"{DEFAULT_REFERENCE}.webp"
# The physical sheet is 150 x 200 mm (fits A5 or half-letter with margins) so a
# desk arm like the SO-ARM101 (reach ~370 mm) can cover all of it. The design
# geometry below is written in a 300 x 400 unit frame and scaled; at 4 px/mm the
# canonical image is still 600 x 800 px, so the pinned PNG is unchanged.
CANVAS_MM: tuple[float, float] = (150.0, 200.0)  # width, height of the built-in canvas
PX_PER_MM = 4.0
DESIGN_MM: tuple[float, float] = (300.0, 400.0)  # frame the constants below are written in
_S = CANVAS_MM[0] / DESIGN_MM[0]
STROKE_PX = 3  # rendered line width in the reference
INK_THRESHOLD = 128  # gray below this counts as ink
INK_SUFFIX = ".ink.png"
REFERENCE_PNG = f"{LINE_REFERENCE}.png"  # the line reference's pinned render (also its identity)
RUBRIC_JSON = f"{DEFAULT_REFERENCE}.json"
SPEC_SUFFIX = ".spec.json"

Point = tuple[float, float]
Stroke = list[Point]


def user_reference_dir() -> Path:
    """Where ``sacpaint new`` writes specs and where extra references are discovered."""
    return Path(os.environ.get("SACPAINT_REFERENCES", "~/.sacpaint/references")).expanduser()


def _rect(x0: float, y0: float, x1: float, y1: float) -> Stroke:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int = 24) -> Stroke:
    return [
        (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
        for a in np.linspace(a0, a1, n)
    ]


@dataclass
class ReferenceSpec:
    """One benchmark reference. Distances in canvas millimetres; boxes ``[x0, y0, x1, y1]`` mm, y up."""

    name: str
    strokes: dict[str, list[Stroke]]
    canvas_mm: tuple[float, float] = CANVAS_MM
    landmarks: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # {"weight": n, "bbox_mm": [...]?}
    relations: list[dict[str, Any]] = field(default_factory=list)
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "landmark_geometry": 0.45,
            "structure": 0.30,
            "discipline": 0.15,
            "efficiency": 0.10,
        }
    )
    structure_tolerance_frac: float = 0.02
    landmark_tolerance_frac: float = 0.01
    discipline_band_frac: float = 0.025
    bbox_pad_mm: float = 6.0
    px_per_mm: float = PX_PER_MM
    stroke_px: int = STROKE_PX
    description: str = ""
    instruction: str | None = None
    #: File name of the photograph the model sees, relative to ``base_dir``; None = the strokes.
    photo: str | None = None
    photo_credit: str = ""
    #: Where ``photo`` resolves from (the spec file's directory). Not serialised.
    base_dir: str | None = field(default=None, repr=False, compare=False)

    # -- kind -----------------------------------------------------------------

    @property
    def kind(self) -> str:
        """``"photo"`` when the model sees a photograph, ``"line"`` when it sees the strokes."""
        return "photo" if self.photo else "line"

    def photo_path(self) -> Path | None:
        """Resolved path of the photograph, or None for a line reference."""
        if not self.photo:
            return None
        path = Path(self.photo).expanduser()
        if not path.is_absolute():
            path = Path(self.base_dir or user_reference_dir()) / path
        return path

    def photo_bytes(self) -> bytes:
        """The photograph's bytes, exactly as pinned."""
        path = self.photo_path()
        if path is None:
            raise ValueError(f"reference {self.name!r} has no photograph")
        return path.read_bytes()

    def photo_image(self) -> np.ndarray:
        """The photograph decoded as RGB uint8 at its native size."""
        arr = cv2.imdecode(np.frombuffer(self.photo_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise ValueError(f"cannot decode photograph {self.photo_path()}")
        return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)

    def reference_size(self) -> tuple[int, int]:
        """Size (width_px, height_px) of the image the model sees."""
        if self.photo:
            h, w = self.photo_image().shape[:2]
            return w, h
        return self.canonical_size()

    # -- geometry -----------------------------------------------------------

    def canonical_size(self) -> tuple[int, int]:
        """Canonical image size as (width_px, height_px)."""
        return round(self.canvas_mm[0] * self.px_per_mm), round(self.canvas_mm[1] * self.px_per_mm)

    def mm_to_px(self, p: Point) -> tuple[int, int]:
        """Canvas-mm point (x right, y up) to image pixel (col, row)."""
        w, h = self.canonical_size()
        col = round(p[0] * self.px_per_mm)
        row = round(h - p[1] * self.px_per_mm)
        return min(max(col, 0), w - 1), min(max(row, 0), h - 1)

    def render(self, strokes_by_name: dict[str, list[Stroke]] | None = None) -> np.ndarray:
        """Render polylines onto a white canonical canvas (RGB uint8)."""
        w, h = self.canonical_size()
        img = np.full((h, w, 3), 255, dtype=np.uint8)
        for group in (strokes_by_name or self.strokes).values():
            for stroke in group:
                pts = np.array([self.mm_to_px(p) for p in stroke], dtype=np.int32).reshape(-1, 1, 2)
                cv2.polylines(img, [pts], isClosed=False, color=(0, 0, 0), thickness=self.stroke_px)
        return img

    def png_bytes(self) -> bytes:
        """Deterministic PNG encoding of the rendered strokes (the ink the scorers read)."""
        ok, buf = cv2.imencode(
            ".png", cv2.cvtColor(self.render(), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 9]
        )
        if not ok:  # pragma: no cover - cv2 failure
            raise RuntimeError("PNG encoding failed")
        return buf.tobytes()

    def ink_sha256(self) -> str:
        """SHA-256 of the rendered stroke PNG: the identity of the scoring rubric's ink."""
        return hashlib.sha256(self.png_bytes()).hexdigest()

    def sha256(self) -> str:
        """Identity of the reference: SHA-256 of what the model sees (photo bytes, else the ink PNG)."""
        if self.photo:
            return hashlib.sha256(self.photo_bytes()).hexdigest()
        return self.ink_sha256()

    # -- rubric ---------------------------------------------------------------

    def _bbox_mm(self, name: str) -> list[float]:
        lm = self.landmarks.get(name, {})
        if "bbox_mm" in lm:
            return [float(v) for v in lm["bbox_mm"]]
        pts = [p for stroke in self.strokes[name] for p in stroke]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        pad = self.bbox_pad_mm
        return [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]

    def _norm_bbox(self, box: list[float]) -> list[float]:
        w, h = self.canvas_mm
        x0, y0, x1, y1 = box
        x0, x1 = max(x0, 0.0), min(x1, w)
        y0, y1 = max(y0, 0.0), min(y1, h)
        return [round(x0 / w, 4), round(1 - y1 / h, 4), round(x1 / w, 4), round(1 - y0 / h, 4)]

    def rubric(self) -> dict[str, Any]:
        """The scoring rubric the scorers read: normalized boxes, relations, weights, tolerances."""
        landmarks = {}
        for name in self.strokes:
            lm = self.landmarks.get(name, {})
            if lm.get("score", True) is False:
                continue
            landmarks[name] = {
                "bbox": self._norm_bbox(self._bbox_mm(name)),
                "weight": lm.get("weight", 1),
            }
        return {
            "version": self.name,
            "kind": self.kind,
            "canvas_mm": list(self.canvas_mm),
            "px_per_mm": self.px_per_mm,
            "ink_threshold": INK_THRESHOLD,
            "landmarks": landmarks,
            "relations": list(self.relations),
            "structure_tolerance_frac": self.structure_tolerance_frac,
            "landmark_tolerance_frac": self.landmark_tolerance_frac,
            "discipline_band_frac": self.discipline_band_frac,
            "weights": dict(self.weights),
        }

    # -- serialization ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-able form (the ``.spec.json`` file)."""
        d = asdict(self)
        d.pop("base_dir", None)
        d["canvas_mm"] = list(self.canvas_mm)
        d["strokes"] = {
            k: [[[float(x), float(y)] for x, y in s] for s in v] for k, v in self.strokes.items()
        }
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], base_dir: str | Path | None = None) -> ReferenceSpec:
        """Parse a ``.spec.json`` document, validating the parts the scorers depend on."""
        d = dict(d)
        d.pop("base_dir", None)
        if base_dir is not None:
            d["base_dir"] = str(base_dir)
        strokes = {
            k: [[(float(x), float(y)) for x, y in s] for s in v]
            for k, v in d.pop("strokes").items()
        }
        if not strokes:
            raise ValueError("a reference needs at least one stroke group")
        canvas = tuple(float(v) for v in d.pop("canvas_mm", CANVAS_MM))
        if len(canvas) != 2 or min(canvas) <= 0:
            raise ValueError(f"canvas_mm must be [width, height] > 0, got {canvas}")
        spec = cls(strokes=strokes, canvas_mm=canvas, **d)  # type: ignore[arg-type]
        for rel in spec.relations:
            for key in ("a", "b"):
                if rel.get(key) not in strokes:
                    raise ValueError(f"relation refers to unknown landmark {rel.get(key)!r}")
        return spec

    def save(self, path: Path) -> None:
        """Write the ``.spec.json`` file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")


# --- the built-in Sacramento reference -----------------------------------------
# The tower sits on the canopy line at y=205; the dome is in the foreground at the
# bottom; the mall converges from behind the dome up to the bridge deck.

HORIZON_Y = 300.0
CANOPY_Y = 200.0
TOWER = {
    "x0": 125.0,
    "x1": 175.0,
    "y0": 205.0,
    "y1": 290.0,
    "cap_x0": 120.0,
    "cap_x1": 180.0,
    "cap_y1": 298.0,
}
DOME = {"cx": 150.0, "cy": 60.0, "r": 70.0}
CUPOLA = {"x0": 135.0, "x1": 165.0, "y0": 130.0, "y1": 165.0, "ball_cy": 178.0, "ball_r": 8.0}
ROAD = {"left": ((112.0, 140.0), (140.0, 205.0)), "right": ((188.0, 140.0), (160.0, 205.0))}
LEFT_BUILDING = {"x0": 10.0, "x1": 70.0, "y0": 100.0, "y1": 260.0}
RIGHT_BUILDING = {"x0": 230.0, "x1": 290.0, "y0": 100.0, "y1": 250.0}


def sacramento_spec() -> ReferenceSpec:
    """The V0 Sacramento composition: Tower Bridge over the Capitol dome, joined by the Mall."""
    t = TOWER
    mid = (t["y0"] + t["y1"]) / 2
    tower = [
        _rect(t["x0"], t["y0"], t["x1"], t["y1"]),
        _rect(t["cap_x0"], t["y1"], t["cap_x1"], t["cap_y1"]),
        [(t["x0"], mid), (t["x1"], mid)],
        [(t["x0"], t["y0"]), (t["x1"], mid)],
        [(t["x1"], t["y0"]), (t["x0"], mid)],
        _rect(140.0, t["y0"], 160.0, 225.0),  # deck opening
    ]
    d, c = DOME, CUPOLA
    dome = [
        _arc(d["cx"], d["cy"], d["r"], 0.0, 180.0),
        [(d["cx"] - d["r"], d["cy"]), (d["cx"] + d["r"], d["cy"])],
    ]
    cupola = [
        _rect(c["x0"], c["y0"], c["x1"], c["y1"]),
        _arc(d["cx"], c["ball_cy"], c["ball_r"], 0.0, 360.0, n=20),
        [(d["cx"], c["y1"]), (d["cx"], c["ball_cy"] - c["ball_r"])],
    ]
    lb, rb = LEFT_BUILDING, RIGHT_BUILDING
    pad = 6.0
    design_strokes = {
        "horizon": [[(0.0, HORIZON_Y), (DESIGN_MM[0], HORIZON_Y)]],
        "tower_bridge": tower,
        "canopy": [
            [(lb["x1"] + 5, CANOPY_Y), (t["x0"], CANOPY_Y)],
            [(t["x1"], CANOPY_Y), (rb["x0"] - 5, CANOPY_Y)],
        ],
        "road": [list(ROAD["left"]), list(ROAD["right"])],
        "cupola": cupola,
        "capitol_dome": dome,
        "buildings": [
            _rect(lb["x0"], lb["y0"], lb["x1"], lb["y1"]),
            _rect(rb["x0"], rb["y0"], rb["x1"], rb["y1"]),
        ],
    }
    design_boxes = {
        "tower_bridge": {
            "weight": 3,
            "bbox_mm": [t["cap_x0"] - pad, t["y0"] - pad, t["cap_x1"] + pad, t["cap_y1"] + pad],
        },
        "capitol_dome": {
            "weight": 3,
            "bbox_mm": [
                d["cx"] - d["r"] - pad,
                d["cy"] - pad,
                d["cx"] + d["r"] + pad,
                d["cy"] + d["r"] + pad,
            ],
        },
        "cupola": {
            "weight": 1,
            "bbox_mm": [
                c["x0"] - pad,
                c["y0"] - pad,
                c["x1"] + pad,
                c["ball_cy"] + c["ball_r"] + pad,
            ],
        },
        "road": {"weight": 2, "bbox_mm": [106.0, 134.0, 194.0, 211.0]},
        "buildings": {
            "weight": 1,
            "bbox_mm": [lb["x0"] - pad, lb["y0"] - pad, rb["x1"] + pad, lb["y1"] + pad],
        },
        "horizon": {"weight": 1, "bbox_mm": [0.0, HORIZON_Y - pad, DESIGN_MM[0], HORIZON_Y + pad]},
    }
    return ReferenceSpec(
        name=LINE_REFERENCE,
        description="Sacramento: Tower Bridge above the Capitol dome and cupola, the Capitol Mall between them, two building masses, a horizon.",
        strokes={
            k: [[(x * _S, y * _S) for x, y in st] for st in v] for k, v in design_strokes.items()
        },
        landmarks={
            **{
                k: {"weight": v["weight"], "bbox_mm": [b * _S for b in v["bbox_mm"]]}
                for k, v in design_boxes.items()
            },
            "canopy": {"score": False},
        },
        bbox_pad_mm=pad * _S,
        relations=[
            {"kind": "same_x", "a": "tower_bridge", "b": "capitol_dome", "tolerance": 0.05},
            {"kind": "above", "a": "tower_bridge", "b": "capitol_dome"},
            {"kind": "above", "a": "cupola", "b": "capitol_dome"},
            {"kind": "above", "a": "horizon", "b": "tower_bridge"},
        ],
    )


# --- the built-in photograph reference ---------------------------------------------
# Landmarks measured on the photograph (1499 x 2000 px, the sheet's 3:4), in canvas mm:
# x = px / 10, y = (2000 - py) / 10. The rubric is the skeleton a line drawing of this
# photo shares with it: the bridge tower, the Capitol cupola and dome, the Mall, the
# main building masses, the horizon. Foliage, cars, and windows are deliberately absent.

P_HORIZON_Y = 151.0
# The photograph's central axis: tower, cupola and dome all sit on it (within the 1 mm the
# photo can be read to), so the rubric puts them exactly there and the same_x relations hold.
P_AXIS = 76.5
P_TOWER = {
    "x0": 69.0,
    "x1": 84.0,
    "y0": 97.0,
    "y1": 133.0,
    "cap_x0": 68.0,
    "cap_x1": 85.0,
    "cap_y1": 136.0,
    "mid": 116.0,
    "brace": 106.5,
}
P_DECK = {"x0": 63.0, "x1": 90.0, "y": 94.0}
P_DOME = {"cx": P_AXIS, "top": 34.0, "half_width_at_bottom": 52.0}
P_CUPOLA = {
    "skirt": (58.5, 94.5, 36.0),
    "drum": (61.5, 37.0, 91.5, 49.0),
    "colonnade": (63.5, 49.0, 89.5, 64.0),
    "columns": (68.5, 73.5, 79.5, 84.5),
    "gold_cx": P_AXIS,
    "gold_cy": 67.0,
    "gold_r": 8.0,
    "neck_top": 80.0,
    "ball_cy": 82.5,
    "ball_r": 2.5,
}
P_ROAD = {"left": ((57.5, 40.0), (66.5, 92.0)), "right": ((95.5, 40.0), (86.5, 92.0))}
P_BUILDINGS = {
    "left_tower": (0.0, 90.0, 19.0, 199.5),
    "embassy": (24.0, 92.0, 40.0, 113.0),
    "left_mid": (5.0, 50.0, 25.0, 83.0),
    "left_low": (0.0, 8.0, 22.0, 47.0),
    "round_top": (93.0, 88.0, 122.0, 117.0),
    "visit_california": (118.0, 50.0, 150.0, 121.0),
    "right_low": (127.0, 20.0, 150.0, 45.0),
}


def sacramento_photo_spec() -> ReferenceSpec:
    """The V1 Sacramento reference: the original photograph, scored against a traced landmark skeleton."""
    t = P_TOWER
    portal_cx, portal_r = (t["x0"] + t["x1"]) / 2, 4.5
    tower = [
        _rect(t["x0"], t["y0"], t["x1"], t["y1"]),
        _rect(t["cap_x0"], t["y1"], t["cap_x1"], t["cap_y1"]),
        [(t["x0"], t["mid"]), (t["x1"], t["mid"])],
        # two levels of cross bracing below the window block, as on the real tower
        [(t["x0"], t["y0"]), (t["x1"], t["brace"])],
        [(t["x1"], t["y0"]), (t["x0"], t["brace"])],
        [(t["x0"], t["brace"]), (t["x1"], t["brace"])],
        [(t["x0"], t["brace"]), (t["x1"], t["mid"])],
        [(t["x1"], t["brace"]), (t["x0"], t["mid"])],
        _rect(71.0, 119.0, 82.0, 130.0),  # the tall window block
        [(P_DECK["x0"], P_DECK["y"]), (P_DECK["x1"], P_DECK["y"])],
        _arc(portal_cx, 92.0, portal_r, 0.0, 180.0) + [(portal_cx - portal_r, 90.0)],
        [(portal_cx + portal_r, 92.0), (portal_cx + portal_r, 90.0)],
    ]
    d = P_DOME
    hw, top = d["half_width_at_bottom"], d["top"]
    r = (hw * hw + top * top) / (2 * top)
    cy = top - r
    a0 = math.degrees(math.asin(-cy / r))
    dome = [_arc(d["cx"], cy, r, a0, 180.0 - a0, n=40)]
    for x_bottom in (
        P_AXIS - 32.0,
        P_AXIS - 16.0,
        P_AXIS + 16.0,
        P_AXIS + 32.0,
    ):  # four roof ribs, apex to frame edge
        dome.append([(d["cx"] + 0.25 * (x_bottom - d["cx"]), top - 1.0), (x_bottom, 0.0)])
    c = P_CUPOLA
    sx0, sx1, sy = c["skirt"]
    cupola = [
        [(sx0, sy), (sx1, sy)],
        _rect(*c["drum"]),
        _rect(*c["colonnade"]),
        *[[(x, c["colonnade"][1] + 1.0), (x, c["colonnade"][3] - 1.0)] for x in c["columns"]],
        _arc(c["gold_cx"], c["gold_cy"], c["gold_r"], 0.0, 180.0),
        [(c["gold_cx"], c["gold_cy"] + c["gold_r"]), (c["gold_cx"], c["neck_top"])],
        _arc(c["gold_cx"], c["ball_cy"], c["ball_r"], 0.0, 360.0, n=20),
    ]
    strokes = {
        "ridge": [[(19.0, 160.0), (40.0, 162.0), (80.0, 161.0), (120.0, 163.0), (150.0, 161.0)]],
        "horizon": [[(19.0, P_HORIZON_Y), (150.0, P_HORIZON_Y)]],
        "tower_bridge": tower,
        "road": [list(P_ROAD["left"]), list(P_ROAD["right"])],
        "cupola": cupola,
        "capitol_dome": dome,
        "buildings": [_rect(*box) for box in P_BUILDINGS.values()],
    }
    pad = 3.0
    return ReferenceSpec(
        name=DEFAULT_REFERENCE,
        description=(
            "Sacramento, photographed from above the Capitol: the Tower Bridge at the end of the "
            "Capitol Mall, the Capitol cupola and dome in the foreground, office towers either side, "
            "the valley and mountains on the horizon."
        ),
        photo=PHOTO_FILE,
        photo_credit="open-source photograph; no attribution required",
        base_dir=str(_assets_dir()),
        strokes=strokes,
        landmarks={
            # Boxes stop short of neighbouring strokes (the round-top building's edges at x=93, y=88), so
            # a landmark's ink centroid is its own and the same_x relations hold exactly on the reference.
            "tower_bridge": {
                "weight": 3,
                "bbox_mm": [P_DECK["x0"] - pad, 90.0 - pad, 92.0, t["cap_y1"] + pad],
            },
            # the dome's box stops 2 mm short of the corner buildings so their edges cannot pull its centroid
            "capitol_dome": {
                "weight": 3,
                "bbox_mm": [d["cx"] - hw + 2.0, 0.0, d["cx"] + hw - 2.0, top + pad],
            },
            "cupola": {"weight": 2, "bbox_mm": [sx0 - pad, sy - pad, sx1 + pad, 87.0]},
            "road": {"weight": 2, "bbox_mm": [55.0, 37.0, 98.0, 95.0]},
            "buildings": {"weight": 1},
            "horizon": {
                "weight": 1,
                "bbox_mm": [19.0, P_HORIZON_Y - pad, 150.0, P_HORIZON_Y + pad],
            },
            "ridge": {"score": False},
        },
        bbox_pad_mm=pad,
        relations=[
            {"kind": "same_x", "a": "tower_bridge", "b": "capitol_dome", "tolerance": 0.05},
            {"kind": "same_x", "a": "cupola", "b": "capitol_dome", "tolerance": 0.05},
            {"kind": "above", "a": "tower_bridge", "b": "cupola"},
            {"kind": "above", "a": "cupola", "b": "capitol_dome"},
            {"kind": "above", "a": "horizon", "b": "tower_bridge"},
        ],
    )


# --- discovery -------------------------------------------------------------------


def _assets_dir() -> Path:
    return Path(str(resources.files("castor.bench.sacpaint").joinpath("assets")))


def spec_search_dirs() -> list[Path]:
    """Directories scanned for ``*.spec.json``, package assets first."""
    return [_assets_dir(), user_reference_dir()]


@lru_cache(maxsize=1)
def _discover() -> dict[str, ReferenceSpec]:
    specs: dict[str, ReferenceSpec] = {
        DEFAULT_REFERENCE: sacramento_photo_spec(),
        LINE_REFERENCE: sacramento_spec(),
    }
    for d in spec_search_dirs():
        if not d.is_dir():
            continue
        for path in sorted(d.glob(f"*{SPEC_SUFFIX}")):
            name = path.name[: -len(SPEC_SUFFIX)]
            if name in BUILTIN_REFERENCES:
                continue  # the built-ins are defined in code; the files are copies for humans
            try:
                spec = ReferenceSpec.from_dict(json.loads(path.read_text()), base_dir=path.parent)
            except Exception as exc:  # noqa: BLE001 - a broken user spec must not hide the others
                import warnings

                warnings.warn(
                    f"ignoring reference spec {path}: {exc}", RuntimeWarning, stacklevel=2
                )
                continue
            spec.name = name
            specs[name] = spec
    return specs


def refresh() -> None:
    """Forget discovered specs (after ``sacpaint new`` writes one in-process)."""
    _discover.cache_clear()
    _image_cache.clear()


def available() -> list[str]:
    """Names of every discoverable reference, built-in first."""
    return list(_discover())


def get_spec(name: str = DEFAULT_REFERENCE) -> ReferenceSpec:
    """Look a reference up by name (``.png`` / ``.spec.json`` suffixes tolerated)."""
    for suffix in (".png", SPEC_SUFFIX, ".json"):
        name = name.removesuffix(suffix)
    try:
        return _discover()[name]
    except KeyError:
        raise KeyError(f"unknown reference {name!r}; known: {available()}") from None


_image_cache: dict[str, np.ndarray] = {}


def _pinned_ink_file(name: str) -> str | None:
    """The checked-in ink PNG for a built-in, so the scorers read the pinned bytes, not a re-render."""
    if name == LINE_REFERENCE:
        return REFERENCE_PNG
    if name == DEFAULT_REFERENCE:
        return f"{DEFAULT_REFERENCE}{INK_SUFFIX}"
    return None


def _decode_rgb(data: bytes) -> np.ndarray:
    arr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)


def reference_ink(name: str = DEFAULT_REFERENCE) -> np.ndarray:
    """The stroke rubric rendered on the canonical canvas, RGB uint8: what every scorer reads."""
    spec = get_spec(name)
    key = f"ink:{spec.name}"
    if key not in _image_cache:
        pinned = _pinned_ink_file(spec.name)
        if pinned:
            _image_cache[key] = _decode_rgb(
                resources.files("castor.bench.sacpaint").joinpath("assets", pinned).read_bytes()
            )
        else:
            _image_cache[key] = spec.render()
    return _image_cache[key].copy()


def reference_image(name: str = DEFAULT_REFERENCE) -> np.ndarray:
    """What the model sees on the ``reference`` camera: the photograph if the spec has one, else the ink."""
    spec = get_spec(name)
    if not spec.photo:
        return reference_ink(spec.name)
    key = f"photo:{spec.name}"
    if key not in _image_cache:
        _image_cache[key] = spec.photo_image()
    return _image_cache[key].copy()


def reference_kind(name: str = DEFAULT_REFERENCE) -> str:
    """``"photo"`` or ``"line"``: what kind of image the model is shown."""
    return get_spec(name).kind


def reference_sha256(name: str = DEFAULT_REFERENCE) -> str:
    """SHA-256 of what the model sees (photo bytes, else the pinned ink PNG): the benchmark's identity."""
    spec = get_spec(name)
    if spec.photo:
        return spec.sha256()
    pinned = _pinned_ink_file(spec.name)
    if pinned:
        return hashlib.sha256(
            resources.files("castor.bench.sacpaint").joinpath("assets", pinned).read_bytes()
        ).hexdigest()
    return spec.sha256()


def ink_sha256(name: str = DEFAULT_REFERENCE) -> str:
    """SHA-256 of the stroke rubric's rendered PNG (pinned bytes for the built-ins)."""
    spec = get_spec(name)
    pinned = _pinned_ink_file(spec.name)
    if pinned:
        return hashlib.sha256(
            resources.files("castor.bench.sacpaint").joinpath("assets", pinned).read_bytes()
        ).hexdigest()
    return spec.ink_sha256()


def load_rubric(name: str = DEFAULT_REFERENCE) -> dict[str, Any]:
    """The rubric for a reference (generated from its spec)."""
    return get_spec(name).rubric()


def strokes(name: str = DEFAULT_REFERENCE) -> dict[str, list[Stroke]]:
    """The reference polylines grouped by landmark (canvas mm)."""
    return get_spec(name).strokes


def canonical_size(name: str = DEFAULT_REFERENCE) -> tuple[int, int]:
    """Canonical image size (width_px, height_px) of a reference."""
    return get_spec(name).canonical_size()


def mm_to_px(p: Point, name: str = DEFAULT_REFERENCE) -> tuple[int, int]:
    """Canvas-mm point to image pixel for a reference's canvas."""
    return get_spec(name).mm_to_px(p)


def render(
    strokes_by_name: dict[str, list[Stroke]] | None = None, name: str = DEFAULT_REFERENCE
) -> np.ndarray:
    """Render polylines (default: the reference's own) on that reference's canvas."""
    return get_spec(name).render(strokes_by_name)


def write_assets(out_dir: str) -> None:
    """Regenerate the built-in renders, rubrics, and spec copies. Maintainers only; a change re-versions a task.

    The photograph itself is never written here: it is the original file, pinned by hash.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    line = sacramento_spec()
    (out / REFERENCE_PNG).write_bytes(line.png_bytes())
    (out / f"{LINE_REFERENCE}.json").write_text(
        json.dumps(line.rubric(), indent=2, sort_keys=True) + "\n"
    )
    line.save(out / f"{LINE_REFERENCE}{SPEC_SUFFIX}")
    photo = sacramento_photo_spec()
    (out / f"{DEFAULT_REFERENCE}{INK_SUFFIX}").write_bytes(photo.png_bytes())
    (out / RUBRIC_JSON).write_text(json.dumps(photo.rubric(), indent=2, sort_keys=True) + "\n")
    photo.save(out / f"{DEFAULT_REFERENCE}{SPEC_SUFFIX}")


if __name__ == "__main__":  # pragma: no cover
    import sys

    write_assets(sys.argv[1] if len(sys.argv) > 1 else "src/sacpaint/assets")
