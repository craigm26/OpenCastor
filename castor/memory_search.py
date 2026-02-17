"""
OpenCastor Memory Search -- semantic search over operational logs.

Searches past operational logs and session recordings for patterns,
events, and anomalies using keyword and fuzzy matching.

Usage:
    castor search "when did the robot overheat"
    castor search "navigation failures" --since 7d
    castor search "battery low" --log-file castor.log
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta

logger = logging.getLogger("OpenCastor.MemorySearch")


def search_logs(
    query: str,
    log_file: str = None,
    since: str = None,
    max_results: int = 20,
) -> list:
    """Search operational logs for a query string.

    Args:
        query: Search query (keywords or phrases).
        log_file: Path to log file (auto-detected if None).
        since: Time window (e.g. ``"7d"``, ``"24h"``, ``"1w"``).
        max_results: Maximum number of results to return.

    Returns:
        List of ``{"line": str, "score": float, "source": str}`` dicts.
    """
    results = []

    # Build keyword set from query
    keywords = _extract_keywords(query)
    if not keywords:
        return results

    # Calculate time cutoff
    cutoff = _parse_since(since) if since else None

    # Search log files
    log_paths = _find_log_files(log_file)
    for path in log_paths:
        try:
            file_results = _search_file(path, keywords, cutoff, max_results)
            results.extend(file_results)
        except Exception as exc:
            logger.debug(f"Error searching {path}: {exc}")

    # Search session recordings (.jsonl files)
    for jsonl in _find_recordings():
        try:
            rec_results = _search_recording(jsonl, keywords, cutoff, max_results)
            results.extend(rec_results)
        except Exception as exc:
            logger.debug(f"Error searching {jsonl}: {exc}")

    # Sort by relevance score (higher is better)
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def _extract_keywords(query: str) -> list:
    """Extract meaningful keywords from a search query."""
    # Remove common stop words
    stop_words = {
        "the", "a", "an", "is", "was", "were", "did", "do", "does",
        "when", "what", "where", "how", "why", "which", "that", "this",
        "from", "to", "in", "on", "at", "for", "of", "with", "and",
        "or", "but", "not", "my", "your", "it", "its",
    }
    words = re.findall(r'\w+', query.lower())
    return [w for w in words if w not in stop_words and len(w) > 1]


def _parse_since(since: str) -> datetime:
    """Parse a time window string into a datetime cutoff."""
    now = datetime.now()
    match = re.match(r'^(\d+)([dhwm])$', since.strip())
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    if unit == "h":
        return now - timedelta(hours=amount)
    elif unit == "d":
        return now - timedelta(days=amount)
    elif unit == "w":
        return now - timedelta(weeks=amount)
    elif unit == "m":
        return now - timedelta(days=amount * 30)
    return None


def _find_log_files(log_file: str = None) -> list:
    """Find log files to search."""
    if log_file and os.path.exists(log_file):
        return [log_file]

    candidates = [
        "opencastor.log",
        "/var/log/opencastor.log",
        os.path.expanduser("~/.opencastor/opencastor.log"),
    ]
    return [p for p in candidates if os.path.exists(p)]


def _find_recordings() -> list:
    """Find session recording files."""
    recordings = []
    for f in os.listdir("."):
        if f.endswith(".jsonl"):
            recordings.append(f)
    return recordings


def _search_file(path: str, keywords: list, cutoff: datetime, max_results: int) -> list:
    """Search a text log file for keywords."""
    results = []

    with open(path, errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            # Time filtering (try to parse timestamp from log line)
            if cutoff:
                line_time = _extract_timestamp(line)
                if line_time and line_time < cutoff:
                    continue

            score = _score_line(line, keywords)
            if score > 0:
                results.append({
                    "line": line[:200],
                    "score": score,
                    "source": f"{path}:{line_num}",
                })

            if len(results) >= max_results * 2:
                break

    return results


def _search_recording(path: str, keywords: list, cutoff: datetime, max_results: int) -> list:
    """Search a JSONL session recording."""
    results = []

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "step":
                continue

            # Combine thought + action for searching
            text = (entry.get("thought", "") + " " + str(entry.get("action", "")))
            score = _score_line(text, keywords)
            if score > 0:
                results.append({
                    "line": entry.get("thought", "")[:200],
                    "score": score,
                    "source": f"{path} (step {entry.get('step', '?')})",
                })

            if len(results) >= max_results * 2:
                break

    return results


def _score_line(line: str, keywords: list) -> float:
    """Score a line against keywords (higher = more relevant)."""
    line_lower = line.lower()
    score = 0.0
    for keyword in keywords:
        count = line_lower.count(keyword)
        if count > 0:
            score += count * (len(keyword) / 3.0)  # Longer keywords worth more
    return score


def _extract_timestamp(line: str) -> datetime:
    """Try to extract a timestamp from a log line."""
    # Common log format: 2026-02-16 14:30:00
    match = re.match(r'^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})', line)
    if match:
        try:
            return datetime.fromisoformat(match.group(1))
        except ValueError:
            pass
    return None


def print_search_results(results: list, query: str):
    """Print search results."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if has_rich:
        console.print(f"\n[bold cyan]  Search: '{query}'[/]")
    else:
        print(f"\n  Search: '{query}'")

    if not results:
        msg = "  No matching results found."
        if has_rich:
            console.print(f"  [dim]{msg}[/]\n")
        else:
            print(f"  {msg}\n")
        return

    if has_rich:
        console.print(f"  [dim]{len(results)} result(s)[/]\n")
    else:
        print(f"  {len(results)} result(s)\n")

    for i, result in enumerate(results, 1):
        source = result["source"]
        line = result["line"]
        score = result["score"]

        if has_rich:
            console.print(
                f"  [bold]{i}.[/] [dim]({score:.1f})[/] {source}\n"
                f"     {line}\n"
            )
        else:
            print(f"  {i}. ({score:.1f}) {source}")
            print(f"     {line}")
            print()
