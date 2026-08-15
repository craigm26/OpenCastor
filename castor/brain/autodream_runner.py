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

WHERE THE MEMORY LIVES. This runner does NOT name ``robot-memory.md`` itself —
it asks ``castor.brain.memory_paths.memory_file()``, the one resolver the CLI,
the console and the gateway brain's reader all use, so a nightly consolidation
cannot quietly write a file nothing else reads. ``CASTOR_OPENCASTOR_DIR`` still
selects the STATE directory (dream-log.jsonl, health reports, the git checkout
it reads commits from) and is still the first place that resolver looks for a
memory store.

KNOWN SEAM — autoDream DOES NOT EMBED. ``_write_structured_memory`` upserts
entries through ``memory_schema`` and stops there; it never calls
``memory_recall.embed_entries``. So a memory this runner wrote overnight is in
the store, shows up in ``castor memory show``, rides into the brain's context
block — and is NOT recallable by meaning, in ``castor memory recall``, in
``GET /memory/recall``, or in a chat turn's grounding, until someone runs
``castor memory reembed``. That is deliberate for now: this is an unattended
2 a.m. cron job, and having it block on (or silently swallow) a cold Ollama is
a bigger problem than a one-command catch-up. It is written down here rather
than fixed here so that "why did last night's observation not recall?" has an
answer that does not require reading the diff.
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
DREAM_LOG_FILE = OPENCASTOR_DIR / "dream-log.jsonl"
GATEWAY_LOG = Path(os.getenv("CASTOR_GATEWAY_LOG", "/tmp/castor-gateway.log"))

#: An OVERRIDE, not the path — ``None`` means "ask the one resolver" at call
#: time (see the module docstring). This used to be ``OPENCASTOR_DIR /
#: "robot-memory.md"``, computed at import, which on a `castor up` host is a
#: different file from the one the CLI and console mean. Tests set it.
MEMORY_FILE: Path | None = None


def memory_file_path() -> Path:
    """The robot-memory.md this runner reads and writes. See ``MEMORY_FILE``."""
    if MEMORY_FILE is not None:
        return Path(MEMORY_FILE)
    from castor.brain.memory_paths import memory_file

    return memory_file()


def _load_recent_commits() -> list[str]:
    """Return last 5 git commit oneline summaries from OPENCASTOR_DIR."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(OPENCASTOR_DIR), "log", "--oneline", "-5"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.strip().splitlines()
        return [ln.strip() for ln in lines if ln.strip()]
    except Exception:
        return []


def _load_bridge_log_tail(max_lines: int = 20) -> list[str]:
    """Return the last *max_lines* lines of /tmp/castor-bridge.log (if it exists)."""
    bridge_log = Path("/tmp/castor-bridge.log")
    try:
        lines = bridge_log.read_text(encoding="utf-8", errors="replace").splitlines()
        return [ln.strip() for ln in lines[-max_lines:] if ln.strip()]
    except Exception:
        return []


def _load_cron_outcomes(max_entries: int = 3) -> list[str]:
    """Return the *max_entries* most recent dream-log.jsonl 'summary' values."""
    try:
        lines = DREAM_LOG_FILE.read_text(encoding="utf-8").splitlines()
        summaries = []
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
                summary = entry.get("summary", "")
                if summary:
                    summaries.append(summary)
            except Exception:
                continue
            if len(summaries) >= max_entries:
                break
        return list(reversed(summaries))
    except Exception:
        return []


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
        return memory_file_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _write_memory_atomic(content: str) -> None:
    target = memory_file_path()
    # The temp file goes NEXT TO the target, not in OPENCASTOR_DIR: those are
    # the same directory on a legacy host and different ones on a `castor up`
    # host, and os.replace across filesystems is not atomic (and may not work).
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".memory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp).replace(target)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _write_structured_memory(new_entries: list[dict], date_str: str) -> None:
    """Upsert new entries into the structured robot-memory.md via memory_schema."""
    from datetime import datetime, timezone

    from castor.brain.memory_schema import (
        EntryType,
        MemoryEntry,
        apply_confidence_decay,
        load_memory,
        make_entry_id,
        prune_entries,
        save_memory,
    )

    target = memory_file_path()
    mem = load_memory(str(target))
    mem = apply_confidence_decay(mem)
    mem.rrn = RRN if RRN != "unknown" else mem.rrn

    existing_ids = {e.id for e in mem.entries}
    existing_texts = {e.text.lower()[:80]: e for e in mem.entries}

    now = datetime.now(timezone.utc)
    added, reinforced = 0, 0

    for raw in new_entries:
        try:
            etype = EntryType(raw.get("type", "hardware_observation"))
        except ValueError:
            etype = EntryType.HARDWARE_OBSERVATION

        # Untrusted at the source: these entries are an LLM's summary of log
        # lines, and a log line is whatever a stranger's HTTP request put in it.
        # One line, printable, capped — the same contract `castor memory add`
        # enforces, applied where the text enters the store.
        from castor.brain.memory_recall import MEMORY_TEXT_MAX, sanitize_memory_text

        text = sanitize_memory_text(str(raw.get("text", "")))[0][:MEMORY_TEXT_MAX]
        confidence = float(raw.get("confidence", 0.7))
        tags = list(raw.get("tags", []))
        entry_id = make_entry_id(text, etype)

        # Reinforce if text is very similar to an existing entry
        key = text.lower()[:80]
        if entry_id in existing_ids or key in existing_texts:
            existing = existing_texts.get(key) or next(
                (e for e in mem.entries if e.id == entry_id), None
            )
            if existing:
                idx = mem.entries.index(existing)
                mem.entries[idx] = existing.reinforce(nudge=0.1)
                reinforced += 1
                continue

        # New entry
        entry = MemoryEntry(
            id=entry_id,
            type=etype,
            text=text,
            confidence=confidence,
            first_seen=now,
            last_reinforced=now,
            observation_count=1,
            tags=tags,
        )
        mem.entries.append(entry)
        existing_ids.add(entry_id)
        existing_texts[key] = entry
        added += 1

    # Prune stale entries and save
    mem, pruned = prune_entries(mem)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_memory(mem, str(target))
    # KNOWN SEAM: no embedding here — see the module docstring. These entries are
    # in the store and out of `castor memory recall` until `castor memory reembed`.
    logger.info(
        "autoDream: structured memory updated — added=%d reinforced=%d pruned=%d total=%d",
        added,
        reinforced,
        pruned,
        len(mem.entries),
    )


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
    recent_commits = _load_recent_commits()
    bridge_log_tail = _load_bridge_log_tail()
    cron_outcomes = _load_cron_outcomes()

    session = DreamSession(
        session_logs=logs,
        robot_memory=memory,
        health_report=health,
        date=date_str,
        recent_commits=recent_commits,
        bridge_log_tail=bridge_log_tail,
        cron_outcomes=cron_outcomes,
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
    try:
        result: DreamResult = brain.run(session)
    except (TimeoutError, __import__("subprocess").TimeoutExpired) as exc:
        logger.error("autoDream: brain.run() timed out — %s", exc)
        sys.exit(1)

    # Write memory — prefer structured entries, fall back to free-form text
    try:
        if result.entries:
            _write_structured_memory(result.entries, date_str)
        elif result.updated_memory:
            _write_memory_atomic(result.updated_memory)
            logger.info(
                "autoDream: memory updated (free-form, %d chars)", len(result.updated_memory)
            )
        else:
            logger.warning("autoDream: no memory output from brain — leaving unchanged")
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
