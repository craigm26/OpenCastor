"""Edit and sign ROBOT.md — the operator's pen for the document of record.

WHAT THIS EXISTS TO REPLACE. Adding `sensor.battery` to the bench rover — the
first skill through the gap rail — took a hand-written Python snippet that
stripped the signature footer, spliced a YAML list entry, re-signed with the
right key, and knew that the signature covers the body WITHOUT the newline
before the footer. That is a working process for exactly one person. "Adding
capabilities and manifest updates should be easy" (the operator, verbatim) —
easy for the OPERATOR, while staying impossible for everyone else:

  * The manifest key never leaves the robot, and signing REUSES it — the same
    reuse-don't-refuse contract `castor pair` learned after a rotation
    destroyed a live key.
  * The gateway still only trusts what verifies, and a fresh capability still
    starts outside the gateway's tool allowlist — declaring is not permitting.
  * The phone still cannot do any of this. Easy means one command at the
    robot's own shell, not a new remote surface.

EDITS ARE TEXT-SURGICAL, NOT YAML ROUND-TRIPS. Loading the frontmatter into a
YAML parser and dumping it back would reformat the whole document — and a
ROBOT.md's comments are load-bearing safety prose (the rover's explains why
its wheels must stay off the ground). A capability is added by splicing one
line into the existing list, leaving every other byte alone.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

#: The gateway's footer shape, verbatim (robot-md-gateway manifest_provenance).
#: The signature covers text[:match.start()] — the body WITHOUT the newline
#: that precedes the comment. One byte of framing; see castor.up's bench note.
SIG_RE = re.compile(
    r"\n<!--\s*ROBOT-MD-SIG\s+kid=(?P<kid>\S+)\s+sig=(?P<sig>[A-Za-z0-9+/=]+)\s*-->\s*\Z"
)

_CAP_LINE = re.compile(r"^(?P<indent>\s*)-\s*(?P<name>[A-Za-z0-9._-]+)\s*$")


@dataclass(frozen=True)
class Manifest:
    """One parsed-enough view of a ROBOT.md: its body, and where things are."""

    body: str
    kid: str | None

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        text = path.read_text()
        m = SIG_RE.search(text)
        if m is None:
            return cls(body=text.rstrip("\n"), kid=None)
        return cls(body=text[: m.start()], kid=m.group("kid"))


def capabilities(path: Path) -> list[str]:
    """The declared capability names, in declaration order."""
    return [name for name, _ in _capability_lines(Manifest.load(path).body)]


def _capability_lines(body: str) -> list[tuple[str, int]]:
    """(name, line_index) for each entry of the top-level capabilities list."""
    lines = body.split("\n")
    out: list[tuple[str, int]] = []
    in_caps = False
    for i, line in enumerate(lines):
        if line.rstrip() == "capabilities:":
            in_caps = True
            continue
        if in_caps:
            if line.strip().startswith("#"):
                continue
            m = _CAP_LINE.match(line)
            if m:
                out.append((m.group("name"), i))
            elif line.strip():  # a new top-level key ends the list
                break
    return out


def add_capability(path: Path, name: str, *, key_file: Path,
                   kid: str | None = None, comment: str | None = None) -> bool:
    """Declare one capability and re-sign. False if already declared.

    The new entry is spliced directly after the LAST existing entry, at the
    same indentation, so the document keeps its own shape. An optional comment
    rides above it — a capability with no stated reason is how manifests decay
    into lists nobody can audit.
    """
    manifest = Manifest.load(path)
    entries = _capability_lines(manifest.body)
    if not entries:
        raise ValueError(f"{path} has no top-level `capabilities:` list to add to")
    if any(existing == name for existing, _ in entries):
        return False

    lines = manifest.body.split("\n")
    last_name, last_idx = entries[-1]
    indent = _CAP_LINE.match(lines[last_idx]).group("indent")  # type: ignore[union-attr]
    insert: list[str] = []
    if comment:
        insert += [f"{indent}# {line}" for line in comment.splitlines()]
    insert.append(f"{indent}- {name}")
    lines[last_idx + 1:last_idx + 1] = insert

    _write_signed(path, "\n".join(lines), key_file=key_file,
                  kid=kid or manifest.kid)
    return True


def remove_capability(path: Path, name: str, *, key_file: Path,
                      kid: str | None = None) -> bool:
    """Withdraw one declaration and re-sign. False if it was not declared.

    Removes the entry line only — a comment above it stays, because prose
    explaining why something WAS declared is history worth keeping in a
    document whose whole job is being auditable.
    """
    manifest = Manifest.load(path)
    entries = _capability_lines(manifest.body)
    hits = [idx for entry, idx in entries if entry == name]
    if not hits:
        return False
    lines = manifest.body.split("\n")
    for idx in reversed(hits):
        del lines[idx]
    _write_signed(path, "\n".join(lines), key_file=key_file,
                  kid=kid or manifest.kid)
    return True


def sign(path: Path, *, key_file: Path, kid: str) -> None:
    """Re-sign after ANY edit — the general 'I changed the manifest' tool.

    Hand edits stay first-class: an operator who rewrote a safety paragraph in
    an editor runs `castor manifest sign` and the document verifies again.
    """
    _write_signed(path, Manifest.load(path).body, key_file=key_file, kid=kid)


def verify(path: Path, *, pub_pem: bytes) -> tuple[bool, str]:
    """Check the footer the way the GATEWAY will, against a given verify key."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization

    text = path.read_text()
    m = SIG_RE.search(text)
    if m is None:
        return False, "no ROBOT-MD-SIG footer"
    try:
        pub = serialization.load_pem_public_key(pub_pem)
        pub.verify(base64.b64decode(m.group("sig")), text[: m.start()].encode("utf-8"))
    except InvalidSignature:
        return False, "signature did not verify against the body"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"ok (kid {m.group('kid')})"


def _write_signed(path: Path, body: str, *, key_file: Path, kid: str | None) -> None:
    from cryptography.hazmat.primitives import serialization

    if kid is None:
        raise ValueError("no kid: the manifest was never signed and none was given")
    priv = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    canonical = body.rstrip("\n")
    sig = base64.b64encode(priv.sign(canonical.encode("utf-8"))).decode()
    # Atomic-ish: a crash mid-write must not leave a half manifest where a
    # gateway restart would read it.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(f"{canonical}\n<!-- ROBOT-MD-SIG kid={kid} sig={sig} -->\n")
    tmp.replace(path)
