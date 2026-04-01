"""autoDream runner — CLI entry point for nightly KAIROS memory consolidation.

**OPERATOR SCRIPT** — this module is a reference implementation for robot operators.
It is not auto-enabled by default. To use it:

1. Set env vars (see below)
2. Schedule via cron: ``0 2 * * * python -m castor.brain.autodream_runner``

Environment variables:
    CASTOR_MODEL          — LLM model for summarization (default: claude-haiku-4-5-20251001)
    CASTOR_RRN            — Robot Registration Number (e.g. RRN-000000000001)
    CASTOR_OPENCASTOR_DIR — State directory (default: ~/.opencastor)
    CASTOR_GATEWAY_LOG    — Gateway log path (default: /tmp/castor-gateway.log)
    CASTOR_AUTODREAM_DRY_RUN=1  — Skip LLM call and issue filing (safe for testing)
    CASTOR_AUTODREAM_FILE_ISSUES=1  — Enable GitHub issue filing (opt-in, disabled by default)
    CASTOR_GITHUB_REPO    — GitHub repo for issue filing (required if filing enabled)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from castor.brain.autodream import AutoDreamBrain, DreamResult, DreamSession

logger = logging.getLogger("OpenCastor.AutoDreamRunner")

# ── Configuration (all from env — no hardcoded operator defaults) ─────────────
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DRY_RUN = os.getenv("CASTOR_AUTODREAM_DRY_RUN", "0") != "0"
FILE_ISSUES = os.getenv("CASTOR_AUTODREAM_FILE_ISSUES", "0") != "0"
GITHUB_REPO = os.getenv("CASTOR_GITHUB_REPO", "")  # No default — must be set explicitly
RRN = os.getenv("CASTOR_RRN", "unknown")

OPENCASTOR_DIR = Path(os.getenv("CASTOR_OPENCASTOR_DIR", str(Path.home() / ".opencastor")))
MEMORY_FILE = OPENCASTOR_DIR / "robot-memory.md"
DREAM_LOG_FILE = OPENCASTOR_DIR / "dream-log.jsonl"
GATEWAY_LOG = Path(os.getenv("CASTOR_GATEWAY_LOG", "/tmp/castor-gateway.log"))


def _load_health_report(date_str: str) -> dict:
    path = OPENCASTOR_DIR / f"health-{date_str.replace('-', '')}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _load_session_logs(max_lines: int = 200) -> list[str]:
    try:
        with open(GATEWAY_LOG) as f:
            lines = f.readlines()
        error_lines = [
            line.strip()
            for line in lines
            if any(k in line for k in ("ERROR", "WARN", "Exception", "Traceback"))
        ]
        return error_lines[-max_lines:]
    except Exception:
        return []


def _load_memory() -> str:
    try:
        return MEMORY_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _write_memory_atomic(content: str) -> None:
    OPENCASTOR_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=OPENCASTOR_DIR, prefix=".memory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp).replace(MEMORY_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _append_dream_log(entry: dict) -> None:
    OPENCASTOR_DIR.mkdir(parents=True, exist_ok=True)
    with open(DREAM_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _file_issues_if_enabled(issues: list[str], date_str: str) -> list[str]:
    """File GitHub issues only if explicitly opted in. Returns list of URLs."""
    if not FILE_ISSUES:
        if issues:
            logger.info(
                "autoDream detected %d issue(s) but CASTOR_AUTODREAM_FILE_ISSUES not set — skipping. "
                "Set CASTOR_AUTODREAM_FILE_ISSUES=1 and CASTOR_GITHUB_REPO=owner/repo to enable.",
                len(issues),
            )
        return []

    if not GITHUB_REPO:
        logger.warning(
            "CASTOR_AUTODREAM_FILE_ISSUES=1 but CASTOR_GITHUB_REPO is not set — cannot file issues."
        )
        return []

    from castor.brain.autodream_issues import build_issue_template, file_github_issue

    urls = []
    for issue_text in issues:
        template = build_issue_template(issue_text, RRN, date_str)
        url = file_github_issue(template, GITHUB_REPO, dry_run=DRY_RUN)
        if url:
            urls.append(url)
    if urls:
        logger.info("autoDream filed %d issue(s): %s", len(urls), urls)
    return urls


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    date_str = datetime.now().strftime("%Y-%m-%d")
    logger.info(
        "autoDream starting: date=%s dry_run=%s file_issues=%s", date_str, DRY_RUN, FILE_ISSUES
    )

    health = _load_health_report(date_str)
    logs = _load_session_logs()
    memory = _load_memory()

    session = DreamSession(
        session_logs=logs,
        robot_memory=memory,
        health_report=health,
        date=date_str,
    )

    if DRY_RUN:
        logger.info("DRY_RUN: skipping LLM call")
        print(f"autoDream {date_str}: dry-run mode — no LLM call")
        return

    model = os.getenv("CASTOR_MODEL", DEFAULT_MODEL)
    try:
        from castor.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider({"model": model, "system_prompt": ""})
    except Exception as exc:
        logger.error("autoDream: could not init provider (%s) — aborting", exc)
        sys.exit(1)

    brain = AutoDreamBrain(provider=provider)
    result: DreamResult = brain.run(session)

    # Write memory atomically
    try:
        _write_memory_atomic(result.updated_memory)
        logger.info("autoDream: memory updated (%d chars)", len(result.updated_memory))
    except Exception as exc:
        logger.error("autoDream: failed to write memory: %s", exc)
        sys.exit(1)

    # File issues (opt-in only)
    issue_urls = _file_issues_if_enabled(result.issues_detected, date_str)

    # Append dream log
    _append_dream_log(
        {
            "date": date_str,
            "model": model,
            "rrn": RRN,
            "learnings": result.learnings,
            "issues_detected": result.issues_detected,
            "issue_urls": issue_urls,
            "summary": result.summary,
        }
    )

    print(result.summary)
    logger.info(
        "autoDream complete: learnings=%d issues=%d",
        len(result.learnings),
        len(result.issues_detected),
    )


if __name__ == "__main__":
    main()
