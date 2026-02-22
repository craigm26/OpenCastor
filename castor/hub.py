"""OpenCastor Community Hub — share and discover robot configs.

Users can package their working RCAN configs with markdown writeups,
scrub PII, and share them as "recipes" that others can browse and use.

Recipes are stored as JSON manifests in the community-recipes/ directory
of the repo, with configs and docs alongside them.
"""

import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("OpenCastor.Hub")


def _opencastor_version() -> str:
    """Return the installed OpenCastor version string."""
    try:
        from castor import __version__

        return __version__
    except Exception:
        return "unknown"


# Where community recipes live in the repo
RECIPES_DIR = Path(__file__).parent.parent / "community-recipes"

# PII patterns to scrub
PII_PATTERNS = [
    # API keys and tokens
    (r"(sk-[a-zA-Z0-9_-]{20,})", "[REDACTED_API_KEY]"),
    (r"(hf_[a-zA-Z0-9]{20,})", "[REDACTED_HF_TOKEN]"),
    (r"(AIza[a-zA-Z0-9_-]{35})", "[REDACTED_GOOGLE_KEY]"),
    (r"(ghp_[a-zA-Z0-9]{36})", "[REDACTED_GITHUB_TOKEN]"),
    (r"(xox[bpsa]-[a-zA-Z0-9-]+)", "[REDACTED_SLACK_TOKEN]"),
    # Passwords and secrets in config (generic — runs after specific token patterns above)
    (r'(password\s*[:=]\s*)["\']?[^\s"\']+', r"\1[REDACTED]"),
    (r'(secret\s*[:=]\s*)["\']?[^\s"\']+', r"\1[REDACTED]"),
    (r'(api_key\s*[:=]\s*)["\']?[^\s"\']+', r"\1[REDACTED]"),
    (r"(token\s*[:=]\s*)[\"']?(?!hf_|sk-|AIza|ghp_|xox|\[REDACTED)[^\s\"']+", r"\1[REDACTED]"),
    # Email addresses
    (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[REDACTED_EMAIL]"),
    # IP addresses (private ranges kept, public scrubbed)
    (
        r"\b(?!10\.)(?!172\.(1[6-9]|2\d|3[01])\.)(?!192\.168\.)"
        r"(?!127\.)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        "[REDACTED_IP]",
    ),
    # Phone numbers (require at least one separator to avoid matching numeric strings)
    (r"\+\d{1,3}[-.\s]\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}", "[REDACTED_PHONE]"),
    (r"\(?\d{3}\)[-.\s]\d{3}[-.\s]\d{4}", "[REDACTED_PHONE]"),
    # WiFi SSIDs and passwords in config
    (r'(ssid\s*[:=]\s*)["\']?[^\s"\']+', r"\1[REDACTED_SSID]"),
    (r'(wifi_password\s*[:=]\s*)["\']?[^\s"\']+', r"\1[REDACTED]"),
    # Home directory paths
    (r"/home/[a-zA-Z0-9_-]+", "/home/user"),
    (r"/Users/[a-zA-Z0-9_-]+", "/Users/user"),
    # Hostnames that look personal
    (r'(hostname\s*[:=]\s*)["\']?[^\s"\']+', r"\1[REDACTED_HOSTNAME]"),
]

# Recipe categories
CATEGORIES = {
    "home": "Home & Indoor",
    "outdoor": "Outdoor & Exploration",
    "service": "Service & Hospitality",
    "industrial": "Industrial & Manufacturing",
    "education": "Education & Research",
    "agriculture": "Agriculture & Farming",
    "security": "Security & Surveillance",
    "companion": "Companion & Social",
    "art": "Art & Creative",
    "custom": "Custom / Other",
}

# Hardware difficulty tiers
DIFFICULTY = {
    "beginner": "Beginner — off-the-shelf kit, no soldering",
    "intermediate": "Intermediate — some assembly or wiring required",
    "advanced": "Advanced — custom hardware, 3D printing, or machining",
}


def scrub_pii(text: str) -> str:
    """Remove personally identifiable information from text.

    Applies regex patterns to strip API keys, emails, IPs, phone numbers,
    home directory paths, and other common PII from config files and docs.
    """
    result = text
    for pattern, replacement in PII_PATTERNS:
        result = re.sub(pattern, replacement, result)
    return result


def scrub_file(filepath: Path) -> str:
    """Read a file and return PII-scrubbed contents."""
    content = filepath.read_text(encoding="utf-8", errors="replace")
    return scrub_pii(content)


def generate_recipe_id(name: str) -> str:
    """Generate a URL-safe recipe slug from a name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    # Add short hash for uniqueness
    h = hashlib.sha256(f"{slug}-{time.time()}".encode()).hexdigest()[:6]
    return f"{slug}-{h}"


def create_recipe_manifest(
    name: str,
    description: str,
    author: str,
    category: str,
    difficulty: str,
    hardware: list[str],
    ai_provider: str,
    ai_model: str,
    tags: list[str] | None = None,
    budget: str | None = None,
    use_case: str | None = None,
) -> dict[str, Any]:
    """Create a recipe manifest (metadata)."""
    recipe_id = generate_recipe_id(name)
    return {
        "id": recipe_id,
        "name": name,
        "description": description,
        "author": author,
        "category": category,
        "difficulty": difficulty,
        "hardware": hardware,
        "ai": {
            "provider": ai_provider,
            "model": ai_model,
        },
        "tags": tags or [],
        "budget": budget,
        "use_case": use_case,
        "created": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "opencastor_version": _opencastor_version(),
        "files": {
            "config": "config.rcan.yaml",
            "readme": "README.md",
            "docs": [],
        },
    }


def package_recipe(
    config_path: str,
    output_dir: str | None = None,
    docs: list[str] | None = None,
    manifest: dict | None = None,
    dry_run: bool = False,
) -> Path:
    """Package a config file and docs into a shareable recipe.

    1. Reads the RCAN config and any doc files
    2. Scrubs all PII
    3. Creates a recipe directory with manifest, config, and docs
    4. Returns the path to the packaged recipe
    """
    config = Path(config_path)
    if not config.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    if manifest is None:
        manifest = create_recipe_manifest(
            name=config.stem,
            description="A community-shared robot config",
            author="Anonymous",
            category="custom",
            difficulty="intermediate",
            hardware=[],
            ai_provider="unknown",
            ai_model="unknown",
        )

    recipe_id = manifest["id"]
    recipe_dir = Path(output_dir or ".") / f"recipe-{recipe_id}"

    if dry_run:
        print(f"\n  📦 Would create recipe at: {recipe_dir}")
        print(f"  Config: {config_path} (scrubbed)")
        if docs:
            for d in docs:
                print(f"  Doc: {d} (scrubbed)")
        return recipe_dir

    recipe_dir.mkdir(parents=True, exist_ok=True)

    # Scrub and write config
    scrubbed_config = scrub_file(config)
    (recipe_dir / "config.rcan.yaml").write_text(scrubbed_config)

    # Scrub and write docs
    doc_filenames = []
    for doc_path in docs or []:
        doc = Path(doc_path)
        if doc.exists():
            scrubbed = scrub_file(doc)
            dest_name = doc.name
            (recipe_dir / dest_name).write_text(scrubbed)
            doc_filenames.append(dest_name)

    manifest["files"]["docs"] = doc_filenames

    # Generate README stub if not provided
    readme_path = recipe_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(_generate_readme(manifest))

    # Write manifest
    (recipe_dir / "recipe.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    logger.info("Recipe packaged at: %s", recipe_dir)
    return recipe_dir


def _generate_readme(manifest: dict) -> str:
    """Generate a README.md stub for a recipe."""
    category_label = CATEGORIES.get(manifest["category"], manifest["category"])
    difficulty_label = DIFFICULTY.get(manifest["difficulty"], manifest["difficulty"])

    hardware_list = "\n".join(f"- {h}" for h in manifest.get("hardware", [])) or "- (not specified)"

    return f"""# {manifest["name"]}

> {manifest["description"]}

## Overview

| | |
|---|---|
| **Category** | {category_label} |
| **Difficulty** | {difficulty_label} |
| **AI Provider** | {manifest["ai"]["provider"]} |
| **AI Model** | {manifest["ai"]["model"]} |
| **Budget** | {manifest.get("budget", "N/A")} |
| **OpenCastor Version** | {manifest.get("opencastor_version", "latest")} |

## Use Case

{manifest.get("use_case", "_Describe what this robot does and why this config works well for it._")}

## Hardware

{hardware_list}

## Quick Start

```bash
# Install OpenCastor
curl -sL opencastor.com/install | bash

# Copy this config
cp config.rcan.yaml my_robot.rcan.yaml

# Run it
castor run --config my_robot.rcan.yaml
```

## What I Learned

_Share tips, gotchas, and lessons learned here. What worked? What didn't?
What would you do differently?_

## Photos / Videos

_Add photos or links to videos of your robot in action!_

---

*Shared via [OpenCastor Community Hub](https://opencastor.com/hub)*
"""


def list_recipes(
    category: str | None = None,
    difficulty: str | None = None,
    provider: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """List available community recipes with optional filters."""
    recipes = []

    if not RECIPES_DIR.exists():
        return recipes

    for recipe_dir in sorted(RECIPES_DIR.iterdir()):
        manifest_path = recipe_dir / "recipe.json"
        if not manifest_path.exists():
            continue

        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        # Apply filters
        if category and manifest.get("category") != category:
            continue
        if difficulty and manifest.get("difficulty") != difficulty:
            continue
        if provider and manifest.get("ai", {}).get("provider") != provider:
            continue
        if search:
            haystack = json.dumps(manifest).lower()
            if search.lower() not in haystack:
                continue

        recipes.append(manifest)

    return recipes


def get_recipe(recipe_id: str) -> dict | None:
    """Load a specific recipe by ID."""
    for recipe_dir in RECIPES_DIR.iterdir():
        manifest_path = recipe_dir / "recipe.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                if manifest.get("id") == recipe_id:
                    manifest["_dir"] = str(recipe_dir)
                    return manifest
            except (json.JSONDecodeError, OSError):
                continue
    return None


def install_recipe(recipe_id: str, dest: str = ".") -> Path | None:
    """Copy a recipe's config to the current directory."""
    recipe = get_recipe(recipe_id)
    if not recipe:
        return None

    recipe_dir = Path(recipe["_dir"])
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    # Copy config
    config_src = recipe_dir / recipe["files"]["config"]
    config_dest = dest_path / recipe["files"]["config"]
    if config_src.exists():
        shutil.copy2(config_src, config_dest)

    # Copy readme
    readme_src = recipe_dir / "README.md"
    readme_dest = dest_path / f"README-{recipe['id']}.md"
    if readme_src.exists():
        shutil.copy2(readme_src, readme_dest)

    # Copy docs
    for doc in recipe["files"].get("docs", []):
        doc_src = recipe_dir / doc
        if doc_src.exists():
            shutil.copy2(doc_src, dest_path / doc)

    return config_dest


def print_recipe_card(manifest: dict, verbose: bool = False) -> None:
    """Pretty-print a recipe summary."""
    category_label = CATEGORIES.get(manifest.get("category", ""), "?")
    difficulty_label = manifest.get("difficulty", "?")
    ai = manifest.get("ai", {})
    tags = ", ".join(manifest.get("tags", [])) or "none"

    print(f"\n  📋 {manifest['name']}")
    print(f"     {manifest.get('description', '')}")
    print(f"     ├─ Category:   {category_label}")
    print(f"     ├─ Difficulty: {difficulty_label}")
    print(f"     ├─ AI:         {ai.get('provider', '?')} / {ai.get('model', '?')}")
    print(f"     ├─ Hardware:   {', '.join(manifest.get('hardware', ['?']))}")
    if manifest.get("budget"):
        print(f"     ├─ Budget:     {manifest['budget']}")
    print(f"     ├─ Tags:       {tags}")
    print(f"     ├─ Author:     {manifest.get('author', 'Anonymous')}")
    print(f"     └─ ID:         {manifest['id']}")

    if verbose and manifest.get("use_case"):
        print(f"\n     Use Case: {manifest['use_case']}")


# ---------------------------------------------------------------------------
# Auto-PR submission via `gh` CLI
# ---------------------------------------------------------------------------

UPSTREAM_REPO = "craigm26/OpenCastor"


class SubmitError(Exception):
    """Raised when recipe PR submission fails."""


def _run_gh(
    args: list[str], check: bool = True, capture: bool = True
) -> subprocess.CompletedProcess:
    """Run a ``gh`` CLI command and return the result."""
    cmd = ["gh"] + args
    try:
        return subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            check=check,
            timeout=60,
        )
    except FileNotFoundError as err:
        raise SubmitError(
            "GitHub CLI (gh) is not installed.\n"
            "  Install: https://cli.github.com\n"
            "  Then run: gh auth login"
        ) from err
    except subprocess.TimeoutExpired as err:
        raise SubmitError("gh command timed out after 60 seconds.") from err
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        if "not logged" in stderr.lower() or "auth" in stderr.lower():
            raise SubmitError("Not authenticated with GitHub CLI.\n  Run: gh auth login") from exc
        raise SubmitError(f"gh command failed: {stderr.strip()}") from exc


def _check_gh_auth() -> str:
    """Verify ``gh`` auth and return the username."""
    result = _run_gh(["auth", "status"], check=False)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise SubmitError("Not authenticated with GitHub CLI.\n  Run: gh auth login")
    # Try to extract username
    for line in output.splitlines():
        if "logged in" in line.lower():
            # Format: "✓ Logged in to github.com account USERNAME ..."
            parts = line.split()
            for i, part in enumerate(parts):
                if part.lower() == "account" and i + 1 < len(parts):
                    return parts[i + 1].strip("()")
    return "unknown"


def _ensure_fork() -> str:
    """Fork the upstream repo if not already forked. Returns the fork clone URL."""
    result = _run_gh(
        ["repo", "fork", UPSTREAM_REPO, "--clone=false"],
        check=False,
    )
    # gh repo fork succeeds or says "already exists" — both are fine
    stderr = result.stderr or ""
    if result.returncode != 0 and "already exists" not in stderr.lower():
        raise SubmitError(f"Failed to fork {UPSTREAM_REPO}: {stderr.strip()}")

    # Get the fork name
    result = _run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return result.stdout.strip()


def submit_recipe_pr(
    recipe_dir: Path,
    manifest: dict[str, Any],
) -> str:
    """Submit a packaged recipe as a PR to the upstream repo.

    Steps:
      1. Verify ``gh`` auth
      2. Fork if needed
      3. Clone the fork to a temp dir
      4. Create branch ``recipe/<slug>``
      5. Copy recipe files to ``community-recipes/<slug>/``
      6. Commit and push
      7. Create PR with templated description

    Returns the PR URL.
    """
    recipe_id = manifest["id"]
    slug = re.sub(r"-[a-f0-9]{6}$", "", recipe_id)  # strip hash suffix for branch name
    branch_name = f"recipe/{slug}"

    # Step 1: Check auth
    username = _check_gh_auth()
    logger.info("Authenticated as %s", username)

    # Step 2: Fork
    print("  🍴 Ensuring fork exists...")
    _ensure_fork()

    # Step 3–6: Use gh to create PR directly from local files
    # Clone fork into temp dir, copy files, push
    import tempfile

    with tempfile.TemporaryDirectory(prefix="opencastor-submit-") as tmpdir:
        tmpdir = Path(tmpdir)

        print("  📥 Cloning fork...")
        _run_gh(["repo", "clone", f"{username}/OpenCastor", str(tmpdir / "repo")], check=True)
        repo_path = tmpdir / "repo"

        # Create branch
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

        # Copy recipe files
        dest = repo_path / "community-recipes" / recipe_id
        dest.mkdir(parents=True, exist_ok=True)
        for f in recipe_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, dest / f.name)

        # Commit
        subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"feat: add community recipe '{manifest['name']}'"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

        # Push
        print("  🚀 Pushing branch...")
        subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

        # Create PR
        print("  📝 Creating pull request...")
        pr_body = _build_pr_description(manifest)
        result = _run_gh(
            [
                "pr",
                "create",
                "--repo",
                UPSTREAM_REPO,
                "--head",
                f"{username}:{branch_name}",
                "--title",
                f"[Recipe] {manifest['name']}",
                "--body",
                pr_body,
            ],
        )
        pr_url = result.stdout.strip()

    return pr_url


def _build_pr_description(manifest: dict[str, Any]) -> str:
    """Build a templated PR description from the recipe manifest."""
    category_label = CATEGORIES.get(manifest.get("category", ""), manifest.get("category", ""))
    difficulty_label = DIFFICULTY.get(
        manifest.get("difficulty", ""), manifest.get("difficulty", "")
    )
    ai = manifest.get("ai", {})
    hardware = ", ".join(manifest.get("hardware", [])) or "Not specified"
    tags = ", ".join(manifest.get("tags", [])) or "None"

    return f"""## 🤖 New Community Recipe: {manifest["name"]}

{manifest.get("description", "")}

### Details

| Field | Value |
|-------|-------|
| **Category** | {category_label} |
| **Difficulty** | {difficulty_label} |
| **AI Provider** | {ai.get("provider", "N/A")} |
| **AI Model** | {ai.get("model", "N/A")} |
| **Hardware** | {hardware} |
| **Budget** | {manifest.get("budget", "N/A")} |
| **Tags** | {tags} |

### Use Case

{manifest.get("use_case", "_Not provided_")}

### Checklist

- [x] Config has been PII-scrubbed by OpenCastor Hub
- [ ] README includes setup instructions
- [ ] Tested on real hardware

---
*Submitted via `castor hub share --submit`*
"""
