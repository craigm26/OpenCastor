"""The phone as the eyes and ears of a robot EVALUATION.

WHY THIS IS NOT `/camera/*`. The camera routes describe cameras the ROBOT owns
and the robot pushes nothing — a viewer pulls. An eval runs the other way round:
an iPhone held or clamped over a drawing canvas PUSHES what it sees, and an
Inspect Robots embodiment on this host PULLS the newest frame when its policy
asks for an observation. The two directions cannot share one route without one
of them lying about who owns the device, so this is its own small surface:

    POST /eval/frame          the phone pushes a JPEG (~2 fps)
    GET  /eval/frame/latest   the embodiment pulls the newest one
    POST /eval/corners        the operator taps the canvas corners, once
    GET  /eval/corners        the scorer reads them and rectifies without markers
    POST /eval/feedback       what the operator said or typed
    GET  /eval/feedback       the embodiment reads new lines, by sequence
    GET  /eval/reference.png  the target image, so the phone and the model see one file
    GET  /eval/status         everything above, in one poll
    POST /eval/reset          a new episode starts with nothing carried over

**THE FRAME NEVER TOUCHES DISK.** It lives in this process's memory, one frame
per stream, overwritten by the next one. An eval rig points a camera at a table
in somebody's house; a directory of everything it ever saw is a different
product with a different consent story, and nothing here needs one. The same
rule is why `POST /eval/frame` keeps no history: the newest frame is the whole
state.

**THE BODY IS RAW JPEG, and that is deliberate.** It is byte-for-byte the
contract carbot's phone head already speaks (`POST /phone-frame`,
`Content-Type: image/jpeg`, the JPEG as the body) — one wire format for "the
phone is this robot's eye" across both rigs, rather than a second one wearing a
base64 coat. A JSON envelope would inflate every frame by a third for nothing.

**AUTH IS THE CONSOLE BEARER**, mounted by `build_app`. That token is read-only
by construction (it cannot actuate — see `app._auth`), which is what makes it
safe to hand to an `<img>` URL and to a poller running unattended for an hour.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter()

#: A stream name is a label, not a path. Robot-supplied strings ride in URLs and
#: in dict keys here; anything outside this shape is refused rather than escaped,
#: because a name that needs escaping is a name nobody meant to send.
_STREAM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

#: The two the benchmark names. Others are allowed — an embodiment may want a
#: second vantage — but only a few, so a typo in a loop cannot grow the process.
DEFAULT_STREAM = "overhead"
MAX_STREAMS = 4

#: A 1280-wide JPEG at quality 0.6 is ~120 KB. Eight megabytes is far past any
#: honest frame and still small enough that a stuck uploader cannot swap the Pi.
MAX_FRAME_BYTES = 8 * 1024 * 1024

#: Operator feedback is a sentence, not a document.
MAX_FEEDBACK_CHARS = 2000
#: What one episode can plausibly produce, kept whole. Older lines are dropped
#: from the front and the drop is REPORTED, so a reader can tell "nothing was
#: said" from "I arrived too late to hear it".
MAX_FEEDBACK_LINES = 500

#: Where the reference image comes from when the operator has not said.
#: The runtime ships it (castor.bench.sacpaint); an operator with another target sets this instead.
REFERENCE_ENV = "EVAL_REFERENCE_PATH"


class _Stream:
    """One pushed camera stream: the newest frame, and the canvas under it."""

    def __init__(self) -> None:
        self.jpeg: bytes | None = None
        self.seq = 0
        self.received_at = 0.0
        self.corners: list[list[float]] | None = None
        self.corners_at = 0.0
        self.corners_frame_seq = 0


class _State:
    """Everything one eval episode accumulates. Reset between episodes."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.streams: dict[str, _Stream] = {}
        self.feedback: list[dict[str, Any]] = []
        self.feedback_seq = 0
        self.feedback_dropped = 0

    def stream(self, name: str, create: bool = False) -> _Stream | None:
        existing = self.streams.get(name)
        if existing is not None or not create:
            return existing
        if len(self.streams) >= MAX_STREAMS:
            raise HTTPException(
                status_code=429,
                detail=f"at most {MAX_STREAMS} eval streams; POST /eval/reset to start over",
            )
        created = _Stream()
        self.streams[name] = created
        return created


_state = _State()


def _check_stream(name: str) -> str:
    if not _STREAM_RE.match(name or ""):
        raise HTTPException(
            status_code=422,
            detail="stream must be 1-32 chars of a-z, 0-9, '_' or '-'",
        )
    return name


def _age(then: float) -> float:
    return round(max(0.0, time.time() - then), 3)


def _corner_list(payload: Any) -> list[list[float]]:
    """Four normalized ``[x, y]`` pairs, TL, TR, BR, BL, or a 422 saying why not.

    TWO SHAPES IN, ONE SHAPE OUT. The phone posts a named object because a human
    reading the request has to be able to tell which corner is which; the scorer
    wants the flat list `castor.bench.sacpaint.rectify.rectify(corners=...)` takes. Accepting
    both here is what stops either end from carrying a translation of the other's
    idea of the order — which is exactly where a mirrored canvas comes from.

    NORMALIZED, ORIGIN TOP-LEFT, x right and y DOWN, both in 0..1, measured
    against the JPEG this same phone posts to `/eval/frame`. Not against the
    phone's screen: a preview layer crops, and the crop is invisible in the
    numbers. The app marks corners on the exact still it uploaded for that
    reason.
    """
    if isinstance(payload, dict):
        missing = [k for k in ("tl", "tr", "br", "bl") if k not in payload]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"corners object is missing {', '.join(missing)}",
            )
        raw = [payload["tl"], payload["tr"], payload["br"], payload["bl"]]
    elif isinstance(payload, (list, tuple)):
        raw = list(payload)
    else:
        raise HTTPException(
            status_code=422,
            detail="corners must be [[x,y] x4] in TL,TR,BR,BL order, or {tl,tr,br,bl}",
        )

    if len(raw) != 4:
        raise HTTPException(status_code=422, detail="corners needs exactly 4 points")

    out: list[list[float]] = []
    for i, point in enumerate(raw):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise HTTPException(status_code=422, detail=f"corner {i} is not [x, y]")
        pair: list[float] = []
        for value in point:
            # bool is an int in Python and `float(True)` is 1.0 — a JSON `true`
            # must not become a coordinate.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(status_code=422, detail=f"corner {i} is not numeric")
            number = float(value)
            # NaN fails every comparison, so it must be caught by identity with
            # itself rather than by a range test that silently passes it.
            if number != number or number in (float("inf"), float("-inf")):
                raise HTTPException(status_code=422, detail=f"corner {i} is not finite")
            if not -0.001 <= number <= 1.001:
                raise HTTPException(
                    status_code=422,
                    detail=f"corner {i} is {number}; corners are normalized 0..1",
                )
            pair.append(min(1.0, max(0.0, number)))
        out.append(pair)

    if _quad_area(out) < 0.01:
        raise HTTPException(
            status_code=422,
            detail="those four taps enclose less than 1% of the frame; mark the canvas, not a point",
        )
    return out


def _quad_area(points: list[list[float]]) -> float:
    """The shoelace area of the quad, as a fraction of the frame.

    A degenerate mark — four taps in one spot, or three of them collinear — makes
    a homography that is arithmetically fine and geometrically nonsense, and the
    resulting rectified canvas is noise the scorer would happily score. Refusing
    it here is the only place it is cheap.
    """
    total = 0.0
    for i in range(4):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % 4]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def _reference_path() -> Path | None:
    """The reference image file, or None if this host has not got one.

    The env var wins so an operator can point a rig at any target without
    installing a benchmark; the packaged photograph (castor.bench.sacpaint)
    is the fallback so the common case needs no configuration at all.
    """
    override = os.environ.get(REFERENCE_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    try:  # pragma: no cover - ships with the runtime; None only on a broken install
        from importlib.resources import files

        candidate = Path(
            str(files("castor.bench.sacpaint") / "assets" / "sacramento-photo-v1.webp")
        )
        return candidate if candidate.is_file() else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #


@router.post("/eval/frame")
async def push_frame(request: Request, stream: str = DEFAULT_STREAM) -> dict:
    """The phone's newest look at the canvas. Body is the JPEG itself.

    Answers with the sequence number it was given, so the phone can tell a frame
    that arrived from one that timed out on the way — at two frames a second
    over a LAN, "did that send?" is otherwise unanswerable.
    """
    name = _check_stream(stream)
    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail="empty body; POST the JPEG bytes")
    if len(body) > MAX_FRAME_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"frame is {len(body)} bytes; the ceiling is {MAX_FRAME_BYTES}",
        )
    # The two-byte JPEG SOI. Cheap, and it turns "the scorer read a blank canvas"
    # into "the phone sent a PNG", which is a bug somebody can fix.
    if body[:2] != b"\xff\xd8":
        raise HTTPException(status_code=415, detail="body is not a JPEG (no SOI marker)")

    now = time.time()
    with _state.lock:
        target = _state.stream(name, create=True)
        assert target is not None
        target.jpeg = body
        target.seq += 1
        target.received_at = now
        seq = target.seq
    return {"ok": True, "stream": name, "seq": seq, "bytes": len(body), "received_at": now}


@router.get("/eval/frame/latest")
def latest_frame(stream: str = DEFAULT_STREAM, max_age_s: float | None = None) -> Response:
    """The newest pushed frame, as `image/jpeg`.

    `max_age_s` is the reader's own staleness rule and it is a 404, not a 200
    with an old picture: a policy that inspects its work must never be shown the
    canvas as it was before its last stroke. A reader that would rather decide
    for itself omits the parameter and reads `X-Eval-Frame-Age-S`.
    """
    name = _check_stream(stream)
    with _state.lock:
        target = _state.stream(name)
        if target is None or target.jpeg is None:
            raise HTTPException(status_code=404, detail=f"no frame pushed to '{name}' yet")
        jpeg, seq, received_at = target.jpeg, target.seq, target.received_at
        corners = target.corners
    age = _age(received_at)
    if max_age_s is not None and age > max_age_s:
        raise HTTPException(
            status_code=404,
            detail=f"newest '{name}' frame is {age}s old, older than max_age_s={max_age_s}",
        )
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={
            "X-Eval-Frame-Seq": str(seq),
            "X-Eval-Frame-Age-S": str(age),
            "X-Eval-Frame-Received-At": str(received_at),
            "X-Eval-Corners-Marked": "1" if corners else "0",
            "Cache-Control": "no-store",
        },
    )


@router.get("/eval/frame/info")
def frame_info(stream: str = DEFAULT_STREAM) -> dict:
    """Is there a frame, how old is it, and is the canvas marked — without moving
    the bytes. A poller deciding whether to fetch asks this."""
    name = _check_stream(stream)
    with _state.lock:
        target = _state.stream(name)
        return _stream_info(name, target)


def _stream_info(name: str, target: _Stream | None) -> dict:
    if target is None or target.jpeg is None:
        return {"stream": name, "present": False, "seq": 0, "corners_marked": False}
    return {
        "stream": name,
        "present": True,
        "seq": target.seq,
        "bytes": len(target.jpeg),
        "age_s": _age(target.received_at),
        "received_at": target.received_at,
        "corners_marked": target.corners is not None,
    }


# --------------------------------------------------------------------------- #
# Canvas corners
# --------------------------------------------------------------------------- #


@router.post("/eval/corners")
async def set_corners(request: Request) -> dict:
    """Where the canvas is in the frame, as the operator taps it.

    THIS IS THE PRINTED FIXTURE, REPLACED BY A PERSON. The benchmark rectifies through
    four ArUco markers when somebody printed them; four taps on the phone are the
    same four points from the same photograph, obtained without a printer. They
    are stored per stream, they survive every subsequent frame, and they are only
    invalidated by moving the phone — which nothing here can detect, so the
    operator re-marks and the record says when they last did.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="body must be JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    name = _check_stream(str(payload.get("stream", DEFAULT_STREAM)))
    corners = _corner_list(payload.get("corners", payload))

    now = time.time()
    with _state.lock:
        target = _state.stream(name, create=True)
        assert target is not None
        target.corners = corners
        target.corners_at = now
        target.corners_frame_seq = target.seq
        return _corner_record(name, target)


@router.get("/eval/corners")
def get_corners(stream: str = DEFAULT_STREAM) -> dict:
    """The canvas corners for a stream, or `marked: false`.

    `corners` is the flat TL,TR,BR,BL list `castor.bench.sacpaint.rectify.rectify(image,
    corners=...)` accepts as-is; `named` is the same four points for a human.
    """
    name = _check_stream(stream)
    with _state.lock:
        target = _state.stream(name)
        if target is None or target.corners is None:
            return {"stream": name, "marked": False}
        return _corner_record(name, target)


def _corner_record(name: str, target: _Stream) -> dict:
    corners = target.corners or []
    return {
        "stream": name,
        "marked": True,
        "corners": corners,
        "named": dict(zip(("tl", "tr", "br", "bl"), corners, strict=False)),
        "order": ["tl", "tr", "br", "bl"],
        "space": "normalized 0..1 of the posted JPEG, origin top-left, y down",
        "marked_at": target.corners_at,
        "marked_at_frame_seq": target.corners_frame_seq,
    }


# --------------------------------------------------------------------------- #
# Operator feedback
# --------------------------------------------------------------------------- #


@router.post("/eval/feedback")
async def add_feedback(request: Request) -> dict:
    """One line of what the operator said or typed.

    It is EVIDENCE, NOT A COMMAND, and the wording of that is the reader's job:
    nothing here acts on the text, and an embodiment that hands it to a model
    should hand it over labelled as an observation from a person watching. A line
    that arrives as an instruction the policy obeys blindly turns the eval's
    human into an unsigned control path.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="body must be JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")

    text = payload.get("text")
    if not isinstance(text, str):
        raise HTTPException(status_code=422, detail="text is required and must be a string")
    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is empty")
    if len(text) > MAX_FEEDBACK_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"text is {len(text)} characters; the ceiling is {MAX_FEEDBACK_CHARS}",
        )
    source = payload.get("source", "typed")
    if source not in ("voice", "typed"):
        raise HTTPException(status_code=422, detail="source must be 'voice' or 'typed'")

    now = time.time()
    with _state.lock:
        _state.feedback_seq += 1
        line = {"seq": _state.feedback_seq, "at": now, "source": source, "text": text}
        _state.feedback.append(line)
        while len(_state.feedback) > MAX_FEEDBACK_LINES:
            _state.feedback.pop(0)
            _state.feedback_dropped += 1
        return {"ok": True, **line}


@router.get("/eval/feedback")
def read_feedback(since: int = 0, limit: int = 100) -> dict:
    """Lines with `seq > since`, oldest first.

    `next_since` is what to pass next time, and it is the seq of the last line
    RETURNED rather than the last line held — a reader that hit `limit` picks up
    exactly where it stopped. `dropped` is non-zero only if the ring overflowed,
    which is the one case where "no new lines" would otherwise be a lie.
    """
    if limit < 1 or limit > MAX_FEEDBACK_LINES:
        raise HTTPException(status_code=422, detail=f"limit must be 1..{MAX_FEEDBACK_LINES}")
    with _state.lock:
        newer = [line for line in _state.feedback if line["seq"] > since][:limit]
        return {
            "lines": newer,
            "next_since": newer[-1]["seq"] if newer else since,
            "latest_seq": _state.feedback_seq,
            "dropped": _state.feedback_dropped,
        }


# --------------------------------------------------------------------------- #
# The target, and the whole picture
# --------------------------------------------------------------------------- #


@router.get("/eval/reference.png")
def reference_png() -> Response:
    """The image the drawing is being compared against.

    ONE FILE, TWO READERS. The phone shows it to the operator and the scorer
    scores against it; serving it from the robot rather than bundling a copy in
    the app is what keeps those from drifting when the benchmark revises its
    reference.
    """
    path = _reference_path()
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"no reference image on this host; set {REFERENCE_ENV} to a PNG",
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"reference unreadable: {exc}") from exc
    if path.suffix.lower() != ".png":
        # The route promises a PNG (the phone decodes exactly that); the packaged
        # reference is the original photograph as supplied, so transcode on the way out.
        data = _as_png(data)
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


_PNG_CACHE: dict = {}


def _as_png(data: bytes) -> bytes:
    key = hash(data)
    if key not in _PNG_CACHE:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(status_code=500, detail="reference image could not be decoded")
        ok, buf = cv2.imencode(".png", image)
        if not ok:
            raise HTTPException(
                status_code=500, detail="reference image could not be encoded as PNG"
            )
        _PNG_CACHE.clear()
        _PNG_CACHE[key] = buf.tobytes()
    return _PNG_CACHE[key]


@router.get("/eval/status")
def status() -> dict:
    """One poll that answers "is anybody looking, and has anybody spoken"."""
    with _state.lock:
        streams = [_stream_info(name, s) for name, s in sorted(_state.streams.items())]
        feedback = {
            "latest_seq": _state.feedback_seq,
            "held": len(_state.feedback),
            "dropped": _state.feedback_dropped,
        }
    return {
        "streams": streams,
        "feedback": feedback,
        "reference": _reference_path() is not None,
        "default_stream": DEFAULT_STREAM,
    }


@router.post("/eval/reset")
def reset() -> dict:
    """Start an episode with nothing carried over.

    Corners go too. They belong to a camera pose, and the reason to reset is
    almost always that something about the rig changed — keeping the old marks
    would silently rectify the next episode through the last one's geometry.
    """
    with _state.lock:
        _state.streams.clear()
        _state.feedback.clear()
        _state.feedback_seq = 0
        _state.feedback_dropped = 0
    return {"ok": True}
