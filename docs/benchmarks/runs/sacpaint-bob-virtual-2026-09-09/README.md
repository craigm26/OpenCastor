# Sacramento PaintBench submission: bob-virtual-oracle

Benchmark `sacpaint` reference `sacramento-photo-v1` (photo; sha256 `f4e9648527d0e8b2d5d8cfbf007c4fad313afd8e60d2e84af4eb2931c2198b8d`, scoring ink sha256 `ff0127c5ffc1a71aff9c26db088cf733dfc497e126981477fa71831018af719f`).
Layout follows robocurve's published run datasets (clapboardbench): raw EvalLogs, one markdown page per run,
self-contained HTML reports from `inspect-robots view`, videos from `inspect-robots video` when ffmpeg is present,
and the rectified final canvas the scorers read for every trial.

| What | Where |
|---|---|
| EvalLog (config, results, transcript) | `*.json` |
| Run pages (start here) | [`runs/`](runs/README.md) |
| HTML reports | `html/index.html` |
| Final canvases + score breakdowns | `canvases/` |
| Videos | `videos/` |
| Reference (what the model saw), its hash, the scoring ink, the rubric | `reference.webp`, `reference.sha256`, `reference-ink.png`, `rubric.json` |

Scores are recomputable offline from `canvases/*.png` with `sacpaint score --canonical`.
