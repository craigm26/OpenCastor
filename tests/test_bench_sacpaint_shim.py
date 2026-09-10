"""Translation tests for the Claude-subscription shim.

Every test that needs a CLI uses a fake ``claude`` script that echoes canned
JSON. The real CLI is never invoked here: it costs subscription usage, needs
network and a login, and its latency would make the suite useless.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "inspect_robots", reason="castor bench sacpaint needs pip install 'opencastor[paintbench]'"
)

import base64
import json
import stat
import struct
import sys
import threading
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import pytest

from castor.bench.sacpaint.claude_shim import translate
from castor.bench.sacpaint.claude_shim.server import ClaudeCLIError, ClaudeRunner, make_server

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _png_bytes(width: int = 2, height: int = 2) -> bytes:
    """A real, minimal PNG so the shim's writes can be checked byte for byte."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _data_url(raw: bytes, media: str = "image/png") -> str:
    return f"data:{media};base64," + base64.b64encode(raw).decode("ascii")


MOVE_TOOL = {
    "type": "function",
    "function": {
        "name": "move_to",
        "description": "Move to absolute targets.",
        "parameters": {
            "type": "object",
            "properties": {"targets": {"type": "object"}, "note": {"type": "string"}},
            "required": ["targets", "note"],
        },
    },
}
DONE_TOOL = {
    "type": "function",
    "function": {
        "name": "done",
        "description": "Declare the task finished.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "hindsight": {"type": "string"},
            },
            "required": ["summary", "hindsight"],
        },
    },
}
TAKE_PIC_TOOL = {
    "type": "function",
    "function": {
        "name": "take_pic",
        "description": "Capture the current frame.",
        "parameters": {
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": ["note"],
        },
    },
}
GIVE_UP_TOOL = {
    "type": "function",
    "function": {
        "name": "give_up",
        "description": "Stop trying.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "hindsight": {"type": "string"},
            },
            "required": ["reason", "hindsight"],
        },
    },
}
ALL_TOOLS = [MOVE_TOOL, DONE_TOOL, GIVE_UP_TOOL, TAKE_PIC_TOOL]
ALL_NAMES = ("move_to", "done", "give_up", "take_pic")


def _envelope(result: str, *, structured: dict | None = None, **extra) -> dict:
    """A CLI JSON envelope shaped like the real ``--output-format json`` one."""
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result,
        "session_id": "fake-session",
        "num_turns": 2,
        "usage": {
            "input_tokens": 20,
            "output_tokens": 130,
            "cache_creation_input_tokens": 10000,
            "cache_read_input_tokens": 500,
        },
    }
    if structured is not None:
        payload["structured_output"] = structured
    payload.update(extra)
    return payload


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    """Write a fake ``claude`` executable and return its path.

    It records the argv and stdin it was handed into ``calls.jsonl`` beside
    itself, then prints whatever JSON sits in ``response.json``. That is enough
    to assert both directions of the translation without a real CLI.
    """
    home = tmp_path / "fakebin"
    home.mkdir()
    response = home / "response.json"
    response.write_text(
        json.dumps(
            _envelope(
                json.dumps(
                    {
                        "tool": "move_to",
                        "arguments": {"targets": {"x": 0.1}, "note": "hi"},
                    }
                )
            )
        )
    )
    script = home / "claude"
    script.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        "here = os.path.dirname(os.path.abspath(__file__))\n"
        "record = {'argv': sys.argv[1:], 'stdin': sys.stdin.read(), 'cwd': os.getcwd(),\n"
        "          'has_api_key': 'ANTHROPIC_API_KEY' in os.environ}\n"
        "with open(os.path.join(here, 'calls.jsonl'), 'a') as fh:\n"
        "    fh.write(json.dumps(record) + '\\n')\n"
        "code = int(os.environ.get('FAKE_CLAUDE_EXIT', '0'))\n"
        "with open(os.path.join(here, 'response.json')) as fh:\n"
        "    sys.stdout.write(fh.read())\n"
        "sys.exit(code)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _calls(fake_claude: Path) -> list[dict]:
    path = fake_claude.parent / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------
# request -> prompt
# --------------------------------------------------------------------------


def test_system_messages_leave_the_transcript(tmp_path: Path) -> None:
    system, transcript, images = translate.render_conversation(
        [
            {"role": "system", "content": "You drive a plotter."},
            {"role": "user", "content": "Goal: draw the skyline"},
        ],
        tmp_path,
    )
    assert system == "You drive a plotter."
    assert "You drive a plotter." not in transcript
    assert "Goal: draw the skyline" in transcript
    assert images == []


def test_multiple_system_messages_concatenate(tmp_path: Path) -> None:
    system, _, _ = translate.render_conversation(
        [
            {"role": "system", "content": "First."},
            {"role": "developer", "content": "Second."},
            {"role": "user", "content": "go"},
        ],
        tmp_path,
    )
    assert system == "First.\n\nSecond."


def test_images_become_files_referenced_by_absolute_path(tmp_path: Path) -> None:
    raw = _png_bytes()
    _, transcript, images = translate.render_conversation(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "camera 'top_cam' (step 3):"},
                    {"type": "image_url", "image_url": {"url": _data_url(raw)}},
                ],
            }
        ],
        tmp_path,
    )
    assert len(images) == 1
    written = Path(images[0])
    assert written.is_absolute()
    # The bytes must survive intact or the model sees a corrupt frame.
    assert written.read_bytes() == raw
    assert written.suffix == ".png"
    assert str(written) in transcript
    assert "Read tool" in transcript
    # The base64 payload must not also be inlined; that would double the prompt.
    assert base64.b64encode(raw).decode("ascii")[:32] not in transcript


def test_jpeg_media_type_keeps_its_extension(tmp_path: Path) -> None:
    _, _, images = translate.render_conversation(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_url(b"\xff\xd8\xff", "image/jpeg")},
                    }
                ],
            }
        ],
        tmp_path,
    )
    assert Path(images[0]).suffix == ".jpg"


def test_multiple_images_get_distinct_paths(tmp_path: Path) -> None:
    url = _data_url(_png_bytes())
    _, transcript, images = translate.render_conversation(
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": url}},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": url}}],
            },
        ],
        tmp_path,
    )
    assert len(set(images)) == 3
    assert "[image #3" in transcript


def test_remote_image_url_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(translate.TranslationError, match="data: URL"):
        translate.render_conversation(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/a.png"},
                        }
                    ],
                }
            ],
            tmp_path,
        )


def test_prior_tool_calls_and_results_are_replayed(tmp_path: Path) -> None:
    _, transcript, _ = translate.render_conversation(
        [
            {"role": "user", "content": "Goal: draw"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "move_to",
                            "arguments": '{"targets": {"x": 0.2}, "note": "start"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "moved 12 steps"},
        ],
        tmp_path,
    )
    assert "you called move_to" in transcript
    assert '"targets": {"x": 0.2}' in transcript
    assert "call_1" in transcript
    assert "moved 12 steps" in transcript
    # Order matters: the model must see the result after the call.
    assert transcript.index("you called move_to") < transcript.index("moved 12 steps")


def test_full_history_is_rebuilt_every_time_statelessly(tmp_path: Path) -> None:
    """Two prepares of the same body produce the same prompt: no hidden state."""
    body = {
        "model": "haiku",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Goal: draw"},
        ],
        "tools": ALL_TOOLS,
    }
    first = translate.prepare(body, tmp_path / "a")
    second = translate.prepare(body, tmp_path / "b")
    assert first.prompt == second.prompt
    assert first.system_prompt == second.system_prompt


def test_prepare_rejects_an_empty_conversation(tmp_path: Path) -> None:
    with pytest.raises(translate.TranslationError, match="messages"):
        translate.prepare({"model": "haiku", "messages": []}, tmp_path)


# --------------------------------------------------------------------------
# tools -> instruction + schema
# --------------------------------------------------------------------------


def test_tool_instruction_names_every_tool_and_its_schema() -> None:
    text = translate.tool_instruction(ALL_TOOLS)
    for name in ALL_NAMES:
        assert name in text
    assert '"tool"' in text
    assert "exactly one" in text
    # The required arguments have to reach the model or it will omit them.
    assert "hindsight" in text
    assert "note" in text


def test_tool_schema_pins_the_answer_to_one_known_tool() -> None:
    schema = translate.tool_schema(ALL_TOOLS)
    assert schema is not None
    assert schema["properties"]["tool"]["enum"] == list(ALL_NAMES)
    assert schema["required"] == ["tool", "arguments"]
    assert schema["additionalProperties"] is False


def test_no_tools_means_no_schema_and_no_instruction() -> None:
    assert translate.tool_schema([]) is None
    assert translate.tool_instruction(None) == ""


def test_anthropic_native_tool_declarations_are_understood() -> None:
    native = [
        {
            "name": "move_to",
            "description": "Move.",
            "input_schema": {
                "type": "object",
                "properties": {"targets": {"type": "object"}},
            },
        }
    ]
    assert translate.tool_schema(native)["properties"]["tool"]["enum"] == ["move_to"]
    assert "move_to" in translate.tool_instruction(native)


# --------------------------------------------------------------------------
# CLI envelope -> response
# --------------------------------------------------------------------------


def test_structured_output_becomes_an_openai_tool_call() -> None:
    envelope = _envelope(
        "ignored text",
        structured={
            "tool": "move_to",
            "arguments": {"targets": {"x": 0.3}, "note": "n"},
        },
    )
    response = translate.chat_response(envelope, ALL_NAMES, "haiku")
    message = response["choices"][0]["message"]
    assert response["choices"][0]["finish_reason"] == "tool_calls"
    assert message["content"] is None
    call = message["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "move_to"
    # arguments must be a JSON *string*, which is what the caller parses.
    assert isinstance(call["function"]["arguments"], str)
    assert json.loads(call["function"]["arguments"]) == {
        "targets": {"x": 0.3},
        "note": "n",
    }


def test_tool_call_ids_are_unique_across_responses() -> None:
    envelope = _envelope(
        "", structured={"tool": "done", "arguments": {"summary": "s", "hindsight": "h"}}
    )
    a = translate.chat_response(envelope, ALL_NAMES, "haiku")
    b = translate.chat_response(envelope, ALL_NAMES, "haiku")
    id_a = a["choices"][0]["message"]["tool_calls"][0]["id"]
    id_b = b["choices"][0]["message"]["tool_calls"][0]["id"]
    assert id_a != id_b


@pytest.mark.parametrize(
    "text",
    [
        '{"tool": "done", "arguments": {"summary": "s", "hindsight": "h"}}',
        'Sure!\n```json\n{"tool": "done", "arguments": {"summary": "s", "hindsight": "h"}}\n```',
        'Here is my call: {"tool": "done", "arguments": {"summary": "s", "hindsight": "h"}} done.',
        '{"name": "done", "arguments": {"summary": "s", "hindsight": "h"}}',
        '{"tool": "done", "input": {"summary": "s", "hindsight": "h"}}',
        '{"done": {"summary": "s", "hindsight": "h"}}',
    ],
)
def test_tool_call_survives_the_shapes_models_actually_emit(text: str) -> None:
    """A step lost to a spelling difference is a wasted robot turn."""
    _, call = translate.extract_tool_call(_envelope(text), ALL_NAMES)
    assert call is not None, text
    assert call["name"] == "done"
    assert call["arguments"]["summary"] == "s"


def test_stringified_arguments_are_reparsed() -> None:
    text = json.dumps(
        {"tool": "move_to", "arguments": json.dumps({"targets": {"x": 1}, "note": "n"})}
    )
    _, call = translate.extract_tool_call(_envelope(text), ALL_NAMES)
    assert call["arguments"] == {"targets": {"x": 1}, "note": "n"}


def test_take_pic_and_give_up_round_trip() -> None:
    for name, args in (
        ("take_pic", {"note": "check the canvas", "cameras": ["top_cam"]}),
        ("give_up", {"reason": "pen is dry", "hindsight": "check ink first"}),
    ):
        response = translate.chat_response(
            _envelope("", structured={"tool": name, "arguments": args}),
            ALL_NAMES,
            "haiku",
        )
        call = response["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == name
        assert json.loads(call["function"]["arguments"]) == args


def test_an_unknown_tool_name_is_not_smuggled_through() -> None:
    """Better a plain-text turn the caller can nudge than a bogus tool call."""
    _, call = translate.extract_tool_call(
        _envelope('{"tool": "teleport", "arguments": {}}'), ALL_NAMES
    )
    assert call is None


def test_plain_prose_becomes_a_content_only_message() -> None:
    response = translate.chat_response(_envelope("I am not sure what to do."), ALL_NAMES, "haiku")
    choice = response["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "I am not sure what to do."
    assert "tool_calls" not in choice["message"]


def test_response_carries_the_wire_label() -> None:
    response = translate.chat_response(_envelope("hi"), ALL_NAMES, "haiku")
    assert response["wire"] == translate.WIRE_LABEL == "claude-code-cli"


def test_usage_folds_cache_tokens_into_the_prompt_count() -> None:
    usage = translate.usage_from_envelope(_envelope("hi"))
    assert usage["prompt_tokens"] == 20 + 500 + 10000
    assert usage["completion_tokens"] == 130
    assert usage["total_tokens"] == 10650
    assert usage["prompt_tokens_details"]["cached_tokens"] == 500


def test_missing_usage_block_is_omitted_not_zeroed() -> None:
    assert translate.usage_from_envelope({"result": "hi"}) == {}
    assert "usage" not in translate.chat_response({"result": "hi"}, ALL_NAMES, "haiku")


# --------------------------------------------------------------------------
# Anthropic Messages wire
# --------------------------------------------------------------------------


def test_anthropic_body_normalises_to_the_chat_shape() -> None:
    body = translate.anthropic_to_chat_body(
        {
            "model": "haiku",
            "system": [{"type": "text", "text": "You drive a plotter."}],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Goal: draw"}]},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "move_to",
                            "input": {"x": 1},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "moved",
                        }
                    ],
                },
            ],
        }
    )
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "move_to"
    assert body["messages"][3]["tool_call_id"] == "toolu_1"
    assert body["messages"][3]["content"] == "moved"


def test_anthropic_response_emits_a_tool_use_block() -> None:
    response = translate.anthropic_response(
        _envelope(
            "",
            structured={
                "tool": "done",
                "arguments": {"summary": "s", "hindsight": "h"},
            },
        ),
        ALL_NAMES,
        "haiku",
    )
    assert response["stop_reason"] == "tool_use"
    block = response["content"][0]
    assert block["type"] == "tool_use"
    assert block["name"] == "done"
    assert block["input"] == {"summary": "s", "hindsight": "h"}
    assert response["usage"]["output_tokens"] == 130


# --------------------------------------------------------------------------
# the subprocess call, against the fake binary
# --------------------------------------------------------------------------


def test_argv_carries_the_flags_that_make_the_call_cheap_and_safe(
    fake_claude: Path, tmp_path: Path
) -> None:
    runner = ClaudeRunner(claude_bin=str(fake_claude), model="haiku")
    plan = translate.prepare(
        {
            "model": "sonnet",
            "messages": [{"role": "user", "content": "go"}],
            "tools": ALL_TOOLS,
        },
        tmp_path / "frames",
    )
    argv = runner.build_argv(plan, tmp_path / "frames")
    assert argv[0] == str(fake_claude)
    assert "-p" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"  # request beats the default
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--restricted" in argv
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["tool"]["enum"] == list(ALL_NAMES)
    # No images in this request, so the Read tool stays off.
    assert argv[argv.index("--allowedTools") + 1] == ""


def test_images_switch_the_read_tool_on(fake_claude: Path, tmp_path: Path) -> None:
    runner = ClaudeRunner(claude_bin=str(fake_claude))
    frames = tmp_path / "frames"
    plan = translate.prepare(
        {
            "model": "haiku",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": _data_url(_png_bytes())},
                        }
                    ],
                }
            ],
            "tools": ALL_TOOLS,
        },
        frames,
    )
    argv = runner.build_argv(plan, frames)
    assert argv[argv.index("--allowedTools") + 1] == "Read"
    assert argv[argv.index("--add-dir") + 1] == str(frames)


def test_run_sends_the_prompt_on_stdin_and_strips_the_api_key(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")
    runner = ClaudeRunner(claude_bin=str(fake_claude), workdir=tmp_path / "work")
    frames = tmp_path / "frames"
    frames.mkdir()
    plan = translate.prepare(
        {
            "model": "haiku",
            "messages": [{"role": "user", "content": "Goal: draw"}],
            "tools": ALL_TOOLS,
        },
        frames,
    )
    envelope = runner.run(plan, frames)
    assert envelope["result"]

    (call,) = _calls(fake_claude)
    # A long conversation must not go through argv, which has a hard size cap.
    assert "Goal: draw" in call["stdin"]
    assert not any("Goal: draw" in arg for arg in call["argv"])
    # The whole point of the shim is the subscription, not a metered key.
    assert call["has_api_key"] is False


def test_a_nonzero_exit_with_a_usable_envelope_keeps_the_answer(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observed live: the CLI exits 1 and still prints a good answer.

    Discarding it turned one paid call into a 502, and the caller's 5xx retry
    then spent a second one. The answer must survive the exit code.
    """
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    (fake_claude.parent / "response.json").write_text(
        json.dumps(_envelope('{"tool": "done", "arguments": {"summary": "s", "hindsight": "h"}}'))
    )
    runner = ClaudeRunner(claude_bin=str(fake_claude), workdir=tmp_path / "work")
    frames = tmp_path / "frames"
    frames.mkdir()
    plan = translate.prepare(
        {
            "model": "haiku",
            "messages": [{"role": "user", "content": "x"}],
            "tools": ALL_TOOLS,
        },
        frames,
    )
    envelope = runner.run(plan, frames)
    _, call = translate.extract_tool_call(envelope, ALL_NAMES)
    assert call["name"] == "done"


def test_a_nonzero_exit_with_no_usable_output_is_an_error(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    (fake_claude.parent / "response.json").write_text("Invalid API key")
    runner = ClaudeRunner(claude_bin=str(fake_claude), workdir=tmp_path / "work")
    frames = tmp_path / "frames"
    frames.mkdir()
    plan = translate.prepare(
        {"model": "haiku", "messages": [{"role": "user", "content": "x"}]}, frames
    )
    with pytest.raises(ClaudeCLIError, match="exited 1"):
        runner.run(plan, frames)


def test_a_missing_binary_says_how_to_fix_it(tmp_path: Path) -> None:
    runner = ClaudeRunner(claude_bin=str(tmp_path / "nope"), workdir=tmp_path / "work")
    frames = tmp_path / "frames"
    frames.mkdir()
    plan = translate.prepare(
        {"model": "haiku", "messages": [{"role": "user", "content": "x"}]}, frames
    )
    with pytest.raises(ClaudeCLIError, match="claude CLI not found"):
        runner.run(plan, frames)


def test_non_json_output_is_reported_not_swallowed(fake_claude: Path, tmp_path: Path) -> None:
    (fake_claude.parent / "response.json").write_text("Login required.")
    runner = ClaudeRunner(claude_bin=str(fake_claude), workdir=tmp_path / "work")
    frames = tmp_path / "frames"
    frames.mkdir()
    plan = translate.prepare(
        {"model": "haiku", "messages": [{"role": "user", "content": "x"}]}, frames
    )
    with pytest.raises(ClaudeCLIError, match="not JSON"):
        runner.run(plan, frames)


def test_an_error_envelope_that_still_answered_is_kept(fake_claude: Path, tmp_path: Path) -> None:
    """A max-turns stop after a good answer must not throw away the answer."""
    (fake_claude.parent / "response.json").write_text(
        json.dumps(
            _envelope(
                '{"tool": "done", "arguments": {"summary": "s", "hindsight": "h"}}',
                is_error=True,
                subtype="error_max_turns",
            )
        )
    )
    runner = ClaudeRunner(claude_bin=str(fake_claude), workdir=tmp_path / "work")
    frames = tmp_path / "frames"
    frames.mkdir()
    plan = translate.prepare(
        {
            "model": "haiku",
            "messages": [{"role": "user", "content": "x"}],
            "tools": ALL_TOOLS,
        },
        frames,
    )
    envelope = runner.run(plan, frames)
    _, call = translate.extract_tool_call(envelope, ALL_NAMES)
    assert call["name"] == "done"


def test_an_empty_error_envelope_is_fatal(fake_claude: Path, tmp_path: Path) -> None:
    (fake_claude.parent / "response.json").write_text(
        json.dumps({"is_error": True, "result": "", "errors": ["Credit balance too low"]})
    )
    runner = ClaudeRunner(claude_bin=str(fake_claude), workdir=tmp_path / "work")
    frames = tmp_path / "frames"
    frames.mkdir()
    plan = translate.prepare(
        {"model": "haiku", "messages": [{"role": "user", "content": "x"}]}, frames
    )
    with pytest.raises(ClaudeCLIError, match="Credit balance"):
        runner.run(plan, frames)


# --------------------------------------------------------------------------
# end to end over HTTP, still against the fake binary
# --------------------------------------------------------------------------


@pytest.fixture
def shim_url(fake_claude: Path, tmp_path: Path):
    runner = ClaudeRunner(claude_bin=str(fake_claude), workdir=tmp_path / "work")
    httpd = make_server(runner, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(url: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer whatever",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_chat_completions_endpoint_returns_a_tool_call(shim_url: str) -> None:
    status, payload = _post(
        f"{shim_url}/v1/chat/completions",
        {
            "model": "haiku",
            "messages": [
                {"role": "system", "content": "You drive a plotter."},
                {"role": "user", "content": "Goal: draw the skyline"},
            ],
            "tools": ALL_TOOLS,
        },
    )
    assert status == 200
    # This is the exact path inspect-robots' ChatClient parses.
    message = payload["choices"][0]["message"]
    call = message["tool_calls"][0]
    assert call["function"]["name"] == "move_to"
    assert json.loads(call["function"]["arguments"])["note"] == "hi"
    assert payload["usage"]["completion_tokens"] == 130


def test_messages_endpoint_returns_a_tool_use_block(shim_url: str) -> None:
    status, payload = _post(
        f"{shim_url}/v1/messages",
        {
            "model": "haiku",
            "system": "You drive a plotter.",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Goal: draw"}]}],
            "tools": [
                {
                    "name": "move_to",
                    "description": "Move.",
                    "input_schema": {"type": "object"},
                }
            ],
        },
    )
    assert status == 200
    assert payload["content"][0]["type"] == "tool_use"
    assert payload["content"][0]["name"] == "move_to"


def test_a_bad_body_is_a_400_not_a_crash(shim_url: str) -> None:
    status, payload = _post(f"{shim_url}/v1/chat/completions", {"model": "haiku"})
    assert status == 400
    assert "messages" in payload["error"]["message"]


def test_an_unknown_route_is_a_404(shim_url: str) -> None:
    status, _ = _post(f"{shim_url}/v1/embeddings", {"input": "x"})
    assert status == 404


def test_health_and_models_answer_without_calling_the_cli(shim_url: str, fake_claude: Path) -> None:
    with urllib.request.urlopen(f"{shim_url}/healthz", timeout=10) as response:
        health = json.loads(response.read())
    assert health["status"] == "ok"
    assert health["wire"] == "claude-code-cli"
    with urllib.request.urlopen(f"{shim_url}/v1/models", timeout=10) as response:
        models = json.loads(response.read())
    assert {m["id"] for m in models["data"]} >= {"haiku", "sonnet"}
    assert _calls(fake_claude) == []


def test_frames_are_cleaned_up_after_the_request(shim_url: str, tmp_path: Path) -> None:
    status, _ = _post(
        f"{shim_url}/v1/chat/completions",
        {
            "model": "haiku",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {
                            "type": "image_url",
                            "image_url": {"url": _data_url(_png_bytes())},
                        },
                    ],
                }
            ],
            "tools": ALL_TOOLS,
        },
    )
    assert status == 200
    work = tmp_path / "work"
    leftovers = [p for p in work.rglob("*.png")] if work.exists() else []
    assert leftovers == []


def test_the_real_cli_is_never_touched_by_this_suite(fake_claude: Path) -> None:
    """Guard the guard: every test above must go through the fake binary."""
    assert fake_claude.name == "claude"
    assert str(fake_claude) != shutil_which_claude()


def shutil_which_claude() -> str:
    import shutil

    return shutil.which("claude") or ""
