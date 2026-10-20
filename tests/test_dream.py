"""Dream Labs' Dream-1 with the user's own key: castor/dream.py and the bridge's dream.* verbs.

THE FAKE IS DREAM LABS' WIRE AS IT ANSWERED ON 2026-10-19. A refused key on
the gateway is `DA-1000 unknown_key` in a status envelope; on the platform it
is `{"error": {"code": "invalid_api_key"}}`. Both shapes were read off the
live services with a key that does not exist; nothing here has a real key.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_microduck_bridge import TOKEN, MockRobotd, _line, _start

from castor import dream
from castor import microduck_bridge as bridge

GOOD = "dl_live_" + "a1b2c3d4" * 4
DUCK = {
    "state": {"velocity": [0, 3]},
    "action": {"velocity": [0, 3]},
    "video": {"head": "observation.images.head"},
}
MODELS = [
    {
        "id": dream.BASE_MODEL,
        "owner": "public",
        "base_model": dream.BASE_MODEL,
        "embodiment": "yam",
        "servable": True,
        "cameras": ["overhead", "wrist_l", "wrist_r"],
        "embodiment_config": None,
    },
    {
        "id": "dream-1-ft:a1b2c3d4-duck",
        "owner": "mine",
        "base_model": dream.BASE_MODEL,
        "embodiment": "emb_1",
        "servable": True,
        "cameras": ["head"],
        "embodiment_config": DUCK,
    },
]


class FakeDream:
    """Both services on one localhost port, recording every request."""

    def __init__(self):
        self.requests: list[dict] = []
        self.delay_s = 0.0
        self.next_status = {"code": "DA-0000", "name": "OK"}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                fake.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "ua": self.headers.get("User-Agent"),
                        "body": body,
                    }
                )
                return body

            def do_GET(self):
                self._record()
                if self.headers.get("Authorization") != f"Bearer {GOOD}":
                    return self._reply(
                        401,
                        {
                            "error": {
                                "code": "invalid_api_key",
                                "message": "This api key is not valid.",
                            }
                        },
                    )
                self._reply(200, MODELS)

            def do_POST(self):
                body = self._record()
                time.sleep(fake.delay_s)
                if self.path == "/v1/end_session":
                    return self._reply(200, {"status": {"code": "DA-0000"}})
                if self.headers.get("Authorization") != f"Bearer {GOOD}":
                    return self._reply(
                        200,
                        {
                            "session_id": "",
                            "status": {
                                "code": "DA-1000",
                                "name": "UNAUTHORIZED",
                                "reason": "unknown_key",
                                "retryable": False,
                                "retry_after_s": None,
                            },
                            "actions": None,
                            "timing": None,
                        },
                    )
                status = fake.next_status
                sid = body.get("session_id") or "ses_1"
                rows = [[0.1 * i, 0.0, 0.2] for i in range(48)]
                self._reply(
                    200,
                    {
                        "session_id": sid,
                        "status": status,
                        "actions": {"per_key": {"action.velocity": rows}},
                        "timing": {"serve_infer_ms": 40, "gateway_ms": 55},
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    f = FakeDream()
    monkeypatch.setattr(dream, "GATEWAY_URL", f.url)
    monkeypatch.setattr(dream, "PLATFORM_URL", f.url)
    monkeypatch.setenv("DREAM_CREDENTIALS", str(tmp_path / "creds"))
    monkeypatch.delenv("DREAM_API_KEY", raising=False)
    yield f
    f.close()


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------


def test_a_saved_key_is_0600_and_keeps_the_other_lines(fake, tmp_path):
    path = tmp_path / "creds"
    path.write_text("DREAM_API_URL=https://platform.dreamlabs.ai\nDREAM_API_KEY=dl_live_old\n")
    dream.save_key(GOOD)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    lines = path.read_text().splitlines()
    assert lines == ["DREAM_API_URL=https://platform.dreamlabs.ai", f"DREAM_API_KEY={GOOD}"]
    assert dream.load_key() == GOOD


def test_the_environment_wins_over_the_file(fake, monkeypatch):
    dream.save_key(GOOD)
    monkeypatch.setenv("DREAM_API_KEY", "dl_live_fromenv")
    assert dream.load_key() == "dl_live_fromenv"


def test_clearing_removes_only_the_key(fake, tmp_path):
    path = tmp_path / "creds"
    path.write_text("DREAM_API_URL=https://x.example\n")
    dream.save_key(GOOD)
    assert dream.clear_key() is True
    assert path.read_text() == "DREAM_API_URL=https://x.example\n"
    assert dream.clear_key() is False
    assert dream.load_key() is None


def test_a_pasted_key_with_a_typographic_quote_is_refused_without_showing_it():
    with pytest.raises(ValueError) as why:
        dream.check_key_text("dl_live_abc”")
    assert "dl_live" not in str(why.value)


def test_the_hint_is_the_last_four_only():
    assert dream.hint(GOOD) == "...c3d4"
    assert dream.hint(None) is None


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


def test_the_platform_refusal_is_read_in_its_own_words(fake):
    with pytest.raises(dream.DreamError) as why:
        dream.list_models("dl_live_nope")
    assert why.value.code == "invalid_api_key"


def test_a_model_fits_only_the_body_it_was_trained_on(fake):
    models = {m["id"]: dream.model_summary(m, DUCK) for m in dream.list_models(GOOD)}
    assert models[dream.BASE_MODEL]["fits"] is False
    assert models["dream-1-ft:a1b2c3d4-duck"]["fits"] is True


def test_a_session_learns_its_id_then_sends_it_back(fake):
    s = dream.Session(GOOD, "dream-1-ft:a1b2c3d4-duck")
    s.get_action({"prompt": "walk to the mat", "state.velocity": [0, 0, 0], "video.head": ["AAAA"]})
    s.get_action({"prompt": "walk to the mat", "state.velocity": [0, 0, 0], "video.head": ["AAAA"]})
    first, second = (r["body"] for r in fake.requests)
    assert first["session_id"] is None and second["session_id"] == "ses_1"
    assert first["checkpoint"] == "dream-1-ft:a1b2c3d4-duck"
    assert fake.requests[0]["auth"] == f"Bearer {GOOD}"
    assert fake.requests[0]["ua"].startswith("opencastor-dream/")


def test_a_refused_key_on_the_gateway_names_what_to_do(fake):
    s = dream.Session("dl_live_nope", "dream-1-ft:a1b2c3d4-duck")
    with pytest.raises(dream.DreamError) as why:
        s.get_action({"prompt": "x"})
    assert why.value.code == "DA-1000" and why.value.reason == "unknown_key"
    assert "dl_live_nope" not in str(why.value)


def test_a_warming_model_is_retryable_with_its_wait(fake):
    fake.next_status = {
        "code": "DA-2000",
        "name": "UNAVAILABLE",
        "reason": "pool_warming",
        "retryable": True,
        "retry_after_s": 12,
    }
    with pytest.raises(dream.DreamError) as why:
        dream.Session(GOOD, "m").get_action({"prompt": "x"})
    assert why.value.retryable and why.value.retry_after_s == 12.0


def test_ending_frees_the_seat_and_forgets_the_id(fake):
    s = dream.Session(GOOD, "m")
    s.get_action({"prompt": "x"})
    s.end()
    assert fake.requests[-1]["path"] == "/v1/end_session"
    assert fake.requests[-1]["body"] == {"checkpoint": "m", "session_id": "ses_1"}
    assert s.session_id is None


def test_a_chunk_becomes_rows_of_the_action_vector():
    rows = dream.chunk_rows(
        {"per_key": {"action.a": [[1, 2]], "action.b": [[3]]}}, {"a": [0, 2], "b": [2, 3]}
    )
    assert rows == [[1.0, 2.0, 3.0]]
    with pytest.raises(dream.DreamError):
        dream.chunk_rows({"per_key": {"action.a": [[1]]}}, {"a": [0, 2]})


# ---------------------------------------------------------------------------
# The bridge's dream.* verbs
# ---------------------------------------------------------------------------


@pytest.fixture()
def duck(fake, tmp_path):
    socket_path = str(tmp_path / "robotd.sock")
    robotd = MockRobotd(socket_path)
    port = _start(socket_path, deadman_ms=0)

    def connect():
        client = socket.create_connection(("127.0.0.1", port), timeout=10)
        client.sendall(json.dumps({"microduck": "v1", "token": TOKEN}).encode() + b"\n")
        greeting = json.loads(_line(client))
        return client, greeting

    try:
        yield robotd, connect
    finally:
        robotd.close()


def _call(sock, method, params=None, rpc_id=1):
    sock.sendall(
        json.dumps(
            {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}
        ).encode()
        + b"\n"
    )
    return json.loads(_line(sock))


def test_the_greeting_says_dream_is_here(duck):
    _, connect = duck
    _, greeting = connect()
    assert greeting["dream"] is True


def test_a_key_is_checked_then_saved_and_never_comes_back(duck, fake):
    robotd, connect = duck
    client, _ = connect()
    bad = _call(client, "dream.key.set", {"key": "dl_live_nope"})
    assert bad["error"]["data"]["code"] == "invalid_api_key"
    assert dream.load_key() is None
    good = _call(client, "dream.key.set", {"key": GOOD, "embodiment": DUCK}, rpc_id=2)
    assert good["result"]["saved"] and good["result"]["key"] == "...c3d4"
    assert [m["fits"] for m in good["result"]["models"]] == [False, True]
    assert GOOD not in json.dumps(good)
    assert dream.load_key() == GOOD
    time.sleep(0.2)
    assert robotd.lines == []  # nothing dream.* ever reaches robotd


def test_an_act_answers_the_chunk_and_moves_nothing(duck, fake):
    robotd, connect = duck
    dream.save_key(GOOD)
    client, _ = connect()
    answer = _call(
        client,
        "dream.act",
        {
            "checkpoint": "dream-1-ft:a1b2c3d4-duck",
            "prompt": "walk to the mat",
            "frames": {"head": [f"F{i}" for i in range(12)]},
            "state": {"velocity": [0, 0, 0]},
        },
    )
    rows = answer["result"]["actions"]["per_key"]["action.velocity"]
    assert len(rows) == 48 and answer["result"]["session_id"] == "ses_1"
    sent = fake.requests[-1]["body"]["observation"]
    assert sent["video.head"] == [f"F{i}" for i in range(3, 12)]  # the last nine, oldest first
    assert sent["state.velocity"] == [0.0, 0.0, 0.0] and sent["prompt"] == "walk to the mat"
    time.sleep(0.2)
    assert robotd.lines == []


def test_an_act_without_a_key_is_refused_by_name(duck):
    _, connect = duck
    client, _ = connect()
    answer = _call(client, "dream.act", {"checkpoint": "m", "prompt": "x"})
    assert "dream.key.set" in answer["error"]["message"]


def test_robotds_lines_and_a_dream_answer_never_interleave(duck, fake):
    """A robotd line split across two chunks, with a slow Dream answer landing between them."""
    robotd, connect = duck
    dream.save_key(GOOD)
    fake.delay_s = 0.3
    client, _ = connect()
    client.sendall(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "dream.act",
                "params": {"checkpoint": "m", "prompt": "x"},
            }
        ).encode()
        + b"\n"
    )
    client.sendall(b'{"jsonrpc":"2.0","id":1,"method":"robot.get_state"}\n')
    deadline = time.time() + 5
    while not robotd.conns and time.time() < deadline:
        time.sleep(0.02)
    conn = robotd.conns[0]
    conn.sendall(b'{"jsonrpc":"2.0","id":1,"res')  # half a line
    time.sleep(0.6)  # the Dream answer arrives meanwhile
    conn.sendall(b'ult":{"ok":true}}\n')
    got = [json.loads(_line(client)), json.loads(_line(client))]
    ids = sorted(m["id"] for m in got)
    assert ids == [1, 9]
    assert robotd.lines == ['{"jsonrpc":"2.0","id":1,"method":"robot.get_state"}']


def test_a_second_act_while_one_waits_is_refused(duck, fake):
    _, connect = duck
    dream.save_key(GOOD)
    fake.delay_s = 0.5
    client, _ = connect()
    for rpc_id in (1, 2):
        client.sendall(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "method": "dream.act",
                    "params": {"checkpoint": "m", "prompt": "x"},
                }
            ).encode()
            + b"\n"
        )
    first = json.loads(_line(client))
    assert first["id"] == 2 and "one at a time" in first["error"]["message"]
    assert json.loads(_line(client))["id"] == 1


def test_a_line_that_only_mentions_dream_still_reaches_robotd(duck):
    robotd, connect = duck
    client, _ = connect()
    line = '{"jsonrpc":"2.0","id":3,"method":"robot.sound","params":{"tag":"dream.wav"}}'
    client.sendall(line.encode() + b"\n")
    deadline = time.time() + 3
    while not robotd.lines and time.time() < deadline:
        time.sleep(0.02)
    assert robotd.lines == [line]


def test_a_client_that_leaves_ends_its_session(duck, fake):
    _, connect = duck
    dream.save_key(GOOD)
    client, _ = connect()
    _call(client, "dream.act", {"checkpoint": "m", "prompt": "x"})
    client.close()
    deadline = time.time() + 3
    while time.time() < deadline and fake.requests[-1]["path"] != "/v1/end_session":
        time.sleep(0.05)
    assert fake.requests[-1]["path"] == "/v1/end_session"


def test_bridge_still_runs_with_nothing_installed():
    """castor.dream is standard library only, like the bridge that imports it."""
    import ast
    import sys

    tree = ast.parse(open(dream.__file__).read())
    names = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    names |= {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert names - set(sys.stdlib_module_names) - {"__future__"} == set()
    assert bridge.DreamVerbs.wants(b'{"method":"dream.act"}')
