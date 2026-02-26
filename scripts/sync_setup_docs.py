#!/usr/bin/env python3
"""Sync setup catalog snippets into docs/website files.

Usage:
  python scripts/sync_setup_docs.py          # write updates
  python scripts/sync_setup_docs.py --check  # verify snippets are up-to-date
"""

from __future__ import annotations

import argparse
from pathlib import Path

from castor.setup_catalog import get_model_profiles, get_stack_profiles

ROOT = Path(__file__).resolve().parents[1]

README_PATH = ROOT / "README.md"
API_REF_PATH = ROOT / "docs" / "claude" / "api-reference.md"
SITE_DOCS_PATH = ROOT / "site" / "docs.html"


def _replace_between_markers(text: str, start: str, end: str, body: str) -> str:
    if start not in text or end not in text:
        raise ValueError(f"Missing markers: {start} / {end}")
    s = text.index(start) + len(start)
    e = text.index(end, s)
    return text[:s] + "\n" + body.rstrip() + "\n" + text[e:]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _build_readme_block() -> str:
    stacks = get_stack_profiles(None)
    apples = get_model_profiles("apple")

    lines = []
    for stack in stacks:
        lines.append(f"- `{stack.id}` — {stack.label}")

    lines.append("")
    lines.append("| Apple Profile | Meaning |")
    lines.append("|---|---|")
    for profile in apples:
        lines.append(f"| `{profile.id}` | {profile.label} |")
    return "\n".join(lines)


def _build_api_ref_block() -> str:
    stacks = get_stack_profiles(None)
    apples = get_model_profiles("apple")
    stack_ids = ", ".join(f"`{stack.id}`" for stack in stacks)
    apple_ids = ", ".join(f"`{profile.id}`" for profile in apples)
    return f"- Stack IDs: {stack_ids}\n- Apple profile IDs: {apple_ids}"


def _build_site_block() -> str:
    stacks = get_stack_profiles(None)
    apples = get_model_profiles("apple")
    stack_html = ",\n          ".join(
        f'<code style="padding:2px 5px;font-size:0.8125rem">{stack.id}</code>' for stack in stacks
    )
    apple_html = ",\n          ".join(
        f'<code style="padding:2px 5px;font-size:0.8125rem">{profile.id}</code>' for profile in apples
    )
    return (
        '<div class="wizard-tip reveal" style="margin-bottom:20px">\n'
        "          <strong>Catalog snapshot:</strong> stacks: "
        f"{stack_html} · Apple profiles:\n"
        f"          {apple_html}\n"
        "        </div>"
    )


def _sync_file(path: Path, body: str, *, check: bool) -> bool:
    start = "<!-- SETUP_CATALOG:BEGIN -->"
    end = "<!-- SETUP_CATALOG:END -->"
    original = _read(path)
    updated = _replace_between_markers(original, start, end, body)
    if original == updated:
        return False
    if check:
        raise SystemExit(f"Setup catalog snippets out of date: {path}")
    _write(path, updated)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync setup catalog snippets into docs.")
    parser.add_argument("--check", action="store_true", help="Fail if snippets are out of sync")
    args = parser.parse_args()

    changed = False
    changed |= _sync_file(README_PATH, _build_readme_block(), check=args.check)
    changed |= _sync_file(API_REF_PATH, _build_api_ref_block(), check=args.check)
    changed |= _sync_file(SITE_DOCS_PATH, _build_site_block(), check=args.check)

    if not args.check:
        if changed:
            print("Updated setup catalog snippets.")
        else:
            print("Setup catalog snippets already up-to-date.")
    else:
        print("Setup catalog snippets are in sync.")


if __name__ == "__main__":
    main()

