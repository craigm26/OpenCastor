"""The console's paint door: a job, not a request; the profile is the robot's; nothing here decides motion."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from castor.console import paint

TOKEN = "console-secret"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.setenv("CONSOLE_TOKEN", TOKEN)
    paint._reset_for_tests()
    from castor.console.app import build_app

    return TestClient(build_app(), headers={"Authorization": f"Bearer {TOKEN}"})


def _profile(tmp_path: Path, **extra) -> dict:
    cfg = {
        "embodiment": "opencastor",
        "flags": {
            "pair_payload": "/robot/pair-payload.json",
            "calibration": "easel",
            "tolerance_mm": "5",
        },
        "brain": {"subscription": "opus", "claude_bin": "/usr/local/bin/claude"},
        "max_llm_calls": 40,
        "media": ["virtual"],
    }
    cfg.update(extra)
    (tmp_path / "paint.json").write_text(json.dumps(cfg))
    return cfg


class _FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self._code: int | None = None

    def finish(self, code: int) -> None:
        self._code = code

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + (timeout if timeout is not None else 60.0)
        while self._code is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._code is None:
            raise paint.subprocess.TimeoutExpired("fake", timeout or 0)
        return self._code

    def terminate(self) -> None:
        self.terminated = True
        self._code = -15

    def kill(self) -> None:
        self._code = -9


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch):
    """Stand in for subprocess.Popen: record the argv, hand back a process the test finishes."""
    launched: list[dict] = []

    def popen(cmd, env=None, stdout=None, stderr=None, cwd=None, **kwargs):
        assert (
            kwargs.get("start_new_session") is True
        )  # a stop must reach the shim and the runner's child
        proc = _FakeProcess()
        launched.append({"cmd": cmd, "env": env, "cwd": cwd, "proc": proc})
        return proc

    monkeypatch.setattr(paint.subprocess, "Popen", popen)
    return launched


def test_config_reports_media_and_never_secrets(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/eval/paint/config").json()["configured"] is False
    _profile(tmp_path)
    body = client.get("/eval/paint/config").json()
    assert body["media"] == ["virtual"] and body["default_medium"] == "virtual"
    assert body["default_picture"] == "sacramento" and body["brain"] == "subscription"
    assert "pair_payload" not in json.dumps(body) and "claude" not in json.dumps(body)


def test_no_profile_is_a_503_that_names_the_file(client: TestClient) -> None:
    r = client.post("/eval/paint", json={})
    assert r.status_code == 503 and "paint.json" in r.json()["detail"]


def test_build_command_is_the_documented_run(tmp_path: Path) -> None:
    cfg = _profile(tmp_path)
    job = paint._Job("j1", "sacramento", "virtual", "sacpaint/photo-v1")
    job.dir = tmp_path / "paint" / "j1"
    cmd, env = paint.build_command(job, cfg, console_url="http://127.0.0.1:8082", python="py")
    assert cmd[:6] == ["py", "-m", "castor.cli", "bench", "sacpaint", "run"]
    assert "--subscription" in cmd and cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--claude-bin") + 1] == "/usr/local/bin/claude"
    assert cmd[cmd.index("--max-llm-calls") + 1] == "40"
    assert cmd[cmd.index("--task") + 1] == "sacpaint/photo-v1"
    flags = {
        a.split("=", 1)[0]: a.split("=", 1)[1]
        for a in cmd[cmd.index("--") :]
        if "=" in a and not a.startswith("images")
    }
    assert flags["medium"] == "virtual" and flags["calibration"] == "easel"
    assert flags["progress_path"] == str(job.dir / "progress.json")
    assert flags["canvas_post_url"] == "http://127.0.0.1:8082/eval/frame?stream=canvas"
    assert "overhead_url" not in flags  # virtual: nothing to photograph
    assert env["SACPAINT_ARTIFACTS"] == str(job.dir / "artifacts")
    pen = paint._Job("j2", "sacramento", "pen", "sacpaint/photo-v1")
    pen.dir = tmp_path / "paint" / "j2"
    cmd, _ = paint.build_command(pen, cfg, console_url="http://127.0.0.1:8082")
    joined = " ".join(cmd)
    assert "overhead_url=http://127.0.0.1:8082/eval/frame/latest?stream=overhead" in joined
    assert "corners_url=http://127.0.0.1:8082/eval/corners?stream=overhead" in joined


def test_a_job_runs_reports_progress_and_scores(
    client: TestClient, tmp_path: Path, fake_runner
) -> None:
    _profile(tmp_path)
    assert client.get("/eval/paint").json() == {"state": "idle"}
    r = client.post("/eval/paint", json={"picture": "sacramento", "medium": "virtual"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job"]
    assert fake_runner[0]["cwd"] == str(tmp_path / "paint" / job_id)
    # one arm: a second start is refused while the first runs
    assert client.post("/eval/paint", json={}).status_code == 409
    # the embodiment writes progress; the console relays it
    job_dir = tmp_path / "paint" / job_id
    (job_dir / "progress.json").write_text(
        json.dumps({"steps": 12, "misses": 1, "medium": "virtual"})
    )
    (job_dir / "run.log").write_text("shim: call 3 -> opus (1 images)\n")
    status = client.get("/eval/paint").json()
    assert status["state"] == "running" and status["steps"] == 12 and status["misses"] == 1
    assert status["llm_calls"] == 3 and status["canvas_stream"] == "canvas"
    # the run ends with a scored artifact
    art = job_dir / "artifacts"
    art.mkdir()
    (art / "run-e0.json").write_text(
        json.dumps(
            {
                "composite": 0.81,
                "parts": {"structure": 0.9},
                "medium": "virtual",
                "wire": "claude-code-cli",
                "steps": 120,
            }
        )
    )
    (art / "run-e0.png").write_bytes(b"\x89PNG fake")
    fake_runner[0]["proc"].finish(0)
    for _ in range(200):
        status = client.get("/eval/paint").json()
        if status["state"] != "running":
            break
        time.sleep(0.01)
    assert status["state"] == "done" and status["score"]["composite"] == 0.81
    assert status["score"]["wire"] == "claude-code-cli"
    assert client.get("/eval/paint/canvas.png").content == b"\x89PNG fake"
    # and the slot is free again
    assert client.post("/eval/paint", json={}).status_code == 200


def test_stop_terminates_and_says_so(client: TestClient, tmp_path: Path, fake_runner) -> None:
    _profile(tmp_path)
    assert client.post("/eval/paint/stop").json()["stopped"] is False
    client.post("/eval/paint", json={})
    r = client.post("/eval/paint/stop").json()
    assert r["stopped"] is True and fake_runner[0]["proc"].terminated
    for _ in range(200):
        status = client.get("/eval/paint").json()
        if status["state"] != "running":
            break
        time.sleep(0.01)
    assert status["state"] == "stopped"


def test_medium_must_be_one_the_robot_offers(
    client: TestClient, tmp_path: Path, fake_runner
) -> None:
    _profile(tmp_path, media=["virtual"])
    r = client.post("/eval/paint", json={"medium": "pen"})
    assert r.status_code == 422 and "virtual" in r.json()["detail"]


def test_picture_upload_refuses_junk_and_unknown_names(client: TestClient, tmp_path: Path) -> None:
    assert client.post("/eval/picture?name=Bad Name", content=b"\xff\xd8x").status_code == 422
    assert client.post("/eval/picture?name=sacramento", content=b"\xff\xd8x").status_code == 422
    assert client.post("/eval/picture?name=mine", content=b"not a jpeg").status_code == 415
    _profile(tmp_path)
    r = client.post("/eval/paint", json={"picture": "nothere"})
    assert r.status_code == 404
    assert client.get("/eval/picture/nothere.jpg").status_code == 404


def test_picture_upload_traces_the_photo_into_a_task(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("inspect_robots")
    from importlib.resources import files

    import cv2

    monkeypatch.setenv("SACPAINT_REFERENCES", str(tmp_path / "refs"))
    photo = (
        files("castor.bench.sacpaint").joinpath("assets", "sacramento-photo-v1.webp").read_bytes()
    )
    import numpy as np

    rgb = cv2.imdecode(np.frombuffer(photo, dtype=np.uint8), cv2.IMREAD_COLOR)
    ok, jpg = cv2.imencode(".jpg", cv2.resize(rgb, (600, 800)))
    r = client.post("/eval/picture?name=river", content=jpg.tobytes())
    assert r.status_code == 200, r.text
    assert r.json()["task"] == "sacpaint/river"
    assert (tmp_path / "refs" / "river.spec.json").exists()
    spec = json.loads((tmp_path / "refs" / "river.spec.json").read_text())
    assert (
        spec["photo"] == "river.jpg"
        and "edges" in spec["strokes"]
        and len(spec["strokes"]["edges"]) >= 5
    )
    assert client.get("/eval/picture/river.jpg").status_code == 200
    _profile(tmp_path)
    with pytest.MonkeyPatch.context() as m:
        launched = []
        m.setattr(
            paint.subprocess, "Popen", lambda cmd, **kw: (launched.append(cmd), _FakeProcess())[1]
        )
        r = client.post("/eval/paint", json={"picture": "river"})
        assert r.status_code == 200 and r.json()["task"] == "sacpaint/river"
        assert launched[0][launched[0].index("--task") + 1] == "sacpaint/river"
        paint._reset_for_tests()
