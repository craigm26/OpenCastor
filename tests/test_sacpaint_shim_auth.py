"""The shim's front door: a per-run bearer token and a ceiling on children.

The shim binds 127.0.0.1, which is not a boundary here: agent sessions on this
robot run as the same uid as the bench, so any co-resident process could reach
an unauthenticated shim and spend the owner's Claude subscription, or fork
``claude -p`` until the Pi falls over. These tests pin both doors shut.

Like the rest of the shim suite, every call goes through a fake ``claude``
script. The real CLI is never invoked.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from castor.bench.sacpaint import cli as sacpaint_cli
from castor.bench.sacpaint.claude_shim.server import (
    TOKEN_ENV,
    ClaudeRunner,
    ShimBusy,
    make_server,
    resolve_auth_token,
)

RUN_TOKEN = "run-token-for-tests-0123456789"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _envelope(result: str) -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result,
        "session_id": "fake-session",
        "num_turns": 1,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


ANSWER = json.dumps({"tool": "done", "arguments": {"summary": "s", "hindsight": "h"}})

DONE_TOOL = {
    "type": "function",
    "function": {
        "name": "done",
        "description": "Declare the task finished.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string"}, "hindsight": {"type": "string"}},
            "required": ["summary", "hindsight"],
        },
    },
}

BODY = {
    "model": "haiku",
    "messages": [{"role": "user", "content": "Goal: draw"}],
    "tools": [DONE_TOOL],
}


def _write_fake_claude(tmp_path: Path, *, sleep_s: float = 0.0) -> Path:
    """A stand-in ``claude`` that prints one canned envelope, optionally slowly.

    While it runs it leaves a file named after its pid in ``bin/live``, so a
    test can count real live child processes the way ``pgrep -c claude`` does
    on the robot.
    """
    home = tmp_path / "bin"
    (home / "live").mkdir(parents=True, exist_ok=True)
    (home / "response.json").write_text(json.dumps(_envelope(ANSWER)))
    script = home / "claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys, time\n"
        "here = pathlib.Path(__file__).parent\n"
        "sys.stdin.read()\n"
        "(here / 'started').touch()\n"
        "live = here / 'live' / str(os.getpid())\n"
        "live.touch()\n"
        "try:\n"
        f"    time.sleep({sleep_s!r})\n"
        "    sys.stdout.write((here / 'response.json').read_text())\n"
        "finally:\n"
        "    live.unlink(missing_ok=True)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _live_children(tmp_path: Path) -> int:
    """The local stand-in for ``pgrep -c claude``: live fake-CLI processes."""
    live = tmp_path / "bin" / "live"
    return len(list(live.iterdir())) if live.exists() else 0


def _serve(runner: ClaudeRunner, token: str = RUN_TOKEN):
    httpd = make_server(runner, "127.0.0.1", 0, auth_token=token)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, f"http://127.0.0.1:{httpd.server_address[1]}"


def _post(url: str, body: dict, headers: dict[str, str]) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def shim(tmp_path: Path):
    runner = ClaudeRunner(
        claude_bin=str(_write_fake_claude(tmp_path)), workdir=tmp_path / "work"
    )
    httpd, thread, url = _serve(runner)
    try:
        yield url
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------
# the front door
# --------------------------------------------------------------------------


def test_post_without_authorization_returns_401(shim: str, tmp_path: Path) -> None:
    status, payload = _post(f"{shim}/v1/chat/completions", BODY, {})
    assert status == 401
    assert payload["error"]["type"] == "authentication_error"
    # The point of the 401 is that no subscription call was ever made.
    assert not (tmp_path / "bin" / "started").exists()


def test_post_with_the_wrong_token_returns_401(shim: str, tmp_path: Path) -> None:
    status, _ = _post(
        f"{shim}/v1/chat/completions", BODY, {"Authorization": "Bearer not-the-run-token"}
    )
    assert status == 401
    assert not (tmp_path / "bin" / "started").exists()


def test_post_with_run_token_returns_200(shim: str) -> None:
    status, payload = _post(
        f"{shim}/v1/chat/completions", BODY, {"Authorization": f"Bearer {RUN_TOKEN}"}
    )
    assert status == 200
    call = payload["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "done"


def test_the_messages_wire_may_present_the_token_as_x_api_key(shim: str) -> None:
    """inspect-robots' Anthropic client sends x-api-key, not Authorization."""
    status, payload = _post(
        f"{shim}/v1/messages",
        {
            "model": "haiku",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "go"}]}],
            "tools": [{"name": "done", "description": "d", "input_schema": {"type": "object"}}],
        },
        {"x-api-key": RUN_TOKEN},
    )
    assert status == 200
    assert payload["content"][0]["type"] == "tool_use"


def test_an_unauthenticated_post_to_an_unknown_route_is_still_401(shim: str) -> None:
    """No route probing without the token either."""
    status, _ = _post(f"{shim}/v1/embeddings", {"input": "x"}, {})
    assert status == 401


def test_make_server_refuses_to_run_without_a_token(tmp_path: Path) -> None:
    runner = ClaudeRunner(claude_bin=str(_write_fake_claude(tmp_path)))
    with pytest.raises(ValueError, match="auth_token"):
        make_server(runner, "127.0.0.1", 0, auth_token="")


# --------------------------------------------------------------------------
# the child ceiling
# --------------------------------------------------------------------------


def test_concurrency_ceiling_returns_429_above_limit(tmp_path: Path) -> None:
    """A burst past the ceiling is refused, not queued and not forked."""
    runner = ClaudeRunner(
        claude_bin=str(_write_fake_claude(tmp_path, sleep_s=1.5)),
        workdir=tmp_path / "work",
        max_concurrent_children=1,
    )
    httpd, thread, url = _serve(runner)
    peak = 0
    peak_processes = 0
    results: list[int] = []
    lock = threading.Lock()

    def fire() -> None:
        status, _ = _post(
            f"{url}/v1/chat/completions", BODY, {"Authorization": f"Bearer {RUN_TOKEN}"}
        )
        with lock:
            results.append(status)

    try:
        threads = [threading.Thread(target=fire) for _ in range(6)]
        for t in threads:
            t.start()
        deadline = time.time() + 10
        while time.time() < deadline and any(t.is_alive() for t in threads):
            peak = max(peak, runner.children)
            peak_processes = max(peak_processes, _live_children(tmp_path))
            time.sleep(0.02)
        for t in threads:
            t.join(timeout=10)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert len(results) == 6
    assert 429 in results, f"nothing was refused: {results}"
    assert 200 in results, f"nothing got through: {results}"
    assert set(results) <= {200, 429}
    # The ceiling is the whole point: never more children than configured.
    assert peak <= 1, f"saw {peak} concurrent children with a ceiling of 1"
    # And the same again counted as real processes, not as bookkeeping.
    assert peak_processes >= 1, "the fake CLI never ran; the test proved nothing"
    assert peak_processes <= 1, f"saw {peak_processes} live claude processes with a ceiling of 1"
    assert _live_children(tmp_path) == 0


def test_the_ceiling_lets_sequential_calls_through(shim: str) -> None:
    """The regression gate: a ceiling must not mute a one-call-at-a-time bench."""
    for _ in range(4):
        status, _ = _post(
            f"{shim}/v1/chat/completions", BODY, {"Authorization": f"Bearer {RUN_TOKEN}"}
        )
        assert status == 200


def test_a_slot_is_released_even_when_the_cli_fails(tmp_path: Path) -> None:
    runner = ClaudeRunner(
        claude_bin=str(tmp_path / "no-such-claude"),
        workdir=tmp_path / "work",
        max_concurrent_children=1,
    )
    httpd, thread, url = _serve(runner)
    try:
        for _ in range(3):
            status, _ = _post(
                f"{url}/v1/chat/completions", BODY, {"Authorization": f"Bearer {RUN_TOKEN}"}
            )
            # 502, never 429: a failed call must hand its slot back.
            assert status == 502
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert runner.children == 0


def test_the_slot_helper_raises_rather_than_queueing(tmp_path: Path) -> None:
    runner = ClaudeRunner(claude_bin=str(_write_fake_claude(tmp_path)), max_concurrent_children=1)
    with runner.child_slot():
        with pytest.raises(ShimBusy):
            with runner.child_slot():
                pass  # pragma: no cover - the ceiling must stop us getting here
    # Released again once the first caller is done.
    with runner.child_slot():
        assert runner.children == 1


def test_a_ceiling_below_one_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_concurrent_children"):
        ClaudeRunner(claude_bin=str(_write_fake_claude(tmp_path)), max_concurrent_children=0)


# --------------------------------------------------------------------------
# where the token comes from
# --------------------------------------------------------------------------


def test_the_token_env_var_is_popped_so_no_child_inherits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, "from-the-parent")
    token, invented = resolve_auth_token()
    assert token == "from-the-parent"
    assert invented is False
    assert TOKEN_ENV not in os.environ


def test_a_hand_started_shim_invents_a_token_rather_than_running_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    token, invented = resolve_auth_token()
    assert invented is True
    assert len(token) >= 32


# --------------------------------------------------------------------------
# the run that starts the shim holds the same secret
# --------------------------------------------------------------------------


def _run_args(**over) -> "object":
    import argparse

    base = dict(
        task="sacpaint/photo-v1",
        policy="agent",
        embodiment="sacpaint_plotter",
        log_dir="logs",
        model="haiku",
        max_llm_calls=None,
        no_rerun=False,
        no_prompt=False,
        extra=[],
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_the_run_env_carries_the_real_token_not_a_placeholder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sacpaint_cli, "_inspect_robots_bin", lambda: [sys.executable])
    cmd, env = sacpaint_cli.build_run_command(_run_args(), 8931, RUN_TOKEN)
    assert "api_key_env=SACPAINT_SHIM_KEY" in cmd
    assert env["SACPAINT_SHIM_KEY"] == RUN_TOKEN
    assert env["SACPAINT_SHIM_KEY"] != "unused"


def test_a_run_without_an_explicit_token_still_gets_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sacpaint_cli, "_inspect_robots_bin", lambda: [sys.executable])
    _, first = sacpaint_cli.build_run_command(_run_args(), 8931)
    _, second = sacpaint_cli.build_run_command(_run_args(), 8931)
    assert first["SACPAINT_SHIM_KEY"] not in ("", "unused")
    assert len(first["SACPAINT_SHIM_KEY"]) >= 32
    assert first["SACPAINT_SHIM_KEY"] != second["SACPAINT_SHIM_KEY"]
