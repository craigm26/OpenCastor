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
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from castor.brain.memory_recall import ground_system_prompt

from . import brains
from .config import chat_upstream, ollama_url, robot_home

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
#: first chat becomes a 40-minute download into a disappointment. Sizes are
#: install sizes; ram_gb is the working-set rule of thumb (weights x ~1.3).
SUGGESTED_MODELS = [
    {
        "name": "qwen3.5:2b",
        "size_gb": 1.9,
        "ram_gb": 2.5,
        "good_for": "Fast chat and workflow naming. The snappiest thing a Pi runs.",
    },
    {
        "name": "gemma4:e2b",
        "size_gb": 3.0,
        "ram_gb": 3.9,
        "good_for": "Better answers than 2B, still quick after first load.",
    },
    {
        "name": "qwen3.5:4b",
        "size_gb": 3.3,
        "ram_gb": 4.3,
        "good_for": "Noticeably better reasoning; slower first load (~75 s cold).",
    },
    {
        "name": "gemma4:e4b-it-qat",
        "size_gb": 4.1,
        "ram_gb": 5.3,
        "good_for": "The best local chat quality this bench has run.",
    },
    {
        "name": "gemma3:4b",
        "size_gb": 3.3,
        "ram_gb": 4.3,
        "good_for": "Vision: can look at photos and camera frames locally.",
    },
    {
        "name": "nomic-embed-text",
        "size_gb": 0.3,
        "ram_gb": 0.6,
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
        "size_gb": 7.2,
        "ram_gb": 9.0,
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


@router.get("/models/suggestions")
def suggestions() -> dict:
    """Curated models that FIT this host, judged by the host itself.

    The download box used to be a blank text field with a placeholder — which
    assumes the person already knows the answer to the hardest question a
    first-timer has. The robot knows its own RAM, so it answers.
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
    return {"host_ram_gb": round(ram, 1), "suggestions": out}


@router.get("/models/local")
def local_models() -> dict:
    """Models already on the robot's disk, plus which one is active."""
    try:
        tags = _ollama("/api/tags")
    except Exception as exc:  # noqa: BLE001 - reported as 503 below
        raise HTTPException(status_code=503, detail=f"ollama unreachable: {exc}") from exc
    try:
        loaded = {m["name"] for m in _ollama("/api/ps").get("models", [])}
    except Exception:  # noqa: BLE001 - "which are warm" is optional detail
        loaded = set()
    active = read_active()
    models = [
        {
            "name": m["name"],
            "size_bytes": m.get("size", 0),
            "family": (m.get("details") or {}).get("family"),
            "parameter_size": (m.get("details") or {}).get("parameter_size"),
            "quantization": (m.get("details") or {}).get("quantization_level"),
            "loaded": m["name"] in loaded,
            "active": m["name"] == active.get("model"),
        }
        for m in tags.get("models", [])
    ]
    models.sort(key=lambda m: m["size_bytes"])
    return {
        "models": models,
        "active": active,
        "cold_load_hint_s": COLD_LOAD_HINT_S,
    }


class PullRequest(BaseModel):
    model: str


@router.post("/models/pull")
def start_pull(req: PullRequest) -> dict:
    """Ask the robot to download a model. Returns immediately; poll for status."""
    name = req.model.strip()
    if not name:
        raise HTTPException(status_code=422, detail="model is required")
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
    """
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


class ChatRequest(BaseModel):
    system: str = ""
    message: str
    history: list[dict] = []
    model: str | None = None
    # Leave this false for local models on Pi-class hardware. Measured
    # 2026-07-31: qwen3.5:2b with think=true does not return within 175 s even
    # for "2+2", directly against Ollama — so it is the model/runtime, not this
    # bridge. Reasoning still reaches a trace for models that emit inline
    # <think> tags, when CHAT_UPSTREAM points at a recording proxy.
    think: bool = False
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


@router.post("/models/chat")
def chat(req: ChatRequest) -> dict:
    """One chat turn against the robot's brain, grounded in what it remembers.

    The CALLER still supplies the system prompt (the phone builds it from the
    robot's manifest), so policy and capabilities are still not this endpoint's
    business. The one thing it adds is the thing only the robot can add: the
    message is embedded here and the memories that bear on it ride in as a
    RECALLED MEMORIES appendix to the caller's prompt — provenance-framed, at
    most five, and ABSENT entirely when nothing clears the relevance floor, so
    an unrelated question carries zero memories and costs zero tokens. The
    phone cannot do this itself: the vectors and the embed model live on the
    robot, next to the memory they index.

    Grounding never blocks a turn. No Ollama, no embed model, an empty or
    corrupt sidecar — every one of them degrades to an ungrounded turn (see
    `castor.brain.memory_recall`), because a robot that refuses to talk when its
    memory index is cold is worse than a robot that forgets.
    """
    active = read_active()
    if req.provider is not None and req.provider not in ("ollama", "anthropic-sub", "gemini-er"):
        raise HTTPException(status_code=422, detail=f"unknown provider {req.provider!r}")
    provider = req.provider or active.get("provider", "ollama")
    # Before the branch, so all three brains are grounded by the same rail.
    system = ground_system_prompt(req.system, req.message)

    # A robot-hosted brain answers from here; Ollama is only one of them.
    if provider == "anthropic-sub":
        client_frame = None
        if req.image_b64:
            import base64 as _b64

            try:
                client_frame = _b64.b64decode(req.image_b64, validate=True)
            except Exception:  # noqa: BLE001 - a bad frame is a client error
                raise HTTPException(
                    status_code=422, detail="image_b64 is not valid base64"
                ) from None
        try:
            out = brains.anthropic_chat(system, req.message, req.history, image_jpeg=client_frame)
        except Exception as exc:  # noqa: BLE001 - upstream failure, reported as 502
            raise HTTPException(status_code=502, detail=f"claude: {exc}") from exc
        return {
            "model": "claude (subscription)",
            "content": out["content"],
            "thinking": out.get("thinking", ""),
            "elapsed_s": 0,
        }
    if provider == "gemini-er":
        # Vision-native, and this console owns no cameras (see app.py): the only
        # frame it can offer is the one the CALLER attached. Without one the
        # model is being used as a plain chat model, which is not what it is
        # for — so say so rather than answering blind about a scene.
        image = None
        if req.image_b64:
            import base64 as _b64

            try:
                image = _b64.b64decode(req.image_b64, validate=True)
            except Exception:  # noqa: BLE001 - a bad frame is a client error
                raise HTTPException(
                    status_code=422, detail="image_b64 is not valid base64"
                ) from None
        try:
            out = brains.gemini_er(system + "\n\n" + req.message, image_jpeg=image)
        except Exception as exc:  # noqa: BLE001 - upstream failure, reported as 502
            raise HTTPException(status_code=502, detail=f"gemini: {exc}") from exc
        return {
            "model": out.get("model", "gemini-robotics-er"),
            "content": out["content"],
            "thinking": "",
            "points": out.get("points", []),
            "elapsed_s": 0,
        }

    model = req.model or active.get("model")
    if not model:
        raise HTTPException(status_code=409, detail="no active model set")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    for turn in req.history[-12:]:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append({"role": role, "content": str(turn["content"])})
    user_turn: dict = {"role": "user", "content": req.message}
    if req.image_b64:
        # Local vision landed with gemma4:12b (encoder-free multimodal — the
        # projector is a 52M single-matmul module, per Google's own writeup).
        # Before this, an attached photo was SILENTLY DROPPED on the Ollama
        # path: the model answered about a picture it never saw, in whatever
        # words made that sound plausible. Ollama takes base64 in `images`.
        user_turn["images"] = [req.image_b64]
    messages.append(user_turn)
    started = time.time()
    try:
        out = _ollama(
            "/api/chat",
            {"model": model, "messages": messages, "stream": False, "think": req.think},
            base=chat_upstream(),
            # Generous: a cold model load dominates the first turn.
            timeout=300.0,
        )
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ollama error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - anything else read as a timeout
        raise HTTPException(status_code=504, detail=f"ollama timeout: {exc}") from exc
    msg = out.get("message") or {}
    return {
        "model": model,
        "content": msg.get("content", ""),
        "thinking": msg.get("thinking", ""),
        "elapsed_s": round(time.time() - started, 2),
        "eval_count": out.get("eval_count"),
    }


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
