"""castor.rcan3.reader — parse ROBOT.md into a typed RcanManifest.

Thin wrapper over rcan.from_manifest (rcan-py 3.3.0+), adding:
- runtime-id selection via ``select_runtime``
- direct attribute access to safety block / agent.runtimes[]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rcan import from_manifest as _from_manifest


@dataclass(frozen=True)
class RcanManifest:
    """Canonical in-memory view of a ROBOT.md for castor.

    Fields mirror ``rcan.ManifestInfo`` but rename for castor-local clarity
    and add a runtime selector.
    """

    path: Path
    rrn: str | None
    rcan_uri: str | None
    endpoint: str | None
    signing_alg: str | None
    robot_name: str | None
    rcan_version: str | None
    agent_runtimes: list[dict[str, Any]] | None
    safety: dict[str, Any] = field(default_factory=dict)
    frontmatter: dict[str, Any] = field(default_factory=dict)

    def select_runtime(self, runtime_id: str | None) -> dict[str, Any]:
        """Return the runtime entry matching ``runtime_id``, or the default.

        - If ``runtime_id`` is None, returns the entry with ``default: true``.
        - If no entry has ``default: true`` and only one runtime is declared,
          returns that sole entry.
        - Raises ``KeyError`` if no match.
        """
        runtimes = self.agent_runtimes or []
        if runtime_id is None:
            defaults = [r for r in runtimes if r.get("default") is True]
            if defaults:
                return defaults[0]
            if len(runtimes) == 1:
                return runtimes[0]
            raise KeyError(
                "no default runtime declared and multiple runtimes present — "
                "pass runtime_id explicitly"
            )
        for entry in runtimes:
            if entry.get("id") == runtime_id:
                return entry
        raise KeyError(f"runtime id {runtime_id!r} not declared in agent.runtimes[]")


def read_robot_md(path: str | Path) -> RcanManifest:
    """Read a ROBOT.md file at ``path`` and return a :class:`RcanManifest`.

    Raises:
        FileNotFoundError: if the file doesn't exist.
        ValueError: if the frontmatter is missing or malformed.
        ImportError: if PyYAML is not installed.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{p} does not exist")

    info = _from_manifest(p)
    safety = dict(info.frontmatter.get("safety") or {})

    return RcanManifest(
        path=p,
        rrn=info.rrn,
        rcan_uri=info.rcan_uri,
        endpoint=info.endpoint,
        signing_alg=info.signing_alg,
        robot_name=info.robot_name,
        rcan_version=info.rcan_version,
        agent_runtimes=info.agent_runtimes,
        safety=safety,
        frontmatter=dict(info.frontmatter),
    )
