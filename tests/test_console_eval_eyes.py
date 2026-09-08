"""castor.console.eval_eyes — the phone as the eyes and ears of an evaluation.

What these pin, and why each one is here rather than left to the reader:

* the WIRE FORMAT, because a sibling repo's embodiment is written against it and
  a silent rename would be found by a scorer reading a blank canvas;
* the CORNER CONTRACT in both shapes and in the exact TL,TR,BR,BL order
  `sacpaint.rectify` consumes — a mirrored quad rectifies without complaint and
  scores like a bad drawing;
* the REFUSALS: a degenerate quad, a non-JPEG body, a coordinate outside 0..1,
  a NaN. Every one of them produces a plausible-looking number downstream.
* the FEEDBACK CURSOR, because "give me what I have not read" that quietly
  re-delivers or skips a line is worse than no cursor at all.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

#: Not a credential — a scratch string, generated nowhere and stored nowhere.
TOKEN = "oc_console_eval_test_only_not_a_real_token"

#: The smallest thing that is honestly a JPEG for the SOI check's purposes.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"

SQUARE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A console over a scratch home, with the eval state emptied first.

    The eval state is process-global on purpose (one console process per robot),
    so the reset is what keeps these tests from reading each other's frames.
    """
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.setenv("CONSOLE_TOKEN", TOKEN)
    monkeypatch.delenv("EVAL_REFERENCE_PATH", raising=False)
    from castor.console.app import build_app

    c = TestClient(build_app())
    c.post("/eval/reset", headers=auth())
    return c


def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def post_frame(client, body=JPEG, **params):
    return client.post("/eval/frame", content=body,
                       headers={**auth(), "Content-Type": "image/jpeg"}, params=params)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


def test_eval_surface_is_behind_the_console_bearer(client):
    """Unauthenticated, every door is shut — including the reference image, which
    is the one that would otherwise look harmless enough to leave open."""
    for method, path in [("get", "/eval/status"), ("get", "/eval/frame/latest"),
                         ("get", "/eval/corners"), ("get", "/eval/feedback"),
                         ("get", "/eval/reference.png")]:
        assert getattr(client, method)(path).status_code == 401, path
    assert client.post("/eval/frame", content=JPEG).status_code == 401


def test_token_may_ride_as_a_query_parameter(client):
    """An <img> tag cannot set a header, and the frame is shown in one. Same
    token, and it is read-only by construction — it cannot move a wheel."""
    post_frame(client)
    r = client.get("/eval/frame/latest", params={"token": TOKEN})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #


def test_pushed_frame_comes_back_byte_for_byte(client):
    body = JPEG + b"\x00distinct"
    r = post_frame(client, body)
    assert r.status_code == 200 and r.json()["seq"] == 1
    got = client.get("/eval/frame/latest", headers=auth())
    assert got.content == body
    assert got.headers["X-Eval-Frame-Seq"] == "1"
    assert float(got.headers["X-Eval-Frame-Age-S"]) < 5


def test_only_the_newest_frame_is_kept(client):
    """One frame per stream, overwritten. No history is a privacy decision, and
    a test is the only thing that keeps somebody from 'improving' it."""
    post_frame(client, JPEG + b"old")
    post_frame(client, JPEG + b"new")
    got = client.get("/eval/frame/latest", headers=auth())
    assert got.content.endswith(b"new")
    assert got.headers["X-Eval-Frame-Seq"] == "2"


def test_no_frame_is_a_404_not_an_empty_200(client):
    r = client.get("/eval/frame/latest", headers=auth())
    assert r.status_code == 404
    assert client.get("/eval/frame/info", headers=auth()).json()["present"] is False


def test_a_stale_frame_is_refused_when_the_reader_says_so(client):
    """A policy inspecting its own work must never be handed the canvas as it was
    before the last stroke, so staleness is a 404 rather than an old picture."""
    post_frame(client)
    assert client.get("/eval/frame/latest", headers=auth(),
                      params={"max_age_s": 60}).status_code == 200
    r = client.get("/eval/frame/latest", headers=auth(), params={"max_age_s": 0})
    assert r.status_code == 404 and "older than" in r.json()["detail"]


def test_a_body_that_is_not_a_jpeg_is_refused(client):
    r = client.post("/eval/frame", content=b"\x89PNG\r\n\x1a\n", headers=auth())
    assert r.status_code == 415
    assert client.post("/eval/frame", content=b"", headers=auth()).status_code == 422


def test_streams_are_separate_and_named_safely(client):
    post_frame(client, JPEG + b"over", stream="overhead")
    post_frame(client, JPEG + b"wrist", stream="wrist")
    assert client.get("/eval/frame/latest", headers=auth(),
                      params={"stream": "wrist"}).content.endswith(b"wrist")
    assert client.get("/eval/frame/latest", headers=auth()).content.endswith(b"over")
    # A name that would need escaping is a name nobody meant to send.
    assert post_frame(client, stream="../../etc/passwd").status_code == 422


# --------------------------------------------------------------------------- #
# Corners
# --------------------------------------------------------------------------- #


def test_corners_round_trip_in_sacpaint_order(client):
    """The flat list is what `rectify(image, corners=...)` takes; the named dict
    is for whoever has to read the request. Both describe the same four points,
    in TL, TR, BR, BL order, and the order is the whole contract."""
    r = client.post("/eval/corners", json={"corners": SQUARE}, headers=auth())
    assert r.status_code == 200
    got = client.get("/eval/corners", headers=auth()).json()
    assert got["marked"] is True
    assert got["corners"] == SQUARE
    assert got["order"] == ["tl", "tr", "br", "bl"]
    assert got["named"]["tl"] == SQUARE[0] and got["named"]["bl"] == SQUARE[3]


def test_corners_accept_the_named_form_the_phone_sends(client):
    named = {"tl": [0.05, 0.1], "tr": [0.95, 0.12], "br": [0.93, 0.88], "bl": [0.07, 0.9]}
    client.post("/eval/corners", json={"corners": named}, headers=auth())
    got = client.get("/eval/corners", headers=auth()).json()
    assert got["corners"] == [named["tl"], named["tr"], named["br"], named["bl"]]


def test_unmarked_is_an_answer_not_an_error(client):
    got = client.get("/eval/corners", headers=auth()).json()
    assert got == {"stream": "overhead", "marked": False}


def test_a_degenerate_mark_is_refused(client):
    """Four taps in one place make an arithmetically valid homography and a
    geometrically meaningless canvas — which the scorer would score."""
    tiny = [[0.5, 0.5], [0.51, 0.5], [0.51, 0.51], [0.5, 0.51]]
    r = client.post("/eval/corners", json={"corners": tiny}, headers=auth())
    assert r.status_code == 422 and "1%" in r.json()["detail"]


@pytest.mark.parametrize("bad", [
    [[0, 0], [1, 0], [1, 1]],                       # three points
    [[0, 0], [1, 0], [1, 1], [0, 2]],               # off the frame
    [[0, 0], [1, 0], [1, 1], [0, "x"]],             # not numeric
    [[0, 0], [1, 0], [1, 1], [0, True]],            # a JSON true is not a coordinate
    "corners",                                       # not a list at all
])
def test_malformed_corners_are_refused(client, bad):
    assert client.post("/eval/corners", json={"corners": bad},
                       headers=auth()).status_code == 422


def test_nan_corners_are_refused(client):
    """NaN fails every range test silently, so it has to be caught by identity.
    `json.dumps(float('nan'))` emits bare NaN, which is exactly what a broken
    client sends."""
    body = json.dumps({"corners": [[float("nan"), 0], [1, 0], [1, 1], [0, 1]]})
    r = client.post("/eval/corners", content=body,
                    headers={**auth(), "Content-Type": "application/json"})
    assert r.status_code == 422


def test_corners_survive_later_frames_and_are_cleared_by_reset(client):
    client.post("/eval/corners", json={"corners": SQUARE}, headers=auth())
    post_frame(client)
    assert client.get("/eval/corners", headers=auth()).json()["marked"] is True
    client.post("/eval/reset", headers=auth())
    assert client.get("/eval/corners", headers=auth()).json()["marked"] is False


def test_the_frame_header_says_whether_the_canvas_is_marked(client):
    post_frame(client)
    assert client.get("/eval/frame/latest", headers=auth()).headers["X-Eval-Corners-Marked"] == "0"
    client.post("/eval/corners", json={"corners": SQUARE}, headers=auth())
    assert client.get("/eval/frame/latest", headers=auth()).headers["X-Eval-Corners-Marked"] == "1"


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #


def test_feedback_cursor_delivers_each_line_exactly_once(client):
    for text in ("the pen is dry", "it drifted left", "that dome is too small"):
        client.post("/eval/feedback", json={"text": text, "source": "voice"}, headers=auth())
    first = client.get("/eval/feedback", headers=auth(), params={"limit": 2}).json()
    assert [l["text"] for l in first["lines"]] == ["the pen is dry", "it drifted left"]
    assert first["next_since"] == 2
    second = client.get("/eval/feedback", headers=auth(),
                        params={"since": first["next_since"]}).json()
    assert [l["text"] for l in second["lines"]] == ["that dome is too small"]
    assert client.get("/eval/feedback", headers=auth(),
                      params={"since": second["next_since"]}).json()["lines"] == []


def test_feedback_records_how_it_arrived(client):
    client.post("/eval/feedback", json={"text": "spoken", "source": "voice"}, headers=auth())
    client.post("/eval/feedback", json={"text": "typed"}, headers=auth())
    lines = client.get("/eval/feedback", headers=auth()).json()["lines"]
    assert [l["source"] for l in lines] == ["voice", "typed"]


@pytest.mark.parametrize("payload", [
    {"text": "   "}, {"text": 7}, {}, {"text": "ok", "source": "telepathy"},
    {"text": "x" * 2001},
])
def test_malformed_feedback_is_refused(client, payload):
    assert client.post("/eval/feedback", json=payload, headers=auth()).status_code == 422


def test_overflow_is_reported_rather_than_hidden(client):
    """A reader that arrives late must be able to tell 'nobody spoke' from
    'I missed it'."""
    from castor.console import eval_eyes

    for i in range(eval_eyes.MAX_FEEDBACK_LINES + 5):
        client.post("/eval/feedback", json={"text": f"line {i}"}, headers=auth())
    got = client.get("/eval/feedback", headers=auth(), params={"limit": 500}).json()
    assert got["dropped"] == 5
    assert got["latest_seq"] == eval_eyes.MAX_FEEDBACK_LINES + 5
    assert got["lines"][0]["text"] == "line 5"


# --------------------------------------------------------------------------- #
# Reference and status
# --------------------------------------------------------------------------- #


def test_reference_serves_the_configured_png(client, tmp_path, monkeypatch):
    png = tmp_path / "target.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nreference bytes")
    monkeypatch.setenv("EVAL_REFERENCE_PATH", str(png))
    r = client.get("/eval/reference.png", headers=auth())
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == png.read_bytes()


def test_a_host_with_no_reference_says_which_variable_to_set(client, monkeypatch):
    monkeypatch.setenv("EVAL_REFERENCE_PATH", "/nonexistent/target.png")
    r = client.get("/eval/reference.png", headers=auth())
    assert r.status_code == 404 and "EVAL_REFERENCE_PATH" in r.json()["detail"]


def test_status_answers_the_whole_question_in_one_poll(client):
    post_frame(client)
    client.post("/eval/corners", json={"corners": SQUARE}, headers=auth())
    client.post("/eval/feedback", json={"text": "looks right"}, headers=auth())
    got = client.get("/eval/status", headers=auth()).json()
    assert got["default_stream"] == "overhead"
    assert got["streams"][0]["present"] is True
    assert got["streams"][0]["corners_marked"] is True
    assert got["feedback"]["latest_seq"] == 1
