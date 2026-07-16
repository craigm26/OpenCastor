#!/usr/bin/env python3
"""Operator helper for the OpenCastor PyPI CalVer remediation (T-003).

PROBLEM
    `pip install opencastor` resolves the stale CalVer line (e.g. 2026.4.23.0)
    ABOVE the maintained 3.x line, because PEP 440 sorts
        Version("2026.4.23.0") > Version("3.0.3")
    A brand-new 3.0.3 STILL sorts below every 2026.* release, so bumping the
    version does not fix it. The durable fix is to YANK the 98 CalVer releases so
    PyPI's resolver stops offering them for a bare/range requirement.

WHAT YANK DOES (and does not)
    Yanking (PEP 592) marks a release so pip will NOT select it for a bare or
    range spec (`opencastor`, `opencastor>=3`), but it is NOT a delete — a build
    that pins the EXACT version (`opencastor==2026.4.23.0`) can still install it,
    so nothing already pinned breaks. After yanking all 98 CalVer releases, a bare
    `pip install opencastor` resolves 3.0.2 (the highest non-yanked release).

HOW TO YANK
    Warehouse (pypi.org) exposes NO token-authenticated REST endpoint for yank —
    twine cannot yank. Yanking is a maintainer web-UI action (2FA login required):
        https://pypi.org/manage/project/opencastor/release/<version>/
        -> "Options" -> "Yank" -> enter a reason (e.g. "CalVer line superseded by
           3.x SemVer; see docs/pypi-versioning.md") -> confirm.

USAGE
    python scripts/pypi_yank_calver.py            # list the 98 versions
    python scripts/pypi_yank_calver.py --urls     # print the manage/yank URL per version
    python scripts/pypi_yank_calver.py --verify   # print the post-yank verification command

This script deliberately performs NO network writes: yanking is operator-gated
(needs the PyPI maintainer session). It only enumerates the work.
"""

from __future__ import annotations

import argparse

PROJECT = "opencastor"

# The 98 stale CalVer (YYYY.M.D.patch) releases to yank, oldest -> newest.
# Generated from https://pypi.org/pypi/opencastor/json (2026-07-16). The three
# maintained SemVer releases 3.0.0 / 3.0.1 / 3.0.2 are intentionally NOT listed.
CALVER_VERSIONS = [
    "2026.2.17.3", "2026.2.17.6", "2026.2.17.7", "2026.2.17.8", "2026.2.17.10", "2026.2.17.11",
    "2026.2.17.12", "2026.2.17.13", "2026.2.17.14", "2026.2.17.15", "2026.2.17.16",
    "2026.2.17.17", "2026.2.17.18", "2026.2.17.19", "2026.2.17.20", "2026.2.17.21",
    "2026.2.18.3", "2026.2.18.4", "2026.2.18.5", "2026.2.18.6", "2026.2.18.7", "2026.2.18.8",
    "2026.2.18.9", "2026.2.18.10", "2026.2.18.11", "2026.2.18.12", "2026.2.18.13",
    "2026.2.19.0", "2026.2.19.1", "2026.2.20.0", "2026.2.20.1", "2026.2.20.2", "2026.2.20.5",
    "2026.2.20.6", "2026.2.20.7", "2026.2.20.8", "2026.2.20.9", "2026.2.20.10", "2026.2.23.5",
    "2026.2.23.6", "2026.2.23.7", "2026.2.23.8", "2026.2.23.9", "2026.2.23.10", "2026.2.23.11",
    "2026.2.23.12", "2026.2.23.13", "2026.2.26.1", "2026.2.26.2", "2026.2.27.2", "2026.3.1.14",
    "2026.3.1.15", "2026.3.1.16", "2026.3.3.0", "2026.3.7.0", "2026.3.8.0", "2026.3.8.3",
    "2026.3.12.0", "2026.3.12.3", "2026.3.12.4", "2026.3.12.5", "2026.3.12.7", "2026.3.12.8",
    "2026.3.13.0", "2026.3.13.1", "2026.3.13.2", "2026.3.13.3", "2026.3.13.4", "2026.3.13.5",
    "2026.3.13.6", "2026.3.13.7", "2026.3.13.8", "2026.3.13.9", "2026.3.13.10", "2026.3.13.11",
    "2026.3.13.12", "2026.3.13.13", "2026.3.13.14", "2026.3.14.0", "2026.3.14.1",
    "2026.3.14.2", "2026.3.14.6", "2026.3.17.13", "2026.3.20.3", "2026.3.20.4", "2026.3.21.1",
    "2026.3.21.2", "2026.3.27.1", "2026.3.28.0", "2026.3.29.0", "2026.3.29.1", "2026.3.30.0",
    "2026.4.1.0", "2026.4.2.0", "2026.4.3.0", "2026.4.12.0", "2026.4.15.0", "2026.4.23.0",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--urls", action="store_true", help="Print the manage/yank URL for each version")
    ap.add_argument("--verify", action="store_true", help="Print the post-yank verification command")
    args = ap.parse_args()

    if args.verify:
        print("# After yanking all versions below, confirm a bare install resolves 3.x:")
        print("python -m venv /tmp/ocverify && /tmp/ocverify/bin/pip install --upgrade pip")
        print(f"/tmp/ocverify/bin/pip download {PROJECT} --no-deps -d /tmp/ocverify/dl")
        print("ls /tmp/ocverify/dl   # expect opencastor-3.0.2-* (NOT 2026.4.x)")
        return

    print(f"# {len(CALVER_VERSIONS)} CalVer releases to yank for '{PROJECT}':")
    for v in CALVER_VERSIONS:
        if args.urls:
            print(f"https://pypi.org/manage/project/{PROJECT}/release/{v}/")
        else:
            print(v)


if __name__ == "__main__":
    main()
