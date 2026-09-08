# Eval eyes — the phone as the eyes and ears of an evaluation

The console surface an iPhone pushes to and an [Inspect Robots](https://github.com/robocurve/inspect-robots)
embodiment pulls from, so a benchmark that needs an overhead camera and a human
observer can run with neither bolted to the robot.

Shipped in `castor/console/eval_eyes.py`, mounted by `castor.console.app.build_app`,
consumed by **OpenCastor iOS build 76** ("Eval mode", `OpenCastor/Sources/EvalModeView.swift`).

---

## Why it exists

The motivating rig is [`sacpaint`](https://github.com/craigm26/sacpaint): a robot
arm draws the Sacramento reference on a 300 × 400 mm sheet and a scorer compares
the result to a pinned PNG. The scorer cannot read a photograph directly — the
camera's pose, lens and mounting height are baked into it — so it rectifies
through four known canvas corners. Its default way of getting those corners is
four printed ArUco markers taped around the paper.

The phone replaces both missing pieces at once: it *is* the overhead camera, and
four taps on its screen *are* the four corners. It also carries the microphone,
so what the person watching says becomes part of the run's evidence.

Bob's own OAK-D is not an alternative here: its fitted extrinsic is off by 142 mm
against a 16 mm brick, which is fine for reaching and useless for a pen.

## Where it lives

Base URL is the **robot console** — `castor up`'s port layout is
`base_port + 2`, so a default host serves it on `:8082`; the rover on the bench
runs the packaged console on **`:8004`**. The app learns the address and the
token from the pairing QR (`console_url` / `console_token`) and does not invent
either.

### Live on this bench (verified 2026-09-08)

| Robot | Console | Serves `/eval/*` | How |
|---|---|---|---|
| rover (`rover-spec-a-drive`) | `:8004` | yes | packaged `castor.console`, editable install — a `systemctl --user restart rover-console` was all it took |
| Bob (`SO-ARM 101`) | `:8002` | yes | `~/bob/console_service.py` is a bench file, not the package; it now **imports** this router (`from castor.console.eval_eyes import router as eval_router`) rather than carrying a copy |

Both have `EVAL_REFERENCE_PATH` pointed at sacpaint's asset, so
`GET /eval/reference.png` answers 200 with the real 4520-byte PNG on either.
Bob is the robot that actually draws, so it is the one an embodiment should
target first.

Auth is the **console bearer**, the same read-only credential every other console
route takes, in either form:

```
Authorization: Bearer <CONSOLE_TOKEN>
?token=<CONSOLE_TOKEN>            # for <img> and other loaders that cannot set headers
```

That token cannot actuate — the act path is `GatewayClient` and signed envelopes,
a different port and a different credential. Nothing under `/eval/` moves a
robot.

## The endpoints

### `POST /eval/frame?stream=overhead`

The phone pushes what it sees, about twice a second.

* **Body**: the JPEG itself, raw. `Content-Type: image/jpeg`.
  This is byte-for-byte the contract carbot's phone head already speaks
  (`POST /phone-frame`) — one wire format for "the phone is this robot's eye"
  across both rigs.
* **Ceiling**: 8 MB. A body without the JPEG SOI marker (`FF D8`) is `415`.
* **Answer**: `{"ok": true, "stream": "overhead", "seq": 12, "bytes": 148231, "received_at": 1757337600.12}`

Only the newest frame per stream is kept, in memory. **Nothing is written to
disk**, and there is no history: a rig pointed at a table in somebody's house
does not get a folder of everything it ever saw.

At most 4 distinct stream names (`^[a-z0-9][a-z0-9_-]{0,31}$`); a fifth is `429`.

### `GET /eval/frame/latest?stream=overhead[&max_age_s=2]`

**This is the one the embodiment polls for its `overhead` observation.**

* `200` with `Content-Type: image/jpeg` and the bytes.
* `404` when nothing has been pushed to that stream, or when `max_age_s` is given
  and the newest frame is older than it. Staleness is a 404 rather than an old
  picture on purpose: a policy inspecting its own work must never be shown the
  canvas as it was before its last stroke.

Response headers:

| Header | Meaning |
|---|---|
| `X-Eval-Frame-Seq` | monotonic per stream; `seq` from the POST that delivered it |
| `X-Eval-Frame-Age-S` | seconds since it arrived |
| `X-Eval-Frame-Received-At` | epoch seconds |
| `X-Eval-Corners-Marked` | `1` if corners exist for this stream, else `0` |

### `GET /eval/frame/info?stream=overhead`

The same facts without moving the bytes — for a poller deciding whether to fetch.

```json
{"stream": "overhead", "present": true, "seq": 12, "bytes": 148231,
 "age_s": 0.42, "received_at": 1757337600.12, "corners_marked": true}
```

An absent stream answers `{"stream": "overhead", "present": false, "seq": 0, "corners_marked": false}` — 200, not 404.

### `POST /eval/corners`

The operator's four taps. Sent once per camera pose; kept until re-marked or reset.

```json
{"stream": "overhead",
 "corners": {"tl": [0.08, 0.11], "tr": [0.93, 0.09], "br": [0.95, 0.88], "bl": [0.06, 0.90]}}
```

The flat form is accepted too, and means the same thing:

```json
{"corners": [[0.08, 0.11], [0.93, 0.09], [0.95, 0.88], [0.06, 0.90]]}
```

**The coordinate space, which is the whole contract:**

* normalized **0…1**, against **the JPEG posted to `/eval/frame`** — not against
  the phone's screen (a preview layer crops, and the crop is invisible in the
  numbers; the app marks corners on the exact still it uploaded for that reason);
* origin **top-left**, x right, y **down**;
* order **TL, TR, BR, BL** as the canvas appears upright — the same order
  `sacpaint.rectify.order_corners` produces, and the app sorts the operator's
  taps into it with a port of that function (`EvalCanvasCorners.ordered`,
  cross-checked against the Python on a skewed quad).

Refused with `422`: not four points, non-numeric or non-finite, outside 0…1, or a
quad enclosing **less than 1% of the frame** (a degenerate mark makes an
arithmetically valid homography over meaningless geometry, and the scorer would
happily score the result).

### `GET /eval/corners?stream=overhead`

```json
{"stream": "overhead", "marked": true,
 "corners": [[0.08, 0.11], [0.93, 0.09], [0.95, 0.88], [0.06, 0.90]],
 "named": {"tl": [0.08, 0.11], "tr": [0.93, 0.09], "br": [0.95, 0.88], "bl": [0.06, 0.90]},
 "order": ["tl", "tr", "br", "bl"],
 "space": "normalized 0..1 of the posted JPEG, origin top-left, y down",
 "marked_at": 1757337580.4, "marked_at_frame_seq": 9}
```

Unmarked answers `{"stream": "overhead", "marked": false}` — 200.

`corners` is exactly what `sacpaint.rectify.rectify(image, corners=...)` takes,
and what an embodiment should put in `observation.extra["canvas_corners"]`.

### `POST /eval/feedback`

```json
{"text": "the pen skipped on the second stroke", "source": "voice"}
```

`source` is `voice` (dictated on the phone by `SFSpeechRecognizer`) or `typed`.
Text is trimmed, non-empty, at most 2000 characters. Answers
`{"ok": true, "seq": 3, "at": 1757337610.0, "source": "voice", "text": "..."}`.

🔴 **It is evidence, not a command.** Nothing on either side of this endpoint acts
on the text. An embodiment that hands it to a model must label it as an
observation from a person watching; a line treated as an instruction the policy
obeys turns the eval's human into an unsigned control path into a robot whose
every other input is signed.

### `GET /eval/feedback?since=0&limit=100`

```json
{"lines": [{"seq": 1, "at": 1757337610.0, "source": "voice", "text": "..."}],
 "next_since": 1, "latest_seq": 3, "dropped": 0}
```

Lines with `seq > since`, oldest first. Pass the returned `next_since` next time
— it is the seq of the last line **returned**, so a reader that hit `limit` picks
up exactly where it stopped. The ring holds 500 lines; `dropped` is non-zero only
if it overflowed, which is the one case where "no new lines" would be a lie.

### `GET /eval/reference.png`

The target image, served by the robot so the phone and the scorer read one file.
Source, in order: `EVAL_REFERENCE_PATH` if set to a readable file, else sacpaint's
packaged `sacramento-line-v0.png` if sacpaint is importable in the console's
environment. `404` naming the env var otherwise.

The console venv on this bench (`~/venvs/castor`) does **not** have sacpaint
installed, so set the variable in the robot's `console.env`:

```
EVAL_REFERENCE_PATH=/home/craigm26/projects/craigm26/sacpaint/src/sacpaint/assets/sacramento-line-v0.png
```

### `GET /eval/status`

One poll for "is anybody looking, and has anybody spoken":

```json
{"streams": [{"stream": "overhead", "present": true, "seq": 12, "bytes": 148231,
              "age_s": 0.4, "received_at": 1757337600.1, "corners_marked": true}],
 "feedback": {"latest_seq": 3, "held": 3, "dropped": 0},
 "reference": true, "default_stream": "overhead"}
```

### `POST /eval/reset`

Clears every stream, the corners, and the feedback ring. Corners go too: they
belong to a camera pose, and the reason to reset is almost always that something
about the rig changed.

---

## An embodiment sketch

```python
import requests

BASE  = "http://rover.local:8004"
AUTH  = {"Authorization": f"Bearer {CONSOLE_TOKEN}"}

def overhead(max_age_s=3.0) -> bytes | None:
    r = requests.get(f"{BASE}/eval/frame/latest",
                     params={"stream": "overhead", "max_age_s": max_age_s},
                     headers=AUTH, timeout=5)
    return r.content if r.status_code == 200 else None   # 404 = nobody is looking, or it is stale

def canvas_corners() -> list[list[float]] | None:
    got = requests.get(f"{BASE}/eval/corners", headers=AUTH, timeout=5).json()
    return got["corners"] if got.get("marked") else None

def new_operator_notes(cursor: int) -> tuple[list[dict], int]:
    got = requests.get(f"{BASE}/eval/feedback", params={"since": cursor},
                       headers=AUTH, timeout=5).json()
    return got["lines"], got["next_since"]
```

Then, in `observe()`:

```python
frame = overhead()
corners = canvas_corners()
observation.images["overhead"] = frame
if corners is not None:
    observation.extra["canvas_corners"] = corners       # sacpaint rectifies through these
# observation.extra["canonical_canvas"] stays False: this is a photograph.
```

`observation.images["reference"]` comes from `GET /eval/reference.png` (or from
the benchmark package directly, if the embodiment has it installed).

## What the phone does

`EvalModeView` in opencastor-ios, reachable from **a robot → Control →
"Eval mode — be the eyes of a benchmark"**, offered on any non-simulated robot
whose pairing carries a console.

* Live rear-camera preview at `.resizeAspect` — the *whole* frame, because
  framing a canvas against a cropping preview puts the sheet's corners outside
  the uploaded JPEG with nothing on screen to say so.
* A **Stream** toggle: 1080-wide portrait frames (`EvalFrame.maxWidth` = 1280;
  a 1080-wide frame over a 300 mm canvas is ~3.6 px/mm, against the 2 px/mm
  canonical image), JPEG q0.6, paced at 2 fps by `EvalFrame.shouldSend`, one
  upload in flight at a time. The screen stays awake while it is on.
* **Mark the corners**: freezes the last uploaded still and takes four taps on
  *those pixels*, sorted by the `order_corners` port, refusing a degenerate quad
  before it reaches the network.
* **The reference**: `AsyncImage` on `GET /eval/reference.png` (the URL is
  editable, so a different benchmark's target works with no app change).
* **Hold to talk**: `SFSpeechRecognizer` on device, delivered on release; plus a
  plain text field. Both `POST /eval/feedback`.

## Tests

`tests/test_console_eval_eyes.py` (31) pins the wire format, both corner shapes,
every refusal, and the feedback cursor. `CastorKit/Tests/CastorKitTests/EvalEyesTests.swift`
(17, in opencastor-ios) pins the corner ordering against sacpaint's rule, the
degeneracy floor, the frame-fitting arithmetic and the cadence.
