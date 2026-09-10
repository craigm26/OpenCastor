"""The console's "paint a picture" door: the phone asks, the robot draws, the phone watches.

A benchmark run is minutes long and moves a real arm, so it is a JOB, not a
request: ``POST /eval/paint`` starts one ``castor bench sacpaint run`` process
in the background and answers at once with a job id; ``GET /eval/paint`` is
polled for progress (the embodiment writes a small progress file and posts its
canvas to this console's ``canvas`` stream as it goes) and, at the end, for the
score; ``POST /eval/paint/stop`` ends it. One job at a time: the arm is one arm.

What the robot draws with is the robot's business, not the phone's. The
operator writes ``<ROBOT_HOME>/paint.json`` once, on the robot::

    {
      "embodiment": "opencastor",
      "flags": {"pair_payload": "/home/craigm26/bob/pair-payload.json",
                "medium": "virtual", "calibration": "easel",
                "move_tool": "arm.reach_point", "move_args": "reach_point",
                "tolerance_mm": "5", "strict_reach": "false", "timeout_s": "120"},
      "brain": {"subscription": "opus"},        # or {"model": "anthropic/claude-..."} with a key in the env
      "max_llm_calls": 60,
      "media": ["virtual"]                      # what this rig can do today
    }

The phone chooses the picture (the packaged photograph of Sacramento, or one it
uploads) and, among the media the robot offers, which one. Nothing here decides
whether a motion is allowed: every stroke still goes through the gateway.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from castor.console.config import robot_home

router = APIRouter()

DEFAULT_PICTURE = "sacramento"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_PICTURE_BYTES = 12 * 1024 * 1024
CANVAS_STREAM = "canvas"


def paint_config_path() -> Path:
    """``<ROBOT_HOME>/paint.json``: how this robot paints. Read per call, never cached."""
    return robot_home() / "paint.json"


def paint_dir() -> Path:
    """Where jobs keep their logs, progress, artifacts and uploaded pictures."""
    return robot_home() / "paint"


def read_config() -> dict[str, Any]:
    """The operator's paint profile, or {} when the robot has none."""
    path = paint_config_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class _Job:
    def __init__(self, job_id: str, picture: str, medium: str, task: str) -> None:
        self.id = job_id
        self.picture = picture
        self.medium = medium
        self.task = task
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.exit_code: int | None = None
        self.error: str | None = None
        self.dir = paint_dir() / job_id
        self.stopped = False


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.job: _Job | None = None
        self.last: _Job | None = None


_state = _State()


# --------------------------------------------------------------------------- #
# Building the run
# --------------------------------------------------------------------------- #


def _console_base_url(request: Request) -> str:
    """This console as the run's process will reach it: loopback, same port."""
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return f"http://127.0.0.1:{port}"


def build_command(
    job: _Job, config: dict[str, Any], *, console_url: str, python: str = sys.executable
) -> tuple[list[str], dict[str, str]]:
    """The ``castor bench sacpaint run`` argv and env for a job. Pure, so a test can read it."""
    flags = dict(config.get("flags") or {})
    flags["medium"] = job.medium
    flags["no_prompt"] = "true"
    flags["progress_path"] = str(job.dir / "progress.json")
    flags["canvas_post_url"] = f"{console_url}/eval/frame?stream={CANVAS_STREAM}"
    flags["receipts_dir"] = str(job.dir / "receipts")
    if job.medium == "pen":
        # The phone is the eyes: its frames and tapped corners are on this console.
        flags.setdefault(
            "overhead_url", f"{console_url}/eval/frame/latest?stream=overhead&max_age_s=5"
        )
        flags.setdefault("corners_url", f"{console_url}/eval/corners?stream=overhead")
    cmd = [
        python,
        "-m",
        "castor.cli",
        "bench",
        "sacpaint",
        "run",
        "--task",
        job.task,
        "--policy",
        "agent",
        "--embodiment",
        str(config.get("embodiment") or "opencastor"),
        "--no-rerun",
        "--no-prompt",
        "--log-dir",
        str(job.dir / "logs"),
    ]
    brain = dict(config.get("brain") or {})
    if brain.get("subscription"):
        cmd += ["--subscription", "--model", str(brain["subscription"])]
        if brain.get("claude_bin"):
            cmd += ["--claude-bin", str(brain["claude_bin"])]
    elif brain.get("model"):
        cmd += ["--model", str(brain["model"])]
    max_calls = config.get("max_llm_calls")
    if max_calls:
        cmd += ["--max-llm-calls", str(int(max_calls))]
    cmd += ["--", "--epochs", "1", "-P", f"images={config.get('images', 'on_demand')}"]
    for key, value in flags.items():
        cmd += ["-E", f"{key}={value}"]
    env = dict(os.environ)
    env["SACPAINT_ARTIFACTS"] = str(job.dir / "artifacts")
    return cmd, env


def _task_for_picture(picture: str) -> str:
    if picture == DEFAULT_PICTURE:
        return "sacpaint/photo-v1"
    return f"sacpaint/{picture}"


def _launch(job: _Job, cmd: list[str], env: dict[str, str]) -> None:
    job.dir.mkdir(parents=True, exist_ok=True)
    log = open(job.dir / "run.log", "ab")  # noqa: SIM115 - the process owns it now
    # Its own session, so a stop reaches the whole tree: the runner, the
    # inspect-robots process it execs, and the subscription shim it spawns.
    job.process = subprocess.Popen(
        cmd, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=str(job.dir), start_new_session=True
    )
    )

    def reap() -> None:
        assert job.process is not None
        job.exit_code = job.process.wait()
        job.ended_at = time.time()
        log.close()
        with _state.lock:
            if _state.job is job:
                _state.last, _state.job = job, None

    threading.Thread(target=reap, name=f"paint-{job.id}", daemon=True).start()


# --------------------------------------------------------------------------- #
# Reading progress and results
# --------------------------------------------------------------------------- #


def _progress(job: _Job) -> dict[str, Any]:
    try:
        return json.loads((job.dir / "progress.json").read_text())
    except (OSError, ValueError):
        return {}


def _score(job: _Job) -> dict[str, Any] | None:
    art = job.dir / "artifacts"
    if not art.is_dir():
        return None
    files = sorted(art.glob("*.json"))
    if not files:
        return None
    try:
        data = json.loads(files[-1].read_text())
    except (OSError, ValueError):
        return None
    return {
        "composite": data.get("composite"),
        "parts": data.get("parts"),
        "medium": data.get("medium"),
        "wire": data.get("wire"),
        "steps": data.get("steps"),
        "canvas_png": files[-1].with_suffix(".png").name
        if files[-1].with_suffix(".png").exists()
        else None,
    }


def _llm_calls(job: _Job) -> int | None:
    try:
        text = (job.dir / "run.log").read_text(errors="replace")
    except OSError:
        return None
    calls = re.findall(r"call (\d+) -> ", text)
    return int(calls[-1]) if calls else None


def _describe(job: _Job | None, *, running: bool) -> dict[str, Any]:
    if job is None:
        return {"state": "idle"}
    progress = _progress(job)
    if running:
        state = "running"
    elif job.stopped:
        state = "stopped"
    elif job.exit_code == 0:
        state = "done"
    else:
        state = "error"
    out: dict[str, Any] = {
        "state": state,
        "job": job.id,
        "picture": job.picture,
        "medium": job.medium,
        "task": job.task,
        "started_at": job.started_at,
        "elapsed_s": round((job.ended_at or time.time()) - job.started_at, 1),
        "steps": progress.get("steps", 0),
        "misses": progress.get("misses", 0),
        "llm_calls": _llm_calls(job),
        "canvas_stream": CANVAS_STREAM,
    }
    if not running:
        out["exit_code"] = job.exit_code
        out["score"] = _score(job)
        if job.error:
            out["error"] = job.error
        elif state == "error":
            out["error"] = _last_error_line(job)
    return out


def _last_error_line(job: _Job) -> str | None:
    try:
        lines = (job.dir / "run.log").read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if line.startswith("error:") or "Error" in line or "denied" in line:
            return line[:300]
    return lines[-1][:300] if lines else None


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/eval/paint/config")
def paint_config() -> dict[str, Any]:
    """What this robot offers: media it can paint in, and the default picture. No secrets."""
    config = read_config()
    media = [m for m in (config.get("media") or []) if m in ("pen", "virtual")]
    return {
        "configured": bool(config),
        "media": media,
        "default_medium": media[0] if media else None,
        "default_picture": DEFAULT_PICTURE,
        "brain": (
            "subscription"
            if (config.get("brain") or {}).get("subscription")
            else "api"
            if (config.get("brain") or {}).get("model")
            else None
        ),
        "config_path": str(paint_config_path()),
    }


@router.get("/eval/paint")
def paint_status() -> dict[str, Any]:
    """The running job, else the last one, else idle."""
    with _state.lock:
        job, last = _state.job, _state.last
    if job is not None:
        return _describe(job, running=True)
    return _describe(last, running=False)


@router.post("/eval/paint")
async def paint_start(request: Request) -> dict[str, Any]:
    """Start a run. Body: ``{"picture": "sacramento" | <uploaded name>, "medium": "virtual" | "pen"}``."""
    try:
        body = await request.json() if await request.body() else {}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="body must be JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    config = read_config()
    if not config:
        raise HTTPException(
            status_code=503,
            detail=f"this robot has no paint profile: write {paint_config_path()} (see castor.console.paint)",
        )
    media = [m for m in (config.get("media") or []) if m in ("pen", "virtual")]
    if not media:
        raise HTTPException(
            status_code=503, detail="the paint profile lists no media this rig can paint in"
        )
    medium = str(body.get("medium") or media[0])
    if medium not in media:
        raise HTTPException(
            status_code=422, detail=f"medium must be one of {media}, got {medium!r}"
        )
    picture = str(body.get("picture") or DEFAULT_PICTURE)
    if picture != DEFAULT_PICTURE and not (
        _NAME_RE.match(picture) and (paint_dir() / "pictures" / f"{picture}.jpg").exists()
    ):
        raise HTTPException(
            status_code=404,
            detail=f"no uploaded picture named {picture!r}; POST /eval/picture first",
        )
    with _state.lock:
        if _state.job is not None:
            raise HTTPException(
                status_code=409,
                detail=f"a paint job is already running ({_state.job.id}); stop it first",
            )
        job = _Job(time.strftime("%Y%m%dT%H%M%S"), picture, medium, _task_for_picture(picture))
        _state.job = job
    try:
        cmd, env = build_command(job, config, console_url=_console_base_url(request))
        _launch(job, cmd, env)
    except Exception as exc:  # noqa: BLE001 - report, and free the slot
        with _state.lock:
            job.error = f"could not start: {exc}"
            job.exit_code, job.ended_at = -1, time.time()
            _state.last, _state.job = job, None
        raise HTTPException(status_code=500, detail=job.error) from exc
    return {"ok": True, "job": job.id, "picture": picture, "medium": medium, "task": job.task}


@router.post("/eval/paint/stop")
def paint_stop() -> dict[str, Any]:
    """End the running job. The arm finishes the move it is on; nothing else is sent."""
    with _state.lock:
        job = _state.job
    if job is None or job.process is None:
        return {"ok": True, "stopped": False, "state": "idle"}
    job.stopped = True
    _signal_tree(job.process, signal.SIGTERM)
    try:
        job.process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        _signal_tree(job.process, signal.SIGKILL)
    return {"ok": True, "stopped": True, "job": job.id}


@router.post("/eval/picture")
async def picture_upload(request: Request, name: str = "mine") -> dict[str, Any]:
    """Upload a JPEG to paint instead of the default. It becomes task ``sacpaint/<name>``.

    The photo's salient edges are traced automatically into the scoring
    skeleton (``castor bench sacpaint new --auto-trace``), so an uploaded
    picture is still scored: on how much of its edge map the robot reproduced.
    """
    if not _NAME_RE.match(name) or name == DEFAULT_PICTURE:
        raise HTTPException(
            status_code=422,
            detail="name must be lowercase letters, digits, - or _, and not 'sacramento'",
        )
    data = await request.body()
    if len(data) > MAX_PICTURE_BYTES:
        raise HTTPException(status_code=413, detail=f"picture over {MAX_PICTURE_BYTES} bytes")
    if not data.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=415, detail="picture must be a JPEG")
    pictures = paint_dir() / "pictures"
    pictures.mkdir(parents=True, exist_ok=True)
    path = pictures / f"{name}.jpg"
    path.write_bytes(data)
    cmd = [
        sys.executable,
        "-m",
        "castor.cli",
        "bench",
        "sacpaint",
        "new",
        name,
        "--photo",
        str(path),
        "--auto-trace",
        "--force",
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"could not trace the picture: {exc}") from exc
    if done.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"could not trace the picture: {done.stderr[-400:] or done.stdout[-400:]}",
        )
    return {"ok": True, "picture": name, "task": f"sacpaint/{name}", "bytes": len(data)}


@router.get("/eval/picture/{name}.jpg")
def picture_get(name: str):  # noqa: ANN201 - FastAPI Response
    """The uploaded picture, so the phone can show what it asked for."""
    from fastapi import Response

    if not _NAME_RE.match(name):
        raise HTTPException(status_code=422, detail="bad name")
    path = paint_dir() / "pictures" / f"{name}.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such picture")
    return Response(
        content=path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"}
    )


@router.get("/eval/paint/canvas.png")
def paint_canvas():  # noqa: ANN201 - FastAPI Response
    """The finished (or last) job's scored canvas, as the scorer read it."""
    from fastapi import Response

    with _state.lock:
        job = _state.job or _state.last
    if job is None:
        raise HTTPException(status_code=404, detail="no paint job yet")
    score = _score(job)
    if not score or not score.get("canvas_png"):
        raise HTTPException(status_code=404, detail="no scored canvas yet")
    path = job.dir / "artifacts" / score["canvas_png"]
    return Response(
        content=path.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"}
    )


def _signal_tree(process: Any, sig: int) -> None:
    """Signal the job's whole process group (it was started in its own session), else just it."""
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except (ProcessLookupError, PermissionError, OSError, AttributeError, TypeError):
        if sig == signal.SIGKILL:
            process.kill()
        else:
            process.terminate()


def _reset_for_tests() -> None:
    """Forget every job. Tests only; the state is process-global like eval_eyes'."""
    with _state.lock:
        _state.job = None
        _state.last = None


__all__ = ["router", "build_command", "read_config", "paint_config_path", "shutil"]
