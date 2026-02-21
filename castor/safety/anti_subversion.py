"""
Anti-subversion / prompt-injection defense for OpenCastor.

Detects prompt injection attacks in AI model inputs and outputs,
blocks access to forbidden filesystem scopes, and performs basic
anomaly detection on request rates per principal.

Main entry points:
- :func:`scan_input` — scans any input (binary, JSON, motor commands).
  Does **not** apply the base64-payload pattern so that legitimate
  camera frames forwarded through messaging channels are not blocked.
- :func:`scan_text_only` — additionally applies the ``base64_payload``
  pattern; use this for freeform human-typed text fields where a long
  base64 blob is genuinely suspicious.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger("OpenCastor.Safety.AntiSubversion")

# =====================================================================
# Scan result types
# =====================================================================


class ScanVerdict(Enum):
    PASS = "pass"
    FLAG = "flag"  # suspicious but not blocked
    BLOCK = "block"


@dataclass
class ScanResult:
    verdict: ScanVerdict
    reasons: List[str] = field(default_factory=list)
    matched_patterns: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == ScanVerdict.PASS


# =====================================================================
# Prompt injection patterns
# =====================================================================
# Each tuple: (name, compiled_regex, verdict)
# Patterns use word boundaries and case-insensitive matching to
# minimise false positives on legitimate robot commands.

_INJECTION_PATTERNS: List[tuple] = []


def _p(name: str, pattern: str, verdict: ScanVerdict = ScanVerdict.BLOCK):
    _INJECTION_PATTERNS.append((name, re.compile(pattern, re.IGNORECASE | re.DOTALL), verdict))


# 1 — Override previous instructions
_p(
    "ignore_instructions",
    r"\bignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions|prompts|rules)\b",
)
# 2 — Identity hijack
_p("identity_hijack", r"\byou\s+are\s+now\b(?!\s+(?:moving|stopped|idle|active|ready|connected))")
# 3 — Role play injection
_p(
    "role_play",
    r"\b(?:act|behave)\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:a\s+)?(?!robot|controller|motor|arm|sensor)",
)
# 4 — Pretend injection
_p("pretend", r"\bpretend\s+(?:you\s+are|to\s+be)\b(?!\s+(?:stopped|idle))")
# 5 — System prompt extraction
_p(
    "system_prompt_extract",
    r"\b(?:reveal|show|print|output|display|repeat)\s+(?:your\s+)?(?:system\s+prompt|instructions|initial\s+prompt)\b",
)
# 6 — Jailbreak keywords
_p(
    "jailbreak_keyword",
    r"\b(?:jailbreak|jail\s+break|DAN\s+mode|do\s+anything\s+now|developer\s+mode\s+enabled)\b",
)
# 7 — Prompt leaking
_p("prompt_leak", r"\bwhat\s+(?:is|are)\s+your\s+(?:system\s+)?(?:prompt|instructions|rules)\b")
# 8 — Markdown/delimiter injection (triple backtick break-out)
_p("delimiter_injection", r"```\s*(?:system|assistant|user)\b")
# 9 — Token repetition attack (same word repeated 10+ times)
_p("token_repetition", r"\b(\w{3,})\s+(?:\1\s+){9,}")
# 10 — New system message injection
_p("system_msg_inject", r"\[?\s*(?:SYSTEM|ADMIN|ROOT)\s*(?:\]|:)\s*", ScanVerdict.FLAG)
# 11 — Instruction override phrases
_p(
    "instruction_override",
    r"\b(?:disregard|forget|override)\s+(?:all\s+)?(?:previous|prior|above|earlier)?\s*(?:instructions|rules|constraints)\b",
)
# 12 — Encoding evasion (hex escape sequences)
_p("hex_escape", r"(?:\\x[0-9a-fA-F]{2}){6,}")
# 13 — Unicode smuggling (excessive zero-width chars)
_p("unicode_smuggle", r"[\u200b\u200c\u200d\ufeff]{3,}")
# 14 — Multi-line separator attacks
_p("separator_attack", r"[-=]{20,}\s*(?:system|instructions|new\s+prompt)", ScanVerdict.FLAG)


# =====================================================================
# Text-only patterns (applied by scan_text_only, NOT by scan_input)
# These patterns produce false positives on binary/image data and must
# only be used when scanning freeform human-typed text.
# =====================================================================
_TEXT_ONLY_PATTERNS: List[tuple] = []


def _tp(name: str, pattern: str, verdict: ScanVerdict = ScanVerdict.BLOCK):
    _TEXT_ONLY_PATTERNS.append((name, re.compile(pattern, re.IGNORECASE | re.DOTALL), verdict))


# 9 — Base64 encoded payload (long b64 blocks that could hide injections).
#     NOT included in _INJECTION_PATTERNS to avoid false positives on
#     legitimate camera frames forwarded through messaging channels.
_tp("base64_payload", r"[A-Za-z0-9+/]{80,}={0,2}")


# =====================================================================
# Forbidden filesystem scopes
# =====================================================================
FORBIDDEN_PATH_PATTERNS: List[re.Pattern] = [
    re.compile(r"/etc/safety\b"),
    re.compile(r"/var/log/safety\b"),
    re.compile(r"/etc/shadow\b"),
    re.compile(r"/etc/passwd\b"),
    re.compile(r"\.\./"),  # path traversal
    re.compile(r"~root\b"),
]


def _check_forbidden_paths(text: str) -> List[str]:
    """Return list of forbidden path pattern names found in *text*."""
    hits: List[str] = []
    for pat in FORBIDDEN_PATH_PATTERNS:
        if pat.search(text):
            hits.append(f"forbidden_path:{pat.pattern}")
    return hits


# =====================================================================
# Anomaly detection — per-principal rate tracking
# =====================================================================
_ANOMALY_WINDOW_S = 300.0  # 5 minutes
_ANOMALY_MULTIPLIER = 3.0  # flag if current rate > 3× baseline
_MIN_BASELINE_REQUESTS = 5  # need at least this many to compute baseline

_rate_lock = threading.Lock()
_request_history: Dict[str, List[float]] = {}  # principal → timestamps
_baseline_rates: Dict[str, float] = {}  # principal → avg requests per window


def _record_and_check_anomaly(principal: str) -> Optional[str]:
    """Record a request timestamp and return anomaly reason if triggered."""
    now = time.time()
    with _rate_lock:
        hist = _request_history.setdefault(principal, [])
        hist.append(now)
        # Trim to 2× window
        cutoff = now - _ANOMALY_WINDOW_S * 2
        hist[:] = [t for t in hist if t > cutoff]

        # Count in current window
        window_start = now - _ANOMALY_WINDOW_S
        current_count = sum(1 for t in hist if t >= window_start)

        # Compute baseline from older data
        older = [t for t in hist if t < window_start]
        if len(older) >= _MIN_BASELINE_REQUESTS:
            baseline = len(older) / _ANOMALY_WINDOW_S * _ANOMALY_WINDOW_S
            if baseline <= 0:
                baseline = _MIN_BASELINE_REQUESTS
            _baseline_rates[principal] = baseline
        baseline = _baseline_rates.get(principal)
        if baseline and current_count > baseline * _ANOMALY_MULTIPLIER:
            return (
                f"anomaly: {current_count} requests in {_ANOMALY_WINDOW_S}s "
                f"(baseline {baseline:.0f})"
            )
    return None


def reset_anomaly_state():
    """Clear all anomaly tracking (for testing)."""
    with _rate_lock:
        _request_history.clear()
        _baseline_rates.clear()


# =====================================================================
# Main API
# =====================================================================


def scan_input(text: str, principal: str = "unknown") -> ScanResult:
    """Scan *text* for prompt injection and other subversion attempts.

    Returns a :class:`ScanResult` with verdict ``pass``, ``flag``, or ``block``.
    """
    if not text:
        return ScanResult(verdict=ScanVerdict.PASS)

    reasons: List[str] = []
    matched: List[str] = []
    worst = ScanVerdict.PASS

    # --- Prompt injection patterns ---
    for name, pattern, verdict in _INJECTION_PATTERNS:
        if pattern.search(text):
            matched.append(name)
            reasons.append(f"injection:{name}")
            if _verdict_ord(verdict) > _verdict_ord(worst):
                worst = verdict

    # --- Forbidden paths ---
    path_hits = _check_forbidden_paths(text)
    if path_hits:
        matched.extend(path_hits)
        reasons.extend(path_hits)
        worst = ScanVerdict.BLOCK

    # --- Anomaly detection ---
    anomaly = _record_and_check_anomaly(principal)
    if anomaly:
        reasons.append(anomaly)
        if _verdict_ord(ScanVerdict.FLAG) > _verdict_ord(worst):
            worst = ScanVerdict.FLAG

    return ScanResult(verdict=worst, reasons=reasons, matched_patterns=matched)


def scan_text_only(text: str, principal: str = "unknown") -> ScanResult:
    """Scan freeform human-typed *text*, including the base64-payload check.

    Use this instead of :func:`scan_input` when the input is a plain-text
    field (e.g. a chat message typed by a user) and binary/image data will
    never appear.  The additional ``base64_payload`` pattern detects injections
    that try to hide instructions inside a long base64-encoded string, but
    would produce false positives if applied to binary image data.

    Returns a :class:`ScanResult` with verdict ``pass``, ``flag``, or ``block``.
    """
    # Start with the general scan (records anomaly for this principal).
    result = scan_input(text, principal)

    if not text:
        return result

    reasons = list(result.reasons)
    matched = list(result.matched_patterns)
    worst = result.verdict

    # --- Text-only patterns (e.g. base64_payload) ---
    for name, pattern, verdict in _TEXT_ONLY_PATTERNS:
        if pattern.search(text):
            matched.append(name)
            reasons.append(f"injection:{name}")
            if _verdict_ord(verdict) > _verdict_ord(worst):
                worst = verdict

    return ScanResult(verdict=worst, reasons=reasons, matched_patterns=matched)


def _verdict_ord(v: ScanVerdict) -> int:
    return {"pass": 0, "flag": 1, "block": 2}[v.value]


# =====================================================================
# Integration helpers
# =====================================================================


def check_input_safety(text: str, principal: str = "unknown") -> ScanResult:
    """High-level hook for providers / SafetyLayer.

    Logs warnings for flagged/blocked inputs. Returns the ScanResult
    so callers can decide how to respond.
    """
    result = scan_input(text, principal)
    if result.verdict == ScanVerdict.BLOCK:
        logger.warning(
            "BLOCKED input from %s: %s",
            principal,
            "; ".join(result.reasons),
        )
    elif result.verdict == ScanVerdict.FLAG:
        logger.info(
            "FLAGGED input from %s: %s",
            principal,
            "; ".join(result.reasons),
        )
    return result


def scan_before_write(path: str, data, principal: str = "unknown") -> ScanResult:
    """Scan data before writing to /dev/ paths (motor commands from AI).

    Converts *data* to string for scanning. Returns ScanResult.
    """
    if not path.startswith("/dev/"):
        return ScanResult(verdict=ScanVerdict.PASS)
    text = str(data) if data is not None else ""
    return check_input_safety(text, principal)
