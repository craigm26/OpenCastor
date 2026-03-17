"""
castor/skills/loader.py — SkillLoader and SkillSelector.

SkillLoader scans skill directories for SKILL.md files and parses them into
skill dicts ready for injection by the ContextBuilder.

SkillSelector matches an incoming instruction to the best skill using
keyword overlap (with embedding-based cosine similarity when available).

Search paths (in priority order):
  1. castor/skills/builtin/   — shipped with OpenCastor
  2. ~/.config/opencastor/skills/   — user-installed
  3. Paths listed in agent.skills RCAN config

Usage::

    from castor.skills.loader import SkillLoader, SkillSelector

    loader = SkillLoader()
    skills = loader.load_all()

    selector = SkillSelector()
    skill = selector.select("pick up the red brick", skills)
    # skill["name"] == "arm-manipulate"
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("OpenCastor.Skills")

__all__ = ["SkillLoader", "SkillSelector", "Skill"]

# Built-in skills directory (alongside this file)
_BUILTIN_DIR = Path(__file__).parent / "builtin"
_USER_DIR = Path.home() / ".config" / "opencastor" / "skills"

# Minimum keyword overlap for a skill match (fallback mode)
_KEYWORD_THRESHOLD = 1
# Cosine similarity threshold (embedding mode)
_EMBEDDING_THRESHOLD = 0.68

# Type alias
Skill = dict  # keys: name, description, version, requires, consent, tools, max_iterations, body


class SkillLoader:
    """Scans skill directories and parses SKILL.md files.

    Args:
        extra_paths: Additional directories to scan (from RCAN config).
    """

    def __init__(self, extra_paths: Optional[list[Path]] = None) -> None:
        self._extra_paths: list[Path] = extra_paths or []
        self._cache: Optional[dict[str, Skill]] = None

    def load_all(self) -> dict[str, Skill]:
        """Return all discovered skills as {name: skill_dict}."""
        if self._cache is not None:
            return self._cache

        skills: dict[str, Skill] = {}
        search_dirs = [_BUILTIN_DIR, _USER_DIR] + self._extra_paths

        for directory in search_dirs:
            if not directory.exists():
                continue
            for skill_dir in sorted(directory.iterdir()):
                skill_md = skill_dir / "SKILL.md"
                if skill_dir.is_dir() and skill_md.exists():
                    try:
                        skill = self._parse_skill(skill_md)
                        if skill:
                            skills[skill["name"]] = skill
                            logger.debug("Skill loaded: %s from %s", skill["name"], skill_dir)
                    except Exception as exc:
                        logger.warning("Failed to load skill at %s: %s", skill_dir, exc)

        logger.info("SkillLoader: %d skills loaded", len(skills))
        self._cache = skills
        return skills

    def load_skill(self, path: Path) -> Optional[Skill]:
        """Load a single skill from a directory path."""
        skill_md = path / "SKILL.md" if path.is_dir() else path
        if not skill_md.exists():
            return None
        return self._parse_skill(skill_md)

    def invalidate_cache(self) -> None:
        """Force reload on next load_all() call."""
        self._cache = None

    def _parse_skill(self, skill_md: Path) -> Optional[Skill]:
        """Parse a SKILL.md file into a skill dict."""
        content = skill_md.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(content)

        if frontmatter is None:
            logger.warning("SKILL.md has no frontmatter: %s", skill_md)
            return None

        parsed = _parse_yaml_simple(frontmatter)
        name = parsed.get("name", "")
        if not name:
            logger.warning("SKILL.md missing 'name': %s", skill_md)
            return None

        # Normalise description (may be a YAML block scalar)
        description = parsed.get("description", "")
        if isinstance(description, str):
            description = " ".join(description.split())

        return {
            "name": name,
            "description": description,
            "version": parsed.get("version", "1.0"),
            "requires": _to_list(parsed.get("requires", [])),
            "consent": parsed.get("consent", "none"),
            "tools": _to_list(parsed.get("tools", [])),
            "max_iterations": int(parsed.get("max_iterations", 6)),
            "body": body.strip(),
            "path": str(skill_md.parent),
        }


class SkillSelector:
    """Selects the best skill for an incoming instruction.

    Selection cascade:
      1. Explicit trigger: instruction starts with /skill-name
      2. Embedding cosine similarity (if EmbeddingInterpreter available)
      3. Keyword overlap fallback
      4. None if no match above threshold
    """

    def select(
        self,
        instruction: str,
        skills: dict[str, Skill],
        robot_capabilities: Optional[list[str]] = None,
    ) -> Optional[Skill]:
        """Return best matching skill or None."""
        if not skills or not instruction.strip():
            return None

        # 1. Explicit /skill-name trigger — if starts with /, only match explicit
        if instruction.startswith("/"):
            name = instruction.split()[0][1:]
            if name in skills:
                logger.debug("Explicit skill trigger: %s", name)
                return skills[name]
            return None  # explicit trigger with unknown name → no match

        # 2. Filter by robot capabilities
        available = {
            n: s for n, s in skills.items()
            if self._has_capabilities(s, robot_capabilities or [])
        }
        if not available:
            available = skills

        # 3. Try embedding similarity
        best = self._select_by_embedding(instruction, available)
        if best is not None:
            return best

        # 4. Keyword fallback
        return self._select_by_keywords(instruction, available)

    def _has_capabilities(self, skill: Skill, robot_caps: list[str]) -> bool:
        """Return True if robot has all required capabilities for this skill."""
        if not robot_caps:
            return True  # no capability info — don't filter
        required = skill.get("requires", [])
        for req in required:
            # vision requirement: check for "vision" or "camera"
            if req == "vision" and "vision" not in robot_caps and "camera" not in robot_caps:
                return False
            # control requirement: check for "control" or "drive" or "arm"
            if req == "control" and not any(c in robot_caps for c in ("control", "drive", "arm")):
                return False
            # gripper requirement
            if req == "gripper" and "gripper" not in robot_caps:
                return False
        return True

    def _select_by_embedding(
        self, instruction: str, skills: dict[str, Skill]
    ) -> Optional[Skill]:
        """Select via cosine similarity of embedded descriptions."""
        try:
            from castor.learner.embedding_interpreter import EmbeddingInterpreter
            interp = EmbeddingInterpreter.get_default()
            if interp is None:
                return None

            instr_emb = interp.embed(instruction)
            best_score = _EMBEDDING_THRESHOLD
            best_skill: Optional[Skill] = None

            for skill in skills.values():
                desc_emb = interp.embed(skill["description"])
                score = _cosine_similarity(instr_emb, desc_emb)
                if score > best_score:
                    best_score = score
                    best_skill = skill

            if best_skill:
                logger.debug("Skill selected by embedding: %s (score=%.3f)",
                             best_skill["name"], best_score)
            return best_skill
        except Exception:
            return None

    def _select_by_keywords(
        self, instruction: str, skills: dict[str, Skill]
    ) -> Optional[Skill]:
        """Select by keyword overlap between instruction and skill description."""
        instr_words = set(_tokenise(instruction))
        best_count = _KEYWORD_THRESHOLD - 1
        best_skill: Optional[Skill] = None

        for skill in skills.values():
            desc_words = set(_tokenise(skill["description"]))
            # Exact match + prefix match (handles see/sees, pick/picking, etc.)
            overlap = 0
            for iw in instr_words:
                for dw in desc_words:
                    if iw == dw or dw.startswith(iw) or iw.startswith(dw):
                        overlap += 1
                        break
            if overlap > best_count:
                best_count = overlap
                best_skill = skill

        if best_skill:
            logger.debug("Skill selected by keywords: %s (overlap=%d)",
                         best_skill["name"], best_count)
        return best_skill


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_frontmatter(content: str) -> tuple[Optional[str], str]:
    """Split SKILL.md into (frontmatter, body). Returns (None, content) if no frontmatter."""
    if not content.startswith("---"):
        return None, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, content
    return parts[1].strip(), parts[2].strip()


def _parse_yaml_simple(yaml_text: str) -> dict:
    """Minimal YAML parser for SKILL.md frontmatter.

    Handles: string scalars, block scalars (>), lists (- item), integers.
    Falls back to python-yaml if available.
    """
    try:
        import yaml
        return yaml.safe_load(yaml_text) or {}
    except ImportError:
        pass

    result: dict = {}
    lines = yaml_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Key: value
        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.*)', line)
        if m:
            key = m.group(1)
            val = m.group(2).strip()
            if val == ">":
                # Block scalar — collect indented lines
                block_lines = []
                i += 1
                while i < len(lines) and (lines[i].startswith("  ") or lines[i].strip() == ""):
                    block_lines.append(lines[i].strip())
                    i += 1
                result[key] = " ".join(filter(None, block_lines))
                continue
            elif val == "":
                # Possible list follows
                items = []
                i += 1
                while i < len(lines) and lines[i].strip().startswith("- "):
                    items.append(lines[i].strip()[2:].strip().strip('"').strip("'"))
                    i += 1
                result[key] = items
                continue
            else:
                # Strip quotes
                val = val.strip('"').strip("'")
                # Try int
                try:
                    result[key] = int(val)
                except ValueError:
                    result[key] = val
        i += 1
    return result


def _to_list(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val:
        return [val]
    return []


def _tokenise(text: str) -> list[str]:
    """Lowercase word tokens, filtering stop words."""
    _STOP = {"the", "a", "an", "to", "in", "of", "for", "and", "or", "it",
             "is", "at", "on", "do", "you", "i", "my", "your", "can", "please",
             "with", "that", "this", "what", "when", "how", "me",
             "use", "when", "asks", "user", "robot", "want", "tell"}
    words = re.findall(r'\b[a-z]+\b', text.lower())
    return [w for w in words if w not in _STOP and len(w) >= 2]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
