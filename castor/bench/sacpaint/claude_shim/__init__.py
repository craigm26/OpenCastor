"""OpenAI/Anthropic-compatible localhost endpoint backed by the Claude Code CLI.

Lets a tool that only speaks HTTP to an LLM API — ``inspect-robots
--policy agent``, for instance — run on the owner's Claude subscription
instead of a metered API key.

Results produced this way go through Claude Code's own harness and system
prompt, so they carry the ``claude-code-cli`` wire label and are not
comparable to raw-API runs. See ``docs/subscription.md``.
"""

from castor.bench.sacpaint.claude_shim.server import (
    DEFAULT_MAX_CONCURRENT_CHILDREN,
    TOKEN_ENV,
    ClaudeCLIError,
    ClaudeRunner,
    ShimBusy,
    make_server,
    resolve_auth_token,
    serve,
)
from castor.bench.sacpaint.claude_shim.translate import (
    WIRE_LABEL,
    Prepared,
    TranslationError,
    anthropic_response,
    anthropic_to_chat_body,
    chat_response,
    extract_tool_call,
    prepare,
    render_conversation,
    tool_instruction,
    tool_schema,
    usage_from_envelope,
)

__all__ = [
    "DEFAULT_MAX_CONCURRENT_CHILDREN",
    "TOKEN_ENV",
    "WIRE_LABEL",
    "ClaudeCLIError",
    "ClaudeRunner",
    "ShimBusy",
    "Prepared",
    "TranslationError",
    "anthropic_response",
    "anthropic_to_chat_body",
    "chat_response",
    "extract_tool_call",
    "make_server",
    "prepare",
    "render_conversation",
    "resolve_auth_token",
    "serve",
    "tool_instruction",
    "tool_schema",
    "usage_from_envelope",
]
