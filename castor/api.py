"""
OpenCastor API Gateway.
FastAPI server that provides REST endpoints for remote control,
telemetry streaming, and messaging channel webhooks.

Run with:
    python -m castor.api --config robot.rcan.yaml
    # or
    castor gateway --config robot.rcan.yaml
"""

import argparse
import collections
import hashlib
import hmac
import logging
import os
import posixpath
import signal
import threading
import time
from typing import Any, Dict, Optional

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from castor.api_errors import CastorAPIError, register_error_handlers
from castor.auth import (
    list_available_channels,
    list_available_providers,
    load_dotenv_if_available,
)
from castor.fs import CastorFS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("OpenCastor.Gateway")

# ---------------------------------------------------------------------------
# App & state
# ---------------------------------------------------------------------------
app = FastAPI(
    title="OpenCastor Gateway",
    description="REST API for controlling your robot and receiving messages from channels.",
    version=__import__("importlib.metadata", fromlist=["version"]).version("opencastor"),
)

# CORS: configurable via OPENCASTOR_CORS_ORIGINS env var (comma-separated).
# Defaults to ["*"] for local development. Restrict for production.
_cors_origins = os.getenv("OPENCASTOR_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register structured JSON error handlers (replaces plain FastAPI HTTPException text)
register_error_handlers(app)

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
_COMMAND_RATE_LIMIT = int(os.getenv("OPENCASTOR_COMMAND_RATE", "5"))  # max calls/second/IP
_MAX_STREAMS = int(os.getenv("OPENCASTOR_MAX_STREAMS", "3"))  # max concurrent MJPEG clients
_WEBHOOK_RATE_LIMIT = int(os.getenv("OPENCASTOR_WEBHOOK_RATE", "10"))  # max webhook calls/minute/sender
_rate_lock = threading.Lock()
_command_history: Dict[str, list] = collections.defaultdict(list)  # ip -> [timestamps]
_webhook_history: Dict[str, list] = collections.defaultdict(list)  # sender_id -> [timestamps]
_active_streams = 0


def _check_command_rate(client_ip: str) -> None:
    """Sliding-window rate limit for /api/command. Raises 429 on breach."""
    now = time.time()
    with _rate_lock:
        history = _command_history[client_ip]
        _command_history[client_ip] = [t for t in history if now - t < 1.0]
        if len(_command_history[client_ip]) >= _COMMAND_RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({_COMMAND_RATE_LIMIT} req/s). Try again shortly.",
                headers={"Retry-After": "1"},
            )
        _command_history[client_ip].append(now)


def _check_webhook_rate(sender_id: str) -> None:
    """Sliding-window rate limit for webhook endpoints (per-sender, 1-minute window).

    Raises 429 when a sender exceeds _WEBHOOK_RATE_LIMIT messages per minute.
    """
    now = time.time()
    with _rate_lock:
        history = _webhook_history[sender_id]
        _webhook_history[sender_id] = [t for t in history if now - t < 60.0]
        if len(_webhook_history[sender_id]) >= _WEBHOOK_RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Webhook rate limit exceeded ({_WEBHOOK_RATE_LIMIT} req/min). Try again later.",
                headers={"Retry-After": "60"},
            )
        _webhook_history[sender_id].append(now)


# ---------------------------------------------------------------------------
# VFS path validation
# ---------------------------------------------------------------------------

def _validate_vfs_path(path: str) -> str:
    """Normalise and validate a VFS path. Rejects traversal attempts."""
    if "\x00" in path:
        raise HTTPException(status_code=400, detail="Invalid path: null byte in path")
    # posixpath.normpath resolves '..' and redundant slashes
    normalized = posixpath.normpath("/" + path.lstrip("/"))
    # After normalisation, the path must start with '/' (i.e. no escaping)
    if not normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    return normalized


class AppState:
    """Mutable application state shared across endpoints."""

    config: Optional[dict] = None
    brain = None
    driver = None
    channels: Dict[str, object] = {}
    last_thought: Optional[dict] = None
    boot_time: float = time.time()
    fs: Optional[CastorFS] = None
    ruri: Optional[str] = None  # RCAN URI for this robot instance
    mdns_broadcaster = None
    mdns_browser = None
    rcan_router = None  # RCAN message router
    capability_registry = None  # Capability registry
    offline_fallback = None   # OfflineFallbackManager (optional)
    thought_history = None    # deque(maxlen=50) — ring buffer of recent thoughts
    learner = None            # SisyphusLoop instance (optional)


state = AppState()

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
API_TOKEN = os.getenv("OPENCASTOR_API_TOKEN")


async def verify_token(request: Request):
    """Multi-layer auth: JWT first, then bearer token, then anonymous/GUEST.

    When JWT is configured (OPENCASTOR_JWT_SECRET), Bearer tokens are
    checked as JWT first.  Falls back to the static API token.
    If no auth is configured at all, access is open.
    """
    auth = request.headers.get("Authorization", "")

    # Try JWT first (if configured)
    jwt_secret = os.getenv("OPENCASTOR_JWT_SECRET")
    if jwt_secret and auth.startswith("Bearer "):
        token = auth[7:]
        try:
            from castor.rcan.jwt_auth import RCANTokenManager

            mgr = RCANTokenManager(secret=jwt_secret, issuer=state.ruri or "")
            principal = mgr.verify(token)
            request.state.principal = principal
            return
        except Exception:
            pass  # Fall through to static token check

    # Try static API token
    if API_TOKEN:
        if auth != f"Bearer {API_TOKEN}":
            raise HTTPException(status_code=401, detail="Invalid or missing API token")
        return

    # No auth configured -- open access


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class CommandRequest(BaseModel):
    instruction: str
    image_base64: Optional[str] = None


class ActionRequest(BaseModel):
    type: str  # move, stop, grip, wait
    linear: Optional[float] = None
    angular: Optional[float] = None
    state: Optional[str] = None  # open / close (for grip)
    duration_ms: Optional[int] = None  # for wait


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Health check -- returns OK if the gateway is running."""
    return {
        "status": "ok",
        "uptime_s": round(time.time() - state.boot_time, 1),
        "brain": state.brain is not None,
        "driver": state.driver is not None,
        "channels": list(state.channels.keys()),
    }


def _maybe_wrap_rcan(payload: dict, request: Request) -> dict:
    """Wrap a response payload in an RCANMessage envelope if ``?envelope=rcan``."""
    if request.query_params.get("envelope") != "rcan":
        return payload
    try:
        from castor.rcan.message import RCANMessage

        msg = RCANMessage.status(
            source=state.ruri or "rcan://opencastor.unknown.00000000",
            target="rcan://*.*.*/status",
            payload=payload,
        )
        return msg.to_dict()
    except Exception:
        return payload


@app.get("/api/status", dependencies=[Depends(verify_token)])
async def get_status(request: Request):
    """Return current runtime status, provider health, and available integrations."""
    from castor.safety.authorization import DEFAULT_AUDIT_LOG_PATH

    payload = {
        "config_loaded": state.config is not None,
        "robot_name": (
            state.config.get("metadata", {}).get("robot_name") if state.config else None
        ),
        "ruri": state.ruri,
        "providers": list_available_providers(),
        "channels_available": list_available_channels(),
        "channels_active": list(state.channels.keys()),
        "last_thought": state.last_thought,
        "audit_log_path": str(DEFAULT_AUDIT_LOG_PATH.expanduser()),
    }

    # Provider health check (non-blocking — skip if brain not ready)
    if state.brain is not None:
        try:
            payload["provider_health"] = state.brain.health_check()
        except Exception as exc:
            payload["provider_health"] = {"ok": False, "error": str(exc)}

    # Offline fallback status
    if state.offline_fallback is not None:
        payload["fallback_ready"] = state.offline_fallback.fallback_ready
        payload["using_fallback"] = state.offline_fallback.is_using_fallback
    else:
        payload["fallback_ready"] = None

    return _maybe_wrap_rcan(payload, request)


@app.post("/api/command", dependencies=[Depends(verify_token)])
async def send_command(cmd: CommandRequest, request: Request):
    """Send an instruction to the robot's brain and receive the action."""
    _check_command_rate(request.client.host if request.client else "unknown")
    if state.brain is None:
        raise HTTPException(status_code=503, detail="Brain not initialized")

    # Use provided image, live camera frame, or blank
    if cmd.image_base64:
        import base64

        image_bytes = base64.b64decode(cmd.image_base64)
    else:
        image_bytes = _capture_live_frame()

    active = state.offline_fallback.get_active_provider() if state.offline_fallback else state.brain
    thought = active.think(image_bytes, cmd.instruction)
    _record_thought(cmd.instruction, thought.raw_text, thought.action)

    # Execute action on hardware if available
    if thought.action and state.driver:
        _execute_action(thought.action)

    return {
        "raw_text": thought.raw_text,
        "action": thought.action,
    }


@app.post("/api/action", dependencies=[Depends(verify_token)])
async def direct_action(action: ActionRequest):
    """Send a direct motor command, bypassing the brain.

    Requires bearer auth (enforced via verify_token dependency).
    Bounds are checked against the safety layer before executing.
    """
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No hardware driver active")

    action_dict = action.model_dump(exclude_none=True)

    # Run bounds check via the virtual filesystem safety layer
    if state.fs:
        ok = state.fs.write("/dev/motor", action_dict, principal="api")
        if not ok:
            raise HTTPException(
                status_code=422,
                detail="Action rejected by safety layer (bounds violation or e-stop active)",
            )
        # Use the safety-clamped action
        clamped = state.fs.read("/dev/motor", principal="api")
        if clamped:
            action_dict = clamped

    _execute_action(action_dict)
    return {"status": "executed", "action": action_dict}


@app.post("/api/stop", dependencies=[Depends(verify_token)])
async def emergency_stop():
    """Emergency stop -- immediately halt all motors."""
    if state.driver:
        state.driver.stop()
    if state.fs:
        state.fs.estop(principal="api")
    return {"status": "stopped"}


@app.post("/api/estop/clear", dependencies=[Depends(verify_token)])
async def clear_estop():
    """Clear emergency stop (requires API token)."""
    if state.fs:
        if state.fs.clear_estop(principal="api"):
            return {"status": "cleared"}
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"status": "no_fs"}


# ---------------------------------------------------------------------------
# Virtual Filesystem endpoints
# ---------------------------------------------------------------------------
class FSReadRequest(BaseModel):
    path: str


class FSWriteRequest(BaseModel):
    path: str
    data: Any = None


@app.post("/api/fs/read", dependencies=[Depends(verify_token)])
async def fs_read(req: FSReadRequest):
    """Read a virtual filesystem path."""
    if not state.fs:
        raise HTTPException(status_code=503, detail="Filesystem not initialized")
    safe_path = _validate_vfs_path(req.path)
    data = state.fs.read(safe_path, principal="api")
    if data is None and not state.fs.exists(safe_path):
        raise HTTPException(status_code=404, detail=f"Path not found: {safe_path}")
    return {"path": safe_path, "data": data}


@app.post("/api/fs/write", dependencies=[Depends(verify_token)])
async def fs_write(req: FSWriteRequest):
    """Write to a virtual filesystem path."""
    if not state.fs:
        raise HTTPException(status_code=503, detail="Filesystem not initialized")
    safe_path = _validate_vfs_path(req.path)
    ok = state.fs.write(safe_path, req.data, principal="api")
    if not ok:
        raise HTTPException(status_code=403, detail="Write denied")
    return {"path": safe_path, "status": "written"}


@app.get("/api/fs/ls", dependencies=[Depends(verify_token)])
async def fs_ls(path: str = "/"):
    """List virtual filesystem directory."""
    if not state.fs:
        raise HTTPException(status_code=503, detail="Filesystem not initialized")
    children = state.fs.ls(path, principal="api")
    if children is None:
        raise HTTPException(status_code=404, detail=f"Not a directory: {path}")
    return {"path": path, "children": children}


@app.get("/api/fs/tree", dependencies=[Depends(verify_token)])
async def fs_tree(path: str = "/", depth: int = 3):
    """Get a tree view of the virtual filesystem."""
    if not state.fs:
        raise HTTPException(status_code=503, detail="Filesystem not initialized")
    return {"tree": state.fs.tree(path, depth=depth)}


@app.get("/api/fs/proc", dependencies=[Depends(verify_token)])
async def fs_proc():
    """Get /proc snapshot (runtime telemetry)."""
    if not state.fs:
        raise HTTPException(status_code=503, detail="Filesystem not initialized")
    return state.fs.proc.snapshot()


@app.get("/api/fs/memory", dependencies=[Depends(verify_token)])
async def fs_memory(tier: str = "all", limit: int = 20):
    """Query memory stores."""
    if not state.fs:
        raise HTTPException(status_code=503, detail="Filesystem not initialized")
    result = {}
    if tier in ("all", "episodic"):
        result["episodic"] = state.fs.memory.get_episodes(limit=limit)
    if tier in ("all", "semantic"):
        result["semantic"] = state.fs.memory.list_facts()
    if tier in ("all", "procedural"):
        result["procedural"] = state.fs.memory.list_behaviors()
    return result


class TokenRequest(BaseModel):
    subject: str
    role: str = "GUEST"
    scopes: Optional[list] = None
    ttl_seconds: int = 86400


@app.post("/api/auth/token", dependencies=[Depends(verify_token)])
async def issue_token(req: TokenRequest):
    """Issue a JWT token (requires OPENCASTOR_JWT_SECRET)."""
    jwt_secret = os.getenv("OPENCASTOR_JWT_SECRET")
    if not jwt_secret:
        raise HTTPException(
            status_code=501,
            detail="JWT not configured. Set OPENCASTOR_JWT_SECRET.",
        )
    try:
        from castor.rcan.jwt_auth import RCANTokenManager
        from castor.rcan.rbac import RCANRole

        mgr = RCANTokenManager(secret=jwt_secret, issuer=state.ruri or "")
        role = RCANRole[req.role.upper()]
        token = mgr.issue(
            subject=req.subject,
            role=role,
            scopes=req.scopes,
            ttl_seconds=req.ttl_seconds,
        )
        return {"token": token, "expires_in": req.ttl_seconds}
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Invalid role: {req.role}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/auth/whoami", dependencies=[Depends(verify_token)])
async def whoami(request: Request):
    """Return the authenticated principal's identity."""
    principal = getattr(request.state, "principal", None)
    if principal:
        return principal.to_dict()
    # No JWT -- return based on static token or anonymous
    if API_TOKEN and request.headers.get("Authorization") == f"Bearer {API_TOKEN}":
        return {"name": "api", "role": "LEASEE", "auth_method": "bearer_token"}
    return {"name": "anonymous", "role": "GUEST", "auth_method": "none"}


@app.get("/api/audit", dependencies=[Depends(verify_token)])
async def get_audit_log():
    """Expose the WorkAuthority audit log (requested, approved, denied, executed, revoked events)."""
    if not state.fs:
        raise HTTPException(status_code=503, detail="Filesystem not initialized")
    try:
        safety_layer = state.fs.safety
        work_authority = getattr(safety_layer, "work_authority", None)
        if work_authority is None:
            return {"audit_log": [], "note": "WorkAuthority not initialized"}
        return {"audit_log": work_authority.get_audit_log()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Audit log unavailable: {exc}") from exc


@app.get("/api/stream/mjpeg", dependencies=[Depends(verify_token)])
async def mjpeg_stream():
    """MJPEG live camera stream.

    Opens a persistent HTTP chunked response that pushes JPEG frames
    in multipart/x-mixed-replace format. Compatible with <img src=> tags
    and VLC without any plugins.

    Concurrent streams are capped at ``OPENCASTOR_MAX_STREAMS`` (default 3)
    to prevent CPU/memory exhaustion.
    """
    import asyncio

    global _active_streams
    with _rate_lock:
        if _active_streams >= _MAX_STREAMS:
            raise HTTPException(
                status_code=429,
                detail=f"Max concurrent streams ({_MAX_STREAMS}) reached. Try again later.",
                headers={"Retry-After": "5"},
            )
        _active_streams += 1

    async def _frame_generator():
        global _active_streams
        try:
            boundary = b"--opencastor-frame"
            while True:
                frame = _capture_live_frame()
                if frame:
                    yield (
                        boundary
                        + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(frame)).encode()
                        + b"\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                await asyncio.sleep(0.033)  # ~30 fps cap
        finally:
            with _rate_lock:
                _active_streams -= 1

    return StreamingResponse(
        _frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=opencastor-frame",
    )


@app.get("/api/rcan/peers", dependencies=[Depends(verify_token)])
async def get_peers():
    """List discovered RCAN peers on the local network."""
    if state.mdns_browser:
        return {"peers": list(state.mdns_browser.peers.values())}
    return {"peers": [], "note": "mDNS not enabled"}


# ---------------------------------------------------------------------------
# RCAN Protocol endpoints
# ---------------------------------------------------------------------------
@app.post("/rcan", dependencies=[Depends(verify_token)])
async def rcan_message_endpoint(request: Request):
    """Unified RCAN message endpoint.  Accepts an RCANMessage JSON body."""
    if not state.rcan_router:
        raise HTTPException(status_code=501, detail="RCAN router not initialized")

    body = await request.json()
    try:
        from castor.rcan.message import RCANMessage

        msg = RCANMessage.from_dict(body)
        principal = getattr(request.state, "principal", None)
        response = state.rcan_router.route(msg, principal)
        return response.to_dict()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid RCAN message: {e}") from e


@app.get("/cap/status", dependencies=[Depends(verify_token)])
async def cap_status(request: Request):
    """Capability endpoint: status / telemetry."""
    payload = {
        "ruri": state.ruri,
        "uptime_s": round(time.time() - state.boot_time, 1),
        "brain": state.brain is not None,
        "driver": state.driver is not None,
        "channels_active": list(state.channels.keys()),
        "capabilities": state.capability_registry.names if state.capability_registry else [],
    }
    if state.fs:
        payload["proc"] = state.fs.proc.snapshot()
    return _maybe_wrap_rcan(payload, request)


@app.post("/cap/teleop", dependencies=[Depends(verify_token)])
async def cap_teleop(action: ActionRequest):
    """Capability endpoint: teleoperation."""
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No hardware driver active")
    _execute_action(action.model_dump(exclude_none=True))
    return {"status": "executed", "action": action.model_dump(exclude_none=True)}


@app.post("/cap/chat", dependencies=[Depends(verify_token)])
async def cap_chat(cmd: CommandRequest):
    """Capability endpoint: conversational AI."""
    if state.brain is None:
        raise HTTPException(status_code=503, detail="Brain not initialized")
    image_bytes = _capture_live_frame()
    if cmd.image_base64:
        import base64

        image_bytes = base64.b64decode(cmd.image_base64)
    active = state.offline_fallback.get_active_provider() if state.offline_fallback else state.brain
    thought = active.think(image_bytes, cmd.instruction)
    return {"raw_text": thought.raw_text, "action": thought.action}


@app.get("/cap/vision", dependencies=[Depends(verify_token)])
async def cap_vision():
    """Capability endpoint: visual perception (last camera frame metadata)."""
    if state.fs:
        cam_data = state.fs.ns.read("/dev/camera")
        return {"camera": cam_data or {"status": "no_frame"}}
    return {"camera": {"status": "offline"}}


@app.get("/api/roles", dependencies=[Depends(verify_token)])
async def get_roles():
    """List RCAN roles and the current principal mapping."""
    try:
        from castor.rcan.rbac import RCANPrincipal, RCANRole

        roles = {r.name: r.value for r in RCANRole}
        principals = {}
        for name in ("root", "brain", "api", "channel", "driver"):
            p = RCANPrincipal.from_legacy(name)
            principals[name] = p.to_dict()
        return {"roles": roles, "principals": principals}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RBAC not available: {e}") from e


@app.get("/api/fs/permissions", dependencies=[Depends(verify_token)])
async def fs_permissions():
    """Dump the current permission table."""
    if not state.fs:
        raise HTTPException(status_code=503, detail="Filesystem not initialized")
    return state.fs.perms.dump()


# ---------------------------------------------------------------------------
# Thought history helper
# ---------------------------------------------------------------------------

def _record_thought(instruction: str, raw_text: str, action: Optional[dict]) -> None:
    """Append a thought to the ring buffer and update last_thought."""
    entry = {
        "raw_text": raw_text,
        "action": action,
        "instruction": instruction,
        "timestamp": time.time(),
    }
    state.last_thought = entry
    if state.thought_history is not None:
        state.thought_history.appendleft(entry)


# ---------------------------------------------------------------------------
# Streaming command endpoint (#68)
# ---------------------------------------------------------------------------

@app.post("/api/command/stream", dependencies=[Depends(verify_token)])
async def stream_command(cmd: CommandRequest, request: Request):
    """Stream LLM tokens back as newline-delimited JSON (NDJSON).

    Each line is a JSON object:
    - Mid-stream: ``{"chunk": "token text", "done": false}``
    - Final line: ``{"chunk": "", "done": true, "action": {...}}``

    Falls back to non-streaming ``think()`` if the active provider does not
    implement ``think_stream()``.
    """
    import json

    _check_command_rate(request.client.host if request.client else "unknown")
    if state.brain is None:
        raise HTTPException(status_code=503, detail="Brain not initialized")

    if cmd.image_base64:
        import base64 as _b64

        image_bytes = _b64.b64decode(cmd.image_base64)
    else:
        image_bytes = _capture_live_frame()

    active = state.offline_fallback.get_active_provider() if state.offline_fallback else state.brain

    async def _generate():
        chunks = []
        if hasattr(active, "think_stream"):
            for chunk in active.think_stream(image_bytes, cmd.instruction):
                chunks.append(chunk)
                yield json.dumps({"chunk": chunk, "done": False}) + "\n"
        else:
            thought = active.think(image_bytes, cmd.instruction)
            chunks.append(thought.raw_text)
            yield json.dumps({"chunk": thought.raw_text, "done": False}) + "\n"

        combined = "".join(chunks)
        action = active._clean_json(combined) if hasattr(active, "_clean_json") else None
        _record_thought(cmd.instruction, combined, action)

        if action and state.driver:
            _execute_action(action)

        yield json.dumps({"chunk": "", "done": True, "action": action}) + "\n"

    return StreamingResponse(_generate(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# Driver health endpoint (#69)
# ---------------------------------------------------------------------------

@app.get("/api/driver/health", dependencies=[Depends(verify_token)])
async def driver_health():
    """Check hardware driver health.

    Returns ``{"ok": bool, "mode": "hardware"|"mock", "error": str|null,
    "driver_type": str}`` or HTTP 503 if no driver is initialized.
    """
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No hardware driver initialized")

    result = state.driver.health_check()
    result["driver_type"] = type(state.driver).__name__
    return result


# ---------------------------------------------------------------------------
# Learner endpoints (#70, #74)
# ---------------------------------------------------------------------------

@app.get("/api/learner/stats", dependencies=[Depends(verify_token)])
async def learner_stats():
    """Return current Sisyphus loop statistics.

    Returns ``{"available": false}`` when the learner is not initialized.
    """
    if state.learner is None:
        return {"available": False}

    s = state.learner.stats
    return {
        "available": True,
        "episodes_analyzed": s.episodes_analyzed,
        "improvements_applied": s.improvements_applied,
        "improvements_rejected": s.improvements_rejected,
        "total_duration_ms": s.total_duration_ms,
        "avg_duration_ms": s.avg_duration_ms,
    }


@app.get("/api/learner/episodes", dependencies=[Depends(verify_token)])
async def learner_episodes(limit: int = 20):
    """Return the most recent recorded episodes.

    Query param ``limit`` (default 20, max 100) controls how many to return.
    """
    limit = min(max(1, limit), 100)
    try:
        from castor.learner.episode_store import EpisodeStore

        store = EpisodeStore()
        episodes = store.list_recent(n=limit)
        return {
            "episodes": [
                {
                    "id": ep.id,
                    "goal": ep.goal,
                    "success": ep.success,
                    "start_time": ep.start_time,
                    "duration_s": ep.duration_s,
                }
                for ep in episodes
            ],
            "count": len(episodes),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Episode store error: {exc}") from exc


class EpisodeSubmitRequest(BaseModel):
    goal: str
    success: bool
    duration_s: float = 0.0
    actions: Optional[list] = None
    sensor_readings: Optional[list] = None
    metadata: Optional[dict] = None


@app.post("/api/learner/episode", dependencies=[Depends(verify_token)])
async def submit_episode(body: EpisodeSubmitRequest, run_improvement: bool = False):
    """Submit a recorded episode and optionally trigger the improvement loop.

    Query param ``run_improvement=true`` runs the Sisyphus loop on the episode
    immediately after saving and returns the improvement result.
    """
    try:
        from castor.learner.episode import Episode
        from castor.learner.episode_store import EpisodeStore
        from castor.learner.sisyphus import SisyphusLoop

        episode = Episode(
            goal=body.goal,
            success=body.success,
            duration_s=body.duration_s,
            actions=body.actions or [],
            sensor_readings=body.sensor_readings or [],
            metadata=body.metadata or {},
        )

        store = EpisodeStore()
        store.save(episode)

        response: Dict[str, Any] = {"episode_id": episode.id, "saved": True}

        if run_improvement:
            learner = state.learner or SisyphusLoop(config=state.config or {})
            result = learner.run_episode(episode)
            response["improvement"] = result.to_dict()

        return response
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Episode submission error: {exc}") from exc


# ---------------------------------------------------------------------------
# Command history endpoint (#75)
# ---------------------------------------------------------------------------

@app.get("/api/command/history", dependencies=[Depends(verify_token)])
async def command_history(limit: int = 20):
    """Return recent brain thought/action pairs.

    Query param ``limit`` (default 20, max 50) controls how many to return.
    History is a ring buffer that resets on gateway restart.
    """
    limit = min(max(1, limit), 50)
    if state.thought_history is None:
        return {"history": [], "count": 0}

    entries = list(state.thought_history)[:limit]
    return {"history": entries, "count": len(entries)}


# ---------------------------------------------------------------------------
# Webhook endpoints for messaging channels
# ---------------------------------------------------------------------------
def _verify_twilio_signature(request_url: str, form_params: dict, signature: str) -> bool:
    """Verify Twilio HMAC-SHA1 webhook signature.

    https://www.twilio.com/docs/usage/webhooks/webhooks-security
    """
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        return True  # No token configured — skip verification (log warning at startup)

    # Build the validation string: URL + sorted POST params
    s = request_url
    for key in sorted(form_params.keys()):
        s += key + (form_params[key] or "")

    expected = hmac.new(
        auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1
    ).digest()
    import base64

    expected_b64 = base64.b64encode(expected).decode("utf-8")
    return hmac.compare_digest(expected_b64, signature)


@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request):
    """Twilio WhatsApp webhook endpoint (for whatsapp_twilio channel only).

    Verifies X-Twilio-Signature before processing.
    """
    channel = state.channels.get("whatsapp_twilio")
    if not channel:
        raise HTTPException(
            status_code=503,
            detail="WhatsApp (Twilio) channel not configured. "
            "This webhook is for the legacy Twilio integration only.",
        )

    # HMAC signature verification
    twilio_sig = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()
    form_dict = dict(form)
    if twilio_sig:
        request_url = str(request.url)
        if not _verify_twilio_signature(request_url, form_dict, twilio_sig):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    elif os.getenv("TWILIO_AUTH_TOKEN"):
        raise HTTPException(status_code=403, detail="Missing X-Twilio-Signature header")

    # Rate limit by sender phone number
    sender_id = form_dict.get("From", "unknown")
    _check_webhook_rate(sender_id)

    reply = await channel.handle_webhook(form_dict)
    return JSONResponse(content={"reply": reply})


@app.get("/api/whatsapp/status", dependencies=[Depends(verify_token)])
async def whatsapp_status():
    """Return WhatsApp (neonize) connection status."""
    channel = state.channels.get("whatsapp")
    if not channel:
        return {"status": "not_configured"}
    connected = getattr(channel, "connected", False)
    return {"status": "connected" if connected else "disconnected"}


def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify Slack HMAC-SHA256 webhook signature.

    https://api.slack.com/authentication/verifying-requests-from-slack
    """
    signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        return True  # No secret configured — skip verification

    # Reject requests older than 5 minutes to prevent replay attacks
    try:
        age = abs(time.time() - float(timestamp))
        if age > 300:
            return False
    except (TypeError, ValueError):
        return False

    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhooks/slack")
async def slack_webhook(request: Request):
    """Slack Events API fallback webhook (Socket Mode is preferred).

    Verifies X-Slack-Signature before processing.
    """
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if signature:
        if not _verify_slack_signature(body, timestamp, signature):
            raise HTTPException(status_code=403, detail="Invalid Slack signature")
    elif os.getenv("SLACK_SIGNING_SECRET"):
        raise HTTPException(status_code=403, detail="Missing X-Slack-Signature header")

    import json

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Slack URL verification challenge (exempt from rate limiting)
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    # Rate limit by Slack user ID
    sender_id = payload.get("event", {}).get("user", "unknown")
    _check_webhook_rate(sender_id)

    return {"ok": True}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _print_gateway_qr(host: str, port: str):
    """Print a terminal QR code linking to the gateway URL for mobile access."""
    try:
        import socket

        # Determine LAN IP if bound to 0.0.0.0 or 127.0.0.1
        if host in ("0.0.0.0", "127.0.0.1", "localhost"):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
            except Exception:
                lan_ip = host
            finally:
                s.close()
        else:
            lan_ip = host

        url = f"http://{lan_ip}:{port}"

        try:
            import qrcode

            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.make(fit=True)
            logger.info(f"Scan to connect from mobile: {url}")
            qr.print_ascii(invert=True)
        except ImportError:
            logger.info(f"Connect from mobile: {url}")
            logger.info("Install qrcode for terminal QR: pip install qrcode")
    except Exception:
        pass


def _execute_action(action: dict):
    """Translate an action dict into driver commands."""
    action_type = action.get("type", "")
    if action_type == "move":
        state.driver.move(
            action.get("linear", 0.0),
            action.get("angular", 0.0),
        )
    elif action_type == "stop":
        state.driver.stop()
    elif action_type == "grip":
        logger.info(f"Grip: {action.get('state', 'unknown')}")
    elif action_type == "wait":
        logger.info(f"Wait: {action.get('duration_ms', 0)}ms")


def _capture_live_frame() -> bytes:
    """Grab a frame from the shared camera if available, else return b''.
    Returns b'' when no camera is ready or the frame is blank/null padding.
    Callers should treat b'' as "no frame" and skip vision inference.
    """
    try:
        from castor.main import get_shared_camera

        camera = get_shared_camera()
        if camera is not None and camera.is_available():
            frame = camera.capture_jpeg()
            # Reject null-padding placeholders (b"\x00" * N) returned on capture failure
            if frame and any(b != 0 for b in frame[:16]):
                return frame
    except Exception:
        pass
    return b""


def _speak_reply(text: str):
    """Speak via USB speaker if available."""
    try:
        from castor.main import get_shared_speaker

        speaker = get_shared_speaker()
        if speaker is not None:
            speaker.say(text[:120])
    except Exception:
        pass


# Map channel names to prompt surface types.
# Governs tone/format injected into build_messaging_prompt().
_CHANNEL_SURFACE: dict[str, str] = {
    "whatsapp": "whatsapp",   # no markdown, short, phone-friendly
    "telegram": "whatsapp",   # same constraints
    "signal":   "whatsapp",
    "sms":      "whatsapp",
    "discord":  "dashboard",  # supports markdown, richer context
    "slack":    "dashboard",
    "irc":      "terminal",   # plain text only
    "terminal": "terminal",
    "dashboard": "dashboard",
    "voice":    "voice",      # TTS path — no symbols, spoken phrasing
}


def _handle_channel_message(channel_name: str, chat_id: str, text: str) -> str:
    """Callback invoked by channels when a message arrives."""
    if state.brain is None:
        return "Robot brain is not initialized. Please load a config first."

    # Resolve prompt surface from channel name (default: whatsapp)
    surface = _CHANNEL_SURFACE.get(channel_name.lower(), "whatsapp")

    # Push the incoming message into the context window
    if state.fs:
        state.fs.context.push("user", text, metadata={"channel": channel_name, "chat_id": chat_id})

    # Build instruction with memory context
    instruction = text
    if state.fs:
        memory_ctx = state.fs.memory.build_context_summary()
        context_ctx = state.fs.context.build_prompt_context()
        if memory_ctx:
            instruction = f"{instruction}\n\n{memory_ctx}"
        if context_ctx:
            instruction = f"{instruction}\n\n{context_ctx}"

    # Use live camera frame so the brain can see what's in front of it
    image_bytes = _capture_live_frame()

    # Route through offline fallback manager if active, else use brain directly
    active_provider = (
        state.offline_fallback.get_active_provider()
        if state.offline_fallback
        else state.brain
    )
    thought = active_provider.think(image_bytes, instruction, surface=surface)
    state.last_thought = {
        "raw_text": thought.raw_text,
        "action": thought.action,
        "timestamp": time.time(),
        "source": f"{channel_name}:{chat_id}",
    }

    if thought.action and state.driver:
        # Write through safety layer before executing
        if state.fs:
            state.fs.write("/dev/motor", thought.action, principal="channel")
            # Use the clamped action from the safety layer
            clamped_action = state.fs.read("/dev/motor", principal="channel")
            if clamped_action:
                _execute_action(clamped_action)
        else:
            _execute_action(thought.action)

    # Record in memory and context
    if state.fs:
        state.fs.memory.record_episode(
            observation=text[:100],
            action=thought.action,
            outcome=thought.raw_text[:100],
            tags=[channel_name],
        )
        state.fs.context.push("brain", thought.raw_text[:200], metadata=thought.action)
        state.fs.proc.record_thought(thought.raw_text, thought.action)

    # Speak the reply out loud
    _speak_reply(thought.raw_text)

    return thought.raw_text


async def _start_channels():
    """Initialize and start all configured messaging channels."""
    from castor.channels import create_channel, get_ready_channels

    for name in get_ready_channels():
        try:
            channel_cfg = (state.config or {}).get("channels", {}).get(name, {})
            channel = create_channel(name, config=channel_cfg, on_message=_handle_channel_message)
            await channel.start()
            state.channels[name] = channel
            logger.info(f"Channel started: {name}")
        except Exception as e:
            logger.warning(f"Failed to start channel {name}: {e}")


async def _stop_channels():
    """Gracefully stop all active channels."""
    for name, channel in state.channels.items():
        try:
            await channel.stop()
        except Exception as e:
            logger.warning(f"Error stopping channel {name}: {e}")
    state.channels.clear()


# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    # Always initialize thought history ring buffer (no config needed)
    state.thought_history = collections.deque(maxlen=50)

    load_dotenv_if_available()

    config_path = os.getenv("OPENCASTOR_CONFIG", "robot.rcan.yaml")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                state.config = yaml.safe_load(f)
            logger.info(f"Loaded config: {state.config['metadata']['robot_name']}")

            # Validate RCAN config before initialising anything
            from castor.config_validation import log_validation_result

            log_validation_result(state.config, label="Startup RCAN config")

            # Initialize virtual filesystem (use shared FS if runtime started it)
            from castor.main import get_shared_fs, set_shared_fs

            state.fs = get_shared_fs()
            if state.fs is None:
                memory_dir = os.getenv("OPENCASTOR_MEMORY_DIR")
                state.fs = CastorFS(persist_dir=memory_dir)
                state.fs.boot(state.config)
                set_shared_fs(state.fs)
            logger.info("Virtual Filesystem online")

            # Construct RURI from config
            try:
                from castor.rcan.ruri import RURI

                ruri = RURI.from_config(state.config)
                state.ruri = str(ruri)
                if state.fs:
                    state.fs.proc.set_ruri(state.ruri)
                logger.info(f"RURI: {state.ruri}")
            except Exception as e:
                logger.warning(f"RURI construction skipped: {e}")

            # Initialize RCAN capability registry and message router
            try:
                from castor.rcan.capabilities import CapabilityRegistry
                from castor.rcan.router import MessageRouter
                from castor.rcan.ruri import RURI as RURIClass

                state.capability_registry = CapabilityRegistry(state.config)
                ruri_obj = (
                    RURIClass.parse(state.ruri)
                    if state.ruri
                    else RURIClass.from_config(state.config)
                )
                state.rcan_router = MessageRouter(ruri_obj, state.capability_registry)

                # Register default handlers
                def _status_handler(msg, p):
                    return {
                        "uptime_s": round(time.time() - state.boot_time, 1),
                        "brain": state.brain is not None,
                        "driver": state.driver is not None,
                    }

                def _chat_handler(msg, p):
                    if state.brain is None:
                        raise RuntimeError("Brain not initialized")
                    image_bytes = _capture_live_frame()
                    thought = state.brain.think(image_bytes, msg.payload.get("instruction", ""))
                    return {"raw_text": thought.raw_text, "action": thought.action}

                def _teleop_handler(msg, p):
                    if state.driver:
                        _execute_action(msg.payload)
                    return {"accepted": True}

                def _nav_handler(msg, p):
                    if state.driver:
                        _execute_action(msg.payload)
                    return {"accepted": True}

                def _vision_handler(msg, p):
                    cam = state.fs.ns.read("/dev/camera") if state.fs else None
                    return {"camera": cam or {"status": "offline"}}

                state.rcan_router.register_handler("status", _status_handler)
                state.rcan_router.register_handler("chat", _chat_handler)
                state.rcan_router.register_handler("teleop", _teleop_handler)
                state.rcan_router.register_handler("nav", _nav_handler)
                state.rcan_router.register_handler("vision", _vision_handler)

                if state.fs:
                    state.fs.proc.set_capabilities(state.capability_registry.names)
                logger.info(f"RCAN capabilities: {state.capability_registry.names}")
            except Exception as e:
                logger.debug(f"RCAN router init skipped: {e}")

            # Initialize brain
            from castor.providers import get_provider

            state.brain = get_provider(state.config["agent"])
            logger.info(f"Brain online: {state.config['agent'].get('model')}")

            # Initialize offline fallback manager (if configured)
            if state.config.get("offline_fallback", {}).get("enabled"):
                try:
                    from castor.offline_fallback import OfflineFallbackManager

                    state.offline_fallback = OfflineFallbackManager(
                        config=state.config,
                        primary_provider=state.brain,
                    )
                    state.offline_fallback.start()
                    logger.info("Offline fallback manager started")
                except Exception as _of_exc:
                    logger.warning("Offline fallback init failed: %s", _of_exc)

            # Initialize driver (simulation-safe)
            from castor.drivers import get_driver
            from castor.main import Camera, Speaker

            state.driver = get_driver(state.config)

            # Initialize camera + speaker for live frames and TTS
            from castor.main import set_shared_camera, set_shared_speaker

            state.camera = Camera(state.config)
            set_shared_camera(state.camera)
            if state.fs:
                state.fs.proc.set_camera("online" if state.camera.is_available() else "offline")

            state.speaker = Speaker(state.config)
            set_shared_speaker(state.speaker)
            if state.fs:
                state.fs.proc.set_speaker("online" if state.speaker.enabled else "offline")

            # Initialize Sisyphus learner loop (provider-wired for LLM augmentation)
            try:
                from castor.learner.sisyphus import SisyphusLoop

                state.learner = SisyphusLoop(config=state.config, provider=state.brain)
                logger.info("Learner loop initialized")
            except Exception as _learner_exc:
                logger.debug("Learner init skipped: %s", _learner_exc)

        except Exception as e:
            logger.warning(f"Config load error (gateway still operational): {e}")
    else:
        logger.info(
            f"No config at {config_path} -- gateway running in unconfigured mode. "
            "Use POST /api/command after loading a config."
        )

    # Start mDNS (opt-in via rcan_protocol.enable_mdns)
    if state.config:
        rcan_proto = state.config["rcan_protocol"]
        if rcan_proto.get("enable_mdns"):
            try:
                from castor.rcan.mdns import RCANServiceBroadcaster, RCANServiceBrowser

                ruri_str = state.ruri or "rcan://opencastor.unknown.00000000"
                state.mdns_broadcaster = RCANServiceBroadcaster(
                    ruri=ruri_str,
                    robot_name=state.config.get("metadata", {}).get("robot_name", "OpenCastor"),
                    port=int(rcan_proto.get("port", 8000)),
                    capabilities=rcan_proto.get("capabilities", []),
                    model=state.config.get("metadata", {}).get("model", "unknown"),
                )
                state.mdns_broadcaster.start()
                state.mdns_browser = RCANServiceBrowser()
                state.mdns_browser.start()
            except Exception as e:
                logger.debug(f"mDNS startup skipped: {e}")

    await _start_channels()

    host = os.getenv("OPENCASTOR_API_HOST", "127.0.0.1")
    port = os.getenv("OPENCASTOR_API_PORT", "8000")
    logger.info(f"OpenCastor Gateway ready on {host}:{port}")

    # Warn if running without authentication
    if not os.getenv("OPENCASTOR_API_TOKEN") and not os.getenv("OPENCASTOR_JWT_SECRET"):
        logger.warning(
            "Gateway running WITHOUT authentication. "
            "Set OPENCASTOR_API_TOKEN or OPENCASTOR_JWT_SECRET in .env for production."
        )

    # Print QR code for mobile access
    _print_gateway_qr(host, port)


@app.on_event("shutdown")
async def on_shutdown():
    await _stop_channels()

    # Stop mDNS
    if state.mdns_broadcaster:
        state.mdns_broadcaster.stop()
        state.mdns_broadcaster = None
    if state.mdns_browser:
        state.mdns_browser.stop()
        state.mdns_browser = None

    # Clear shared references first so in-flight requests cannot grab
    # a closing/closed device.
    from castor.main import set_shared_camera, set_shared_fs, set_shared_speaker

    set_shared_camera(None)
    set_shared_speaker(None)

    if state.driver:
        state.driver.close()
    if hasattr(state, "speaker") and state.speaker:
        state.speaker.close()
        state.speaker = None
    if hasattr(state, "camera") and state.camera:
        state.camera.close()
        state.camera = None

    # Flush memory and shut down virtual filesystem
    if state.fs:
        state.fs.shutdown()
        set_shared_fs(None)
        state.fs = None

    logger.info("OpenCastor Gateway shut down")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------
def _setup_signal_handlers() -> None:
    """Register signal handlers for graceful shutdown."""

    def _handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, initiating graceful shutdown...")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)


def main():
    import uvicorn

    load_dotenv_if_available()
    _setup_signal_handlers()

    parser = argparse.ArgumentParser(description="OpenCastor API Gateway")
    parser.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    parser.add_argument("--host", default=os.getenv("OPENCASTOR_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OPENCASTOR_API_PORT", "8000")))
    args = parser.parse_args()

    os.environ["OPENCASTOR_CONFIG"] = args.config

    uvicorn.run(
        "castor.api:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
