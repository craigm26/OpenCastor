"""Model management — the local (Ollama) catalog and the one chat turn.

Design decision, driven by App Review constraints:

  * DOWNLOADS HAPPEN ON THE ROBOT. The phone never pulls weights; it names a
    model and this service asks the local Ollama daemon to fetch it. That keeps
    the iOS app free of remote-payload loading and keeps its "works with no
    hardware" review path honest.
  * FRONTIER API KEYS NEVER REACH THIS SERVICE. The phone holds a user-pasted
    key in its Keychain and calls the provider directly. There is deliberately
    no key field, no key storage, and no proxy endpoint here for those — a
    first-party proxy would turn a user's own traffic into data this project
    collects. (The one robot-side key, Gemini's, is different in kind: that
    model reasons about the robot's own frames, so it has to live where the
    frames are.)

So this module serves exactly two things: the local model catalog (and the
machinery to grow it), and enough provider metadata for a settings screen to
render. It holds no secrets.

THE CHAT TURN IS A RECEIPT, NOT JUST AN ANSWER. Every reply, JSON or streamed,
says which branch answered (`provider`), what the robot actually applied
(`options_applied`, after its own clamps), how many recalled memories it added
and the sha256 of the system prompt it really sent. The phone decides whether
words stayed on the owner's network, and a trace claims temperature 0 and seed
7, from what the robot SAYS it did, never from what the phone asked for: an
old console that silently dropped an option looked exactly like one that
applied it.

ONE GENERATION AT A TIME, FOR EVERYBODY. The robot's model shares a Pi with the
gateway and the runtime, and that Pi has rebooted under load. Two phones, a
bench run and a watch each starting a turn is four generations on one CPU, so a
second turn is refused with 409 "busy" at once rather than queued behind the
first. The same reasoning refuses chat, pulls and activations with 409
"driving" while the robot is moving or holds an open drive approval, when this
console has been told how to ask (see `read_drive_state`). It never guesses.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from castor.brain.memory_recall import ground_system_prompt

from . import brains
from .config import chat_upstream, manifest_path, ollama_url, robot_home

#: Ollama holds a model in RAM for this long after use; the first turn on a cold
#: model costs a load (~75 s for a 4B on a Pi 5), which the UI must not read as
#: a hang. Surfaced in /models/local so the client can warn before the first send.
COLD_LOAD_HINT_S = 75

router = APIRouter()


def state_file() -> Path:
    return robot_home() / "active-model.json"


def _ollama(path: str, payload: dict | None = None, timeout: float = 15.0, base: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{base or ollama_url()}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def read_active() -> dict:
    """The model the robot's chat should use."""
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {"provider": "ollama", "model": ""}


def write_active(provider: str, model: str) -> dict:
    state = {"provider": provider, "model": model, "updated_at": time.time()}
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))
    return state


class PullState:
    """Progress of the one in-flight `ollama pull`.

    Only one pull runs at a time: they are bandwidth- and disk-bound, and two
    concurrent pulls on a Pi make both slower with no benefit.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.model = ""
        self.status = "idle"
        self.completed = 0
        self.total = 0
        self.error: str | None = None
        self.started_at = 0.0
        self.finished_at = 0.0

    @property
    def running(self) -> bool:
        return self.status not in ("idle", "success", "error")

    def snapshot(self) -> dict:
        with self.lock:
            pct = (self.completed / self.total * 100) if self.total else 0.0
            return {
                "model": self.model,
                "status": self.status,
                "completed_bytes": self.completed,
                "total_bytes": self.total,
                "percent": round(pct, 1),
                "error": self.error,
                "running": self.running,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }


pull_state = PullState()


def _pull_worker(model: str) -> None:
    body = json.dumps({"model": model, "stream": True}).encode()
    req = urllib.request.Request(
        f"{ollama_url()}/api/pull",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # No read timeout: a multi-GB pull on a Pi legitimately runs for many
        # minutes between progress lines.
        with urllib.request.urlopen(req) as resp:
            for raw in resp:
                if not raw.strip():
                    continue
                try:
                    evt = json.loads(raw.decode())
                except ValueError:
                    continue
                with pull_state.lock:
                    if evt.get("error"):
                        pull_state.status = "error"
                        pull_state.error = str(evt["error"])
                        pull_state.finished_at = time.time()
                        return
                    pull_state.status = evt.get("status", pull_state.status)
                    if evt.get("total"):
                        pull_state.total = int(evt["total"])
                    if evt.get("completed") is not None:
                        pull_state.completed = int(evt["completed"])
        with pull_state.lock:
            pull_state.status = "success"
            pull_state.completed = pull_state.total or pull_state.completed
            pull_state.finished_at = time.time()
    except Exception as exc:  # noqa: BLE001 - the status field is the report
        with pull_state.lock:
            pull_state.status = "error"
            pull_state.error = f"{type(exc).__name__}: {exc}"
            pull_state.finished_at = time.time()


#: Models this project has actually run on Pi-class hosts, with the job each
#: is good at. CURATED, NOT SCRAPED: a suggestion is a recommendation, and
#: recommending a model nobody here has watched answer is how a first-timer's
#: first chat becomes a 40-minute download into a disappointment.
#:
#: SIZES ARE MEASURED. ``size_bytes`` is what Ollama's GET /api/tags reported
#: for each installed tag on the bench Pi 5 (Ollama 0.30.11, 2026-09-26) and
#: ``size_gb`` is that number in decimal gigabytes. The table used to be typed
#: from model cards and had gemma4:e2b at 3.0 GB; it is 7.16 GB on disk, so an
#: 8 GB robot was told a model "fits" that would have taken it down. ``ram_gb``
#: is still a rule of thumb (the measured size x 1.3), not a measurement,
#: because measuring it means loading the model.
#:
#: ``kind`` is "chat" or "embedding". An embedding model is memory machinery,
#: not something that answers, and anything choosing a model to TALK to (the
#: app's "use the robot's own model", `castor up`'s brain detection) must skip
#: it. ``vision`` is from the GGUF headers of the same installed files: every
#: chat model here carries a vision projector.
SUGGESTED_MODELS = [
    {
        "name": "qwen3.5:2b",
        "size_bytes": 2741192820,
        "size_gb": 2.74,
        "ram_gb": 3.6,
        "kind": "chat",
        "vision": True,
        "good_for": "Fast chat and workflow naming. The snappiest thing a Pi runs.",
    },
    {
        "name": "gemma4:e2b",
        "size_bytes": 7162405886,
        "size_gb": 7.16,
        "ram_gb": 9.3,
        "kind": "chat",
        "vision": True,
        "good_for": "Better answers than 2B, still quick after first load.",
    },
    {
        "name": "qwen3.5:4b",
        "size_bytes": 3389983735,
        "size_gb": 3.39,
        "ram_gb": 4.4,
        "kind": "chat",
        "vision": True,
        "good_for": "Noticeably better reasoning; slower first load (~75 s cold).",
    },
    {
        "name": "gemma4:e4b-it-qat",
        "size_bytes": 6146501801,
        "size_gb": 6.15,
        "ram_gb": 8.0,
        "kind": "chat",
        "vision": True,
        "good_for": "The best local chat quality this bench has run.",
    },
    {
        "name": "gemma3:4b",
        "size_bytes": 3338801804,
        "size_gb": 3.34,
        "ram_gb": 4.3,
        "kind": "chat",
        "vision": True,
        "good_for": "Vision: can look at photos and camera frames locally.",
    },
    {
        "name": "nomic-embed-text",
        "size_bytes": 274302450,
        "size_gb": 0.27,
        "ram_gb": 0.4,
        "kind": "embedding",
        "vision": False,
        "good_for": "Memory: turns notes and chat into searchable long-term memory "
        "(embeddings — not a chat model).",
    },
    # Benched 2026-08-14 on a Pi 5/16GB through the console's own chat path:
    # cold turn 136 s, warm turn 73 s for two sentences (~0.7 tok/s). The
    # QUALITY earned the listing — its answers matched the subscription's
    # causal reasoning on the dead-drive-chip question — and the speed is
    # stated plainly so nobody mistakes it for a conversation partner.
    # Encoder-free multimodal (Google's writeup: vision is a single-matmul
    # 52M projector, audio raw-projected) — the first LOCAL model on this
    # bench that can genuinely look at a photo. Verified: read a dense QR
    # correctly, on-device. Vision tolerates its pace far better than chat:
    # one photo, one answer, a minute is fine while parked.
    {
        "name": "gemma4:12b-it-qat",
        "size_bytes": 7151003754,
        "size_gb": 7.15,
        "ram_gb": 9.3,
        "kind": "chat",
        "vision": True,
        "good_for": "Vision + highest-quality local answers (encoder-free "
        "multimodal, 262K context). SLOW on a Pi (~1 min/reply) — "
        "best as the VISION brain, with a fast model on chat.",
    },
]

#: How much of the host's RAM a model may claim. The remaining 40% is the
#: gateway, the runtime, and this console — a model that fits with nothing else
#: running is a model that gets the robot OOM-killed mid-drive.
RAM_FIT_FRACTION = 0.6


def _total_ram_gb() -> float:
    try:
        with open("/proc/meminfo") as meminfo:
            for line in meminfo:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 0.0


def _base_tag(name: str) -> str:
    """Ollama reports "nomic-embed-text:latest" for a pull of
    "nomic-embed-text"; an exact match marked an installed model as missing and
    offered a download the user already had."""
    return name[:-7] if name.endswith(":latest") else name


# --------------------------------------------------------------------------- #
# What each installed model can do, as Ollama itself reports it
# --------------------------------------------------------------------------- #

#: Families whose models only embed. The FALLBACK classifier, for an Ollama too
#: old to report `capabilities` from /api/show or one that failed to answer it;
#: when Ollama does report capabilities, they win outright.
EMBEDDING_FAMILIES = frozenset({"bert", "nomic-bert", "xlm-roberta", "jina-bert-v2"})

#: What POST /api/show said about each model, keyed by DIGEST. A digest names
#: exact weights, so the answer can never go stale under it: re-pulling a tag
#: that changed gets a new digest and a fresh look, and a name alone would have
#: kept serving the old model's context length for the new one.
_facts_lock = threading.Lock()
_facts_by_digest: dict[str, dict] = {}


def _facts_from_show(show: dict) -> dict:
    caps = show.get("capabilities")
    capabilities = [str(c) for c in caps] if isinstance(caps, list) else None
    info = show.get("model_info") or {}
    context_length = None
    arch = info.get("general.architecture")
    candidates = [f"{arch}.context_length"] if arch else []
    candidates += [k for k in info if k.endswith(".context_length")]
    for key in candidates:
        value = info.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            context_length = value
            break
    return {"capabilities": capabilities, "context_length": context_length}


def model_facts(
    name: str, digest: str | None = None, *, base: str | None = None, timeout: float = 5.0
) -> dict | None:
    """``{"capabilities": [...] | None, "context_length": int | None}`` for one
    installed model, or None when Ollama would not say.

    /api/show reads the model's manifest and GGUF header; it does not load
    weights, so asking costs milliseconds and no RAM. A FAILED look is not
    cached: a daemon that was restarting must not leave a model looking blind
    and contextless until the console restarts.
    """
    if digest:
        with _facts_lock:
            cached = _facts_by_digest.get(digest)
        if cached is not None:
            return cached
    try:
        show = _ollama("/api/show", {"model": name}, timeout=timeout, base=base)
    except Exception:  # noqa: BLE001 - "unknown" is an answer the caller handles
        return None
    facts = _facts_from_show(show if isinstance(show, dict) else {})
    if digest:
        with _facts_lock:
            _facts_by_digest[digest] = facts
    return facts


def is_embedding_model(
    name: str, *, details: dict | None = None, capabilities: list[str] | None = None
) -> bool:
    """True for a model that embeds and cannot answer a chat turn.

    Capabilities from Ollama decide when there are any. Without them the
    family Ollama reported decides, and only then the name. `castor up` used to
    pick the SMALLEST installed model as the chat brain, which on a robot with
    memory recall is nomic-embed-text, and every chat turn with it fails.
    """
    if capabilities is not None:
        return "embedding" in capabilities and "completion" not in capabilities
    details = details or {}
    families = {str(f).lower() for f in (details.get("families") or []) if f}
    if details.get("family"):
        families.add(str(details["family"]).lower())
    if families & EMBEDDING_FAMILIES:
        return True
    return "embed" in name.split(":", 1)[0].lower()


def is_chat_model(row: dict, *, base: str | None = None, timeout: float = 5.0) -> bool:
    """Whether an /api/tags row is a model that can answer a chat turn."""
    facts = model_facts(row.get("name", ""), row.get("digest"), base=base, timeout=timeout)
    return not is_embedding_model(
        row.get("name", ""),
        details=row.get("details"),
        capabilities=facts["capabilities"] if facts else None,
    )


@router.get("/models/suggestions")
def suggestions() -> dict:
    """Curated models that FIT this host, judged by the host itself.

    The download box used to be a blank text field with a placeholder — which
    assumes the person already knows the answer to the hardest question a
    first-timer has. The robot knows its own RAM, so it answers.

    ``measured_sizes`` tells a caller the sizes are the measured ones (see
    SUGGESTED_MODELS): a console without it is running the old typed-in table,
    and a caller choosing a download by fit must not trust those numbers.
    """
    ram = _total_ram_gb()
    try:
        installed = {_base_tag(m["name"]) for m in _ollama("/api/tags").get("models", [])}
    except Exception:  # noqa: BLE001 - no daemon means "nothing installed"
        installed = set()
    active = read_active().get("model", "")
    out = []
    for m in SUGGESTED_MODELS:
        fits = ram > 0 and m["ram_gb"] <= ram * RAM_FIT_FRACTION
        out.append(
            {
                **m,
                "fits": fits,
                "installed": _base_tag(m["name"]) in installed,
                "active": m["name"] == active,
                "note": None
                if fits
                else f"needs ~{m['ram_gb']:.0f} GB free; this host has {ram:.0f} GB total",
            }
        )
    return {"host_ram_gb": round(ram, 1), "measured_sizes": True, "suggestions": out}


@router.get("/models/local")
def local_models() -> dict:
    """Models already on the robot's disk, plus which one is active.

    Each row says what the model CAN DO, from Ollama's own /api/show (cached per
    digest): ``capabilities`` as Ollama lists them ("completion" or
    "embedding", plus "vision", "tools", "thinking"), ``vision``,
    ``context_length`` and ``digest``, and ``kind`` ("chat" or "embedding").
    The phone classified robot models by name and read an unknown prefix as
    remote, so nomic-embed-text would have been labelled a brain "over the
    internet". ``capabilities``, ``vision`` and ``context_length`` are null when
    Ollama would not say, which is different from "no".

    The top-level flags are what this console can do, so a phone can tell it
    from an older one without guessing from a version string:
    ``honors_provider`` (a per-turn `provider` is obeyed), ``stream`` (NDJSON
    chat), ``measured_sizes`` (suggestion sizes are measured), and
    ``drive_aware`` (this console can ask the gateway whether the robot is
    driving right now, and refuses to think while it is; false means it cannot
    know, because it was not configured or the gateway would not say, and the
    phone must check for itself).
    """
    try:
        tags = _ollama("/api/tags")
    except Exception as exc:  # noqa: BLE001 - reported as 503 below
        raise HTTPException(status_code=503, detail=f"ollama unreachable: {exc}") from exc
    try:
        loaded = {m["name"] for m in _ollama("/api/ps").get("models", [])}
    except Exception:  # noqa: BLE001 - "which are warm" is optional detail
        loaded = set()
    active = read_active()
    models = []
    for m in tags.get("models", []):
        details = m.get("details") or {}
        facts = model_facts(m["name"], m.get("digest"))
        capabilities = facts["capabilities"] if facts else None
        models.append(
            {
                "name": m["name"],
                "size_bytes": m.get("size", 0),
                "family": details.get("family"),
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
                "loaded": m["name"] in loaded,
                "active": m["name"] == active.get("model"),
                "digest": m.get("digest"),
                "capabilities": capabilities,
                "vision": ("vision" in capabilities) if capabilities is not None else None,
                "context_length": facts["context_length"] if facts else None,
                "kind": "embedding"
                if is_embedding_model(m["name"], details=details, capabilities=capabilities)
                else "chat",
            }
        )
    models.sort(key=lambda m: m["size_bytes"])
    return {
        "models": models,
        "active": active,
        "cold_load_hint_s": COLD_LOAD_HINT_S,
        "honors_provider": True,
        "stream": True,
        "measured_sizes": True,
        # What the console can actually know right now, not only whether it was
        # configured: a manifest with no RRN, a refused token or a gateway that is
        # down all leave every turn unrefused.
        "drive_aware": drive_guard_configured() and read_drive_state()["known"],
    }


@router.get("/models/ps")
def running_models() -> dict:
    """What Ollama holds in memory right now, and whether the robot is thinking.

    The phone's wait line comes from here ("Waking up gemma4:e2b on Bob" versus
    an answer that starts at once), not from a load time measured on some other
    device. ``busy`` is this console's generation lock: true means the next
    chat turn will be refused with 409 "busy" until the current one ends.
    """
    try:
        ps = _ollama("/api/ps")
    except Exception as exc:  # noqa: BLE001 - reported as 503 below
        raise HTTPException(status_code=503, detail=f"ollama unreachable: {exc}") from exc
    return {
        "models": [
            {
                "name": m.get("name") or m.get("model", ""),
                "size_bytes": m.get("size", 0),
                "size_vram": m.get("size_vram", 0),
                "context_length": m.get("context_length"),
                "expires_at": m.get("expires_at"),
            }
            for m in ps.get("models", []) or []
        ],
        "busy": generation_lock.busy,
    }


class PullRequest(BaseModel):
    model: str


@router.post("/models/pull")
def start_pull(req: PullRequest) -> dict:
    """Ask the robot to download a model. Returns immediately; poll for status.

    Refused with 409 "driving" while the robot is moving or holds an open drive
    approval: a multi-GB pull is disk and network load on the Pi that is also
    running the wheels.
    """
    name = req.model.strip()
    if not name:
        raise HTTPException(status_code=422, detail="model is required")
    refuse_while_driving()
    with pull_state.lock:
        if pull_state.running:
            raise HTTPException(
                status_code=409,
                detail=f"a pull is already running ({pull_state.model})",
            )
        pull_state.model = name
        pull_state.status = "starting"
        pull_state.completed = 0
        pull_state.total = 0
        pull_state.error = None
        pull_state.started_at = time.time()
        pull_state.finished_at = 0.0
    threading.Thread(target=_pull_worker, args=(name,), daemon=True).start()
    return pull_state.snapshot()


@router.get("/models/pull/status")
def pull_status() -> dict:
    return pull_state.snapshot()


class ActiveRequest(BaseModel):
    provider: str = "ollama"
    model: str


@router.post("/models/active")
def set_active(req: ActiveRequest) -> dict:
    """Choose the brain the robot's chat uses.

    A local model must actually be on disk: silently accepting a name that is
    not installed would surface much later as a confusing chat failure.

    Refused with 409 "driving" while the robot is moving or holds an open drive
    approval. Changing the brain under a drive approval changes who drafts the
    next plan, and activating a local model is the first step of loading it.
    """
    refuse_while_driving()
    if req.provider in ("anthropic-sub", "gemini-er"):
        # Robot-hosted brains have no local model file to validate; refuse only
        # if the operator has not configured them, so the failure is visible in
        # Settings rather than at the first chat turn.
        ok = (
            brains.anthropic_available()
            if req.provider == "anthropic-sub"
            else brains.gemini_available()
        )
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=f"{req.provider} is not configured on the robot",
            )
        return write_active(req.provider, req.model or req.provider)
    if req.provider == "ollama":
        try:
            names = {m["name"] for m in _ollama("/api/tags").get("models", [])}
        except Exception as exc:  # noqa: BLE001 - reported as 503 below
            raise HTTPException(status_code=503, detail=f"ollama unreachable: {exc}") from exc
        if req.model not in names:
            raise HTTPException(
                status_code=404,
                detail=f"model {req.model!r} is not installed — pull it first",
            )
    return write_active(req.provider, req.model)


@router.get("/models/active")
def get_active() -> dict:
    return read_active()


# --------------------------------------------------------------------------- #
# The one chat turn: options, receipts, the generation lock, the drive guard
# --------------------------------------------------------------------------- #

#: The branches a turn can take. Anything else named per turn is refused (422),
#: never guessed at.
PROVIDERS = ("ollama", "anthropic-sub", "gemini-er")

#: The sampling options a caller may set, and nothing else. An ALLOWLIST, not a
#: passthrough: Ollama's options also take num_gpu, num_thread, use_mmap and
#: friends, which change how a model is LOADED on a host that is also driving a
#: robot. An unknown key is refused (422) rather than dropped, so no trace can
#: ever claim an option the robot never applied.
CHAT_OPTION_KEYS = ("temperature", "top_p", "top_k", "seed", "num_predict", "num_ctx")

#: The most tokens one turn may generate. Ollama's own default is "until the
#: model stops", which on a Pi at a few tokens a second is a turn that can hold
#: the CPU, and the one generation lock every phone shares, for an hour. A
#: caller asking for more, for -1 ("forever") or -2 ("fill the context"), or
#: naming no limit at all, gets this, and the receipt says so.
NUM_PREDICT_MAX = 2048

#: The smallest context a caller may ask for. Below this nothing useful fits.
NUM_CTX_MIN = 512

#: The context ceiling on a host whose RAM this console cannot read, and the
#: floor of the RAM rule below. 4096 is what Ollama itself uses on a CPU host.
NUM_CTX_BASE = 4096

#: The context ceiling on the largest host.
NUM_CTX_MAX = 32768

#: How long a caller may ask Ollama to keep a model loaded after the turn. A
#: warm model is a fast next answer; a model pinned forever is RAM the gateway
#: and runtime never get back. Negative ("keep forever") is clamped to this too.
KEEP_ALIVE_MAX_S = 3600

#: The think levels Ollama accepts besides true/false.
THINK_LEVELS = ("low", "medium", "high")

#: llama.cpp takes a 32-bit seed. A larger one would be silently truncated into a
#: DIFFERENT seed, so it is refused instead.
SEED_MAX = 2**32 - 1

#: Wall clock for one blocking Ollama chat call, and the longest wait between
#: two streamed lines. Generous: a cold load of a 4B on a Pi 5 is ~75 s.
CHAT_TIMEOUT_S = 300.0

NDJSON = "application/x-ndjson"

_KEEP_ALIVE_RE = re.compile(r"(-?\d+(?:\.\d+)?)(ms|s|m|h)?")
_KEEP_ALIVE_UNIT_S = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, None: 1.0}


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def parse_options(raw: Any) -> dict:
    """Validate a turn's ``options`` object. Returns the options as given.

    Nonsense is REFUSED (422): an unknown key, a wrong type, a temperature below
    zero, a seed llama.cpp would truncate. Resource knobs that are merely too
    big (num_ctx, num_predict) are not nonsense; `clamp_options` shrinks those
    and the receipt shows the value that was applied. A null value means "the
    backend's default" and is dropped.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _refuse("options must be an object")
    unknown = sorted(set(raw) - set(CHAT_OPTION_KEYS))
    if unknown:
        raise _refuse(
            f"unknown option(s) {', '.join(repr(k) for k in unknown)}; "
            f"this robot accepts {', '.join(CHAT_OPTION_KEYS)}"
        )
    out: dict = {}
    for key in CHAT_OPTION_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if key in ("temperature", "top_p"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _refuse(f"{key} must be a number")
            high = 2.0 if key == "temperature" else 1.0
            try:
                in_range = math.isfinite(value) and 0.0 <= value <= high
            except OverflowError:  # an integer too large to be a float
                in_range = False
            if not in_range:
                raise _refuse(f"{key} must be between 0 and {high:g}")
            out[key] = float(value)
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise _refuse(f"{key} must be an integer")
        if key == "top_k" and value < 0:
            raise _refuse("top_k must be 0 or more")
        if key == "seed" and not 0 <= value <= SEED_MAX:
            raise _refuse(f"seed must be between 0 and {SEED_MAX}")
        out[key] = value
    return out


def num_ctx_ceiling(ram_gb: float) -> int:
    """The largest context this host may be asked to hold, from its RAM alone.

    A coarse rule, about 1K tokens of context per GB of RAM, rounded down to a
    power of two between NUM_CTX_BASE and NUM_CTX_MAX: 16384 on a 16 GB Pi 5,
    8192 on an 8 GB one. The KV cache grows linearly with num_ctx and lives in
    the same RAM as the gateway and the runtime, and a num_ctx a phone typed in
    must not be the thing that reboots the robot. A host that cannot read its
    RAM gets the smallest ceiling, never the largest.
    """
    gb = round(ram_gb)
    if gb < 4:
        return NUM_CTX_BASE
    return max(NUM_CTX_BASE, min(NUM_CTX_MAX, 1024 * 2 ** int(math.log2(gb))))


def clamp_options(options: dict, *, context_length: int | None, ram_gb: float) -> dict:
    """The options that will actually be sent, after this robot's clamps.

    num_predict is capped at NUM_PREDICT_MAX, and a request for "no limit"
    (0 or below, which Ollama reads as forever or fill-the-context) gets that
    cap too. num_ctx is capped at the model's own context length, when Ollama
    reports it, and at `num_ctx_ceiling` for this host.
    """
    applied = dict(options)
    if "num_predict" in applied:
        n = applied["num_predict"]
        applied["num_predict"] = NUM_PREDICT_MAX if n <= 0 else min(n, NUM_PREDICT_MAX)
    if "num_ctx" in applied:
        ceiling = num_ctx_ceiling(ram_gb)
        if context_length:
            ceiling = min(ceiling, context_length)
        applied["num_ctx"] = max(min(applied["num_ctx"], ceiling), min(NUM_CTX_MIN, ceiling))
    return applied


def parse_keep_alive(value: Any) -> int | None:
    """``keep_alive`` as whole seconds, clamped to KEEP_ALIVE_MAX_S, or None.

    Takes what Ollama takes in the simple forms: a number of seconds, or a
    string like "30m", "90s", "1h", "500ms". Compound Go durations ("1h30m") are
    refused rather than half-parsed. Negative means "keep forever" to Ollama and
    is clamped to the maximum; 0 unloads the model as soon as the turn ends.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise _refuse("keep_alive must be seconds or a duration like '30m'")
    if isinstance(value, int):
        # Kept an integer: one too large to be a float is still just "a lot".
        seconds = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise _refuse("keep_alive must be a finite number of seconds")
        seconds = value
    elif isinstance(value, str):
        match = _KEEP_ALIVE_RE.fullmatch(value.strip())
        if not match:
            raise _refuse("keep_alive must be seconds or a duration like '30m'")
        seconds = float(match[1]) * _KEEP_ALIVE_UNIT_S[match[2]]
    else:
        raise _refuse("keep_alive must be seconds or a duration like '30m'")
    if seconds < 0:
        return KEEP_ALIVE_MAX_S
    # Clamped before rounding: "999...9s" as a string is a float infinity, which
    # math.ceil cannot turn into an integer.
    return int(math.ceil(min(seconds, KEEP_ALIVE_MAX_S)))


def parse_think(value: Any) -> bool | str:
    """``think`` as Ollama takes it: true, false, or a level.

    Default false, and it stays the default for Pi-class hosts: measured
    2026-07-31, qwen3.5:2b with think=true did not return within 175 s even
    for "2+2", directly against Ollama, so it is the model and runtime, not this
    bridge. A caller who asks for thinking gets it, and a streamed turn shows
    the reasoning as it arrives instead of a silent three minutes.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value in THINK_LEVELS:
        return value
    raise _refuse(f"think must be true, false, or one of {', '.join(THINK_LEVELS)}")


class GenerationLock:
    """At most one generation on this robot at a time, for every caller.

    A TRY-LOCK, never a queue. A turn that waited behind another would hold a
    phone's request open for minutes with nothing to say, and a queue of them is
    exactly the load this lock exists to prevent. The second caller gets 409
    "busy" at once and the phone says the robot is finishing another answer.

    Held until the generation actually ENDS, not until its caller leaves: a
    JSON turn whose phone gave up keeps the lock until Ollama returns, because
    Ollama is still generating; a stream whose phone went away releases it when
    the upstream request is closed, which is what stops Ollama.

    One lock per console process, and `castor up` runs one console per robot.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.what = ""
        self.since = 0.0

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def try_acquire(self, what: str) -> _Ticket | None:
        if not self._lock.acquire(blocking=False):
            return None
        self.what, self.since = what, time.time()
        return _Ticket(self)

    def _release(self) -> None:
        self.what, self.since = "", 0.0
        self._lock.release()


class _Ticket:
    """One holder's claim on the lock. Releasing twice is harmless, which is
    what lets both a stream's own cleanup and its response's cleanup call it."""

    def __init__(self, owner: GenerationLock) -> None:
        self._owner = owner
        self._released = False
        self._mu = threading.Lock()

    def release(self) -> None:
        with self._mu:
            if self._released:
                return
            self._released = True
        self._owner._release()


generation_lock = GenerationLock()


# The drive guard ------------------------------------------------------------

#: How long a drive-state answer is reused. One status.report per second at
#: most, however many phones are asking: every invoke is a line in the gateway's
#: signed trace, and a chat turn is not worth a burst of them.
DRIVE_STATE_TTL_S = 1.0

#: How long the gateway gets to say whether the robot is driving. Short: a chat
#: turn waits on it, and "no answer" is reported as unknown, never as parked.
DRIVE_PROBE_TIMEOUT_S = 2.0

_drive_lock = threading.Lock()
_drive_cached: tuple[float, dict] | None = None


def drive_guard_configured() -> bool:
    """Whether this console has been told how to ask the gateway about motion.

    ``ROBOT_GATEWAY_URL`` and ``GATEWAY_READ_TOKEN`` in console.env. The drive
    approval lives only inside the actuator, in the gateway's process: no state
    file holds it, and the runtime exposes it only on its telemetry socket,
    behind a bearer this console does not hold. `castor up` does not give the
    console a gateway bearer, so on a stock robot this is False and the console
    says so (``drive_aware: false``) instead of guessing. The read bearer is
    enough: status.report is a read-tier tool, and nothing at read tier can
    make a wheel turn.
    """
    return bool(os.environ.get("ROBOT_GATEWAY_URL") and os.environ.get("GATEWAY_READ_TOKEN"))


def _judge_telemetry(telemetry: dict) -> dict:
    """Driving means MOVING, or holding a drive approval that could still move.

    An approval that is revoked, spent or expired says so itself (``revoked``,
    ``usable: false``) and no longer counts: it cannot move the car.
    """
    moving = telemetry.get("moving") is True
    envelope = telemetry.get("envelope")
    approval_open = (
        isinstance(envelope, dict)
        and bool(envelope.get("envelope_id"))
        and envelope.get("revoked") is not True
        and envelope.get("usable", True) is not False
    )
    if moving:
        detail = "the robot is moving"
    elif approval_open:
        detail = "a drive approval is open"
    else:
        detail = "parked, no drive approval open"
    return {"known": True, "driving": moving or approval_open, "detail": detail}


def _unknown(detail: str) -> dict:
    return {"known": False, "driving": False, "detail": detail}


def _probe_drive_state() -> dict:
    url = os.environ.get("ROBOT_GATEWAY_URL", "").rstrip("/")
    token = os.environ.get("GATEWAY_READ_TOKEN", "")
    if not (url and token):
        return _unknown("not configured: set ROBOT_GATEWAY_URL and GATEWAY_READ_TOKEN")
    try:
        from castor.pairing import read_rrn_from_manifest

        rrn = read_rrn_from_manifest(manifest_path())
    except Exception:  # noqa: BLE001 - no manifest means nothing to address
        rrn = ""
    if not rrn:
        return _unknown("this robot's ROBOT.md names no RRN to address")
    # The same RCAN INVOKE the runtime sends for its telemetry frame: OBSERVE
    # scope, the read-tier status tool, this robot's own identity.
    invoke = {
        "msg_id": f"console-{uuid.uuid4().hex[:12]}",
        "type": "rcan/v1/invoke",
        "ruri": f"rcan://{rrn}/status.report",
        "scope": "OBSERVE",
        "tool_name": "status.report",
        "tool_args": {},
        "manifest_path": str(manifest_path()),
        "nonce": uuid.uuid4().hex,
        "timestamp_ms": int(time.time() * 1000),
    }
    req = urllib.request.Request(
        f"{url}/v1/invoke",
        data=json.dumps(invoke).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=DRIVE_PROBE_TIMEOUT_S) as resp:
            body = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return _unknown(f"the gateway answered status.report with HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 - unreachable is unknown, not parked
        return _unknown(f"the gateway did not answer ({type(exc).__name__})")
    telemetry = body.get("telemetry") if isinstance(body, dict) else None
    if not isinstance(telemetry, dict):
        return _unknown("the gateway's status.report carried no telemetry")
    return _judge_telemetry(telemetry)


def read_drive_state() -> dict:
    """``{"known": bool, "driving": bool, "detail": str}``, asked of the gateway.

    UNKNOWN IS NOT PARKED. Not configured, no RRN, a gateway that is down or
    refuses: every one of them is ``known: False``, and a turn is not refused on
    a guess. The refusal happens only when the gateway itself says the robot is
    moving or holds an approval that could move it.
    """
    global _drive_cached
    with _drive_lock:
        if _drive_cached is not None and time.monotonic() - _drive_cached[0] < DRIVE_STATE_TTL_S:
            return _drive_cached[1]
        state = _probe_drive_state()
        _drive_cached = (time.monotonic(), state)
        return state


def refuse_while_driving() -> None:
    """409 "driving" when the gateway says the robot is driving; nothing otherwise."""
    state = read_drive_state()
    if state["known"] and state["driving"]:
        raise HTTPException(status_code=409, detail="driving")


# The request ----------------------------------------------------------------


class ChatRequest(BaseModel):
    system: str = ""
    message: str
    history: list[dict] = []
    model: str | None = None
    # true, false, or "low" | "medium" | "high" (see parse_think). Typed Any so a
    # wrong value earns a sentence saying what is accepted, not a schema dump.
    think: Any = False
    #: A JPEG from the CALLER's camera, base64. The phone's own view answers a
    #: different question from a robot camera: "is the door shut?" is asked from
    #: where the person is standing, and for a phone-driven vehicle it is the
    #: only camera there is.
    image_b64: str | None = None
    #: Route THIS turn through a specific brain, regardless of the active one.
    #: The privacy rail behind it: a caller whose per-robot Vision pick is a
    #: LOCAL model must be able to force the ollama branch even while the
    #: active provider is a cloud/subscription brain — otherwise the frame
    #: rides off-LAN under a label that promised it would not. The active
    #: provider is untouched; this is one turn, not a mode change.
    provider: str | None = None
    #: NDJSON instead of one JSON object (see `chat`).
    stream: bool = False
    #: Sampling options, allowlisted (CHAT_OPTION_KEYS) and clamped.
    options: Any = None
    #: Seconds, or "30m"-style, clamped to KEEP_ALIVE_MAX_S. Absent means
    #: Ollama's own default (OLLAMA_KEEP_ALIVE on the host).
    keep_alive: Any = None
    #: False skips the recalled-memories appendix for this turn. A bench that
    #: compares brains needs every member to see the same instructions, and on a
    #: one-model-at-a-time host the embed call can evict the model under test.
    ground: bool = True


@dataclass
class _Turn:
    """A validated request, before anything has been generated."""

    req: ChatRequest
    provider: str
    model: str
    options: dict
    think: bool | str
    keep_alive_s: int | None
    image: bytes | None


def _decode_frame(image_b64: str) -> bytes:
    import base64

    try:
        return base64.b64decode(image_b64, validate=True)
    except Exception:  # noqa: BLE001 - a bad frame is a client error
        raise HTTPException(status_code=422, detail="image_b64 is not valid base64") from None


def _prepare(req: ChatRequest) -> _Turn:
    """Everything that can be refused before the robot starts thinking."""
    if req.provider is not None and req.provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail=f"unknown provider {req.provider!r}")
    options = parse_options(req.options)
    think = parse_think(req.think)
    keep_alive_s = parse_keep_alive(req.keep_alive)
    active = read_active()
    provider = req.provider or active.get("provider") or "ollama"
    if provider not in PROVIDERS:
        # What this console has always done with an active provider it does not
        # know: the local branch answers, and the receipt says so.
        provider = "ollama"
    image = None
    if provider == "ollama":
        model = req.model or active.get("model") or ""
        if not model:
            raise HTTPException(status_code=409, detail="no active model set")
    else:
        # A robot-hosted brain reads the frame as bytes; Ollama takes the base64.
        if req.image_b64:
            image = _decode_frame(req.image_b64)
        model = "claude (subscription)" if provider == "anthropic-sub" else "gemini-robotics-er"
    return _Turn(req, provider, model, options, think, keep_alive_s, image)


# Grounding and the receipt --------------------------------------------------


def _count_recalled(before: str, after: str) -> int:
    """How many recalled memories `ground_system_prompt` appended.

    Counted from the block itself rather than by running recall a second time:
    one ranker, and the count is of what was SENT. `recalled_block` renders each
    memory as exactly one line beginning "- [" (its header says so to the
    model), and only the appended part is read, so the caller's own prompt can
    never be miscounted as memories.
    """
    if after == before:
        return 0
    appendix = after[len(before) :] if after.startswith(before) else after
    return sum(1 for line in appendix.splitlines() if line.startswith("- ["))


def _ground(turn: _Turn) -> tuple[str, int]:
    if not turn.req.ground:
        return turn.req.system, 0
    grounded = ground_system_prompt(turn.req.system, turn.req.message)
    return grounded, _count_recalled(turn.req.system, grounded)


def _context_length_of(model: str) -> int | None:
    digest = None
    try:
        for row in _ollama("/api/tags").get("models", []):
            if _base_tag(row.get("name", "")) == _base_tag(model):
                digest = row.get("digest")
                break
    except Exception:  # noqa: BLE001 - an unknown context length only skips a clamp
        pass
    facts = model_facts(model, digest)
    return facts["context_length"] if facts else None


def _applied_options(turn: _Turn) -> dict:
    """What the robot will actually apply, as the receipt reports it.

    Empty for the robot-hosted brains: the `claude` CLI and Gemini take none of
    these, and an empty receipt is how the phone learns the options it asked for
    were not confirmed. For Ollama: the clamped options, ``think`` (always sent),
    and ``keep_alive`` in seconds when the caller set one.
    """
    if turn.provider != "ollama":
        return {}
    context_length = _context_length_of(turn.model) if "num_ctx" in turn.options else None
    # No limit named is not "forever": the cap applies to every turn, so a small
    # model in a repetition loop cannot hold the lock with no end.
    options = {"num_predict": NUM_PREDICT_MAX, **turn.options}
    applied = clamp_options(options, context_length=context_length, ram_gb=_total_ram_gb())
    applied["think"] = turn.think
    if turn.keep_alive_s is not None:
        applied["keep_alive"] = turn.keep_alive_s
    return applied


def _receipt(turn: _Turn, system: str, memories: int, applied: dict) -> dict:
    return {
        "provider": turn.provider,
        "options_applied": applied,
        "memories_recalled": memories,
        "system_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
    }


def _metrics(out: dict) -> dict:
    """Ollama's own timings, renamed for what they are (nanoseconds)."""
    return {
        "total_ns": out.get("total_duration"),
        "load_ns": out.get("load_duration"),
        "prompt_eval_count": out.get("prompt_eval_count"),
        "prompt_eval_ns": out.get("prompt_eval_duration"),
        "eval_count": out.get("eval_count"),
        "eval_ns": out.get("eval_duration"),
    }


# The Ollama branch ----------------------------------------------------------


def _messages(turn: _Turn, system: str) -> list[dict]:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    for past in turn.req.history[-12:]:
        role = past.get("role")
        if role in ("user", "assistant") and past.get("content"):
            messages.append({"role": role, "content": str(past["content"])})
    user_turn: dict = {"role": "user", "content": turn.req.message}
    if turn.req.image_b64:
        # Local vision landed with gemma4:12b (encoder-free multimodal — the
        # projector is a 52M single-matmul module, per Google's own writeup).
        # Before this, an attached photo was SILENTLY DROPPED on the Ollama
        # path: the model answered about a picture it never saw, in whatever
        # words made that sound plausible. Ollama takes base64 in `images`.
        user_turn["images"] = [turn.req.image_b64]
    messages.append(user_turn)
    return messages


def _chat_payload(turn: _Turn, messages: list[dict], applied: dict, *, stream: bool) -> dict:
    payload: dict = {
        "model": turn.model,
        "messages": messages,
        "stream": stream,
        "think": turn.think,
    }
    options = {k: applied[k] for k in CHAT_OPTION_KEYS if k in applied}
    if options:
        payload["options"] = options
    if turn.keep_alive_s is not None:
        payload["keep_alive"] = turn.keep_alive_s
    return payload


def _chat_ollama_json(turn: _Turn) -> dict:
    system, memories = _ground(turn)
    applied = _applied_options(turn)
    payload = _chat_payload(turn, _messages(turn, system), applied, stream=False)
    started = time.time()
    try:
        out = _ollama("/api/chat", payload, base=chat_upstream(), timeout=CHAT_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ollama error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - anything else read as a timeout
        raise HTTPException(status_code=504, detail=f"ollama timeout: {exc}") from exc
    msg = out.get("message") or {}
    return {
        "model": turn.model,
        "content": msg.get("content", ""),
        "thinking": msg.get("thinking", ""),
        "elapsed_s": round(time.time() - started, 2),
        "eval_count": out.get("eval_count"),
        **_receipt(turn, system, memories, applied),
        "done_reason": out.get("done_reason"),
        "metrics": _metrics(out),
    }


def _is_loaded(model: str) -> bool | None:
    """Whether Ollama holds *model* in memory now; None when it will not say."""
    try:
        ps = _ollama("/api/ps", timeout=5.0)
    except Exception:  # noqa: BLE001 - unknown, so no "loading" line is claimed
        return None
    names = set()
    for m in ps.get("models", []) or []:
        for key in ("name", "model"):
            if m.get(key):
                names.add(_base_tag(m[key]))
    return _base_tag(model) in names


def _line(event: dict) -> bytes:
    return json.dumps(event, separators=(",", ":")).encode() + b"\n"


def _error(status: int, detail: str) -> bytes:
    return _line({"type": "error", "status": status, "detail": detail})


def _upstream_error_text(raw: bytes) -> str:
    text = raw.decode(errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(parsed, dict) and parsed.get("error"):
        return str(parsed["error"])[:300]
    return text[:300]


async def _stream_ollama(turn: _Turn) -> AsyncIterator[bytes]:
    """Ollama's own /api/chat stream, re-framed line by line as it arrives.

    Deltas are for DISPLAY. The phone interprets only the ``done`` line, which
    carries the whole answer and Ollama's done_reason; a stream that ends
    without one is an ``error`` line, never a quiet short answer.
    """
    try:
        system, memories = await anyio.to_thread.run_sync(_ground, turn)
        if await anyio.to_thread.run_sync(_is_loaded, turn.model) is False:
            yield _line({"type": "status", "state": "loading", "model": turn.model})
        applied = await anyio.to_thread.run_sync(_applied_options, turn)
        payload = _chat_payload(turn, _messages(turn, system), applied, stream=True)
        started = time.time()
        content: list[str] = []
        thinking: list[str] = []
        final: dict | None = None
        timeout = httpx.Timeout(CHAT_TIMEOUT_S, connect=5.0)
        # trust_env off: the operator named this daemon's address outright, and
        # an ambient HTTP_PROXY must not sit between the robot and its own
        # model (a buffering proxy would also turn the stream into one blob).
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            # Leaving this block closes the upstream request, and Ollama stops
            # generating when its client goes away. It is left on every path:
            # the done line, an error, and the phone disconnecting (the
            # response's cleanup closes this generator, see _GenerationStream).
            async with client.stream("POST", f"{chat_upstream()}/api/chat", json=payload) as resp:
                if resp.status_code >= 400:
                    raw = await resp.aread()
                    yield _error(
                        502, f"ollama error: HTTP {resp.status_code}: {_upstream_error_text(raw)}"
                    )
                    return
                async for raw_line in resp.aiter_lines():
                    if not raw_line.strip():
                        continue
                    try:
                        evt = json.loads(raw_line)
                    except ValueError:
                        continue
                    if not isinstance(evt, dict):
                        continue
                    if evt.get("error"):
                        yield _error(502, f"ollama error: {str(evt['error'])[:300]}")
                        return
                    msg = evt.get("message") or {}
                    if msg.get("thinking"):
                        thinking.append(msg["thinking"])
                        yield _line({"type": "thinking", "delta": msg["thinking"]})
                    if msg.get("content"):
                        content.append(msg["content"])
                        yield _line({"type": "content", "delta": msg["content"]})
                    if evt.get("done"):
                        final = evt
                        break
        if final is None:
            yield _error(502, "ollama ended the stream before the answer finished")
            return
        yield _line(
            {
                "type": "done",
                "model": turn.model,
                "content": "".join(content),
                "thinking": "".join(thinking),
                "elapsed_s": round(time.time() - started, 2),
                "eval_count": final.get("eval_count"),
                **_receipt(turn, system, memories, applied),
                "done_reason": final.get("done_reason"),
                "metrics": _metrics(final),
            }
        )
    except httpx.TimeoutException as exc:
        yield _error(504, f"ollama timeout: {type(exc).__name__}")
    except httpx.ConnectError as exc:
        yield _error(503, f"ollama unreachable: {type(exc).__name__}")
    except httpx.HTTPError as exc:
        # Connected, then lost: the daemon died or dropped us mid-answer.
        yield _error(502, f"ollama dropped the stream: {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001 - reported on the stream, not as a dropped socket
        yield _error(500, f"console error: {type(exc).__name__}: {exc}")


# The robot-hosted brains ----------------------------------------------------


def _chat_cloud(turn: _Turn) -> dict:
    """One turn through a robot-hosted brain. Blocking; used by both routes."""
    system, memories = _ground(turn)
    started = time.time()
    if turn.provider == "anthropic-sub":
        try:
            out = brains.anthropic_chat(
                system, turn.req.message, turn.req.history, image_jpeg=turn.image
            )
        except Exception as exc:  # noqa: BLE001 - upstream failure, reported as 502
            raise HTTPException(status_code=502, detail=f"claude: {exc}") from exc
        reply = {
            "model": "claude (subscription)",
            "content": out["content"],
            "thinking": out.get("thinking", ""),
        }
    else:
        # Vision-native, and this console owns no cameras (see app.py): the only
        # frame it can offer is the one the CALLER attached.
        try:
            out = brains.gemini_er(system + "\n\n" + turn.req.message, image_jpeg=turn.image)
        except Exception as exc:  # noqa: BLE001 - upstream failure, reported as 502
            raise HTTPException(status_code=502, detail=f"gemini: {exc}") from exc
        reply = {
            "model": out.get("model", "gemini-robotics-er"),
            "content": out["content"],
            "thinking": "",
            "points": out.get("points", []),
        }
    reply["elapsed_s"] = round(time.time() - started, 2)
    # Neither brain reports a finish reason or timings through its bridge, so
    # both are null: not "stop", which would be this console vouching for a
    # completion it never saw.
    reply.update(_receipt(turn, system, memories, {}))
    reply["done_reason"] = None
    reply["metrics"] = None
    return reply


async def _stream_cloud(turn: _Turn) -> AsyncIterator[bytes]:
    """The robot-hosted brains do not stream through their bridges: one content
    line with the whole answer, then ``done``."""
    try:
        reply = await anyio.to_thread.run_sync(_chat_cloud, turn)
    except HTTPException as exc:
        yield _error(exc.status_code, str(exc.detail))
        return
    except Exception as exc:  # noqa: BLE001 - reported on the stream
        yield _error(500, f"console error: {type(exc).__name__}: {exc}")
        return
    yield _line({"type": "content", "delta": reply["content"]})
    yield _line({"type": "done", **reply})


class _GenerationStream(StreamingResponse):
    """NDJSON that gives the robot back when it ends, however it ends.

    Starlette stops iterating a stream when the client goes away, but it does not
    CLOSE the generator: one suspended at a ``yield`` would keep its upstream
    request to Ollama open, and Ollama would keep generating for nobody, until
    garbage collection got round to it. So the response closes the generator
    itself, which closes the upstream request, and only then gives back the
    generation lock.
    """

    def __init__(self, events: AsyncIterator[bytes], ticket: _Ticket) -> None:
        super().__init__(events, media_type=NDJSON, headers={"Cache-Control": "no-store"})
        self._events = events
        self._ticket = ticket

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await self._events.aclose()  # type: ignore[attr-defined]
            finally:
                self._ticket.release()


@router.post("/models/chat", response_model=None)
def chat(req: ChatRequest) -> dict | StreamingResponse:
    """One chat turn against the robot's brain, grounded in what it remembers.

    The CALLER still supplies the system prompt (the phone builds it from the
    robot's manifest), so policy and capabilities are still not this endpoint's
    business. The one thing it adds is the thing only the robot can add: the
    message is embedded here and the memories that bear on it ride in as a
    RECALLED MEMORIES appendix to the caller's prompt — provenance-framed, at
    most five, and ABSENT entirely when nothing clears the relevance floor, so
    an unrelated question carries zero memories and costs zero tokens. The
    phone cannot do this itself: the vectors and the embed model live on the
    robot, next to the memory they index. ``ground: false`` skips it.

    Grounding never blocks a turn. No Ollama, no embed model, an empty or
    corrupt sidecar — every one of them degrades to an ungrounded turn (see
    `castor.brain.memory_recall`), because a robot that refuses to talk when its
    memory index is cold is worse than a robot that forgets.

    THE REPLY. Without ``stream`` one JSON object, the same fields as ever
    (model, content, thinking, elapsed_s, eval_count; points for Gemini) plus
    the receipt: provider, options_applied, memories_recalled, system_sha256,
    done_reason and metrics (null for the robot-hosted brains). With
    ``stream: true``, NDJSON, one event per line:

      {"type":"status","state":"loading","model":M}   Ollama must load M first
      {"type":"thinking","delta":...}                  reasoning, display only
      {"type":"content","delta":...}                   answer text, display only
      {"type":"done", ...the JSON reply's fields...}   the only line to interpret
      {"type":"error","status":int,"detail":...}       the turn failed mid-stream

    Refusals happen before anything is generated, as ordinary HTTP errors on
    both routes: 422 for a bad request, 409 "no active model set", 409 "busy"
    while another turn is generating, 409 "driving" (see `read_drive_state`).
    """
    turn = _prepare(req)
    ticket = generation_lock.try_acquire(f"{turn.provider}:{turn.model}")
    if ticket is None:
        raise HTTPException(status_code=409, detail="busy")
    try:
        refuse_while_driving()
        if req.stream:
            events = _stream_ollama(turn) if turn.provider == "ollama" else _stream_cloud(turn)
            response = _GenerationStream(events, ticket)
            ticket = None  # the stream owns the lock now and gives it back itself
            return response
        return _chat_ollama_json(turn) if turn.provider == "ollama" else _chat_cloud(turn)
    finally:
        if ticket is not None:
            ticket.release()


@router.get("/models/providers")
def providers() -> dict:
    """Provider metadata for the settings screen.

    Reports only WHETHER each brain is configured — never a key, and never a
    pricing, signup, or console URL: shipping a link to a provider's purchase
    page in an iOS binary is the classic anti-steering rejection.
    """
    return {
        "providers": [
            {
                "id": "ollama",
                "label": "On this robot",
                "kind": "local",
                "configured": True,
                "note": "Runs on the robot. Nothing leaves your network.",
            },
            {
                "id": "anthropic-sub",
                "label": "Claude (your subscription)",
                "kind": "robot_hosted",
                "configured": brains.anthropic_available(),
                "note": "Uses the subscription already signed in on the robot.",
            },
            {
                "id": "gemini-er",
                "label": "Gemini Robotics-ER 2.0",
                "kind": "robot_hosted",
                "configured": brains.gemini_available(),
                "vision": True,
                "note": "Embodied reasoning over an attached frame — points at what it sees.",
            },
            {
                "id": "apple-fm",
                "label": "On this iPhone",
                "kind": "on_device",
                "configured": True,
                "note": "Uses the phone's own on-device model when available.",
            },
        ]
    }


class KeyRequest(BaseModel):
    provider: str
    key: str


@router.post("/models/keys")
def set_key(req: KeyRequest) -> dict:
    """Store a provider key ON THE ROBOT, from the app.

    Gemini Robotics-ER reasons about camera frames, so the key has to live where
    the frames are. The key is written 0600 and is never returned by any
    endpoint — callers only ever learn whether a provider is `configured`.

    It does cross the LAN in the clear on the way here (plain HTTP behind the
    console bearer), so the UI says so rather than implying otherwise.
    """
    provider = req.provider.strip()
    if provider != "gemini-er":
        raise HTTPException(
            status_code=400,
            detail=f"{provider!r} does not take a robot-side key",
        )
    key = req.key.strip()
    if key and not key.startswith("AIza"):
        raise HTTPException(
            status_code=422,
            detail="that does not look like a Google AI Studio key (expected AIza…)",
        )
    ok, detail = brains.set_gemini_key(key)
    if not ok:
        raise HTTPException(status_code=502, detail=detail)
    return {"provider": provider, "configured": bool(key), "detail": detail}


@router.get("/models/auth")
def auth_status() -> dict:
    """What the robot can sign in as — never any key material."""
    return {
        "anthropic_subscription": {
            "configured": brains.anthropic_available(),
            "detail": (
                "Claude is signed in on the robot."
                if brains.anthropic_available()
                else "Run `claude` on the robot once to sign in."
            ),
        },
        "gemini_er": {
            "configured": brains.gemini_available(),
            "detail": (
                "A Gemini key is stored on the robot."
                if brains.gemini_available()
                else "Paste a Google AI Studio key to enable vision."
            ),
        },
    }
