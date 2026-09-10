"""Pure translation between HTTP wire formats and one ``claude -p`` invocation.

Nothing in this module runs a subprocess or opens a socket, so every rule here
is directly testable against canned data. :mod:`sacpaint.claude_shim.server`
owns the process and the socket.

Three translations live here:

1. **Request in.** An OpenAI Chat Completions body (or an Anthropic Messages
   body, normalised to the same shape) becomes a single prompt string plus a
   system prompt plus a list of PNG files written to a scratch directory.
   Images cannot ride inside a ``claude -p`` prompt, so each one is written to
   a real file and referenced by absolute path; the CLI's ``Read`` tool loads
   it. That is the only image path that works and it is why the shim needs a
   writable scratch directory.
2. **Tool contract.** The caller's OpenAI tool schemas become a block of prose
   plus a JSON Schema handed to ``claude --json-schema``. The model answers
   with exactly one JSON object ``{"tool": name, "arguments": {...}}``. The
   shim never executes the named tool: it is the caller's job to run it and
   send back a ``tool`` message, exactly as a real API would.
3. **Response out.** The CLI's JSON envelope becomes a Chat Completions
   response carrying a ``tool_calls`` array, or an Anthropic Messages response
   carrying ``tool_use`` blocks.

The conversation is re-rendered from scratch on every request. The shim is
stateless by design: ``inspect-robots`` resends the full history each turn and
relies on the endpoint holding no state between calls.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Marks results as produced through the Claude Code CLI rather than the raw
#: API. Callers surface this so a shim run is never mistaken for an API run.
WIRE_LABEL = "claude-code-cli"

_DATA_URL = re.compile(r"^data:(?P<media>[\w.+-]+/[\w.+-]+);base64,(?P<data>.*)$", re.DOTALL)

_EXT_BY_MEDIA = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


class TranslationError(ValueError):
    """A request the shim cannot turn into a ``claude -p`` invocation."""


@dataclass
class Prepared:
    """Everything the server needs to launch one ``claude -p``."""

    system_prompt: str
    prompt: str
    json_schema: dict[str, Any] | None
    tool_names: tuple[str, ...]
    image_paths: list[str] = field(default_factory=list)
    model: str | None = None

    @property
    def needs_read_tool(self) -> bool:
        """True when at least one image must be opened by the CLI's Read tool."""
        return bool(self.image_paths)


# --------------------------------------------------------------------------
# request -> prompt
# --------------------------------------------------------------------------


def _decode_data_url(url: str) -> tuple[str, bytes]:
    """Return ``(extension, raw_bytes)`` for a base64 ``data:`` URL."""
    match = _DATA_URL.match(url.strip())
    if match is None:
        raise TranslationError(
            "image_url must be a base64 data: URL; the shim cannot fetch remote images"
        )
    media = match.group("media").lower()
    try:
        raw = base64.b64decode(match.group("data"), validate=False)
    except (binascii.Error, ValueError) as exc:  # pragma: no cover - defensive
        raise TranslationError(f"undecodable base64 image payload: {exc}") from exc
    return _EXT_BY_MEDIA.get(media, "png"), raw


def _write_image(raw: bytes, ext: str, image_dir: Path, index: int) -> str:
    """Write one frame to the scratch directory and return its absolute path."""
    image_dir.mkdir(parents=True, exist_ok=True)
    path = image_dir / f"frame_{index:03d}.{ext}"
    path.write_bytes(raw)
    return str(path)


def _render_content(
    content: Any,
    image_dir: Path,
    image_paths: list[str],
) -> str:
    """Flatten one message's content, spilling images to files as it goes."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    chunks: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            chunks.append(str(part))
            continue
        kind = part.get("type")
        if kind == "text":
            chunks.append(str(part.get("text", "")))
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            ext, raw = _decode_data_url(url)
            path = _write_image(raw, ext, image_dir, len(image_paths) + 1)
            image_paths.append(path)
            chunks.append(
                f"[image #{len(image_paths)} saved at {path} -- call the Read tool on that exact path to see it]"
            )
        elif kind == "image":
            # Anthropic-native inline image block.
            source = part.get("source") or {}
            if source.get("type") != "base64":
                raise TranslationError("only base64 image sources are supported")
            media = str(source.get("media_type", "image/png")).lower()
            raw = base64.b64decode(source.get("data", ""), validate=False)
            path = _write_image(
                raw, _EXT_BY_MEDIA.get(media, "png"), image_dir, len(image_paths) + 1
            )
            image_paths.append(path)
            chunks.append(
                f"[image #{len(image_paths)} saved at {path} -- call the Read tool on that exact path to see it]"
            )
        else:
            # Unknown part kinds are rendered rather than dropped, so nothing
            # the caller sent silently disappears from the model's view.
            chunks.append(json.dumps(part, ensure_ascii=False))
    return "\n".join(c for c in chunks if c)


def _render_tool_calls(calls: Any) -> str:
    """Render an assistant turn's tool calls back as readable transcript lines."""
    lines: list[str] = []
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = fn.get("name", "?")
        args = fn.get("arguments", "")
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        lines.append(f"you called {name} with arguments {args} (call id {call.get('id', '?')})")
    return "\n".join(lines)


def render_conversation(
    messages: list[dict[str, Any]],
    image_dir: Path,
) -> tuple[str, str, list[str]]:
    """Split ``messages`` into (system prompt, transcript, image paths).

    System messages are concatenated and handed to ``claude --system-prompt``
    so they replace Claude Code's own prompt rather than sitting inside the
    user turn. Every other message becomes a labelled transcript block.
    """
    system_chunks: list[str] = []
    image_paths: list[str] = []
    blocks: list[str] = []

    for message in messages:
        if not isinstance(message, dict):
            raise TranslationError("every message must be an object")
        role = message.get("role", "user")
        if role == "system" or role == "developer":
            system_chunks.append(_render_content(message.get("content"), image_dir, image_paths))
            continue
        if role == "tool":
            body = _render_content(message.get("content"), image_dir, image_paths)
            call_id = message.get("tool_call_id", "?")
            blocks.append(f"### TOOL RESULT (for call id {call_id})\n{body}")
            continue
        body = _render_content(message.get("content"), image_dir, image_paths)
        if role == "assistant":
            called = _render_tool_calls(message.get("tool_calls"))
            joined = "\n".join(part for part in (body, called) if part)
            blocks.append(f"### YOUR PREVIOUS TURN\n{joined}")
            continue
        blocks.append(f"### USER\n{body}")

    return "\n\n".join(system_chunks).strip(), "\n\n".join(blocks).strip(), image_paths


# --------------------------------------------------------------------------
# tools -> instruction + schema
# --------------------------------------------------------------------------


def _tool_entries(
    tools: list[dict[str, Any]] | None,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Normalise OpenAI and Anthropic tool declarations to (name, desc, schema)."""
    entries: list[tuple[str, str, dict[str, Any]]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if "function" in tool:
            fn = tool.get("function") or {}
            entries.append(
                (
                    str(fn.get("name", "")),
                    str(fn.get("description", "")),
                    fn.get("parameters") or {},
                )
            )
        elif "name" in tool:  # Anthropic-native shape
            entries.append(
                (
                    str(tool.get("name", "")),
                    str(tool.get("description", "")),
                    tool.get("input_schema") or {},
                )
            )
    return [entry for entry in entries if entry[0]]


def tool_instruction(tools: list[dict[str, Any]] | None) -> str:
    """The prose contract: describe every tool and demand exactly one call."""
    entries = _tool_entries(tools)
    if not entries:
        return ""
    lines = [
        "## Tools you may call",
        "",
        "You are driving a robot through a tool-calling API. You cannot run the",
        "tools yourself and you must not try: you name one tool and the harness",
        "executes it, then sends you the result as the next turn.",
        "",
    ]
    for name, description, schema in entries:
        lines.append(f"### {name}")
        if description:
            lines.append(description)
        lines.append(f"arguments JSON Schema: {json.dumps(schema, ensure_ascii=False)}")
        lines.append("")
    lines.extend(
        [
            "## How to answer",
            "",
            "Answer with exactly one JSON object and nothing else:",
            "",
            '    {"tool": "<one tool name from the list above>", "arguments": {...}}',
            "",
            "Rules:",
            "- `arguments` must satisfy that tool's schema, including every",
            "  required property. A missing required argument fails the step.",
            "- Call exactly one tool. Never two, never zero.",
            "- Do not wrap the object in prose, markdown, or a code fence.",
            "- Do not invent tool names. Only these exist: "
            + ", ".join(name for name, _, _ in entries)
            + ".",
        ]
    )
    return "\n".join(lines)


def tool_schema(tools: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The ``--json-schema`` value pinning the reply to one tool call.

    ``arguments`` stays a free-form object: the per-tool schemas are enforced
    in prose and by the caller, because a ``oneOf`` over every tool is rejected
    by strict structured-output validators.
    """
    entries = _tool_entries(tools)
    if not entries:
        return None
    return {
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "enum": [name for name, _, _ in entries],
                "description": "The single tool to call.",
            },
            "arguments": {
                "type": "object",
                "description": "Arguments for that tool, matching its schema.",
            },
        },
        "required": ["tool", "arguments"],
        "additionalProperties": False,
    }


def prepare(body: dict[str, Any], image_dir: Path) -> Prepared:
    """Turn a Chat Completions request body into one CLI invocation plan."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise TranslationError("request must carry a non-empty 'messages' array")
    tools = body.get("tools")
    system_prompt, transcript, image_paths = render_conversation(messages, image_dir)
    instruction = tool_instruction(tools)

    prompt_parts = ["# Conversation so far", "", transcript]
    if instruction:
        prompt_parts.extend(["", instruction])
    prompt_parts.extend(
        [
            "",
            "Now produce your next turn.",
        ]
    )
    if image_paths:
        prompt_parts.append(
            "Read any image path referenced in the most recent turn before you decide."
        )

    entries = _tool_entries(tools)
    return Prepared(
        system_prompt=system_prompt,
        prompt="\n".join(prompt_parts),
        json_schema=tool_schema(tools),
        tool_names=tuple(name for name, _, _ in entries),
        image_paths=image_paths,
        model=body.get("model"),
    )


# --------------------------------------------------------------------------
# CLI envelope -> response
# --------------------------------------------------------------------------


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first balanced ``{...}`` object out of arbitrary model prose."""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    start = -1
                    continue
                if isinstance(parsed, dict):
                    return parsed
                start = -1
    return None


def extract_tool_call(
    envelope: dict[str, Any],
    tool_names: tuple[str, ...],
) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(free_text, tool_call_dict)`` from one CLI JSON envelope.

    ``structured_output`` is trusted first because ``--json-schema`` already
    validated it. Otherwise the model's text is scanned for a JSON object.
    Some models answer with the bare arguments under the tool name as the only
    key, or with ``name`` instead of ``tool``; both are accepted, because a
    step lost to a spelling difference is a wasted robot turn.
    """
    text = envelope.get("result")
    text = None if text is None else str(text)

    candidate = envelope.get("structured_output")
    if not isinstance(candidate, dict):
        candidate = _first_json_object(text or "")
    if not isinstance(candidate, dict):
        return text, None

    name = candidate.get("tool") or candidate.get("name") or candidate.get("tool_name")
    args = candidate.get("arguments")
    if args is None:
        args = candidate.get("input") or candidate.get("parameters")

    if not isinstance(name, str) or (tool_names and name not in tool_names):
        # Fall back to the "{"move_to": {...}}" shape.
        for key, value in candidate.items():
            if key in tool_names and isinstance(value, dict):
                name, args = key, value
                break
        else:
            return text, None

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"value": args}
    if not isinstance(args, dict):
        args = {}

    return text, {"name": name, "arguments": args}


def usage_from_envelope(envelope: dict[str, Any]) -> dict[str, int]:
    """Map the CLI's usage block onto OpenAI-style token counters."""
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return {}

    def _int(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    cached = _int("cache_read_input_tokens")
    prompt_tokens = _int("input_tokens") + cached + _int("cache_creation_input_tokens")
    completion = _int("output_tokens")
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion,
        "total_tokens": prompt_tokens + completion,
        "prompt_tokens_details": {"cached_tokens": cached},
    }


def chat_response(
    envelope: dict[str, Any],
    tool_names: tuple[str, ...],
    model: str,
) -> dict[str, Any]:
    """Build an OpenAI Chat Completions response from one CLI envelope."""
    text, call = extract_tool_call(envelope, tool_names)
    message: dict[str, Any] = {"role": "assistant", "content": None if call else text}
    finish = "stop"
    if call:
        finish = "tool_calls"
        message["tool_calls"] = [
            {
                "id": f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                },
            }
        ]
    response: dict[str, Any] = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:20]}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "wire": WIRE_LABEL,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    usage = usage_from_envelope(envelope)
    if usage:
        response["usage"] = usage
    return response


def anthropic_response(
    envelope: dict[str, Any],
    tool_names: tuple[str, ...],
    model: str,
) -> dict[str, Any]:
    """Build an Anthropic Messages response from one CLI envelope."""
    text, call = extract_tool_call(envelope, tool_names)
    content: list[dict[str, Any]] = []
    if text and not call:
        content.append({"type": "text", "text": text})
    stop_reason = "end_turn"
    if call:
        stop_reason = "tool_use"
        content.append(
            {
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:20]}",
                "name": call["name"],
                "input": call["arguments"],
            }
        )
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}

    def _int(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    return {
        "id": f"msg_{uuid.uuid4().hex[:20]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "wire": WIRE_LABEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": _int("input_tokens"),
            "output_tokens": _int("output_tokens"),
            "cache_creation_input_tokens": _int("cache_creation_input_tokens"),
            "cache_read_input_tokens": _int("cache_read_input_tokens"),
        },
    }


def anthropic_to_chat_body(body: dict[str, Any]) -> dict[str, Any]:
    """Normalise an Anthropic Messages body into the Chat Completions shape."""
    messages: list[dict[str, Any]] = []
    system = body.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "\n".join(
            str(block.get("text", ""))
            for block in system
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if text:
            messages.append({"role": "system", "content": text})

    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "user")
        content = message.get("content")
        if not isinstance(content, list):
            messages.append({"role": role, "content": content})
            continue
        # Anthropic packs tool results into user turns and tool calls into
        # assistant turns; split them back into OpenAI's flat roles.
        parts: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                        },
                    }
                )
            elif kind == "tool_result":
                body_content = block.get("content")
                if isinstance(body_content, list):
                    body_content = "\n".join(
                        str(item.get("text", "")) for item in body_content if isinstance(item, dict)
                    )
                pending_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": body_content if body_content is not None else "",
                    }
                )
            else:
                parts.append(block)
        if parts or tool_calls:
            entry: dict[str, Any] = {"role": role, "content": parts or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            messages.append(entry)
        messages.extend(pending_results)

    out: dict[str, Any] = {"model": body.get("model"), "messages": messages}
    tools = body.get("tools")
    if tools:
        out["tools"] = tools
    return out
