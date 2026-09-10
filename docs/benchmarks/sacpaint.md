# Sacramento PaintBench (`castor bench sacpaint`)

A physical-AI benchmark on [Inspect Robots](https://github.com/robocurve/inspect-robots), shipped inside OpenCastor as `castor bench sacpaint` (import name `castor.bench.sacpaint`; it was the standalone `sacpaint` package until 0.3.1):
a robot with a pen must reproduce a fixed reference drawing from camera
feedback. One fixed prompt, one pinned reference image, geometric scoring that
anyone can recompute offline from a photo. No printed markers, no special
fixture: a sheet of paper on a desk and any camera, including an iPhone.

> Can a general-purpose frontier model reproduce a visual target with a physical
> tool, using camera feedback to correct itself, with no task-specific training?

The built-in reference is a photograph of Sacramento, shot from above the
Capitol: the Tower Bridge at the end of the Capitol Mall, the Capitol cupola
and dome in the foreground, office towers either side, the valley and the
mountains on the horizon. The model sees the photograph, byte for byte; its
SHA-256 is the benchmark's identity. The scorers never see it: they read a
stroke skeleton traced over the photograph's landmarks (right), because the
scoring is geometric and a photograph has no ink. The physical sheet is
150 × 200 mm, the photograph's own 3:4, small enough for a desk arm such as
the SO-ARM101 to reach every corner.

<p>
<img src="castor/bench/sacpaint/assets/sacramento-photo-v1.webp" alt="the reference: a photograph of Sacramento" width="300">
<img src="castor/bench/sacpaint/assets/sacramento-photo-v1.ink.png" alt="the scoring skeleton traced over its landmarks" width="300">
</p>

The earlier line-drawing reference (`sacramento-line-v0`) is still registered
as `sacpaint/line-v0` for comparison runs; `sacpaint/photo-v1` is the
benchmark.

## Sixty seconds, no robot

```bash
pip install "opencastor[paintbench]"
castor bench sacpaint score photo-of-my-drawing.jpg      # any photo of a finished sheet -> score + overlay
```

`score` finds the sheet in the photo (markers if present, otherwise the largest
bright quadrilateral, otherwise corners you pass with `--corners`), rectifies
it to the canonical canvas, and writes three files next to the photo: the
rectified canvas, an overlay with the reference ink in red, and a JSON
breakdown. That is the whole "process a new eval" path for a drawing made by
any robot, any policy, any hardware.

The full benchmark, in the built-in mock plotter world:

```bash
castor bench sacpaint run --policy sacpaint_trace --embodiment sacpaint_plotter --no-rerun --no-prompt   # oracle, composite ~0.98
castor bench sacpaint run --policy sacpaint_idle  --embodiment sacpaint_plotter --no-rerun --no-prompt   # floor, 0.00
castor bench sacpaint export logs --out submission --label oracle                                          # publishable bundle
```

With a frontier model driving the same mock world (needs `pip install "opencastor[paintbench-agent]"` and a key):

```bash
ANTHROPIC_API_KEY=... castor bench sacpaint run --policy agent --model anthropic/claude-fable-5 --embodiment sacpaint_plotter -- -P images=on_demand
```

No API key? One flag runs the same policy through a Claude subscription (the
Claude Code CLI, logged in on the machine) for development runs. Scores from
this path are labelled `wire=claude-code-cli` and are not leaderboard-comparable;
see [#running-on-a-claude-subscription-no-api-key](#running-on-a-claude-subscription-no-api-key).

```bash
castor bench sacpaint run --subscription --model haiku --policy agent --embodiment sacpaint_plotter --max-llm-calls 40 --no-rerun --no-prompt
```

## The prompt

Fixed. Do not tune it per model.

> Draw the reference image on the canvas with the pen. You may look at the
> overhead camera to inspect your work and make corrections. Stop when you
> believe the drawing is complete.

The model receives the reference as an image stream named `reference` (the
photograph at its native 1499 × 2000) and the canvas as a stream named
`overhead`, because the agent policy attaches camera frames to observations and
the instruction cannot carry an image. Every embodiment exposes those two
streams. Nothing tells the model which landmarks are scored: choosing what to
draw from a photograph is part of the task.

## Scoring

All scorers are pure readers of the final canvas. Nothing calls a model. The
final canvas is the `observe_parked()` frame (pen lifted clear), rectified
unless the embodiment marks it canonical, then thresholded to ink (a 1% border
is ignored, because a rectified photograph of a sheet always carries the
sheet's edge there). The reference ink they compare against is the traced
skeleton, `sacramento-photo-v1.ink.png`, whose hash every EvalLog records as
`ink_sha256` next to the photograph's `reference_sha256`.

| Scorer | Weight | What it measures |
|---|---|---|
| `landmark_geometry` | 0.45 | Per landmark: precision × recall of ink inside its box at 1% of the canvas diagonal (2.5 mm) after centroid alignment (presence), times a centroid-offset term (position). Plus relations (tower over dome, same x, horizon above tower), gated on both landmarks being present. |
| `structure` | 0.30 | Precision × recall of all ink within 2% of the diagonal (5 mm) of reference ink. Product, not F1, because a dense scribble recalls everything. |
| `discipline` | 0.15 | 1 − fraction of ink farther than 2.5% of the diagonal (6 mm) from any reference ink, scaled down past 4× the reference's ink. Blank canvas scores 0. |
| `efficiency` | 0.10 | 1 − steps/max_steps for a declared finish; scaled by `structure` in the composite so finishing a bad drawing fast earns nothing. |
| `composite` | | The leaderboard number. |

Calibration on synthetic canvases:

| Canvas | composite | landmark | structure | discipline |
|---|---|---|---|---|
| perfect trace | 0.98 | 1.00 | 1.00 | 1.00 |
| perfect trace photographed at an angle, plain sheet, rectified | 0.97 | 0.97 | 1.00 | 1.00 |
| hand wobble, σ = 1 mm | 0.97 | 0.97 | 1.00 | 1.00 |
| whole drawing shifted 5 mm | 0.77 | 0.63 | 1.00 | 0.69 |
| top half only (bridge, no dome) | 0.53 | 0.38 | 0.55 | 1.00 |
| random scribble, 30 / 60 / 400 lines | 0.30 / 0.42 / 0.33 | 0.32 / 0.47 / 0.33 | 0.28 / 0.41 / 0.43 | 0.33 / 0.35 / 0.09 |
| blank | 0.00 | 0.00 | 0.00 | 0.00 |

Through the CLI with the framework's default guardrails, 3 epochs: oracle
`sacpaint_trace` composite 0.984, `sacpaint_idle` 0.000.

Known properties: global registration counts (a 5 mm offset is a placement
error, by design); a dense random scribble still collects about 0.3 to 0.5
because the skeleton covers much of the canvas (measured 2026-09-09: 30, 60
and 400 random lines score 0.33, 0.47 and 0.44 against the oracle's 0.995);
the tower, dome and cupola dominate through their weights.

## Media

Every score carries `medium`, what the marks were made of. Each medium is its
own leaderboard category: a number is only ever ranked against numbers made
the same way.

| Medium | What it is | Category |
|---|---|---|
| `pen` | a pen on a sheet, photographed | the benchmark proper |
| `virtual` | no paper, no pen: the real arm moves, and the canvas is inked from where the arm *measured* its tip after each pen-down move (`-E medium=virtual` on the OpenCastor body) | its own category: real arm, real policy, exact and unobstructed canvas |
| `sim` | the mock plotter | development only, never ranked |

Different media are the intended next axis of the benchmark, not a footnote:
brush and watercolour on paper, marker, chalk, a plotter pen, each with its
own rubric (a wash is scored on coverage and edges, a pen on lines), and
stylised rubrics that reward a named artist's way of seeing the same photo. A
medium is a `(body, scoring rubric)` pair; the reference stays the photograph.

## No paper? The virtual easel

A rig with an arm but nothing to draw with can still run the whole loop. With
Bob's calibrated joint limits a flat sheet fits nowhere on the desk, so the
virtual sheet stands upright 325 mm in front of the base, like a canvas on an
easel; the arm draws in the air and the ink is telemetry:

```bash
castor bench sacpaint run --policy sacpaint_trace --embodiment opencastor --no-rerun --no-prompt \
  -- -E pair_payload=/path/to/pair-payload.json -E medium=virtual -E calibration=easel \
     -E move_tool=arm.reach_point -E move_args=reach_point -E tolerance_mm=5 -E strict_reach=false
```

`strict_reach=false` lets a target the arm could not quite reach be inked where
the arm actually got to (the miss count is in every observation); with a real
pen that would be a fault. `-E easel_distance_mm`, `-E easel_elevation_deg` and
`-E easel_azimuth_deg` move the easel; the defaults were measured on an
SO-ARM101 (see `castor/bench/sacpaint/calibration.py`).

## Tracks

| Track | Memory across episodes | Camera | Measures |
|---|---|---|---|
| Cold | none | `-P images=on_demand`, never called | raw open-loop competence |
| Closed loop (default) | none | throughout | self-correction within one drawing |
| Learning | prior attempts via `inspect-robots summarize` + `-P prior_learnings=` | throughout | improvement across 5 canvases |

The learning track reuses the framework's own `summarize` / `prior_learnings`
mechanism, so the "memory" is an auditable markdown file with a recorded hash.
Report initial, final, best-of-5, and slope per attempt.

## Your own reference in two commands

```bash
castor bench sacpaint new mytown --canvas 210x297 --photo mytown.jpg   # spec + preview PNG in ~/.sacpaint/references/
castor bench sacpaint run --task sacpaint/mytown --policy sacpaint_trace --embodiment sacpaint_plotter -- -E reference=mytown
```

A reference is a JSON file of polylines grouped by landmark (millimetres,
origin bottom-left, y up), optional landmark weights and boxes, relations
(`above`, `left_of`, `same_x`), tolerances, and optionally a `photo` the model
sees instead of the strokes (then the strokes are the scoring skeleton: trace
the photo's landmarks). Every spec in `~/.sacpaint/references/` registers as
the task `sacpaint/<name>` the moment the package is imported. The built-in
spec is at `castor/bench/sacpaint/assets/sacramento-photo-v1.spec.json`; copy it, edit
it, done.

## Paint a picture from the phone

The OpenCastor iOS app has a **Paint a picture** screen on every paired robot's
page: the photograph of Sacramento, one button, and a live view of the canvas
while the arm paints. It talks to the robot's console, which runs the benchmark
on the robot's behalf:

| Route | What |
|---|---|
| `GET /eval/paint/config` | the media this robot can paint in, the default picture, whether a brain is configured (no secrets) |
| `POST /eval/paint` `{"picture": "sacramento", "medium": "virtual"}` | start one `castor bench sacpaint run` as a background job; 409 while one runs, 503 without a profile, 422 for a medium the rig lacks |
| `GET /eval/paint` | `idle`, or `running` with `steps`, `misses`, `llm_calls`, `elapsed_s`, or `done`/`stopped`/`error` with the `score` |
| `POST /eval/paint/stop` | end the job; the arm finishes the move it is on |
| `POST /eval/picture?name=mine` (raw JPEG) | a picture of your own; the robot traces its edges into a scoring skeleton (`castor bench sacpaint new --photo … --auto-trace`) and it becomes task `sacpaint/mine` |
| `GET /eval/frame/latest?stream=canvas` | the live canvas: the embodiment posts it after every move (`-E canvas_post_url`) |
| `GET /eval/paint/canvas.png` | the finished canvas as the scorer read it |

The robot decides how it paints. The operator writes `<ROBOT_HOME>/paint.json`
once (Bob's is at `/home/craigm26/bob/paint.json`):

```json
{
  "embodiment": "opencastor",
  "flags": {"pair_payload": "/home/craigm26/bob/pair-payload.json",
            "calibration": "easel", "move_tool": "arm.reach_point", "move_args": "reach_point",
            "tolerance_mm": "5", "strict_reach": "false", "timeout_s": "120"},
  "brain": {"subscription": "opus", "claude_bin": "/home/craigm26/.local/bin/claude"},
  "max_llm_calls": 60,
  "media": ["virtual"]
}
```

`media` lists what the rig can do today; with `"pen"` in it the console points
the run at the phone's frames and tapped corners (Eval mode) automatically.
`brain` is either `{"subscription": "<claude alias>"}` (the shim; scores carry
`wire=claude-code-cli`) or `{"model": "anthropic/…"}` with a key in the
console's environment. Two embodiment flags exist for this: `-E progress_path=`
(a small JSON the console reads for `steps`/`misses`) and `-E canvas_post_url=`
(the virtual canvas, posted as JPEG with the console token).

## Real robots

Any embodiment that exposes this contract runs the benchmark unchanged:

- action space `eef_abs_pose`, dims `(x, y, z)` in metres in the canvas frame
  (x right, y up the sheet, z above the paper; the pen marks at z ≤ 0.002 m,
  travels at z ≥ 0.005 m);
- images `overhead` and `reference`, state `eef_pos` (3,);
- `supported_target_kinds` includes `reference_drawing`;
- `observe_parked()` lifts the pen clear and returns a fresh observation;
- corners of the sheet in the overhead frame, if known, as
  `observation.extra["canvas_corners"]` (four `[x, y]` pairs, 0..1, TL TR BR BL);
  otherwise the scorer finds the sheet itself.

| Body | How |
|---|---|
| Mock plotter (built in) | `--embodiment sacpaint_plotter`, options `-E reference=NAME -E photo_mode=sheet` |
| OpenCastor + SO-ARM101 with signed receipts | [#the-opencastor-body-so-arm101-behind-robot-md-gateway](#the-opencastor-body-so-arm101-behind-robot-md-gateway): `--embodiment opencastor` |
| iPhone as the overhead camera, corner marker, and operator microphone | the OpenCastor iOS app's Eval mode (TestFlight build 76): frames and tapped corners go to the robot console, the embodiment polls them; see [#the-opencastor-body-so-arm101-behind-robot-md-gateway](#the-opencastor-body-so-arm101-behind-robot-md-gateway) |
| Any other arm | implement the contract above; `inspect-robots-so101` (LeRobot, joint space) is a fallback body that needs the agent's `move_joints` |

## Publishing a run the way robocurve does

```bash
castor bench sacpaint export logs --out submission --label opus
```

produces the layout of robocurve's published run datasets (clapboardbench):

```
submission/
 ├── README.md                     # provenance, reference hash, how to recompute
 ├── <log>.json                    # raw EvalLog: config, git rev, versions, scores, transcript
 ├── runs/README.md                # index table: model, policy, embodiment, status, composite, steps
 ├── runs/<label>-<n>.md           # one page per run: metadata, scores per epoch, per-landmark table,
 │                                 #   final canvas, the model's note for every tool call
 ├── html/                         # inspect-robots view reports (frames the model saw)
 ├── canvases/                     # the rectified final canvas and score JSON per trial
 ├── videos/                       # inspect-robots video (when ffmpeg is installed)
 └── reference.png, reference.sha256, rubric.json
```

`castor bench sacpaint worldevals-entry` prints the `Benchmark(...)` block for a
[WorldEvals](https://github.com/robocurve/worldevals) catalog pull request.

## Status

| Piece | State |
|---|---|
| Task `sacpaint/photo-v1` (the photograph), five scorers, three epochs | done, registered via entry points; `sacpaint/line-v0` kept |
| Mock plotter + oracle/idle policies | done; the whole stack runs with no hardware |
| Marker-free rectification (given corners, ArUco, plain sheet) | done, tested under perspective |
| `castor bench sacpaint score / new / run / export / worldevals-entry` | done |
| `--policy agent` (frontier LLM) | works against the mock; needs a key or the subscription shim |
| OpenCastor / SO-ARM101 embodiment | see #the-opencastor-body-so-arm101-behind-robot-md-gateway |
| Hardware runs | virtual medium on an SO-ARM101 (Bob) 2026-09-09: the arm traces the easel, see #the-opencastor-body-so-arm101-behind-robot-md-gateway; pen on paper not yet |
| WorldEvals catalog entry | after the first real-robot log |

## Development

```bash
git clone https://github.com/craigm26/OpenCastor && cd OpenCastor
pip install -e ".[dev,paintbench-agent]"
pytest tests/test_bench_sacpaint*.py
```

Regenerate the built-in assets (only when the reference itself changes; it re-versions the task):

```bash
python -c "from castor.bench.sacpaint.reference import write_assets; write_assets('castor/bench/sacpaint/assets')"
```

MIT.

## The OpenCastor body: SO-ARM101 behind robot-md-gateway

The `opencastor` embodiment drives a pen bolted to an SO-ARM101 wrist. Every
motion goes through the [robot-md-gateway](https://github.com/craigm26/robot-md-gateway)
`/v1/invoke`, so every stroke leaves an Ed25519-signed receipt and a refusal is
a signed promise that nothing moved. The overhead camera can be the robot's own
console, a phone bridge, or the OpenCastor iOS app — anything that answers an
HTTP GET with the latest JPEG.

The policy still speaks the same canvas-frame metres the mock plotter speaks, so
a run that works against `sacpaint_plotter` works here with one flag changed.

Budget ten minutes. Most of it is taping down a sheet.

---

### 1. Install (1 min)

```bash
pip install 'sacpaint[opencastor]'
```

The extra pulls in nothing: the adapter talks HTTP with `urllib` from the
standard library and decodes frames with the OpenCV that `sacpaint` already
needs. It exists so `[opencastor]` stays a stable install target if that ever
changes.

Check the body registered:

```bash
inspect-robots list | grep opencastor
```

### 2. Set the fixture (3 min)

- Tape a **150 × 200 mm sheet, portrait**, flat on the desk in front of the arm.
  A5 or half a sheet of letter paper, trimmed. The whole sheet is inside an
  SO-ARM101's reach, so no part of the drawing is unreachable. Flat matters
  more than square: the calibration handles rotation and offset, but it assumes
  the sheet is a plane.
- Fit the pen to the wrist and take the cap off. **On an SO-ARM101, mount it at
  an angle**, not straight down the wrist axis — see the note in step 4. You
  want the tip near vertical when the arm is in its natural reaching pose.
- Point a camera at the sheet. Any angle. **No printed markers are needed** —
  the scorer rectifies from the corners you tap in the phone app, or finds the
  ArUco markers if you happen to use them, or falls back to the largest bright
  quadrilateral (a white sheet on a darker desk).
- Find the camera's snapshot URL and check it returns a picture:

  ```bash
  curl -s -o /tmp/f.jpg -w '%{http_code} %{content_type}\n' \
      -H "Authorization: Bearer $CONSOLE_TOKEN" \
      http://<robot>:8002/camera/<name>/snapshot
  ```

  `200 image/jpeg` is what you want. List the cameras with
  `curl -s -H "Authorization: Bearer $CONSOLE_TOKEN" http://<robot>:8002/camera/list`.

  > **Not `/api/snapshot/latest`.** That OpenCastor endpoint returns a JSON
  > state snapshot with no pixels in it. The frame endpoints are the console's
  > `/camera/<name>/snapshot` (port 8002 on Bob) and the runtime's
  > `/api/detection/frame`.

### 3. Teach the canvas corners (4 min)

The arm has to know where the sheet is, to about a millimetre. Bob's OAK-D
extrinsic has a 142 mm residual — fine for a gripper, useless for a pen — so the
transform is *taught*, not derived.

Jog the pen to each corner and record where the arm says it is:

```bash
castor bench sacpaint calibrate \
    --out canvas.json \
    --pair-payload /path/to/pair-payload.json \
    --start 180,0,-60
```

It walks the corners in order **bl, br, tr, tl** (bottom-left first; the same
order the sheet reads). At each one you nudge in arm-base millimetres until the
pen tip sits exactly on the corner, then press `r`:

```
  x+ / x- / y+ / y- / z+ / z-   nudge by the step size
  s <mm>                        set the step size (default 5 mm)
  g <x,y,z>                     go to an absolute base position
  r                             record this corner and move on
  q                             give up
```

Three corners are enough. Four make the reported residual worth reading.
Nothing moves without a keystroke, and the first move waits for a confirmation.

If you already know the numbers, skip the arm entirely:

```bash
castor bench sacpaint calibrate --out canvas.json \
    --corner bl=120,75,-95 --corner br=120,-75,-95 --corner tl=320,75,-95
```

That example puts the sheet flat in front of the arm with canvas x running
toward base −y and canvas y running away from the base. It fits a right-handed
frame with the canvas normal pointing up, which is what the guard below checks.
Its far corner sits 329 mm from the base, inside the SO-ARM101's ~370 mm reach,
so the arm covers the whole sheet.

Add `--reference NAME` for a custom reference with a different sheet size; the
tool takes its corner positions from that reference's spec.

Either way it prints the fit quality:

```
wrote canvas.json
  corners      ['bl', 'br', 'tl']
  rms residual 0.41 mm
  max residual 0.63 mm
```

**Over 2 mm and it warns you.** Take the warning seriously: the pen will miss by
that much everywhere, and `landmark_geometry` scores position, so a systematic
2 mm offset is a real score loss. Re-teach the worst corner.

Two failures the fit catches for you:

- *"the taught canvas points are collinear"* — you taught three corners along
  one edge. Teach corners that span the sheet.
- *"the fitted canvas normal points down"* — two corner labels are swapped.
  Lifting the pen would drive it into the paper. `bl br tr tl` run
  anticlockwise as the drawing is read.

### 4. Run (2 min)

```bash
inspect-robots run \
    --task sacpaint/photo-v1 \
    --policy agent \
    --embodiment opencastor \
    -P model=anthropic/claude-fable-5 \
    -E pair_payload=/path/to/pair-payload.json \
    -E calibration=canvas.json \
    -E overhead_url="http://192.168.68.90:8002/eval/frame/latest?stream=overhead&max_age_s=3" \
    -E corners_url="http://192.168.68.90:8002/eval/corners?stream=overhead" \
    -E actuator_name=so-arm101 \
    -E move_tool=arm.reach_point \
    -E move_args=reach_point \
    -E speed=0.3
```

> **Why `arm.reach_point` on an SO-ARM101.** The default `arm.move_to` holds the
> tool pointing straight down, and on this arm that needs `wrist_flex` at about
> +1.47 rad against a measured safe ceiling of +0.41 rad. A 25,480-point sweep
> found **zero usable poses**: `arm.move_to` denies *every* target with
> `actuator_policy/unsafe_pose`, and nothing moves. `arm.reach_point` reaches
> the same points with no tool-orientation constraint, which is why the pen is
> mounted at an angle in step 2 — the mount, not the wrist, is what makes the
> tip vertical. Keep the `arm.move_to` default on an arm whose wrist can
> actually point down.

The adapter asks before it moves:

```
Fresh sheet taped down, pen capped off, hands clear of the arm — press Enter to start:
```

For an unattended run add `-E no_prompt=true` — **the arm then starts moving
with no confirmation**. The gate also skips itself when there is no TTY, rather
than hanging an overnight eval on a dead stdin.

The `castor bench sacpaint run` wrapper turns on artifacts and frame storage for you:

```bash
castor bench sacpaint run --embodiment opencastor --model anthropic/claude-fable-5 \
    -- -E pair_payload=/path/to/pair-payload.json -E calibration=canvas.json
```

---

### No paper or pen: the virtual easel (`-E medium=virtual`)

When the rig has an arm but nothing to draw with, the same body runs the whole
benchmark loop with the sheet replaced by telemetry. The arm makes every motion
for real through the gateway (signed receipts and all); after each pen-down
move the adapter asks `arm.state` where the tip actually is and inks the
segment on a canonical canvas. That canvas is the `overhead` frame (marked
`canonical_canvas`, nothing to rectify), and every score is labelled
`medium=virtual`: its own leaderboard category, never ranked against a mark on paper.

```bash
castor bench sacpaint run --policy sacpaint_trace --embodiment opencastor --no-rerun --no-prompt \
  -- -E pair_payload=/home/craigm26/bob/pair-payload.json \
     -E medium=virtual -E calibration=easel \
     -E move_tool=arm.reach_point -E move_args=reach_point \
     -E tolerance_mm=5 -E strict_reach=false
```

`calibration=easel` is a sheet that is not there, so it is refused with a real
pen. Where it stands was measured on Bob on 2026-09-09: with his calibrated
joint limits the tip reaches a thin shell roughly 300–370 mm from the base,
which no flat sheet on the desk fits inside, but an upright 150 × 200 mm sheet
325 mm straight ahead, centred at base height, does (every point within 0.5 mm
of a reachable pose; 8 of 9 probe points reached within 5 mm, the ninth at
5.9 mm). Move it with `-E easel_distance_mm`, `-E easel_elevation_deg` (the
sheet leans back with it) and `-E easel_azimuth_deg`.

Two things this mode changed in the driver, both live on Bob and in
`so-arm101-actuator` main: `arm.reach_point` now warm-starts from a table of
the safe envelope, waits for the joints to settle before measuring, and
compensates the servos' static error, because before that 0 of 27 reachable
points arrived. A miss now walks the arm back to the closest point it measured
and reports the error history; with `strict_reach=false` the adapter inks to
that measured point and carries on (the observation's `misses` counts them).

### The iPhone as the overhead camera (OpenCastor iOS build 76, Eval mode)

Open the OpenCastor app, pick the robot, open **Eval**, point the rear camera
at the sheet, tap **Stream**, then **Mark corners** (TL, TR, BR, BL). The app
posts JPEG frames and the corners to the robot console; the embodiment polls
them. Nothing is written to disk on the Pi; frames live in memory, one per
stream. The console URL is the robot's console port (Bob: 8002) and the token
is the read-only `CONSOLE_TOKEN` from `~/bob/tokens.env`.

| Purpose | URL |
|---|---|
| latest overhead frame (404 when absent or older than `max_age_s`) | `GET /eval/frame/latest?stream=overhead&max_age_s=3` |
| tapped corners, normalized 0..1 against the posted JPEG, TL TR BR BL | `GET /eval/corners?stream=overhead` |
| operator speech or typed lines (evidence, not commands) | `GET /eval/feedback?since=0` |
| the reference the app shows the operator | `GET /eval/reference.png` |
| one-poll summary, episode reset | `GET /eval/status`, `POST /eval/reset` |

So the two flags are `-E overhead_url="http://<robot>:8002/eval/frame/latest?stream=overhead&max_age_s=3"`
and `-E corners_url="http://<robot>:8002/eval/corners?stream=overhead"`, with
`-E camera_token_env=CONSOLE_TOKEN`. Full endpoint doc: `opencastor-runtime/docs/eval-eyes.md`.

### Every `-E` flag

### Gateway

| Flag | Default | What it does |
|---|---|---|
| `pair_payload` | — | Path to a `pair-payload.json`. **The shortcut:** fills `gateway_url`, `manifest_path`, the actuate bearer, the console URL and the console token from one file. Everything below overrides it. |
| `gateway_url` | `http://127.0.0.1:8080` | Gateway base URL. `/v1/invoke` is appended. |
| `manifest_path` | — | **Required.** Absolute path of `ROBOT.md` *on the robot*, e.g. `/home/you/bob/ROBOT.md`. The client sends no key id; the gateway verifies this file's signature and echoes back the kid it verified. |
| `manifest_kid` | — | Assert the gateway verified this key id. A mismatch aborts before the receipts could attest to a different manifest than the run claims. |
| `token` | — | Actuate-tier bearer. Prefer `token_env` or `pair_payload`; a token on a command line lands in your shell history. |
| `token_env` | `ROBOT_MD_TOKEN` | Environment variable to read the bearer from. |
| `ruri` | `rcan://demo.local/bob` | RCAN resource id. Also settable as `$ROBOT_MD_RURI`. |
| `actuator_name` | `so-arm101` | Which actuator on a multi-actuator gateway. Omitting it on Bob risks a `422 actuator_name_required`. |
| `timeout_s` | `90` | Per-invoke HTTP timeout. A long stroke at low speed needs headroom. |
| `move_tool` | `arm.move_to` | The cartesian tool to call. **Use `arm.reach_point` on an SO-ARM101** — see step 4. |
| `move_args` | `move_to` | Argument spelling: `move_to` sends `{x_mm, y_mm, z_mm, speed?}`; `reach_point` sends `{target_mm: [x,y,z], tolerance_mm}`. Must match `move_tool`. |
| `state_tool` | `arm.state` | Tool asked for the tip position at reset; returns `{joint_positions_rad, eef_mm, tool}` under scope `OBSERVE`. Set `-E state_tool=` (empty) on a gateway that has none — the adapter then uses the commanded pose. |
| `home_tool` | `arm.home` | Tool called by `reset()`. |
| `speed` | — | Passed through to `arm.move_to` when set; omitted entirely when not. |
| `tolerance_mm` | `3.0` | Arrival tolerance, `move_args=reach_point` only. |
| `strict_reach` | `true` | Halt when the arm reports `reached: false`. Set `false` to score runs whose targets were missed — the pen's position is then not what the transcript says it is. |

### Geometry

| Flag | Default | What it does |
|---|---|---|
| `calibration` | — | **Required.** Path to the `canvas.json` from step 3, or `easel` (virtual medium only). |
| `medium` | `pen` | `pen` (a pen on a photographed sheet) or `virtual` (no paper: the canvas is inked from the arm's measured tip; see above). |
| `easel_distance_mm` / `easel_elevation_deg` / `easel_azimuth_deg` | `325` / `0` / `0` | Where the virtual easel stands: distance from the base to the sheet's centre, its elevation above the base plane, its bearing left of straight ahead. |
| `reference` | `sacramento-photo-v1` | Which reference to serve on the `reference` camera. Match the task. |
| `pen_down_z` | `0.002` | Canvas-frame height at or below which the pen marks (metres). |
| `travel_z` | `0.005` | Height that travels without marking. Must be above `pen_down_z`. |
| `park_x`, `park_y` | `0.0`, `0.0` | Where `observe_parked()` parks, in canvas metres. Move it if the arm blocks the camera's view of the sheet there. |
| `park_z` | `0.03` | How far the pen lifts for the final, scored photograph. |

### Cameras

| Flag | Default | What it does |
|---|---|---|
| `overhead_url` | `<console_url>/camera/overhead/snapshot` | Any HTTP endpoint returning the latest JPEG or PNG. A JSON reply is accepted too: the adapter reads `image_b64` (or a `data:` URI), or follows a single `image`/`url` field. |
| `overhead_prime_url` | — | Fetched and discarded immediately before each frame, for rigs whose latest-frame file is only written as a side effect. The carbot phone bridge needs `-E overhead_prime_url=http://<pi>:8100/look -E overhead_url=http://<pi>:8100/snapshot.jpg`. |
| `camera_token` | — | Bearer for the camera endpoint (Bob's console wants `CONSOLE_TOKEN`). |
| `camera_token_env` | `CONSOLE_TOKEN` | Environment variable to read it from. |
| `camera_timeout_s` | `5.0` | Per-frame HTTP timeout. |
| `corners_url` | — | Where the iOS app posts the four tapped sheet corners. Fetched once per trial and attached to every observation as `extra["canvas_corners"]`, which is the scorer's first and best rectification route. |
| `canvas_corners` | — | The same four corners typed in: `-E canvas_corners=x,y,x,y,x,y,x,y`, normalised 0..1, order **TL TR BR BL**. Use it when there is no corner service. |

### Operator and logs

| Flag | Default | What it does |
|---|---|---|
| `no_prompt` | `false` | Skip the readiness gate. The arm moves with no confirmation. |
| `receipts_dir` | `logs/receipts` | Where `close()` writes the receipts if no trial hook ever fires (see below). |

---

### What a run leaves on disk

```
logs/
 ├── sacpaint-line-v0_<id>.json          # the EvalLog: config, git rev, package versions,
 │                                       #   per-scorer scores, and the full transcript
 ├── frames/<run>/                       # what the model saw each step (with --store-frames)
 ├── actions/<run>.jsonl                 # the executed action sequence
 └── receipts/
     └── opencastor-<timestamp>.jsonl    # one signed gateway receipt per motion
canvas.json                              # your calibration, unchanged by the run
```

Each line of the receipts file is one gateway call — allowed or denied — with
the tool, the arguments actually sent, the `msg_id` (the gateway's replay key),
the HTTP status, and the gateway's whole response including
`envelope_signature` and `outcome`. **The bearer is never written.** Verify one
with the gateway's own `scripts/verify_receipt.py`.

A note on where that file lands: the Inspect Robots core offers `on_trial_start`
/ `on_trial_end` to *policies*, not to embodiments, and `TaskEnvelope` carries
no log directory. The adapter implements both hooks anyway — a wrapper or a
future core that calls them gets receipts written to
`<log_dir>/receipts/<run_id>/<scene>-epoch<n>.jsonl`, beside the eval log. Until
then `close()` is the guarantee, writing to `receipts_dir`. Either way,
`embodiment.receipts` holds every call in memory for the life of the run.

To publish:

```bash
castor bench sacpaint export logs --out submission --label opus
```

---

### When it goes wrong

Every failure names the URL or the tool and tells you the fix. The ones you will
actually hit:

| Message | Meaning |
|---|---|
| `gateway denied 'arm.move_to': tool_allowlist` | The tool is not in the gateway's operator allowlist. Add it to `ROBOT_MD_TOOL_ALLOWLIST` **and** `ROBOT_MD_TOOL_MIN_TIER` in the gateway's policy env and restart the gateway. Nothing moved. |
| `gateway denied ...: tier_policy` | An unknown or missing bearer degrades to the `anon` tier, which may not actuate. Check `-E token_env`. |
| `gateway denied ...: safety_state` | The software stop is engaged. Clear it at the console. |
| `gateway denied ...: actuator_policy/unsafe_pose` | The pose needed to satisfy the tool-orientation constraint is outside the arm's safe joint range. On an SO-ARM101 this denies **every** `arm.move_to` target: use `-E move_tool=arm.reach_point -E move_args=reach_point` and angle the pen mount. |
| `gateway denied ...: actuator_policy/unreachable` (or `out_of_workspace`) | The arm's links cannot span to that point. Move the sheet closer to the base and re-teach the calibration. Nothing moved — the driver clamps nothing. |
| `gateway denied ...: actuator_policy/joint_limits` | Reaching that point needs a joint past its safe range. Move or rotate the sheet. |
| `the gateway at ... has no 'arm.move_to' (404)` | An older gateway. Use `-E move_tool=arm.reach_point -E move_args=reach_point`. |
| `the driver failed 'arm.move_to': OutOfRangeError ...` | An unsigned 500: the arm's position is **unknown**. Check the robot before re-running. Reachability now normally arrives as a signed `unreachable` deny instead. |
| `the arm reports it did not reach ...` | The move completed but missed. Every later stroke would start from somewhere unknown, so the run halts. |
| `camera 'overhead' at ... returned HTTP 503` | The camera is cold or absent. Start it. A missing frame is not a blank canvas, and the adapter refuses to score one as if it were. |
| `camera 'overhead' at ... is unreachable` | Wrong host or port, or the console is not running. |
| `no canvas calibration` | Step 3. |

`SafetyAbort` (a deny) and `EmbodimentFault` (a camera or driver failure) both
halt the whole eval, by design: a faulted or refused robot must never
auto-advance to the next sheet unattended.

### What this body deliberately does not do

- **It never sets `canonical_canvas`.** A photograph of a sheet is not a
  canonical canvas, and claiming otherwise would skip the rectification that
  makes the score comparable to the mock plotter's.
- **It never moves on `close()`.** An adapter that moves after the operator
  thinks the run is over is an adapter nobody can stand next to.
- **It clamps every target to the canvas box before the transform**, so a model
  asking for a point 9 metres off the sheet gets the sheet's edge, not a
  gateway deny and a dead run.

## Running on a Claude subscription (no API key)

`inspect-robots run --policy agent` talks to an LLM over HTTP. Normally that
means a metered `ANTHROPIC_API_KEY`. On a machine that has the Claude Code CLI
logged in but no API credits, `castor bench sacpaint shim` stands in: it serves an
OpenAI Chat Completions endpoint on localhost and answers each request with one
`claude -p` invocation, so the owner's own subscription drives the robot.

> [!WARNING]
> **Results from the shim are not comparable to API results.** Every answer is
> produced inside Claude Code's own harness and system prompt, not by the raw
> Messages API. Responses are labelled `wire=claude-code-cli` for exactly this
> reason. Do not put a shim run on a leaderboard, do not compare its score with
> an API run, and do not cite it as a model capability measurement. This is a
> development and personal-use path on the owner's own subscription.

### The one-flag way

```bash
castor bench sacpaint run --subscription --model haiku --policy agent --embodiment sacpaint_plotter --max-llm-calls 40
```

`castor bench sacpaint run --subscription` starts the shim on a free port, waits for its
health check, points the agent policy at it, labels every score artifact with
`wire: claude-code-cli`, and stops the shim when the run ends. Everything below
is what that flag does by hand.

### Install

`pip install "opencastor[paintbench]"` installs the `castor bench sacpaint shim` console script;
`python -m sacpaint.claude_shim` works identically.

### Start it

```bash
castor bench sacpaint shim --port 8931 --model haiku \
    --claude-bin ~/.local/bin/claude --max-turns 6
```

`--claude-bin` matters when `PATH` is odd; pass the full path. Check it is up:

```bash
curl -s http://127.0.0.1:8931/healthz
# {"status": "ok", "model": "haiku", "calls": 0, "wire": "claude-code-cli"}
```

### Point the policy at it

```bash
export SACPAINT_SHIM_KEY=unused   # the shim ignores it; the flag needs a name

inspect-robots run --task sacpaint/photo-v1 --policy agent \
    -P base_url=http://127.0.0.1:8931/v1 \
    -P api_key_env=SACPAINT_SHIM_KEY \
    -P model=haiku \
    -P images=on_demand \
    -P max_llm_calls=6 \
    --embodiment sacpaint_plotter --no-rerun --no-prompt \
    -T epochs=1 -T max_steps=300
```

`-P base_url=` is the first rung of the plugin's provider ladder, so no
provider key is consulted at all. `-P api_key_env=` names a variable the shim
does not check; point it at anything.

**Always set `-P max_llm_calls=`.** Each call spends subscription usage, and
the plugin's default is 100. A handful is enough to prove a rig.

### Verified run

That exact command was run on 2026-09-08 against `claude` 2.1.263 on a Pi with
no `ANTHROPIC_API_KEY`:

```
run status: completed
outcome: gave up            # LLM call budget exhausted, as configured
scenes: 1  trials: 1
  composite: 0
  discipline: 0
  efficiency: 0.8133
  landmark_geometry: 0
  structure: 0
log: .../shimlogs-sacpaint/sacpaint-line-v0_287b8911.json
```

Six shim calls, six valid tool calls, zero malformed replies, zero shim errors,
`llm_usage.llm_calls = 6`, `errored_trials = 0`. The model called `take_pic`
first, read the reference and overhead frames, and drove five `move_to` motions
that executed on the plotter (19, 16, 15 and 5 interpolated steps), lowering the
pen to `z=0.002` to draw the top horizontal line it had seen in the reference.

**`composite: 0` is the honest result of a six-call budget, not a shim fault.**
Six model turns cannot draw a skyline; the rollout stopped at step 55 of 300
with barely any ink down, so every geometry scorer reads zero. `efficiency`
is nonzero only because the trial used few steps. The run proves the transport,
not the model. Raise `max_llm_calls` for a scoring run and expect it to cost
accordingly.

### What the shim actually does

One HTTP request becomes one `claude -p`. Nothing is cached or carried between
requests: `inspect-robots` resends the whole conversation every turn, which is
what makes a stateless one-shot CLI call a faithful stand-in for the API.

| Wire concept | How the shim carries it |
|---|---|
| `system` messages | `claude --system-prompt` (replaces Claude Code's prompt) |
| conversation history | re-rendered into the prompt on stdin, every request |
| assistant `tool_calls` | replayed as `you called <tool> with arguments {...}` |
| `tool` results | replayed as `### TOOL RESULT (for call id ...)` |
| `tools` | described in prose **and** pinned by `claude --json-schema` |
| model's tool call | `{"tool": name, "arguments": {...}}` parsed back into `tool_calls` |
| images | written to a scratch file, read by the CLI's `Read` tool |
| usage | the CLI's `usage` block mapped to OpenAI token counters |

The prompt goes in on **stdin**, not argv: a long conversation would blow past
the argv size cap.

### Images do work

An image cannot ride inside a `claude -p` prompt. The shim decodes each
`image_url` data URL, writes it to a per-request scratch directory, and refers
to it by absolute path:

```
[image #1 saved at /tmp/sacpaint-shim-xxxx/ab12/frame_001.png
 -- call the Read tool on that exact path to see it]
```

The CLI is launched with `--allowedTools Read` and `--add-dir <scratch>` only
when the request carries an image, so the model can open it and nothing else.
This was verified end to end: the model read a real reference frame and moved
the pen toward the top line it saw. Frames are deleted after each request
unless `--keep-frames` is passed.

The cost is turns. A text-only request answers in one CLI turn; an image
request spends one turn on `Read` first, which is why `--max-turns` defaults to
6 rather than 1.

### Tool calls are never executed

The shim asks the model to *name* a tool and returns that name to
`inspect-robots`, which executes it and sends the result back — exactly as a
real API does. The shim has no robot access and runs nothing. `--restricted`
strips Bash and every other code-running tool from the CLI session, so a
prompt-injected instruction in an observation cannot reach a shell.

### Tolerant parsing, on purpose

`--json-schema` pins the answer to `{"tool": ..., "arguments": {...}}` and the
CLI returns it pre-parsed in `structured_output`. The parser still accepts
`name` instead of `tool`, `input`/`parameters` instead of `arguments`, a
stringified arguments object, a bare `{"done": {...}}`, and a JSON object
embedded in prose or a code fence. A step lost to a spelling difference is a
wasted robot turn. A tool name that is not in the request's tool list is
**not** smuggled through: the turn comes back as plain text and the policy
nudges, which is the safe failure.

### Cost

Every request re-sends the whole conversation and pays for Claude Code's
harness prompt again — about 10k cache-creation tokens per call even with
`--restricted`, which roughly halves it. Observed on a real image-bearing
request: 20,355 prompt tokens (9,699 cached) and 897 completion tokens.

The shim reports these back in the response's `usage` block. Note that the
plugin's `chat` wire records `llm_calls` only and drops per-token counts —
that is an `inspect-robots-agent` limitation, not a shim one. Read the shim's
own log for token counts.

### Limits and gotchas

- **The CLI exits non-zero while still printing a good answer.** Seen live: a
  complete envelope with `stop_reason: end_turn` and 3,115 output tokens
  alongside exit code 1. The shim parses stdout *before* judging the exit code
  for exactly this reason. An earlier version trusted the exit code, turned a
  paid call into a 502, and the policy's 5xx retry then spent a second one.
  Never reintroduce an exit-code-first check here.
- **`--restricted` is load-bearing.** It drops the code-running tools and cuts
  the harness prompt. Removing it roughly doubles the per-call token cost.
- **No streaming.** The endpoint answers only when the CLI process exits.
  Latency per step is the CLI's full round trip, several seconds at least.
- **One CLI process per request**, so throughput is low. Fine for a single
  rollout; do not point a sweep at it.
- **`--model` takes CLI aliases** (`haiku`, `sonnet`, `opus`, `fable`) or a
  full model id. A request's own `model` field wins over the server default,
  so `-P model=haiku` selects the model per run.
- **The shim strips `ANTHROPIC_API_KEY`** from the CLI's environment. The
  point is the subscription; a stray key would silently meter the run.
- **Bind address stays `127.0.0.1`.** There is no authentication: anything that
  can reach the port can spend the subscription.
- **`inspect-robots` logs record `wire=chat`**, because that is the wire the
  plugin spoke. The `claude-code-cli` label lives in the shim's own responses
  and in this document. When you save a result, write the label down yourself.

### Anthropic Messages endpoint

`POST /v1/messages` is served too, for callers that need `-P wire=messages`:
`system` (string or blocks), `tool_use`/`tool_result` blocks, and native
`input_schema` tool declarations are all understood, and the reply carries
`tool_use` blocks with Anthropic-shaped `usage`. Note the plugin's Messages
wire also wants `-P max_output_tokens=`, which the CLI ignores.

### Tests

`tests/test_claude_shim.py` covers both directions of the translation against a
**fake** `claude` binary — a script that records its argv and stdin and echoes
canned JSON. The real CLI is never invoked by the suite: it costs subscription
usage, needs a login and network, and its latency would make the tests
useless.

```bash
pytest tests/test_claude_shim.py
```
