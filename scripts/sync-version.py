#!/usr/bin/env python3
"""
Sync version number across all OpenCastor touchpoints.

Usage:
    python scripts/sync-version.py                  # reads version from pyproject.toml
    python scripts/sync-version.py 2026.2.20.5      # explicit version

Touchpoints updated:
    - pyproject.toml             (source of truth, only updated if explicit arg)
    - README.md                  (## What's New header + badge stats)
    - site/index.html            (hero version text + LOC/test stats)
    - site/docs.html             (version in intro paragraph)
    - site/about.html            (version in about section)
    - site/hub.html              (if version present)
    - site/hardware.html         (if version present)
    - castor/__init__.py         (if hardcoded version string)
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def read_version_from_pyproject() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise ValueError("Could not find version in pyproject.toml")
    return m.group(1)


def write_version_to_pyproject(version: str) -> None:
    path = ROOT / "pyproject.toml"
    text = path.read_text()
    new_text = re.sub(
        r'^(version\s*=\s*)"[^"]+"',
        f'\\g<1>"{version}"',
        text,
        flags=re.MULTILINE,
    )
    path.write_text(new_text)
    print(f"  pyproject.toml          → {version}")


def get_test_count() -> str:
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "--co", "-q"],
            capture_output=True, text=True, cwd=ROOT
        )
        lines = result.stdout.strip().splitlines()
        for line in reversed(lines):
            m = re.search(r"(\d+)\s+test", line)
            if m:
                return f"{int(m.group(1)):,}"
    except Exception:
        pass
    return None


def get_loc() -> str:
    try:
        result = subprocess.run(
            "find . -name '*.py' -not -path './.venv/*' -not -path './build/*' "
            "-not -path './.git/*' | xargs wc -l 2>/dev/null | tail -1",
            shell=True, capture_output=True, text=True, cwd=ROOT
        )
        m = re.search(r"(\d+)", result.stdout.strip())
        if m:
            n = int(m.group(1))
            return f"{n:,}"
    except Exception:
        pass
    return None


def replace_version_in_file(path: Path, version: str, v_version: str) -> bool:
    """Replace any older vYYYY.M.DD.P pattern with the current version."""
    if not path.exists():
        return False
    text = path.read_text()
    # Match versioned strings like v2026.2.19.0, v2026.2.20.3, etc.
    new_text = re.sub(
        r'v20\d\d\.\d+\.\d+\.\d+',
        v_version,
        text
    )
    if new_text != text:
        path.write_text(new_text)
        count = len(re.findall(r'v20\d\d\.\d+\.\d+\.\d+', text))
        rel = str(path.relative_to(ROOT))
        print(f"  {rel:<35} → {v_version}  ({count} replacement(s))")
        return True
    return False


def update_stats(loc: str, tests: str) -> None:
    """Update LOC and test count stats across site and README."""
    if not loc or not tests:
        return

    files_to_update = [
        ROOT / "README.md",
        ROOT / "site" / "index.html",
    ]

    for path in files_to_update:
        if not path.exists():
            continue
        text = path.read_text()
        original = text

        # LOC patterns: "49,267 lines of code", "40,287", etc.
        text = re.sub(
            r'[\d,]+(?:\s+lines?\s+of\s+code)',
            f'{loc} lines of code',
            text
        )
        # Standalone LOC numbers in stat divs
        text = re.sub(
            r'(<div class="stat-number">)\s*[\d,]+\s*(</div>\s*(?:<!--.*?-->)?\s*<div class="stat-label">Lines)',
            f'\\g<1>{loc}\\g<2>',
            text, flags=re.DOTALL
        )

        # Test count patterns: "1,998 tests", "1,444 tests"
        text = re.sub(
            r'[\d,]+(?:\s+tests\b)',
            f'{tests} tests',
            text
        )
        # Standalone test numbers in stat divs
        text = re.sub(
            r'(<div class="stat-number">)\s*[\d,]+\s*(</div>\s*(?:<!--.*?-->)?\s*<div class="stat-label">Tests)',
            f'\\g<1>{tests}\\g<2>',
            text, flags=re.DOTALL
        )

        if text != original:
            path.write_text(text)
            rel = str(path.relative_to(ROOT))
            print(f"  {rel:<35} → LOC={loc}, tests={tests}")


def main():
    explicit_version = sys.argv[1] if len(sys.argv) > 1 else None

    if explicit_version:
        write_version_to_pyproject(explicit_version)
        version = explicit_version
    else:
        version = read_version_from_pyproject()

    v_version = f"v{version}"
    print(f"\n🔄  Syncing version {v_version} across all touchpoints...\n")

    # Update version strings in all site + doc files
    site_files = list((ROOT / "site").glob("*.html")) if (ROOT / "site").exists() else []
    doc_files = [ROOT / "README.md"]
    changed = 0
    for f in site_files + doc_files:
        if replace_version_in_file(f, version, v_version):
            changed += 1

    # Update install.sh VERSION variable (bare version, no v prefix)
    install_sh = ROOT / "scripts" / "install.sh"
    if install_sh.exists():
        text = install_sh.read_text()
        new_text = re.sub(
            r'^(VERSION=")[^"]+(")',
            f'\\g<1>{version}\\g<2>',
            text,
            flags=re.MULTILINE,
        )
        if new_text != text:
            install_sh.write_text(new_text)
            print(f"  scripts/install.sh              → VERSION={version}")
            changed += 1
        else:
            print(f"  scripts/install.sh              → already {version}")

    # Update stats
    print("\n📊  Collecting stats...")
    loc = get_loc()
    tests = get_test_count()
    if loc:
        print(f"     LOC:   {loc}")
    if tests:
        print(f"     Tests: {tests}")
    update_stats(loc, tests)

    print(f"\n✅  Done. {changed} file(s) updated with version {v_version}.")
    if loc and tests:
        print(f"     Stats: {loc} LOC, {tests} tests")
    print()


if __name__ == "__main__":
    main()
