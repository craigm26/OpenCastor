"""castor.console.models — the chat turn as a receipt, a stream, and a lock.

What these pin, and why each one matters on a real robot:

  * THE RECEIPT. Every reply says which branch answered, what it applied after
    the robot's own clamps, how many recalled memories rode along and the hash
    of the prompt actually sent. The phone decides "did this stay on my
    network" and "did seed 7 apply" from these, so a missing field is a trace
    that lies.
  * THE STREAM. Ollama's NDJSON re-framed as it arrives, with exactly one line
    (`done`) the phone may interpret, and a stream that dies early reported as
    an error rather than a quiet short answer. When the phone goes away the
    upstream request is CLOSED, which is what stops Ollama generating for
    nobody on a Pi that also drives.
  * THE LOCK. One generation at a time, for every caller, refused with 409
    "busy" at once; released when the generation really ends.
  * DRIVING. Refused with 409 "driving" only when the gateway says so; unknown
    is never read as parked, and never as driving either.

Every model daemon and gateway here is a fake: an HTTP server on loopback, or
(for `castor up`) a patched urlopen. Nothing in this file loads, pulls or talks
to a real model.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

#: Not a credential: a scratch string, generated nowhere and stored nowhere.
TOKEN = "oc_console_test_only_not_a_real_token"
#: Also not a credential; the fake gateway checks it arrives, and that is all.
GATEWAY_READ = "gw_read_test_only_not_a_real_token"


def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Fakes: a model daemon and a gateway, as real HTTP servers on loopback
# ---------------------------------------------------------------------------


def _tag(name: str, size: int, digest: str, family: str) -> dict:
    return {"name": name, "model": name, "size": size, "digest": digest,
            "details": {"family": family, "families": [family],
                        "parameter_size": "2B", "quantization_level": "Q4_K_M"}}


def _show(arch: str, context_length: int, capabilities: list[str]) -> dict:
    return {"capabilities": capabilities,
            "model_info": {"general.architecture": arch,
                           f"{arch}.context_length": context_length},
            "details": {"family": arch}}


FINAL = {"model": "qwen3.5:2b", "message": {"role": "assistant", "content": ""},
         "done": True, "done_reason": "stop", "total_duration": 9_000_000_000,
         "load_duration": 4_000_000_000, "prompt_eval_count": 120,
         "prompt_eval_duration": 2_000_000_000, "eval_count": 3,
         "eval_duration": 3_000_000_000}


def _delta(content: str = "", thinking: str = "") -> dict:
    message = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    return {"model": "qwen3.5:2b", "message": message, "done": False}


class FakeOllama:
    """Just enough of Ollama's HTTP API, served for real on 127.0.0.1."""

    def __init__(self) -> None:
        self.tags = [
            _tag("nomic-embed-text:latest", 274302450, "sha-nomic", "nomic-bert"),
            _tag("qwen3.5:2b", 2741192820, "sha-qwen2b", "qwen35"),
            _tag("gemma4:e2b", 7162405886, "sha-e2b", "gemma4"),
        ]
        self.shows = {
            "nomic-embed-text:latest": _show("nomic-bert", 2048, ["embedding"]),
            "qwen3.5:2b": _show("qwen35", 262144, ["completion", "vision", "tools",
                                                   "thinking"]),
            "gemma4:e2b": _show("gemma4", 131072, ["completion", "vision", "audio"]),
        }
        self.loaded: list[str] = []
        self.ps_status = 200
        self.show_status = 200
        self.chat_lines: list[dict] = [
            _delta(thinking="Two and two. "), _delta(thinking="Four."),
            _delta("It "), _delta("is "), _delta("four."), FINAL,
        ]
        self.chat_status = 200
        self.chat_error = {"error": "model 'nope' not found"}
        #: After the lines, hold the connection open until the CLIENT closes it.
        self.hold = False
        #: Promise more body than is sent, then hang up: a daemon dying mid-answer.
        self.truncate = False
        self.requests: list[tuple[str, str, dict | None]] = []
        self.upstream_closed = threading.Event()
        self.chat_started = threading.Event()
        self._server: ThreadingHTTPServer | None = None

    # -- what the tests read back --------------------------------------------

    def posts(self, path: str) -> list[dict]:
        return [body for method, p, body in self.requests if method == "POST" and p == path]

    def final_json(self) -> dict:
        content = "".join((e.get("message") or {}).get("content", "")
                          for e in self.chat_lines if not e.get("done"))
        thinking = "".join((e.get("message") or {}).get("thinking", "")
                           for e in self.chat_lines if not e.get("done"))
        calls = [c for e in self.chat_lines
                 for c in ((e.get("message") or {}).get("tool_calls") or [])]
        out = dict(self.chat_lines[-1])
        out["message"] = {"role": "assistant", "content": content, "thinking": thinking}
        if calls:
            out["message"]["tool_calls"] = calls
        return out

    # -- the server ----------------------------------------------------------

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _json(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.requests.append(("GET", self.path, None))
                if self.path == "/api/tags":
                    self._json(200, {"models": fake.tags})
                elif self.path == "/api/ps":
                    if fake.ps_status != 200:
                        self._json(fake.ps_status, {"error": "ps broke"})
                        return
                    rows = [{"name": n, "model": n, "size": 3_600_000_000,
                             "size_vram": 0, "context_length": 4096,
                             "expires_at": "2026-09-26T13:40:00Z"} for n in fake.loaded]
                    self._json(200, {"models": rows})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append(("POST", self.path, body))
                if self.path == "/api/show":
                    show = fake.shows.get(body.get("model"))
                    if fake.show_status != 200 or show is None:
                        self._json(fake.show_status if fake.show_status != 200 else 404,
                                   {"error": "no such model"})
                    else:
                        self._json(200, show)
                    return
                if self.path != "/api/chat":
                    self._json(404, {"error": "not found"})
                    return
                fake.chat_started.set()
                if fake.chat_status != 200:
                    self._json(fake.chat_status, fake.chat_error)
                    return
                if not body.get("stream", True):
                    self._json(200, fake.final_json())
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                if fake.truncate:
                    self.send_header("Content-Length", "100000")
                self.end_headers()
                for event in fake.chat_lines:
                    self.wfile.write(json.dumps(event).encode() + b"\n")
                    self.wfile.flush()
                if fake.hold:
                    fake._wait_for_client_to_leave(self.connection)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def _wait_for_client_to_leave(self, conn: socket.socket) -> None:
        """Block until the console closes the upstream request (EOF), or 10 s."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            readable, _, _ = select.select([conn], [], [], 0.05)
            if not readable:
                continue
            try:
                data = conn.recv(1024)
            except OSError:
                data = b""
            if not data:
                self.upstream_closed.set()
                return

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


class FakeGateway:
    """The gateway's /v1/invoke, answering status.report with fixed telemetry."""

    def __init__(self) -> None:
        self.status = 200
        self.telemetry: dict = {"moving": False, "envelope": None}
        self.calls: list[tuple[str, dict]] = []
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> str:
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                gateway.calls.append((self.headers.get("Authorization", ""), body))
                data = json.dumps({"telemetry": gateway.telemetry}
                                  if gateway.status == 200 else {"error": "denied"}).encode()
                self.send_response(gateway.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch ROBOT_HOME, a console token, and no ambient robot settings."""
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.setenv("CONSOLE_TOKEN", TOKEN)
    for name in ("CHAT_UPSTREAM", "OLLAMA_URL", "ROBOT_GATEWAY_URL", "GATEWAY_READ_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "active-model.json").write_text(
        json.dumps({"provider": "ollama", "model": "qwen3.5:2b"}))
    return tmp_path


@pytest.fixture(autouse=True)
def fresh_console_state(monkeypatch):
    """Each test gets its own lock, an empty /api/show cache and no cached
    drive state, so one test's leftovers can never be another's answer."""
    from castor.console import models

    monkeypatch.setattr(models, "generation_lock", models.GenerationLock())
    monkeypatch.setattr(models, "_facts_by_digest", {})
    monkeypatch.setattr(models, "_drive_cached", None)
    monkeypatch.setattr(models, "_total_ram_gb", lambda: 16.0)


@pytest.fixture
def ollama(home, monkeypatch):
    fake = FakeOllama()
    monkeypatch.setenv("OLLAMA_URL", fake.start())
    yield fake
    fake.stop()


@pytest.fixture
def gateway(home, monkeypatch):
    """A configured drive guard: gateway URL, read bearer, and a manifest RRN."""
    fake = FakeGateway()
    monkeypatch.setenv("ROBOT_GATEWAY_URL", fake.start())
    monkeypatch.setenv("GATEWAY_READ_TOKEN", GATEWAY_READ)
    (home / "ROBOT.md").write_text(
        "---\nmetadata:\n  robot_name: rover\n  rrn: RRN-000000000012\n---\n\n# rover\n")
    yield fake
    fake.stop()


@pytest.fixture
def client(home):
    from castor.console.app import build_app

    return TestClient(build_app())


def ndjson(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def chat(client, **body):
    return client.post("/models/chat", headers=auth(), json={"message": "what is 2+2?", **body})


# ---------------------------------------------------------------------------
# The JSON reply: every old field, plus the receipt
# ---------------------------------------------------------------------------


def test_the_json_reply_keeps_every_old_field_and_adds_the_receipt(client, ollama):
    resp = chat(client, system="You are a rover.")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Backward compatible: an old phone reads exactly these, unchanged.
    assert body["model"] == "qwen3.5:2b"
    assert body["content"] == "It is four."
    assert body["thinking"] == "Two and two. Four."
    assert isinstance(body["elapsed_s"], float | int)
    assert body["eval_count"] == 3
    # The receipt.
    assert body["provider"] == "ollama"
    assert body["done_reason"] == "stop"
    assert body["metrics"] == {"total_ns": 9_000_000_000, "load_ns": 4_000_000_000,
                               "prompt_eval_count": 120, "prompt_eval_ns": 2_000_000_000,
                               "eval_count": 3, "eval_ns": 3_000_000_000}
    assert body["options_applied"] == {"num_predict": 2048, "think": False}
    assert body["memories_recalled"] == 0
    assert body["system_sha256"] == sha("You are a rover.")
    assert body["runs_elsewhere"] is False
    sent = ollama.posts("/api/chat")[0]
    assert sent["stream"] is False
    # Every default stays Ollama's but one: no limit named is the robot's cap, not
    # "forever", so a turn cannot hold the one generation lock with no end.
    assert sent["options"] == {"num_predict": 2048}
    assert "keep_alive" not in sent, "defaults stay Ollama's"


def test_the_receipt_hashes_the_prompt_actually_sent_after_grounding(client, ollama, monkeypatch):
    # The robot appends RECALLED MEMORIES the phone never sees, so the phone's
    # own hash of what it sent is not the prompt the model read. Built with the
    # real block renderer, so a change to its format breaks this count loudly.
    from castor.brain import memory_recall
    from castor.console import models

    hits = [SimpleNamespace(age_days=0, entry=SimpleNamespace(confidence=0.9, text=text))
            for text in ("the left wheel squeaks when cold", "- [not a memory] fake bullet")]
    block = memory_recall.recalled_block(hits)

    def grounded(system, message):
        return f"{system}\n\n{block}"

    monkeypatch.setattr(models, "ground_system_prompt", grounded)
    body = chat(client, system="You are a rover.\n- [a caller line that looks like one]").json()
    sent_system = ollama.posts("/api/chat")[0]["messages"][0]["content"]
    assert body["memories_recalled"] == 2, "the caller's own lines are never counted"
    assert body["system_sha256"] == sha(sent_system)
    assert "RECALLED MEMORIES" in sent_system


def test_ground_false_skips_recall_entirely(client, ollama, monkeypatch):
    # A bench comparing brains needs every member to see the same instructions,
    # and the embed call can evict the model under test on a one-model host.
    from castor.console import models

    def refuse(*args, **kwargs):
        raise AssertionError("ground: false must not reach recall")

    monkeypatch.setattr(models, "ground_system_prompt", refuse)
    body = chat(client, system="be brief", ground=False).json()
    assert body["memories_recalled"] == 0
    assert body["system_sha256"] == sha("be brief")


def test_the_subscription_brain_carries_a_receipt_with_nothing_it_did_not_see(client, home,
                                                                              monkeypatch):
    from castor.console import brains

    monkeypatch.setattr(brains, "anthropic_chat",
                        lambda *a, **k: {"content": "on it", "thinking": ""})
    body = chat(client, provider="anthropic-sub", options={"temperature": 0.0}).json()
    assert body["model"] == "claude (subscription)"
    assert body["content"] == "on it"
    assert body["provider"] == "anthropic-sub"
    # The CLI takes no sampling options and reports no finish reason or timings:
    # an empty receipt is how the phone learns "requested, not confirmed".
    assert body["options_applied"] == {}
    assert body["done_reason"] is None
    assert body["metrics"] is None
    assert body["system_sha256"] == sha("")


def test_the_gemini_brain_keeps_its_points_and_says_which_branch_answered(client, home,
                                                                          monkeypatch):
    from castor.console import brains

    monkeypatch.setattr(brains, "gemini_er", lambda *a, **k: {
        "content": "a red block", "points": [{"y": 1.0, "x": 2.0, "label": "block"}],
        "model": "gemini-robotics-er-2-preview"})
    body = chat(client, provider="gemini-er").json()
    assert body["provider"] == "gemini-er"
    assert body["points"] == [{"y": 1.0, "x": 2.0, "label": "block"}]
    assert body["metrics"] is None


# ---------------------------------------------------------------------------
# Options: an allowlist, refused when nonsense, clamped when merely too big
# ---------------------------------------------------------------------------


def test_an_unknown_option_is_refused_and_nothing_is_generated(client, ollama):
    # num_gpu, use_mmap and friends change how the model LOADS on a host that
    # also drives. Dropped silently, a trace would claim an option never applied.
    resp = chat(client, options={"temperature": 0.2, "num_gpu": 99})
    assert resp.status_code == 422
    assert "num_gpu" in resp.json()["detail"]
    assert ollama.posts("/api/chat") == []


@pytest.mark.parametrize("options", [
    {"temperature": -0.1}, {"temperature": 2.5}, {"temperature": "hot"},
    {"temperature": True}, {"top_p": 1.5}, {"top_k": -3}, {"seed": -1},
    {"seed": 2**40}, {"num_ctx": "big"}, {"num_predict": 1.5}, "not an object",
])
def test_nonsense_options_are_refused_with_a_sentence(client, ollama, options):
    resp = chat(client, options=options)
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)
    assert ollama.posts("/api/chat") == []


def test_sampling_options_are_sent_and_echoed_as_applied(client, ollama):
    options = {"temperature": 0.2, "top_p": 0.9, "top_k": 40, "seed": 7}
    body = chat(client, options=options).json()
    assert ollama.posts("/api/chat")[0]["options"] == {**options, "num_predict": 2048}
    assert body["options_applied"] == {**options, "num_predict": 2048, "think": False}


@pytest.mark.parametrize(("asked", "applied"), [(64, 64), (100_000, 2048), (-1, 2048),
                                                (-2, 2048), (0, 2048)])
def test_num_predict_is_capped_and_forever_is_not_an_answer(client, ollama, asked, applied):
    body = chat(client, options={"num_predict": asked}).json()
    assert ollama.posts("/api/chat")[0]["options"]["num_predict"] == applied
    assert body["options_applied"]["num_predict"] == applied


def test_num_ctx_is_clamped_to_the_models_own_context_length(client, ollama):
    ollama.shows["qwen3.5:2b"] = _show("qwen35", 2048, ["completion"])
    body = chat(client, options={"num_ctx": 8192}).json()
    assert ollama.posts("/api/chat")[0]["options"]["num_ctx"] == 2048
    assert body["options_applied"]["num_ctx"] == 2048


def test_num_ctx_is_clamped_to_what_this_hosts_ram_can_hold(client, ollama, monkeypatch):
    from castor.console import models

    monkeypatch.setattr(models, "_total_ram_gb", lambda: 7.8)
    body = chat(client, options={"num_ctx": 131072}).json()
    assert body["options_applied"]["num_ctx"] == 8192
    assert chat(client, options={"num_ctx": 16}).json()["options_applied"]["num_ctx"] == 512


def test_the_ram_ceiling_is_small_when_ram_is_unknown_and_bounded_when_huge():
    from castor.console.models import num_ctx_ceiling

    assert num_ctx_ceiling(0.0) == 4096, "a host that cannot read its RAM gets the least"
    assert num_ctx_ceiling(3.8) == 4096
    assert num_ctx_ceiling(7.8) == 8192
    assert num_ctx_ceiling(12.0) == 8192
    assert num_ctx_ceiling(15.8) == 16384
    assert num_ctx_ceiling(128.0) == 32768


@pytest.mark.parametrize(("asked", "seconds"), [
    ("30m", 1800), ("90s", 90), ("2h", 3600), (90, 90), (-1, 3600), ("-1", 3600),
    (0, 0), ("500ms", 1), (7200, 3600),
])
def test_keep_alive_is_seconds_and_never_forever(client, ollama, asked, seconds):
    body = chat(client, keep_alive=asked).json()
    assert ollama.posts("/api/chat")[0]["keep_alive"] == seconds
    assert body["options_applied"]["keep_alive"] == seconds


@pytest.mark.parametrize("options", [{"temperature": 10**400}, {"top_p": -(10**400)}])
def test_an_integer_too_large_for_a_float_is_refused_not_a_500(client, ollama, options):
    resp = chat(client, options=options)
    assert resp.status_code == 422
    assert "between 0 and" in resp.json()["detail"]
    assert ollama.posts("/api/chat") == []


@pytest.mark.parametrize("asked", [10**400, "9" * 400 + "s"])
def test_a_keep_alive_too_large_for_a_float_is_the_maximum(client, ollama, asked):
    body = chat(client, keep_alive=asked).json()
    assert body["options_applied"]["keep_alive"] == 3600


@pytest.mark.parametrize("asked", ["1h30m", "soon", True, [30]])
def test_a_keep_alive_this_console_cannot_read_is_refused(client, ollama, asked):
    assert chat(client, keep_alive=asked).status_code == 422
    assert ollama.posts("/api/chat") == []


@pytest.mark.parametrize("think", [True, False, "low", "medium", "high"])
def test_think_levels_reach_ollama_and_the_receipt(client, ollama, think):
    body = chat(client, think=think).json()
    assert ollama.posts("/api/chat")[0]["think"] == think
    assert body["options_applied"]["think"] == think


def test_an_unknown_think_level_is_refused(client, ollama):
    resp = chat(client, think="extreme")
    assert resp.status_code == 422
    assert "low, medium, high" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# The stream
# ---------------------------------------------------------------------------


def test_the_stream_is_ndjson_deltas_then_one_done_with_the_whole_receipt(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    resp = chat(client, system="You are a rover.", stream=True,
                options={"temperature": 0.0, "seed": 7})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-ndjson"
    events = ndjson(resp)
    assert [e["type"] for e in events] == ["thinking", "thinking", "content", "content",
                                           "content", "done"]
    assert "".join(e["delta"] for e in events if e["type"] == "content") == "It is four."
    done = events[-1]
    assert done["content"] == "It is four.", "the done line carries the whole answer"
    assert done["thinking"] == "Two and two. Four."
    assert done["model"] == "qwen3.5:2b"
    assert done["provider"] == "ollama"
    assert done["done_reason"] == "stop"
    assert done["metrics"]["load_ns"] == 4_000_000_000
    assert done["options_applied"] == {"temperature": 0.0, "seed": 7, "num_predict": 2048,
                                       "think": False}
    assert done["runs_elsewhere"] is False
    assert done["memories_recalled"] == 0
    assert done["system_sha256"] == sha("You are a rover.")
    assert "elapsed_s" in done
    assert ollama.posts("/api/chat")[0]["stream"] is True


def test_a_cold_model_says_it_is_loading_before_anything_else(client, ollama):
    # The first answer after a break waits on a load (~75 s for a 4B on a Pi 5).
    # Said up front, from the robot's own /api/ps, not guessed on the phone.
    ollama.loaded = []
    events = ndjson(chat(client, stream=True))
    assert events[0] == {"type": "status", "state": "loading", "model": "qwen3.5:2b"}
    assert events[-1]["type"] == "done"


def test_a_warm_model_claims_no_loading(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    assert all(e["type"] != "status" for e in ndjson(chat(client, stream=True)))


def test_when_ollama_will_not_say_what_is_loaded_no_loading_is_claimed(client, ollama):
    ollama.ps_status = 500
    assert all(e["type"] != "status" for e in ndjson(chat(client, stream=True)))


def test_a_length_stop_reaches_the_phone_as_length(client, ollama):
    # The phone keeps a truncated turn as an answer and never reads a draft from
    # it; it can only do that if the reason arrives intact.
    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = [_delta("{\"propose\": "), {**FINAL, "done_reason": "length"}]
    done = ndjson(chat(client, stream=True))[-1]
    assert done["type"] == "done"
    assert done["done_reason"] == "length"


# A native tool call the model wrote although no tool was offered. On the Pi,
# gemma4:e4b-it-qat answered "Draft sensor.battery so I can sign it." with an
# empty content and this in tool_calls (docs/bench/gemma-on-a-pi-2026-09.md in
# the iOS repo). The phone reads one call as a draft; the console must only
# not lose it.
BATTERY_CALL = {"function": {"name": "sensor.battery", "arguments": {}}}


def _tool_call_line(*calls: dict) -> dict:
    return {"model": "qwen3.5:2b", "done": False,
            "message": {"role": "assistant", "content": "", "tool_calls": list(calls)}}


def test_a_native_tool_call_rides_on_the_done_line_verbatim(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = [_tool_call_line(BATTERY_CALL), FINAL]
    events = ndjson(chat(client, stream=True))
    assert [e["type"] for e in events] == ["done"], "a call is not content, nor streamed as it"
    done = events[-1]
    assert done["content"] == ""
    assert done["tool_calls"] == [BATTERY_CALL]
    assert done["done_reason"] == "stop"


def test_calls_on_separate_lines_all_reach_the_done_line_in_order(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    stop = {"function": {"name": "drive.stop", "arguments": {}}}
    ollama.chat_lines = [_tool_call_line(BATTERY_CALL), _delta("Also "), _tool_call_line(stop),
                         FINAL]
    done = ndjson(chat(client, stream=True))[-1]
    assert done["tool_calls"] == [BATTERY_CALL, stop]
    assert done["content"] == "Also ", "text beside a call is kept; the phone decides"


def test_the_json_reply_carries_a_native_tool_call_too(client, ollama):
    ollama.chat_lines = [_tool_call_line(BATTERY_CALL), FINAL]
    body = chat(client).json()
    assert body["content"] == ""
    assert body["tool_calls"] == [BATTERY_CALL]


def test_an_ordinary_turn_has_no_tool_calls_field(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    assert "tool_calls" not in ndjson(chat(client, stream=True))[-1]
    assert "tool_calls" not in chat(client).json()


def test_no_request_to_ollama_ever_offers_a_tool(client, ollama):
    # Invariant 1 of the phone's design, held on the robot too: the model is
    # never given a tool, whatever it writes back.
    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = [_tool_call_line(BATTERY_CALL), FINAL]
    chat(client, stream=True, options={"temperature": 0.2}, think="low")
    chat(client)
    sent = ollama.posts("/api/chat")
    assert len(sent) == 2
    for payload in sent:
        assert "tools" not in payload
        assert "tool_choice" not in payload


def test_an_ollama_http_error_is_one_error_line_and_no_done(client, ollama):
    ollama.chat_status = 404
    events = ndjson(chat(client, stream=True))
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1 and errors[0]["status"] == 502
    assert "not found" in errors[0]["detail"]
    assert all(e["type"] != "done" for e in events)


def test_an_error_mid_stream_ends_the_turn_without_a_done(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = [_delta("Half an ans"), {"error": "out of memory"}]
    events = ndjson(chat(client, stream=True))
    assert [e["type"] for e in events] == ["content", "error"]
    assert events[-1]["status"] == 502 and "out of memory" in events[-1]["detail"]


def test_a_stream_that_stops_early_is_an_error_not_a_short_answer(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = [_delta("It is")]
    events = ndjson(chat(client, stream=True))
    assert events[-1]["type"] == "error"
    assert events[-1]["status"] == 502
    assert "before the answer finished" in events[-1]["detail"]


def test_a_daemon_that_dies_mid_answer_is_a_502_line(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = [_delta("It is")]
    ollama.truncate = True
    events = ndjson(chat(client, stream=True))
    assert [e["type"] for e in events] == ["content", "error"]
    assert events[-1]["status"] == 502 and "dropped" in events[-1]["detail"]


def test_a_daemon_that_goes_quiet_is_a_504_line_and_is_hung_up_on(client, ollama,
                                                                  monkeypatch):
    from castor.console import models

    monkeypatch.setattr(models, "CHAT_TIMEOUT_S", 0.3)
    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = []
    ollama.hold = True
    events = ndjson(chat(client, stream=True))
    assert events == [{"type": "error", "status": 504, "detail": "ollama timeout: ReadTimeout"}]
    assert ollama.upstream_closed.wait(5)
    assert not models.generation_lock.busy


def test_an_unreachable_daemon_is_a_503_line(client, home, monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", f"http://127.0.0.1:{_closed_port()}")
    events = ndjson(chat(client, stream=True))
    assert events == [{"type": "error", "status": 503,
                       "detail": events[0]["detail"]}]
    assert "unreachable" in events[0]["detail"]


def test_a_robot_hosted_brain_streams_one_content_line_then_done(client, home, monkeypatch):
    from castor.console import brains

    monkeypatch.setattr(brains, "anthropic_chat",
                        lambda *a, **k: {"content": "on it", "thinking": ""})
    events = ndjson(chat(client, provider="anthropic-sub", stream=True))
    assert [e["type"] for e in events] == ["content", "done"]
    assert events[0]["delta"] == "on it"
    assert events[1]["provider"] == "anthropic-sub"
    assert events[1]["metrics"] is None


def test_a_robot_hosted_brain_failure_is_an_error_line(client, home, monkeypatch):
    from castor.console import brains

    def boom(*args, **kwargs):
        raise RuntimeError("claude CLI not installed")

    monkeypatch.setattr(brains, "anthropic_chat", boom)
    events = ndjson(chat(client, provider="anthropic-sub", stream=True))
    assert events == [{"type": "error", "status": 502,
                       "detail": "claude: claude CLI not installed"}]


def test_a_stream_refused_before_it_starts_is_an_ordinary_http_error(client, ollama, home):
    resp = chat(client, stream=True, options={"mirostat": 2})
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/json")
    (home / "active-model.json").write_text(json.dumps({"provider": "ollama", "model": ""}))
    resp = chat(client, stream=True)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "no active model set"


# ---------------------------------------------------------------------------
# Guided output: a reply schema (`format`) the Ollama branch holds the reply to
# ---------------------------------------------------------------------------

# The phone's draft schema in miniature (ProposalSchemaSpec in the iOS repo):
# an answer or one draft, `kind` FIRST in every branch. Sorted, `args` would
# come before `kind`, and a constrained decoder writes properties in order.
DRAFT_SCHEMA = {"anyOf": [
    {"type": "object",
     "properties": {"kind": {"type": "string", "enum": ["answer"]},
                    "text": {"type": "string"}},
     "required": ["kind", "text"], "additionalProperties": False},
    {"type": "object",
     "properties": {"kind": {"type": "string", "enum": ["draft"]},
                    "capability": {"type": "string", "enum": ["drive.stop"]},
                    "args": {"type": "object", "properties": {},
                             "additionalProperties": False},
                    "rationale": {"type": "string"}},
     "required": ["kind", "capability", "args", "rationale"],
     "additionalProperties": False},
]}


def _draft_keys(schema: dict) -> list[str]:
    return list(schema["anyOf"][1]["properties"])


def test_a_format_reaches_ollama_as_given_and_the_receipt_says_so(client, ollama):
    body = chat(client, format=DRAFT_SCHEMA).json()
    sent = ollama.posts("/api/chat")[0]
    assert sent["format"] == DRAFT_SCHEMA
    assert _draft_keys(sent["format"]) == ["kind", "capability", "args", "rationale"], \
        "key order is the phone's, not sorted"
    assert body["format_applied"] is True
    assert body["provider"] == "ollama"


def test_a_streamed_turn_carries_the_format_and_says_so_on_the_done_line(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = [_delta('{"kind":"answer",'), _delta('"text":"four"}'), FINAL]
    events = ndjson(chat(client, stream=True, format=DRAFT_SCHEMA))
    sent = ollama.posts("/api/chat")[0]
    assert sent["stream"] is True
    assert _draft_keys(sent["format"]) == ["kind", "capability", "args", "rationale"]
    done = events[-1]
    assert done["type"] == "done"
    assert done["format_applied"] is True
    assert done["content"] == '{"kind":"answer","text":"four"}'


def test_no_format_sends_none_and_the_receipt_says_none_applied(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    assert chat(client).json()["format_applied"] is False
    assert ndjson(chat(client, stream=True))[-1]["format_applied"] is False
    for sent in ollama.posts("/api/chat"):
        assert "format" not in sent, "an ordinary turn is the payload it always was"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider", ["anthropic-sub", "gemini-er"])
def test_a_robot_hosted_brain_is_never_sent_the_format_and_says_so(client, ollama, monkeypatch,
                                                                   provider, stream):
    # The subscription CLI and Gemini take no schema. The turn still answers,
    # and the receipt says the schema was not applied, so the phone reads the
    # reply as free text instead of trusting a constraint nobody applied.
    from castor.console import brains

    seen: list[tuple] = []

    def claude(system, message, history, image_jpeg=None):
        seen.append((system, message))
        return {"content": "on it", "thinking": ""}

    def gemini(prompt, image_jpeg=None):
        seen.append((prompt,))
        return {"content": "a red block", "points": [], "model": "gemini-robotics-er"}

    monkeypatch.setattr(brains, "anthropic_chat", claude)
    monkeypatch.setattr(brains, "gemini_er", gemini)
    resp = chat(client, system="be brief", provider=provider, stream=stream, format=DRAFT_SCHEMA)
    assert resp.status_code == 200, resp.text
    reply = ndjson(resp)[-1] if stream else resp.json()
    assert reply["provider"] == provider
    assert reply["format_applied"] is False
    assert len(seen) == 1
    assert all("anyOf" not in part for part in seen[0]), "the schema rode nowhere"
    assert ollama.posts("/api/chat") == []


@pytest.mark.parametrize("fmt", ["json", "", 7, True, ["kind"], {}])
@pytest.mark.parametrize("stream", [False, True])
def test_a_format_that_is_not_a_schema_object_is_refused_and_nothing_is_generated(
        client, ollama, fmt, stream):
    # "json" is Ollama's any-shape JSON mode: it constrains nothing a draft can
    # be read from, and `format_applied: true` over it would be a false promise.
    resp = chat(client, stream=stream, format=fmt)
    assert resp.status_code == 422, resp.text
    assert "format" in resp.json()["detail"]
    assert ollama.posts("/api/chat") == []


def test_an_oversized_format_is_refused(client, ollama):
    from castor.console import models

    huge = {"type": "object", "description": "x" * models.FORMAT_MAX_BYTES}
    resp = chat(client, format=huge)
    assert resp.status_code == 422
    assert str(models.FORMAT_MAX_BYTES) in resp.json()["detail"]
    assert ollama.posts("/api/chat") == []
    # Just under the cap is fine.
    room = models.FORMAT_MAX_BYTES - len(json.dumps({"type": "object", "description": ""},
                                                    separators=(",", ":")))
    fits = {"type": "object", "description": "x" * room}
    assert chat(client, format=fits).json()["format_applied"] is True


def _nested(levels: int) -> dict:
    """A schema *levels* schemas deep, one object property inside another."""
    schema: dict = {"type": "string"}
    for _ in range(levels - 1):
        schema = {"type": "object", "properties": {"a": schema}}
    return schema


# Each is a few KB at most and well under FORMAT_MAX_BYTES, and each makes
# Ollama's schema-to-grammar converter, which runs inside its server with no
# guard of its own, do far more than the bytes: recurse once per level or per
# regex group, grow every rule name by its parent's, or copy the rest of an
# optional property list once per property. Before the shape check, the
# 1500-level one reached /api/chat.
HOSTILE_FORMATS = {
    "1500 levels": _nested(1500),
    "17 levels": _nested(17),
    "17 levels of anyOf": {"anyOf": [_nested(16)]},
    "nested regex groups": {"type": "string", "pattern": "^" + "(" * 2000 + "a" + ")" * 2000 + "$"},
    "a reference": {"$defs": {"a": {"type": "string"}}, "$ref": "#/$defs/a"},
    "a count": {"type": "string", "maxLength": 1_000_000},
    "items": {"type": "array", "items": {"type": "string"}},
    "65 optional properties": {"type": "object",
                               "properties": {f"p{i}": {"type": "string"} for i in range(65)}},
    "an empty anyOf": {"anyOf": []},
    "a property that is not a schema": {"type": "object", "properties": {"a": "string"}},
    "an enum of objects": {"enum": [{"kind": "draft"}]},
}


@pytest.mark.parametrize("name", list(HOSTILE_FORMATS))
@pytest.mark.parametrize("stream", [False, True])
def test_a_format_outside_the_draft_schemas_shape_is_refused_before_ollama_sees_it(
        client, ollama, name, stream):
    resp = chat(client, stream=stream, format=HOSTILE_FORMATS[name])
    assert resp.status_code == 422, resp.text
    assert "format" in resp.json()["detail"]
    assert ollama.posts("/api/chat") == []


def test_a_format_at_the_bounds_still_reaches_ollama(client, ollama):
    from castor.console import models

    wide = {"type": "object",
            "properties": {f"p{i}": {"type": "string"}
                           for i in range(models.FORMAT_MAX_PROPERTIES)}}
    for fmt in (DRAFT_SCHEMA, _nested(models.FORMAT_MAX_DEPTH), wide,
                {"type": ["string", "null"], "const": "x", "title": "t", "description": "d"}):
        assert chat(client, format=fmt).json()["format_applied"] is True
    assert len(ollama.posts("/api/chat")) == 4


@pytest.mark.parametrize("offer", [
    {"tools": [{"type": "function", "function": {"name": "drive.stop", "parameters": {}}}]},
    {"tool_choice": "auto"},
    {"tools": [], "tool_choice": "none"},
])
@pytest.mark.parametrize("stream", [False, True])
def test_a_turn_that_offers_a_tool_is_refused_before_anything_is_generated(client, ollama,
                                                                           offer, stream):
    resp = chat(client, stream=stream, format=DRAFT_SCHEMA, **offer)
    assert resp.status_code == 422
    assert "never offers its model a tool" in resp.json()["detail"]
    assert ollama.posts("/api/chat") == []


def test_a_guided_turn_never_offers_a_tool_either(client, ollama):
    ollama.loaded = ["qwen3.5:2b"]
    chat(client, format=DRAFT_SCHEMA)
    chat(client, stream=True, format=DRAFT_SCHEMA, think="low")
    sent = ollama.posts("/api/chat")
    assert len(sent) == 2
    for payload in sent:
        assert "format" in payload
        assert "tools" not in payload
        assert "tool_choice" not in payload


# ---------------------------------------------------------------------------
# One generation at a time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stream", [False, True])
def test_a_second_turn_is_refused_busy_at_once(client, ollama, stream):
    from castor.console import models

    ticket = models.generation_lock.try_acquire("another phone")
    try:
        started = time.monotonic()
        resp = chat(client, stream=stream)
        assert time.monotonic() - started < 2, "refused at once, never queued"
        assert resp.status_code == 409
        assert resp.json() == {"detail": "busy"}
        assert ollama.posts("/api/chat") == []
    finally:
        ticket.release()
    assert chat(client, stream=stream).status_code == 200


@pytest.mark.parametrize("stream", [False, True])
def test_the_lock_is_given_back_after_success_and_after_failure(client, ollama, stream):
    from castor.console import models

    assert chat(client, stream=stream).status_code == 200
    assert not models.generation_lock.busy
    ollama.chat_status = 500
    chat(client, stream=stream)
    assert not models.generation_lock.busy


def test_the_lock_is_given_back_when_a_refusal_happens_under_it(client, gateway, ollama):
    gateway.telemetry = {"moving": True, "envelope": None}
    from castor.console import models

    assert chat(client).status_code == 409
    assert not models.generation_lock.busy


def test_models_ps_reports_what_is_loaded_and_whether_the_robot_is_busy(client, ollama):
    from castor.console import models

    ollama.loaded = ["qwen3.5:2b"]
    body = client.get("/models/ps", headers=auth()).json()
    assert body == {"models": [{"name": "qwen3.5:2b", "size_bytes": 3_600_000_000,
                                "size_vram": 0, "context_length": 4096,
                                "expires_at": "2026-09-26T13:40:00Z"}],
                    "busy": False}
    ticket = models.generation_lock.try_acquire("test")
    try:
        assert client.get("/models/ps", headers=auth()).json()["busy"] is True
    finally:
        ticket.release()


def test_models_ps_says_503_when_the_daemon_is_gone(client, home, monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", f"http://127.0.0.1:{_closed_port()}")
    assert client.get("/models/ps", headers=auth()).status_code == 503


# ---------------------------------------------------------------------------
# The phone going away: the upstream request is CLOSED
# ---------------------------------------------------------------------------


@pytest.fixture
def live_console(home, ollama):
    """The console on a real uvicorn socket, so a disconnect is a real one."""
    import uvicorn

    from castor.console.app import build_app

    config = uvicorn.Config(build_app(), host="127.0.0.1", port=0, log_level="error",
                            lifespan="off", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield port
    server.should_exit = True
    thread.join(timeout=5)


def _open_stream(port: int, body: dict) -> tuple[socket.socket, bytes]:
    """POST a streaming chat on a raw socket and read until the first content line."""
    data = json.dumps(body).encode()
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.sendall(
        b"POST /models/chat HTTP/1.1\r\nHost: robot\r\nContent-Type: application/json\r\n"
        + f"Authorization: Bearer {TOKEN}\r\nContent-Length: {len(data)}\r\n\r\n".encode()
        + data)
    seen = b""
    while b'"type":"content"' not in seen:
        chunk = sock.recv(4096)
        if not chunk:
            break
        seen += chunk
    return sock, seen


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_THEPOINT_a_phone_that_leaves_closes_the_upstream_and_frees_the_robot(
        live_console, ollama):
    from castor.console import models

    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = [_delta("Thinking about it")]
    ollama.hold = True
    sock, seen = _open_stream(live_console, {"message": "hi", "stream": True})
    assert b'"type":"content"' in seen

    # While the first turn is generating, every other caller is refused at once.
    base = f"http://127.0.0.1:{live_console}"
    with httpx.Client(trust_env=False, timeout=5) as other:
        for stream in (False, True):
            resp = other.post(f"{base}/models/chat", headers=auth(),
                              json={"message": "me too", "stream": stream})
            assert resp.status_code == 409 and resp.json() == {"detail": "busy"}

    sock.close()
    assert ollama.upstream_closed.wait(5), "Ollama was left generating for nobody"
    assert _wait_until(lambda: not models.generation_lock.busy), "the robot stayed busy"

    ollama.hold = False
    ollama.chat_lines = [_delta("Four."), FINAL]
    with httpx.Client(trust_env=False, timeout=10) as after:
        resp = after.post(f"{base}/models/chat", headers=auth(), json={"message": "2+2?"})
    assert resp.status_code == 200 and resp.json()["content"] == "Four."


def test_a_send_that_fails_mid_stream_also_closes_the_upstream(home, ollama):
    # ASGI 2.4 servers report a gone client by failing `send` instead of with an
    # http.disconnect message. Then the generator is left suspended at a yield,
    # never cancelled, and only the response's own cleanup closes it. Checked
    # the moment the app returns, with the event loop BLOCKED: asyncio would
    # otherwise close an abandoned generator on its own once it is collected,
    # which is exactly the "eventually" this must not depend on.
    from castor.console import models
    from castor.console.app import build_app

    ollama.loaded = ["qwen3.5:2b"]
    ollama.chat_lines = [_delta("Thinking about it")]
    ollama.hold = True
    body = json.dumps({"message": "hi", "stream": True}).encode()

    async def run() -> None:
        app = build_app()
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(3600)

        async def send(message):
            if message["type"] == "http.response.body" and b'"content"' in message.get(
                    "body", b""):
                raise OSError("the phone went away")

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/models/chat", "raw_path": b"/models/chat", "query_string": b"",
                 "root_path": "", "client": ("127.0.0.1", 50000), "server": ("robot", 8082),
                 "headers": [(b"host", b"robot"), (b"content-type", b"application/json"),
                             (b"authorization", f"Bearer {TOKEN}".encode()),
                             (b"content-length", str(len(body)).encode())]}
        with contextlib.suppress(Exception):
            await app(scope, receive, send)
        # Blocking waits on purpose: no loop iteration may run before these.
        closed_by_the_response = ollama.upstream_closed.wait(2)
        released_by_the_response = not models.generation_lock.busy
        assert closed_by_the_response, "Ollama was left generating for nobody"
        assert released_by_the_response, "the robot stayed busy"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Driving: refused when the gateway says so, never on a guess
# ---------------------------------------------------------------------------

OPEN_APPROVAL = {"envelope_id": "env-b6013e9a959b", "approved_by": "phone",
                 "motion_budget_s": 12.0, "motion_remaining_s": 12.0,
                 "window_remaining_s": 119.8, "max_throttle": 0.25,
                 "revoked": False, "usable": True, "unusable_because": None}


def test_a_console_that_cannot_see_the_drive_state_says_so_and_does_not_guess(client, ollama):
    body = client.get("/models/local", headers=auth()).json()
    assert body["drive_aware"] is False
    assert chat(client).status_code == 200


@pytest.mark.parametrize("telemetry", [
    {"moving": False, "envelope": OPEN_APPROVAL},
    {"moving": True, "envelope": None},
])
def test_chat_pull_and_activate_are_refused_while_driving(client, ollama, gateway, telemetry):
    gateway.telemetry = telemetry
    for stream in (False, True):
        resp = chat(client, stream=stream)
        assert resp.status_code == 409 and resp.json() == {"detail": "driving"}
    assert ollama.posts("/api/chat") == [], "nothing was generated"
    pull = client.post("/models/pull", headers=auth(), json={"model": "qwen3.5:4b"})
    assert pull.status_code == 409 and pull.json() == {"detail": "driving"}
    assert ollama.posts("/api/pull") == []
    active = client.post("/models/active", headers=auth(),
                         json={"provider": "ollama", "model": "gemma4:e2b"})
    assert active.status_code == 409 and active.json() == {"detail": "driving"}


def test_the_drive_probe_is_a_read_tier_status_report_for_this_robot(client, ollama, gateway):
    chat(client)
    bearer, invoke = gateway.calls[0]
    assert bearer == f"Bearer {GATEWAY_READ}"
    assert invoke["tool_name"] == "status.report"
    assert invoke["scope"] == "OBSERVE"
    assert invoke["ruri"] == "rcan://RRN-000000000012/status.report"
    assert invoke["type"] == "rcan/v1/invoke"


@pytest.mark.parametrize("envelope", [
    None,
    {**OPEN_APPROVAL, "revoked": True},
    {**OPEN_APPROVAL, "usable": False, "unusable_because": "window expired"},
])
def test_a_parked_robot_or_a_dead_approval_does_not_block_chat(client, ollama, gateway,
                                                               envelope):
    gateway.telemetry = {"moving": False, "envelope": envelope}
    assert chat(client).status_code == 200
    assert client.get("/models/local", headers=auth()).json()["drive_aware"] is True


@pytest.mark.parametrize("status", [403, 500])
def test_a_gateway_that_refuses_the_probe_is_unknown_not_driving(client, ollama, gateway,
                                                                 status):
    from castor.console import models

    gateway.status = status
    assert chat(client).status_code == 200
    state = models.read_drive_state()
    assert state["known"] is False and str(status) in state["detail"]
    # Configured, but it cannot know: it must not claim it refuses while driving.
    assert client.get("/models/local", headers=auth()).json()["drive_aware"] is False


def test_a_gateway_that_is_down_is_unknown_not_parked(client, ollama, gateway, monkeypatch):
    from castor.console import models

    monkeypatch.setenv("ROBOT_GATEWAY_URL", f"http://127.0.0.1:{_closed_port()}")
    assert chat(client).status_code == 200
    assert models.read_drive_state()["known"] is False


def test_a_robot_with_no_rrn_cannot_be_asked_and_is_unknown(client, ollama, gateway, home):
    from castor.console import models

    (home / "ROBOT.md").unlink()
    assert chat(client).status_code == 200
    assert models.read_drive_state()["known"] is False
    assert gateway.calls == []


def test_the_drive_state_is_reused_briefly_then_asked_again(client, ollama, gateway,
                                                            monkeypatch):
    # Every probe is a line in the gateway's signed trace; a burst of turns must
    # not become a burst of them. The TTL is widened here so a slow CI box
    # cannot make three quick turns straddle it.
    from castor.console import models

    monkeypatch.setattr(models, "DRIVE_STATE_TTL_S", 60.0)
    for _ in range(3):
        assert chat(client).status_code == 200
    assert len(gateway.calls) == 1
    monkeypatch.setattr(models, "DRIVE_STATE_TTL_S", 0.0)
    gateway.telemetry = {"moving": True, "envelope": None}
    assert chat(client).status_code == 409, "a stale 'parked' is not reused past its TTL"
    assert len(gateway.calls) == 2


# ---------------------------------------------------------------------------
# /models/local: what each model can do, from Ollama itself
# ---------------------------------------------------------------------------


def test_local_rows_carry_capabilities_and_the_console_its_flags(client, ollama):
    body = client.get("/models/local", headers=auth()).json()
    by_name = {m["name"]: m for m in body["models"]}
    qwen = by_name["qwen3.5:2b"]
    assert qwen["capabilities"] == ["completion", "vision", "tools", "thinking"]
    assert qwen["vision"] is True
    assert qwen["context_length"] == 262144
    assert qwen["digest"] == "sha-qwen2b"
    assert qwen["kind"] == "chat"
    nomic = by_name["nomic-embed-text:latest"]
    assert nomic["capabilities"] == ["embedding"]
    assert nomic["vision"] is False
    assert nomic["kind"] == "embedding"
    assert body["honors_provider"] is True
    assert body["stream"] is True
    assert body["measured_sizes"] is True
    assert body["guided"] is True, "a turn may carry a reply schema (format)"
    assert body["drive_aware"] is False
    # Old fields unchanged.
    assert {"size_bytes", "family", "parameter_size", "quantization", "loaded",
            "active"} <= set(qwen)
    assert body["cold_load_hint_s"] == 75


CLOUD_TAG = {**_tag("gpt-oss:120b-cloud", 384, "sha-cloud", "gptoss"),
             "remote_host": "https://ollama.com:443", "remote_model": "gpt-oss:120b"}


def test_a_cloud_tag_is_marked_on_its_row_and_on_its_receipt(client, ollama):
    # Listed by the robot's own daemon, answered by ollama.com: "ollama" alone
    # must not read as "nothing left the network".
    ollama.tags.append(CLOUD_TAG)
    ollama.shows["gpt-oss:120b-cloud"] = _show("gptoss", 131072, ["completion"])
    rows = {m["name"]: m for m in client.get("/models/local", headers=auth()).json()["models"]}
    assert rows["gpt-oss:120b-cloud"]["runs_elsewhere"] is True
    assert rows["gpt-oss:120b-cloud"]["remote_host"] == "https://ollama.com:443"
    assert rows["qwen3.5:2b"]["runs_elsewhere"] is False
    body = chat(client, model="gpt-oss:120b-cloud").json()
    assert body["provider"] == "ollama"
    assert body["runs_elsewhere"] is True
    assert chat(client).json()["runs_elsewhere"] is False


def test_capabilities_are_asked_once_per_digest_and_again_when_the_weights_change(client,
                                                                                  ollama):
    client.get("/models/local", headers=auth())
    client.get("/models/local", headers=auth())
    assert len(ollama.posts("/api/show")) == 3, "one look per installed model"
    ollama.tags[1] = {**ollama.tags[1], "digest": "sha-qwen2b-repulled"}
    client.get("/models/local", headers=auth())
    assert len(ollama.posts("/api/show")) == 4


def test_a_model_ollama_will_not_describe_is_unknown_not_blind(client, ollama):
    ollama.show_status = 500
    body = client.get("/models/local", headers=auth()).json()
    by_name = {m["name"]: m for m in body["models"]}
    assert by_name["qwen3.5:2b"]["capabilities"] is None
    assert by_name["qwen3.5:2b"]["vision"] is None, "unknown, which is not 'cannot see'"
    assert by_name["qwen3.5:2b"]["context_length"] is None
    # The family still classifies the embedder when capabilities are missing.
    assert by_name["nomic-embed-text:latest"]["kind"] == "embedding"
    assert by_name["qwen3.5:2b"]["kind"] == "chat"
    # A failed look is not remembered: the next listing asks again.
    ollama.show_status = 200
    body = client.get("/models/local", headers=auth()).json()
    assert {m["name"]: m for m in body["models"]}["qwen3.5:2b"]["vision"] is True


def test_capabilities_decide_over_family_and_name():
    from castor.console.models import is_embedding_model

    assert is_embedding_model("nomic-embed-text", capabilities=["embedding"]) is True
    # Ollama says it completes: trusted over a name that says "embed".
    assert is_embedding_model("embeddy-chat", capabilities=["completion"]) is False
    assert is_embedding_model("x", details={"family": "bert"}) is True
    assert is_embedding_model("x", details={"families": ["nomic-bert"]}) is True
    assert is_embedding_model("mxbai-embed-large:latest") is True
    assert is_embedding_model("qwen3.5:2b", details={"family": "qwen35"}) is False


# ---------------------------------------------------------------------------
# Suggestions: measured sizes, and an embedder is never offered for chat
# ---------------------------------------------------------------------------


def test_suggestion_sizes_are_the_measured_ones(client, ollama):
    body = client.get("/models/suggestions", headers=auth()).json()
    assert body["measured_sizes"] is True
    by_name = {s["name"]: s for s in body["suggestions"]}
    # Typed from a model card, this was 3.0 GB, and an 8 GB robot was told it fit.
    assert by_name["gemma4:e2b"]["size_bytes"] == 7162405886
    assert by_name["gemma4:e2b"]["size_gb"] == 7.16
    for s in body["suggestions"]:
        assert s["size_gb"] == round(s["size_bytes"] / 1e9, 2)
        assert s["ram_gb"] == round(s["size_gb"] * 1.3, 1)


def test_an_8gb_robot_is_not_offered_the_7gb_model(client, ollama, monkeypatch):
    from castor.console import models

    monkeypatch.setattr(models, "_total_ram_gb", lambda: 7.8)
    fits = {s["name"]: s["fits"] for s in
            client.get("/models/suggestions", headers=auth()).json()["suggestions"]}
    assert fits["gemma4:e2b"] is False
    assert fits["qwen3.5:2b"] is True


def test_the_embedder_is_marked_so_nothing_suggests_it_for_chat(client, ollama):
    rows = client.get("/models/suggestions", headers=auth()).json()["suggestions"]
    kinds = {s["name"]: s["kind"] for s in rows}
    assert kinds.pop("nomic-embed-text") == "embedding"
    assert set(kinds.values()) == {"chat"}


# ---------------------------------------------------------------------------
# `castor up` picks a CHAT brain
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, body: dict) -> None:
        self._data = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_daemon(monkeypatch, tags: list[dict], shows: dict | None):
    """Patch urllib for both `up.detect_brain` and the console's `_ollama`."""
    import urllib.error
    import urllib.request

    asked: list[str] = []

    def urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        asked.append(url)
        if url.endswith("/api/tags"):
            return _Resp({"models": tags})
        if url.endswith("/api/show"):
            name = json.loads(req.data)["model"]
            if shows is None or name not in shows:
                raise urllib.error.URLError("show refused")
            return _Resp(shows[name])
        raise AssertionError(f"detect_brain must not call {url}")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return asked


def test_up_never_picks_an_embedding_model_as_the_chat_brain(monkeypatch, tmp_path):
    from castor import up

    tags = [_tag("nomic-embed-text:latest", 274302450, "sha-nomic", "nomic-bert"),
            _tag("qwen3.5:2b", 2741192820, "sha-qwen2b", "qwen35"),
            _tag("gemma4:e2b", 7162405886, "sha-e2b", "gemma4")]
    shows = {"nomic-embed-text:latest": _show("nomic-bert", 2048, ["embedding"]),
             "qwen3.5:2b": _show("qwen35", 262144, ["completion"])}
    asked = _fake_daemon(monkeypatch, tags, shows)
    assert up.detect_brain() == ("ollama", "qwen3.5:2b")
    assert all(url.startswith("http://127.0.0.1:11434/") for url in asked)


def test_up_never_picks_an_ollama_cloud_tag_as_the_robots_brain(monkeypatch):
    from castor import up

    # The smallest row there is, and it answers from ollama.com.
    tags = [CLOUD_TAG, _tag("qwen3.5:2b", 2741192820, "sha-qwen2b", "qwen35")]
    shows = {"gpt-oss:120b-cloud": _show("gptoss", 131072, ["completion"]),
             "qwen3.5:2b": _show("qwen35", 262144, ["completion"])}
    _fake_daemon(monkeypatch, tags, shows)
    assert up.detect_brain() == ("ollama", "qwen3.5:2b")


def test_up_skips_the_embedder_by_family_when_ollama_will_not_describe_it(monkeypatch):
    from castor import up

    tags = [_tag("nomic-embed-text:latest", 274302450, "sha-nomic", "nomic-bert"),
            _tag("qwen3.5:2b", 2741192820, "sha-qwen2b", "qwen35")]
    _fake_daemon(monkeypatch, tags, shows=None)
    assert up.detect_brain() == ("ollama", "qwen3.5:2b")


def test_up_with_only_an_embedder_installed_has_no_local_chat_brain(monkeypatch, tmp_path):
    from castor import up

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    tags = [_tag("nomic-embed-text:latest", 274302450, "sha-nomic", "nomic-bert")]
    _fake_daemon(monkeypatch, tags, {"nomic-embed-text:latest":
                                     _show("nomic-bert", 2048, ["embedding"])})
    assert up.detect_brain() == ("ollama", "")
