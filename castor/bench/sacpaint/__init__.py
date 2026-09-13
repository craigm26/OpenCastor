"""Sacramento PaintBench: an Inspect Robots benchmark.

The task is one sentence: draw the fixed reference image on the canvas, using
the camera to inspect and correct, and stop when done. The built-in reference
is the original photograph of Sacramento (Tower Bridge above the Capitol dome,
joined by the Capitol Mall); any ``.spec.json`` of strokes, with or without a
photograph, makes another. Scoring is geometric, not aesthetic: each landmark
must be present, in the right place, in the right relation to the others.

A reference may also carry a **colour** target: the photograph quantised to a
small fixed pen palette. Colour is a property of the ink alone and is scored on
its own, beside the composite and never inside it.
"""

from castor.bench.sacpaint.palette import NAMES as PALETTE_NAMES
from castor.bench.sacpaint.palette import PALETTE, PALETTE_VERSION
from castor.bench.sacpaint.reference import (
    DEFAULT_REFERENCE,
    LINE_REFERENCE,
    ReferenceSpec,
    available,
    canonical_size,
    color_reference,
    get_spec,
    has_color,
    load_rubric,
    reference_image,
    reference_ink,
    reference_kind,
)
from castor.bench.sacpaint.scorers import (
    color_fidelity,
    composite,
    discipline,
    efficiency,
    landmark_geometry,
    score_canvas,
    structure,
)

__all__ = [
    "DEFAULT_REFERENCE",
    "LINE_REFERENCE",
    "PALETTE",
    "PALETTE_NAMES",
    "PALETTE_VERSION",
    "ReferenceSpec",
    "available",
    "canonical_size",
    "color_fidelity",
    "color_reference",
    "composite",
    "discipline",
    "efficiency",
    "get_spec",
    "has_color",
    "landmark_geometry",
    "load_rubric",
    "reference_image",
    "reference_ink",
    "reference_kind",
    "score_canvas",
    "structure",
]
