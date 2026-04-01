"""autoDream issue filer — auto-files GitHub issues for detected problems.

**OPERATOR OPT-IN** — This module is NOT invoked by default. Issue filing must be
explicitly enabled by the operator:
    CASTOR_AUTODREAM_FILE_ISSUES=1
    CASTOR_GITHUB_REPO=owner/repo   ← must be set; no default

Used by autodream_runner when DreamResult.issues_detected is non-empty and filing
is opted in. Uses the `gh` CLI so no token management is needed in Python.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger("OpenCastor.AutoDreamIssues")


@dataclass
class IssueTemplate:
    """Structured GitHub issue ready to file."""

    title: str
    body: str
    labels: list[str] = field(default_factory=lambda: ["autodream", "needs-triage"])


def build_issue_template(issue_text: str, robot_rrn: str, date: str) -> IssueTemplate:
    """Build an IssueTemplate from a plain-English issue description.

    Args:
        issue_text: Raw description from the LLM (one issue per string).
        robot_rrn:  RRN of the robot that generated the issue.
        date:       ISO date string (YYYY-MM-DD) for the dream run.

    Returns:
        IssueTemplate with title truncated to 80 chars, a structured body,
        and labels ``["autodream", "needs-triage"]``.
    """
    # Title: first sentence if available, else raw text — truncated to 80 chars.
    first_sentence = issue_text.split(".")[0].strip()
    raw_title = first_sentence if first_sentence else issue_text
    title = raw_title[:80]

    body = (
        f"**Auto-detected by autoDream on {date}**\n\n"
        f"Robot: {robot_rrn}\n\n"
        f"{issue_text}\n\n"
        "---\n"
        "*Filed automatically by castor/brain/autodream_runner.py*"
    )

    return IssueTemplate(title=title, body=body)


def file_github_issue(
    template: IssueTemplate,
    repo: str,
    dry_run: bool = False,
) -> str | None:
    """File a GitHub issue via the ``gh`` CLI.

    Args:
        template: Populated IssueTemplate to file.
        repo:     GitHub repo slug, e.g. ``"craigm26/OpenCastor"``.
        dry_run:  If True, log intent but do not execute the CLI command.

    Returns:
        The issue URL string on success, or ``None`` on failure / dry-run.
        Never raises.
    """
    if dry_run:
        logger.info("autoDream [dry-run] would file issue: %r on %s", template.title, repo)
        return None

    cmd = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        template.title,
        "--body",
        template.body,
        "--label",
        "autodream",
        "--label",
        "needs-triage",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            logger.info("autoDream filed issue: %s", url)
            return url
        logger.warning(
            "autoDream: gh issue create failed (rc=%d): %s",
            result.returncode,
            result.stderr.strip(),
        )
    except Exception as exc:
        logger.warning("autoDream: could not file GitHub issue: %s", exc)

    return None
