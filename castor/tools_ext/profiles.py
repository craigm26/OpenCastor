"""
castor/tools_ext/profiles.py — Named execution profiles ($deep / $quick).

Profiles are first-class harness configurations activated by a message prefix
or programmatically via profile= in harness invocation config.

Activation examples:
    $deep debug the navigation stack on Bob
    $quick what is Bob's current CPU temp
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from castor.tools_ext.permissions import PermissionMode


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    model: str
    thinking_budget: int  # 0 = disabled
    tool_permission: PermissionMode
    max_turns: int
    timeout_s: int
    isolated: bool  # git worktree isolation
    suppress_follow_up: bool  # post-compaction suppress_follow_up_questions
    compaction_threshold: int  # tokens before compaction fires


PROFILES: dict[str, ExecutionProfile] = {
    "deep": ExecutionProfile(
        name="deep",
        model="claude-opus-4-6",
        thinking_budget=10000,
        tool_permission=PermissionMode.WORKSPACE_WRITE,
        max_turns=25,
        timeout_s=600,
        isolated=True,
        suppress_follow_up=True,
        compaction_threshold=180000,
    ),
    "quick": ExecutionProfile(
        name="quick",
        model="claude-haiku-4-5-20251001",
        thinking_budget=0,
        tool_permission=PermissionMode.READ_ONLY,
        max_turns=3,
        timeout_s=30,
        isolated=False,
        suppress_follow_up=False,
        compaction_threshold=999999,
    ),
}

_PREFIX_RE = re.compile(r"^\$(\w+)\s+(.*)", re.DOTALL)


def get_profile(name: str) -> ExecutionProfile:
    """Return profile by name. Raises ValueError if unknown."""
    if name not in PROFILES:
        raise ValueError(f"Unknown profile '{name}'. Available: {list(PROFILES.keys())}")
    return PROFILES[name]


def parse_profile_prefix(text: str) -> tuple[str | None, str]:
    """
    Parse a $profile prefix from a message.

    Returns (profile_name, remaining_text) if a known profile prefix is found,
    or (None, original_text) if not.

    Examples:
        "$deep debug the nav stack" -> ("deep", "debug the nav stack")
        "$quick cpu temp"           -> ("quick", "cpu temp")
        "plain message"             -> (None, "plain message")
    """
    m = _PREFIX_RE.match(text.strip())
    if m:
        name = m.group(1).lower()
        if name in PROFILES:
            return name, m.group(2).strip()
    return None, text
