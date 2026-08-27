"""What this robot can do — its live surface, its gaps, and its saved macros.

THE SAFETY PROPERTY, stated plainly: composing a capability grants NO new
physical authority. A composed capability is a macro, not a permission. Running
one issues N ordinary /v1/invoke calls, each signed by the operator's device and
each judged independently by the gateway, producing its own signed receipt. A
denied step halts the run and the denial is shown as the gateway wrote it.

That is what lets a chat model help build one. The model can arrange primitives
the operator has ALREADY allowed; it cannot widen the set. Widening is an
operator action at the robot's own shell — it edits ``gateway-policy.env`` and
restarts the gateway, and `castor up` never overwrites that file — never a
model one, and deliberately not reachable from this read-only console.

MANIFEST READS GO THROUGH ``castor.manifest`` / ``castor.pairing``. A ROBOT.md
is signed, and its comments are load-bearing safety prose; this module never
parses or rewrites one itself.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import manifest_path, robot_home

#: A workflow is a convenience, not a program. These caps keep a saved sequence
#: from becoming an unbounded schedule of physical motion.
MAX_STEPS = 25
MAX_WAIT_S = 30.0

router = APIRouter()


def store_file() -> Path:
    return robot_home() / "capabilities.json"


def _short_yaml_reason(exc: Exception) -> str:
    """One actionable line out of PyYAML's multi-line report.

    A parser error already names what broke and where; that is the whole of what
    an operator needs, and the rest of the report is context about PyYAML's own
    state. Counted FROM THE FRONTMATTER, because that is the text the parser was
    handed — the manifest's ``---`` fences and prose never reach it, so a
    file-relative number here would be a guess about someone else's framing.
    """
    problem = getattr(exc, "problem", None)
    mark = getattr(exc, "problem_mark", None)
    if problem and mark is not None:
        return f"{problem} (frontmatter line {mark.line + 1}, column {mark.column + 1})"
    first = str(exc).splitlines()
    return first[0] if first else exc.__class__.__name__


def policy_file() -> Path:
    """The gateway reads its policy here, so it can be changed without editing
    the unit. Read-only from this console."""
    return robot_home() / "gateway-policy.env"


# --------------------------------------------------------------------------- #
# The live surface — what the phone's picture of this robot should be
# --------------------------------------------------------------------------- #


@router.get("/surface")
def surface() -> dict:
    """This robot's capability surface, AS IT IS NOW.

    The pairing QR carries the same projection, but a QR is a photograph: it
    freezes the robot at the moment it was generated, and every capability added
    since (`castor capability add`, a re-signed manifest, a driver upgrade) is
    invisible to a phone that scanned it last week. This endpoint is the
    refresh — same shape, over HTTP, from the same manifest.

    UNTRIMMED ON PURPOSE. ``castor pair`` fits the surface into
    ``PAIR_QR_BYTE_BUDGET`` because a QR too dense to scan pairs nothing; HTTP
    has no such budget, so the contracts and descriptors the QR may have dropped
    all travel here. A client that scanned a trimmed QR gets the whole thing
    back on its first refresh.

    The inner ``capability_surface`` object is byte-for-byte the shape the QR's
    ``capability_surface`` field carries — one projection, one parser on the
    client. ``null`` means the manifest declares no capabilities at all, which
    is a fact about this robot, not an error.
    """
    import yaml

    from castor.pairing import capability_surface_from_manifest, read_rrn_from_manifest

    path = manifest_path()
    if not path.is_file():
        # An honest 404: this robot has no manifest, so it has nothing to
        # declare. Answering with an empty surface would be a claim of its own.
        raise HTTPException(
            status_code=404,
            detail=f"no ROBOT.md at {path} — this robot has not been set up "
            f"(`castor up`) or ROBOT_HOME points somewhere else",
        )
    try:
        rrn = read_rrn_from_manifest(path)
    except yaml.YAMLError as exc:
        # A hand-edited manifest that no longer parses is the operator's to fix,
        # and saying WHERE it broke is the whole value of answering at all. A
        # 500 reads as "the console is down" and sends them to journalctl
        # instead of to the line they just typed. Not a 404 either: the file is
        # right there. And never a degraded 200 — the surface projection
        # swallows a YAML error and returns None, so a robot with a garbled
        # frontmatter would otherwise answer "I declare nothing", which is a
        # claim about the robot rather than a fact about the file.
        raise HTTPException(
            status_code=422,
            detail=f"ROBOT.md frontmatter unparseable: {_short_yaml_reason(exc)}",
        ) from exc
    except (OSError, ValueError):
        rrn = ""
    return {
        "capability_surface": capability_surface_from_manifest(path),
        "rrn": rrn,
    }


@router.get("/gaps")
def gaps() -> dict:
    """What this robot's hardware could do that its software can't yet.

    Written by `castor gaps` / `castor up` (see docs/SKILL-GAPS.md). Served to
    the app so the CHAT can be grounded in the same facts the operator sees — a
    brain asked "what could we add to this robot?" should answer from the
    robot's own detection, not invent hardware. An absent file is an empty
    answer, not an error: gaps are optional context.
    """
    try:
        return json.loads((robot_home() / "gaps.json").read_text())
    except (OSError, ValueError):
        return {"v": 1, "gaps": []}


# --------------------------------------------------------------------------- #
# Manifest + policy
# --------------------------------------------------------------------------- #


def declared_capabilities() -> list[str]:
    """Capabilities the SIGNED manifest declares, in declaration order."""
    from castor import manifest as manifest_mod

    try:
        return manifest_mod.capabilities(manifest_path())
    except OSError:
        return []


def _read_policy() -> dict[str, str]:
    path = policy_file()
    values: dict[str, str] = {}
    for source in path.read_text().splitlines() if path.is_file() else []:
        line = source.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        # Values are written quoted (tier bindings contain '|', which a shell
        # sourcing this file would otherwise read as a pipeline). Strip the
        # quotes back off here, or every tool name carries one and nothing
        # matches the allowlist.
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        values[key.strip()] = val
    return values


def allowed_tools() -> list[str]:
    raw = _read_policy().get("ROBOT_MD_TOOL_ALLOWLIST", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def configured_actuator() -> str:
    """The gateway actuator this robot is wired to, per its bearers.yaml."""
    import yaml

    try:
        data = yaml.safe_load((robot_home() / "bearers.yaml").read_text()) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return ""
    actuator = data.get("actuator") if isinstance(data, dict) else None
    if isinstance(actuator, dict) and actuator.get("name"):
        return str(actuator["name"])
    return ""


def implemented_capabilities() -> set[str]:
    """What the installed actuator can actually execute.

    Read from the DRIVER rather than duplicated here, so the answer cannot drift
    from the code that does the work. Resolved through the gateway's actuator
    entry-point group, so this works for whichever actuator a host installed
    rather than naming one robot's package.

    An empty set means "unknown", and unknown never filters anything out.
    """
    name = configured_actuator()
    if not name:
        return set()
    try:
        from importlib import import_module
        from importlib.metadata import entry_points

        for ep in entry_points(group="robot_md_gateway.actuators"):
            if ep.name != name:
                continue
            module = import_module(ep.module)
            return set(getattr(module, "IMPLEMENTED_CAPABILITIES", ()) or ())
    except Exception:  # noqa: BLE001 - an unloadable plugin is "unknown"
        return set()
    return set()


def permitted_primitives() -> list[str]:
    """What a plan may actually be built from: declared AND allowed.

    The intersection is the honest answer — a capability the manifest declares
    but the operator has not allowed will be denied at run time, so offering it
    as a building block would set the user up to fail.
    """
    allowed = set(allowed_tools())
    implemented = implemented_capabilities()
    declared = declared_capabilities()
    # Implemented matters as much as allowed: a capability the driver cannot
    # execute fails as an actuator error, which reads like a bug rather than a
    # decision. Only offer building blocks that can genuinely run.
    permitted = [c for c in declared if c in allowed and (not implemented or c in implemented)]

    # Capabilities the driver implements and the operator has allowed, but that
    # the SIGNED manifest predates. arm.reach_point is the live example: it was
    # written after that robot's manifest was signed, and re-signing a robot's
    # root-of-trust document is a deliberate operator act, not a side effect of
    # shipping a driver update.
    #
    # Offering them is right — the operator explicitly allowed them and the
    # driver really can run them — but they are appended rather than merged, so
    # the distinction between "declared and signed" and "allowed by policy"
    # stays visible to anything that cares.
    for cap in sorted(implemented - set(declared)):
        if cap in allowed:
            permitted.append(cap)
    return permitted


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


def _placeholders(value) -> set[str]:
    """Every {name} appearing anywhere in a step's arguments."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", value))
    elif isinstance(value, dict):
        for nested in value.values():
            found |= _placeholders(nested)
    elif isinstance(value, list):
        for nested in value:
            found |= _placeholders(nested)
    return found


def _substitute(value, values: dict[str, str]):
    """Replace {name} placeholders throughout a step's arguments."""
    if isinstance(value, str):
        out = value
        for key, replacement in values.items():
            out = out.replace("{" + key + "}", replacement)
        return out
    if isinstance(value, dict):
        return {k: _substitute(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, values) for v in value]
    return value


def _next_revision(name: str) -> int:
    for item in _load():
        if item.get("name") == name:
            return int(item.get("revision", 0)) + 1
    return 1


def _load() -> list[dict]:
    try:
        data = json.loads(store_file().read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    path = store_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2))


class Step(BaseModel):
    """One step of a workflow.

    ``tool_args`` values may contain ``{placeholders}`` naming a parameter; they
    are substituted at RUN time, then the resulting args are validated exactly
    like any other invoke. A parameter can therefore change WHICH object is
    targeted, never WHICH capability runs — the tool name is fixed when the
    workflow is saved.
    """

    tool_name: str
    tool_args: dict = {}
    #: Run this step only when the previous one had this outcome.
    #: "always" (default) | "allowed" | "denied"
    when: str = "always"
    #: Seconds to wait BEFORE this step. Bounded — a workflow is not a scheduler.
    wait_s: float = 0.0
    #: Human-readable note shown while the step runs.
    note: str = ""


class Parameter(BaseModel):
    name: str
    description: str = ""
    default: str = ""


class Capability(BaseModel):
    name: str
    description: str = ""
    steps: list[Step]
    #: Named inputs the caller may supply when running this workflow.
    parameters: list[Parameter] = []


_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9]+)*$")


@router.get("/capabilities/primitives")
def primitives() -> dict:
    """The building blocks, and why anything declared is unavailable."""
    allowed = set(allowed_tools())
    implemented = implemented_capabilities()
    declared = declared_capabilities()
    permitted = set(permitted_primitives())
    blocked = []
    for cap in declared:
        if cap in permitted:
            continue
        if implemented and cap not in implemented:
            # The honest reason. Enabling it in policy would NOT make it work.
            blocked.append(
                {
                    "name": cap,
                    "reason": "not implemented by this robot's driver",
                    "operator_can_enable": False,
                }
            )
        else:
            blocked.append(
                {
                    "name": cap,
                    "reason": "not in the operator allowlist",
                    "operator_can_enable": True,
                }
            )
    return {
        "permitted": sorted(permitted),
        "declared_but_blocked": blocked,
        "allowlist": sorted(allowed),
        "implemented": sorted(implemented),
    }


@router.get("/capabilities/policy")
def get_policy() -> dict:
    """What the gateway will currently run. READ ONLY from here — widening the
    set is an operator act at the robot's own shell (gateway-policy.env)."""
    policy = _read_policy()
    return {
        "allowlist": allowed_tools(),
        "tool_min_tier": policy.get("ROBOT_MD_TOOL_MIN_TIER", ""),
        "declared": declared_capabilities(),
    }


@router.get("/capabilities")
def list_capabilities() -> dict:
    permitted = set(permitted_primitives())
    items = []
    for item in _load():
        missing = [
            s["tool_name"] for s in item.get("steps", []) if s.get("tool_name") not in permitted
        ]
        # A plan can go stale if the operator later revokes a primitive; say so
        # rather than letting it fail halfway through a motion.
        items.append({**item, "runnable": not missing, "blocked_steps": missing})
    return {"capabilities": items, "permitted_primitives": sorted(permitted)}


@router.post("/capabilities")
def save_capability(cap: Capability) -> dict:
    name = cap.name.strip()
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="name must look like 'arm.tidy' — lowercase, dot-separated",
        )
    if not cap.steps:
        raise HTTPException(status_code=422, detail="a capability needs at least one step")
    if name in set(declared_capabilities()):
        raise HTTPException(
            status_code=409,
            detail=f"{name} is already a primitive on this robot; pick another name",
        )
    if len(cap.steps) > MAX_STEPS:
        raise HTTPException(
            status_code=422,
            detail=f"a workflow may have at most {MAX_STEPS} steps (got {len(cap.steps)})",
        )
    declared_params = {p.name for p in cap.parameters}
    permitted = set(permitted_primitives())
    for index, step in enumerate(cap.steps, start=1):
        if step.when not in ("always", "allowed", "denied"):
            raise HTTPException(
                status_code=422,
                detail=f"step {index}: `when` must be always, allowed, or denied",
            )
        if not (0 <= step.wait_s <= MAX_WAIT_S):
            raise HTTPException(
                status_code=422,
                detail=f"step {index}: wait_s must be between 0 and {MAX_WAIT_S}",
            )
        # Every placeholder must name a declared parameter, or the workflow
        # would fail mid-run with the robot already in some intermediate state.
        for missing in _placeholders(step.tool_args) - declared_params:
            raise HTTPException(
                status_code=422,
                detail=f"step {index}: uses {{{missing}}} but no such parameter is declared",
            )
        if step.tool_name not in permitted:
            # Refuse rather than save-and-fail-later: this is the check that
            # keeps composition from becoming a way to widen authority.
            raise HTTPException(
                status_code=422,
                detail=(
                    f"step {index} uses {step.tool_name!r}, which this robot "
                    f"does not currently permit. Allowed: {sorted(permitted)}"
                ),
            )

    items = [i for i in _load() if i.get("name") != name]
    record = {
        "name": name,
        "description": cap.description.strip(),
        "steps": [s.model_dump() for s in cap.steps],
        "parameters": [p.model_dump() for p in cap.parameters],
        "updated_at": time.time(),
        # Bumped on every save so a client can tell a workflow changed since the
        # human last reviewed it.
        "revision": _next_revision(name),
    }
    items.append(record)
    _save(items)
    return {**record, "runnable": True, "blocked_steps": []}


class ResolveRequest(BaseModel):
    arguments: dict = {}


@router.post("/capabilities/{name}/resolve")
def resolve_capability(name: str, req: ResolveRequest) -> dict:
    """Expand a saved workflow into the concrete steps a run would issue.

    The client shows this to the human BEFORE anything is signed, so the
    sequence they approve is the sequence that executes. Substitution happens
    here rather than on the phone so the robot's own view of the workflow is
    the authoritative one.
    """
    item = next((i for i in _load() if i.get("name") == name), None)
    if item is None:
        raise HTTPException(status_code=404, detail=f"no capability named {name!r}")

    values = {p["name"]: p.get("default", "") for p in item.get("parameters", [])}
    values.update({k: str(v) for k, v in req.arguments.items()})
    unset = [k for k, v in values.items() if v == ""]
    if unset:
        raise HTTPException(
            status_code=422,
            detail=f"missing value for: {', '.join(sorted(unset))}",
        )

    permitted = set(permitted_primitives())
    resolved = []
    for step in item.get("steps", []):
        if step.get("tool_name") not in permitted:
            # Re-checked at RESOLVE time, not just at save: the operator may
            # have revoked a primitive since this workflow was written.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{step.get('tool_name')!r} is no longer permitted on this "
                    f"robot — this workflow cannot run as written"
                ),
            )
        resolved.append(
            {
                "tool_name": step["tool_name"],
                "tool_args": _substitute(step.get("tool_args", {}), values),
                "when": step.get("when", "always"),
                "wait_s": step.get("wait_s", 0.0),
                "note": step.get("note", ""),
            }
        )
    return {
        "name": name,
        "revision": item.get("revision", 1),
        "arguments": values,
        "steps": resolved,
        "motion_steps": sum(1 for s in resolved if not s["tool_name"].startswith("status.")),
    }


@router.delete("/capabilities/{name}")
def delete_capability(name: str) -> dict:
    items = _load()
    remaining = [i for i in items if i.get("name") != name]
    if len(remaining) == len(items):
        raise HTTPException(status_code=404, detail=f"no capability named {name!r}")
    _save(remaining)
    return {"deleted": name}
