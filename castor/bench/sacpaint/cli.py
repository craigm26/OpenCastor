"""The ``sacpaint`` command: score a photo, start a new reference, run, export.

- ``sacpaint score PHOTO``: grade any photo of a finished canvas, no robot, no fixture.
- ``sacpaint new NAME``: write a reference spec you can edit; it becomes ``sacpaint/NAME``.
- ``sacpaint run ...``: ``inspect-robots run`` with the benchmark's defaults and artifacts on.
- ``sacpaint export LOGDIR``: a publishable bundle in the layout robocurve uses.
- ``sacpaint worldevals-entry``: the catalog block for a WorldEvals pull request.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from castor.bench.sacpaint import reference as refmod
from castor.bench.sacpaint.reference import (
    DEFAULT_REFERENCE,
    SPEC_SUFFIX,
    ReferenceSpec,
    get_spec,
    reference_ink,
    user_reference_dir,
)


def _load_rgb(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"cannot read image {path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _save_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def _parse_corners(text: str | None) -> list[list[float]] | None:
    if not text:
        return None
    nums = [float(v) for v in text.replace(";", " ").replace(",", " ").split()]
    if len(nums) != 8:
        raise SystemExit("--corners needs eight numbers: x,y for TL TR BR BL (pixels or 0..1)")
    return [[nums[i], nums[i + 1]] for i in range(0, 8, 2)]


def overlay(canvas: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """The reference ink drawn in red over the rectified canvas: what the scorer compared."""
    from castor.bench.sacpaint.scorers import ink_mask

    out = canvas.copy()
    out[ink_mask(ref)] = (220, 30, 30)
    return out


# --- score ---------------------------------------------------------------------


def cmd_score(args: argparse.Namespace) -> int:
    from castor.bench.sacpaint.rectify import find_canvas_corners, rectify
    from castor.bench.sacpaint.scorers import score_canvas

    spec = get_spec(args.reference)
    photo = _load_rgb(args.photo)
    corners = _parse_corners(args.corners)
    if args.canonical:
        w, h = spec.canonical_size()
        canvas, how = cv2.resize(photo, (w, h), interpolation=cv2.INTER_AREA), "canonical"
    else:
        _, how = find_canvas_corners(photo, corners)
        canvas = rectify(photo, corners=corners, size=spec.canonical_size())
    result = score_canvas(canvas, spec)
    result["photo"] = os.path.abspath(args.photo)
    result["corners_from"] = how
    out = Path(args.out) if args.out else Path(args.photo).with_suffix("")
    _save_rgb(out.with_name(out.name + "-canvas.png"), canvas)
    _save_rgb(out.with_name(out.name + "-overlay.png"), overlay(canvas, reference_ink(spec.name)))
    out.with_name(out.name + "-score.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    print(f"reference   {spec.name}")
    print(f"corners     {how}")
    print(f"composite   {result['composite_photo']:.3f}   (image-only: no efficiency term)")
    for k, v in result["parts"].items():
        print(f"  {k:18s} {v:.3f}")
    print("landmarks")
    for name, lm in result["landmark_geometry"]["landmarks"].items():
        print(f"  {name:14s} presence {lm['presence']:.2f}  position {lm['position']:.2f}")
    print(f"wrote       {out.name}-canvas.png, {out.name}-overlay.png, {out.name}-score.json")
    return 0


# --- new ------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> int:
    name = args.name
    if not name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("name must be letters, digits, - or _")
    dest = user_reference_dir() / f"{name}{SPEC_SUFFIX}"
    if dest.exists() and not args.force:
        raise SystemExit(f"{dest} exists; pass --force to overwrite")
    base = get_spec(args.from_reference)
    if args.canvas:
        w, h = (float(v) for v in args.canvas.lower().split("x"))
    else:
        w, h = base.canvas_mm
    spec = ReferenceSpec.from_dict(base.to_dict(), base_dir=dest.parent)
    spec.name = name
    spec.photo, spec.photo_credit = (
        None,
        "",
    )  # the base's photograph is its own; pass --photo for yours
    if args.photo:
        src = Path(args.photo).expanduser()
        if not src.is_file():
            raise SystemExit(f"--photo {src} is not a file")
        dest.parent.mkdir(parents=True, exist_ok=True)
        photo_dest = dest.parent / f"{name}{src.suffix.lower()}"
        shutil.copyfile(src, photo_dest)
        spec.photo, spec.photo_credit = photo_dest.name, args.photo_credit
    if (w, h) != tuple(base.canvas_mm):
        # Scale the copied drawing onto the new sheet so it stays a valid starting point.
        sx, sy = w / base.canvas_mm[0], h / base.canvas_mm[1]
        spec.strokes = {
            k: [[(x * sx, y * sy) for x, y in st] for st in v] for k, v in spec.strokes.items()
        }
        for lm in spec.landmarks.values():
            if "bbox_mm" in lm:
                x0, y0, x1, y1 = lm["bbox_mm"]
                lm["bbox_mm"] = [x0 * sx, y0 * sy, x1 * sx, y1 * sy]
    spec.canvas_mm = (w, h)
    spec.description = (
        args.description or f"Copy of {base.name}; edit the strokes to make it yours."
    )
    spec.save(dest)
    refmod.refresh()
    _save_rgb(dest.with_suffix("").with_suffix(".png"), spec.render())
    print(f"wrote {dest}")
    print(f"preview {dest.with_suffix('').with_suffix('.png')}")
    if spec.photo:
        print(f"photo   {spec.photo_path()} (what the model sees; sha256 {spec.sha256()[:12]}...)")
        print("trace the photo's landmarks as strokes: that skeleton is what the scorers read.")
    print("edit the strokes (mm, origin bottom-left, y up) and landmarks, then:")
    print(f"  sacpaint preview {name}")
    print(
        f"  sacpaint run --task sacpaint/{name} --policy sacpaint_trace --embodiment sacpaint_plotter"
    )
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    spec = get_spec(args.reference)
    out = Path(args.out) if args.out else Path(f"{spec.name}.png")
    _save_rgb(out, spec.render())
    rub = spec.rubric()
    print(
        f"{spec.name} ({spec.kind}): {spec.canvas_mm[0]:.0f} x {spec.canvas_mm[1]:.0f} mm, {len(spec.strokes)} stroke groups, "
        f"{len(rub['landmarks'])} scored landmarks, sha256 {spec.sha256()[:12]}..., ink sha256 {spec.ink_sha256()[:12]}..."
    )
    if spec.photo:
        print(f"photo {spec.photo_path()}  (the model sees this; the PNG below is the scoring ink)")
    print(f"wrote {out}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from castor.bench.sacpaint.tasks import task_name_for

    for name in refmod.available():
        spec = get_spec(name)
        print(
            f"{task_name_for(name):26s} {spec.kind:5s} {spec.canvas_mm[0]:.0f}x{spec.canvas_mm[1]:.0f} mm  {spec.description}"
        )
    return 0


# --- run ----------------------------------------------------------------------


def _inspect_robots_bin() -> list[str]:
    candidate = Path(sys.executable).parent / "inspect-robots"
    if candidate.exists():
        return [str(candidate)]
    found = shutil.which("inspect-robots")
    if found:
        return [found]
    raise SystemExit("inspect-robots is not installed in this environment")


def build_run_command(
    args: argparse.Namespace, shim_port: int | None = None
) -> tuple[list[str], dict[str, str]]:
    """The inspect-robots command line and environment for ``sacpaint run`` (pure, for tests)."""
    log_dir = Path(args.log_dir)
    env = dict(os.environ, SACPAINT_ARTIFACTS=str(log_dir / "sacpaint-artifacts"))
    cmd = _inspect_robots_bin() + [
        "run",
        "--task",
        args.task,
        "--policy",
        args.policy,
        "--embodiment",
        args.embodiment,
        "--log-dir",
        str(log_dir),
        "--store-frames",
    ]
    if shim_port is not None:
        env["SACPAINT_SHIM_KEY"] = "unused"
        env["SACPAINT_WIRE_LABEL"] = "claude-code-cli"
        cmd += [
            "-P",
            f"base_url=http://127.0.0.1:{shim_port}/v1",
            "-P",
            "api_key_env=SACPAINT_SHIM_KEY",
        ]
        cmd += ["-P", f"model={args.model or 'haiku'}"]
    elif args.model:
        cmd += ["-P", f"model={args.model}"]
    if args.max_llm_calls:
        cmd += ["-P", f"max_llm_calls={args.max_llm_calls}"]
    if args.no_rerun:
        cmd.append("--no-rerun")
    if args.no_prompt:
        cmd.append("--no-prompt")
    cmd += args.extra
    return cmd, env


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_healthy(port: int, seconds: float = 15.0) -> bool:
    import time
    import urllib.request

    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - not up yet, keep polling
            time.sleep(0.25)
    return False


def cmd_run(args: argparse.Namespace) -> int:
    shim = None
    port = None
    if args.subscription:
        port = _free_port()
        shim_cmd = [
            sys.executable,
            "-m",
            "castor.bench.sacpaint.claude_shim",
            "--port",
            str(port),
            "--model",
            args.model or "haiku",
        ]
        if args.claude_bin:
            shim_cmd += ["--claude-bin", args.claude_bin]
        shim = subprocess.Popen(shim_cmd)
        if not _wait_healthy(port):
            shim.terminate()
            raise SystemExit(
                "the Claude subscription shim did not come up; is the claude CLI logged in? (see docs/subscription.md)"
            )
        print(
            f"subscription shim up on 127.0.0.1:{port}; scores will be labelled wire=claude-code-cli",
            flush=True,
        )
    cmd, env = build_run_command(args, port)
    print("$ " + " ".join(cmd), flush=True)
    try:
        code = subprocess.call(cmd, env=env)
    finally:
        if shim is not None:
            shim.terminate()
            try:
                shim.wait(timeout=10)
            except subprocess.TimeoutExpired:
                shim.kill()
    if code == 0:
        print(f"\nnext: sacpaint export {args.log_dir} --out {args.log_dir}-submission")
    return code


# --- export ---------------------------------------------------------------------


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "n/a"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _policy_notes(transcript: Any, limit: int = 60) -> list[str]:
    """Pull the per-move notes out of an agent transcript (best effort, format-tolerant)."""
    notes: list[str] = []
    if not isinstance(transcript, list):
        return notes
    for msg in transcript:
        if not isinstance(msg, dict):
            continue
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", call) if isinstance(call, dict) else {}
            name, raw = fn.get("name", "?"), fn.get("arguments", "")
            try:
                a = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                a = {}
            text = a.get("note") or a.get("summary") or a.get("reason") or ""
            notes.append(f"**{name}** {text}".strip())
        content = msg.get("content")
        if msg.get("role") == "assistant" and isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    inp = part.get("input") or {}
                    text = inp.get("note") or inp.get("summary") or inp.get("reason") or ""
                    notes.append(f"**{part.get('name', '?')}** {text}".strip())
        if len(notes) >= limit:
            notes.append("...")
            break
    return notes


def _artifacts_for(log: dict[str, Any], artifacts_dir: Path) -> list[Path]:
    """Artifact JSONs written while this log's run was in progress."""
    if not artifacts_dir.is_dir():
        return []
    from datetime import datetime, timezone

    def ts(s: str | None) -> float | None:
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()

    start, end = (
        ts(log.get("stats", {}).get("started_at")),
        ts(log.get("stats", {}).get("completed_at")),
    )
    out = []
    for p in sorted(artifacts_dir.glob("*.json")):
        m = p.stat().st_mtime
        if start is not None and end is not None and (start - 5) <= m <= (end + 60):
            out.append(p)
    return out


def _run_page(
    label: str, log: dict[str, Any], log_name: str, arts: list[dict[str, Any]], out_dir: Path
) -> str:
    ev, stats, res = log.get("eval", {}), log.get("stats", {}), log.get("results", {})
    sample = (log.get("samples") or [{}])[0]
    pc = ev.get("policy_config") or {}
    rows = [
        ("Created (UTC)", ev.get("created", "n/a")),
        ("Task", ev.get("task", "n/a")),
        ("Policy", ev.get("policy", "n/a")),
        ("Model", pc.get("model", "n/a")),
        ("Effort", pc.get("effort", "n/a")),
        ("Wire", pc.get("wire", "n/a")),
        ("Embodiment", ev.get("embodiment", "n/a")),
        ("Status", log.get("status", "n/a")),
        ("Termination", ", ".join(str(t) for t in sample.get("termination_reasons", [])) or "n/a"),
        ("Steps", stats.get("total_steps", "n/a")),
        ("Duration", _fmt_duration(stats.get("duration_s"))),
        ("Epochs", len(sample.get("epochs", []))),
        ("Seed", ev.get("seed", "n/a")),
        ("inspect-robots", ev.get("inspect_robots_version", "n/a")),
        ("Git commit", ev.get("git_commit") or "n/a"),
        ("Reference", (sample.get("scene_metadata") or {}).get("reference", ev.get("task"))),
    ]
    lines = [
        f"# Run {label}: {log_name}",
        "",
        f"> {sample.get('instruction', '')}",
        "",
        "| | |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += ["", "## Scores", "", "| scorer | mean |", "|---|--:|"]
    lines += [f"| {k} | {v:.3f} |" for k, v in (res.get("metrics") or {}).items()]
    if sample.get("epochs"):
        keys = list(sample["epochs"][0].keys())
        lines += ["", "| epoch | " + " | ".join(keys) + " |", "|--:|" + "--:|" * len(keys)]
        for i, e in enumerate(sample["epochs"]):
            lines.append(
                f"| {i} | " + " | ".join(f"{e.get(k, float('nan')):.3f}" for k in keys) + " |"
            )
    if arts:
        lines += ["", "## Final canvases", ""]
        for a in arts:
            png = a.get("_png")
            lines.append(
                f"### {a.get('scene_id')} epoch {a.get('epoch')}: composite {a.get('composite', 0):.3f}"
            )
            if png:
                lines.append(f"![final canvas]({png})")
            d = a.get("details", {}).get("landmark_geometry", {}).get("landmarks", {})
            if d:
                lines += ["", "| landmark | presence | position | score |", "|---|--:|--:|--:|"]
                lines += [
                    f"| {n} | {v['presence']:.2f} | {v['position']:.2f} | {v['score']:.2f} |"
                    for n, v in d.items()
                ]
            lines.append("")
    notes = []
    for t in sample.get("policy_transcripts") or []:
        notes = _policy_notes(t)
        if notes:
            break
    if notes:
        lines += ["## Model notes (one line per tool call)", ""] + [f"- {n}" for n in notes]
    lines += [
        "",
        f"Files: [raw EvalLog](../{log_name}) · [HTML report](../html/{Path(log_name).stem}.html)",
        "",
    ]
    return "\n".join(lines)


def cmd_export(args: argparse.Namespace) -> int:
    log_dir = Path(args.log_dir)
    out = Path(args.out)
    logs = sorted(log_dir.glob("*.json"))
    if not logs:
        raise SystemExit(f"no EvalLog *.json in {log_dir}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "runs").mkdir(exist_ok=True)
    (out / "canvases").mkdir(exist_ok=True)
    artifacts_dir = log_dir / "sacpaint-artifacts"
    index = [
        "# Runs",
        "",
        "| Run | Model | Policy | Embodiment | Status | composite | Steps | Duration | Log |",
        "|---|---|---|---|---|--:|--:|--:|---|",
    ]
    for i, path in enumerate(logs, 1):
        log = json.loads(path.read_text())
        label = f"{args.label}-{i}" if args.label else path.stem
        shutil.copy2(path, out / path.name)
        arts = []
        for a in _artifacts_for(log, artifacts_dir):
            d = json.loads(a.read_text())
            png = a.with_suffix(".png")
            if png.exists():
                dest = out / "canvases" / f"{label}-{png.name}"
                shutil.copy2(png, dest)
                d["_png"] = f"../canvases/{dest.name}"
            arts.append(d)
        (out / "runs" / f"{label}.md").write_text(_run_page(label, log, path.name, arts, out))
        ev, res, stats = log.get("eval", {}), log.get("results", {}), log.get("stats", {})
        comp = (res.get("metrics") or {}).get("composite")
        index.append(
            f"| [{label}](runs/{label}.md) | {(ev.get('policy_config') or {}).get('model', 'n/a')} | {ev.get('policy')} | "
            f"{ev.get('embodiment')} | {log.get('status')} | {comp if comp is None else f'{comp:.3f}'} | "
            f"{stats.get('total_steps', 'n/a')} | {_fmt_duration(stats.get('duration_s'))} | [json]({path.name}) |"
        )
    # HTML reports and videos through the framework's own renderers.
    ir = _inspect_robots_bin()
    subprocess.call(
        ir + ["view", str(log_dir), "--no-frames"]
        if args.no_frames
        else ir + ["view", str(log_dir)]
    )
    html_src = log_dir / "html"
    if html_src.is_dir():
        shutil.copytree(html_src, out / "html", dirs_exist_ok=True)
    if shutil.which("ffmpeg") and not args.no_video:
        for path in logs:
            subprocess.call(ir + ["video", str(path)])
        vid = log_dir / "videos"
        if vid.is_dir():
            shutil.copytree(vid, out / "videos", dirs_exist_ok=True)
    spec = get_spec(args.reference)
    ink_png = cv2.imencode(
        ".png", cv2.cvtColor(refmod.reference_ink(spec.name), cv2.COLOR_RGB2BGR)
    )[1].tobytes()
    (out / "reference-ink.png").write_bytes(ink_png)
    ref_file = "reference-ink.png"
    if spec.photo:
        ref_file = f"reference{spec.photo_path().suffix.lower()}"
        (out / ref_file).write_bytes(spec.photo_bytes())
    (out / "reference.sha256").write_text(refmod.reference_sha256(spec.name) + "\n")
    (out / "rubric.json").write_text(json.dumps(spec.rubric(), indent=2, sort_keys=True) + "\n")
    readme = [
        f"# Sacramento PaintBench submission: {args.label or log_dir.name}",
        "",
        (
            f"Benchmark `sacpaint` reference `{spec.name}` ({spec.kind}; sha256 `{refmod.reference_sha256(spec.name)}`, "
            f"scoring ink sha256 `{refmod.ink_sha256(spec.name)}`)."
        ),
        "Layout follows robocurve's published run datasets (clapboardbench): raw EvalLogs, one markdown page per run,",
        "self-contained HTML reports from `inspect-robots view`, videos from `inspect-robots video` when ffmpeg is present,",
        "and the rectified final canvas the scorers read for every trial.",
        "",
        "| What | Where |",
        "|---|---|",
        "| EvalLog (config, results, transcript) | `*.json` |",
        "| Run pages (start here) | [`runs/`](runs/README.md) |",
        "| HTML reports | `html/index.html` |",
        "| Final canvases + score breakdowns | `canvases/` |",
        "| Videos | `videos/` |",
        f"| Reference (what the model saw), its hash, the scoring ink, the rubric | `{ref_file}`, `reference.sha256`, `reference-ink.png`, `rubric.json` |",
        "",
        "Scores are recomputable offline from `canvases/*.png` with `sacpaint score --canonical`.",
        "",
    ]
    (out / "README.md").write_text("\n".join(readme))
    (out / "runs" / "README.md").write_text("\n".join(index) + f"\n\n{len(logs)} runs.\n")
    print(f"exported {len(logs)} run(s) to {out}")
    return 0


def cmd_worldevals_entry(args: argparse.Namespace) -> int:
    from castor.bench.sacpaint.tasks import task_name_for

    names = ", ".join(f'"{task_name_for(n)}"' for n in refmod.available())
    print(f"""Benchmark(
    name="sacpaint",
    title="Sacramento PaintBench",
    description=(
        "Draw a fixed photograph of Sacramento (Tower Bridge over the Capitol dome) with a pen "
        "from camera feedback; scored offline against its traced landmarks: geometry, structure, discipline. "
        "Every score carries a medium (pen on paper, or virtual: a real arm inking from its own telemetry); "
        "each medium is ranked as its own category."
    ),
    repo="https://github.com/craigm26/OpenCastor",
    install="pip install sacpaint",  # a pointer package that depends on opencastor[paintbench]
    task_keys=({names},),
    tags=("drawing", "single-arm", "visual-feedback", "manipulation", "media-categories"),
    bimanual=False,
    contributors=("craigm26",),
    status="alpha",
),""")
    print("\n# Add this to src/worldevals/catalog.py in a fork of github.com/robocurve/worldevals,")
    print(
        "# run `uv run pytest`, and open a pull request. Attach a `sacpaint export` bundle of one real run"
    )
    print("# (a medium=virtual run counts: it is a real arm, ranked in its own category).")
    return 0


# --- main ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="castor bench sacpaint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("score", help="grade a photo of a finished canvas (no robot, no fixture)")
    s.add_argument("photo")
    s.add_argument("--reference", default=DEFAULT_REFERENCE)
    s.add_argument(
        "--corners", help="TL TR BR BL as x,y pairs, pixels or 0..1; otherwise auto-detected"
    )
    s.add_argument(
        "--canonical",
        action="store_true",
        help="the image is already the canonical canvas; skip rectification",
    )
    s.add_argument("--out", help="output stem (default: next to the photo)")
    s.set_defaults(fn=cmd_score)

    n = sub.add_parser("new", help="start a new reference spec (becomes task sacpaint/NAME)")
    n.add_argument("name")
    n.add_argument(
        "--from-reference", default=DEFAULT_REFERENCE, help="spec to copy as a starting point"
    )
    n.add_argument("--canvas", help="WxH in mm, e.g. 210x297")
    n.add_argument("--description", default="")
    n.add_argument(
        "--photo",
        help="a photograph the model will see instead of the strokes (copied next to the spec)",
    )
    n.add_argument(
        "--photo-credit", default="", help="attribution recorded in the spec and every EvalLog"
    )
    n.add_argument("--force", action="store_true")
    n.set_defaults(fn=cmd_new)

    v = sub.add_parser("preview", help="render a reference to PNG and print its identity")
    v.add_argument("reference", nargs="?", default=DEFAULT_REFERENCE)
    v.add_argument("--out")
    v.set_defaults(fn=cmd_preview)

    ls = sub.add_parser("list", help="references and the task names they run under")
    ls.set_defaults(fn=cmd_list)

    r = sub.add_parser(
        "run", help="inspect-robots run with the benchmark's defaults (artifacts + frames on)"
    )
    r.add_argument("--task", default="sacpaint/photo-v1")
    r.add_argument("--policy", default="agent")
    r.add_argument("--embodiment", default="sacpaint_plotter")
    r.add_argument(
        "--model",
        help="passed as -P model=... (with --subscription: a claude CLI alias such as haiku, sonnet, opus)",
    )
    r.add_argument(
        "--subscription",
        action="store_true",
        help="drive the agent through the Claude subscription shim instead of an API key",
    )
    r.add_argument(
        "--claude-bin",
        help="path to the claude CLI for --subscription (default: whatever is on PATH)",
    )
    r.add_argument(
        "--max-llm-calls",
        type=int,
        help="passed as -P max_llm_calls=N (caps model calls per trial)",
    )
    r.add_argument("--log-dir", default="logs")
    r.add_argument("--no-rerun", action="store_true")
    r.add_argument("--no-prompt", action="store_true")
    r.add_argument(
        "extra", nargs=argparse.REMAINDER, help="anything after -- goes to inspect-robots run"
    )
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("export", help="publishable bundle from a log dir (robocurve layout)")
    e.add_argument("log_dir")
    e.add_argument("--out", required=True)
    e.add_argument("--label", help="run label prefix, e.g. opus")
    e.add_argument("--reference", default=DEFAULT_REFERENCE)
    e.add_argument("--no-video", action="store_true")
    e.add_argument("--no-frames", action="store_true", help="render HTML without embedded frames")
    e.set_defaults(fn=cmd_export)

    w = sub.add_parser("worldevals-entry", help="print the WorldEvals catalog entry")
    w.set_defaults(fn=cmd_worldevals_entry)

    c = sub.add_parser(
        "calibrate",
        help="teach the canvas-to-arm transform from the sheet corners (OpenCastor body)",
        add_help=False,
    )
    c.add_argument("calibrate_args", nargs=argparse.REMAINDER)
    c.set_defaults(
        fn=lambda a: __import__("castor.bench.sacpaint.calibrate", fromlist=["main"]).main(
            a.calibrate_args
        )
    )

    sh = sub.add_parser(
        "shim",
        help="run the Claude-subscription shim (OpenAI + Messages endpoints over the claude CLI)",
        add_help=False,
    )
    sh.add_argument("shim_args", nargs=argparse.REMAINDER)
    sh.set_defaults(
        fn=lambda a: __import__(
            "castor.bench.sacpaint.claude_shim.__main__", fromlist=["main"]
        ).main(a.shim_args)
    )

    args = p.parse_args(argv)
    if getattr(args, "extra", None) and args.extra and args.extra[0] == "--":
        args.extra = args.extra[1:]
    return int(args.fn(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
