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
import asyncio
import collections
import hashlib
import hmac
import logging
import os
import posixpath
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from castor.api_errors import register_error_handlers
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
_WEBHOOK_RATE_LIMIT = int(
    os.getenv("OPENCASTOR_WEBHOOK_RATE", "10")
)  # max webhook calls/minute/sender
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
    offline_fallback = None  # OfflineFallbackManager (optional)
    provider_fallback = None  # ProviderFallbackManager (optional, for quota errors)
    thought_history = None  # deque(maxlen=50) — ring buffer of recent thoughts
    learner = None  # SisyphusLoop instance (optional)
    paused: bool = False  # Runtime pause flag (issue #93)
    _health_cache_time: float = 0.0  # last time health_check() was called
    _health_cache_result: dict = {}  # cached result (TTL: 30s)
    usage_tracker = None  # UsageTracker singleton (lazy-init)
    listener = None  # Listener instance for STT (issue #119)
    nav_job = None  # Current nav job dict or None (issue #120)
    behavior_runner = None  # BehaviorRunner instance (issue #121)
    behavior_job = None  # Current behavior job dict or None (issue #121)


state = AppState()

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
API_TOKEN = os.getenv("OPENCASTOR_API_TOKEN")


def _check_min_role(request: Request, min_role: str) -> None:
    """Raise HTTP 403 if the authenticated user has insufficient role level.

    Works with both multi-user JWT tokens (jwt_role on request.state) and the
    static bearer token path (treated as admin-level).  Skips the check when
    no auth is configured (open access mode).

    Args:
        request:  The incoming FastAPI request.
        min_role: Minimum required role name (admin / operator / viewer).
    """
    from castor.auth_jwt import ROLES

    role = getattr(request.state, "jwt_role", None)
    if role is None:
        # Static token path sets jwt_role in verify_token;
        # if still None, this is open access — allow.
        return

    level = ROLES.get(role, 0)
    min_level = ROLES.get(min_role, 0)
    if level < min_level:
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient role: '{role}' requires at least '{min_role}'",
        )


async def verify_token(request: Request):
    """Multi-layer auth: JWT first, then bearer token, then anonymous/GUEST.

    Layer 1: Multi-user JWT (castor.auth_jwt) — checked when OPENCASTOR_USERS is set.
    Layer 2: RCAN JWT (castor.rcan.jwt_auth) — checked when OPENCASTOR_JWT_SECRET is set.
    Layer 3: Static bearer token (OPENCASTOR_API_TOKEN) — backwards-compatible.
    Layer 4: Open access when no auth is configured.

    Also accepts the token via ``?token=`` query parameter for streaming
    clients (browsers, VLC) that cannot set Authorization headers.
    """
    # Allow token via query param (e.g. for MJPEG <img> tags and VLC)
    query_token = request.query_params.get("token", "")
    auth = request.headers.get("Authorization", "") or (
        f"Bearer {query_token}" if query_token else ""
    )

    raw_token = auth[7:] if auth.startswith("Bearer ") else ""

    # --- Layer 1: Multi-user JWT (Issue #124) ---
    if raw_token:
        try:
            from castor.auth_jwt import HAS_JWT, decode_token

            if HAS_JWT:
                payload = decode_token(raw_token)
                role = payload.get("role", "viewer")
                request.state.jwt_username = payload.get("sub", "unknown")
                request.state.jwt_role = role
                request.state.auth_type = "jwt"
                return
        except Exception:
            pass  # Not a multi-user JWT; fall through

    # --- Layer 2: RCAN JWT (legacy JWT path) ---
    jwt_secret = os.getenv("OPENCASTOR_JWT_SECRET")
    if jwt_secret and raw_token:
        try:
            from castor.rcan.jwt_auth import RCANTokenManager

            mgr = RCANTokenManager(secret=jwt_secret, issuer=state.ruri or "")
            principal = mgr.verify(raw_token)
            request.state.principal = principal
            request.state.jwt_username = getattr(principal, "name", "unknown")
            request.state.jwt_role = "admin"
            request.state.auth_type = "jwt"
            return
        except Exception:
            pass  # Fall through to static token check

    # --- Layer 3: Static API token ---
    if API_TOKEN:
        if auth != f"Bearer {API_TOKEN}":
            raise HTTPException(status_code=401, detail="Invalid or missing API token")
        request.state.jwt_username = "api"
        request.state.jwt_role = "admin"
        request.state.auth_type = "static"
        return

    # --- Layer 4: No auth configured -- open access ---


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


class WaypointRequest(BaseModel):
    distance_m: float
    heading_deg: float
    speed: float = 0.6


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


# ---------------------------------------------------------------------------
# Multi-user JWT auth endpoints  (Issue #124)
# ---------------------------------------------------------------------------


class UserLoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/token")
async def auth_token(req: UserLoginRequest):
    """Issue a JWT access token using username + password (multi-user auth).

    Reads user credentials from OPENCASTOR_USERS env var.
    Falls back gracefully when multi-user auth is not configured.
    """
    from castor.auth_jwt import authenticate_user, create_token

    result = authenticate_user(req.username, req.password)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    username, role = result
    try:
        token = create_token(username, role, expires_h=24)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": role,
        "expires_in": 86400,
    }


@app.get("/auth/me", dependencies=[Depends(verify_token)])
async def auth_me(request: Request):
    """Return the authenticated user's identity and auth type.

    Works with both JWT tokens (multi-user) and the static bearer token.
    """
    # Populated by verify_token when a valid JWT was used
    jwt_username = getattr(request.state, "jwt_username", None)
    jwt_role = getattr(request.state, "jwt_role", None)
    auth_type = getattr(request.state, "auth_type", None)

    if jwt_username:
        return {"username": jwt_username, "role": jwt_role, "auth_type": auth_type or "jwt"}

    # Populated by the RCAN JWT path
    principal = getattr(request.state, "principal", None)
    if principal:
        return {
            "username": getattr(principal, "name", "unknown"),
            "role": str(getattr(principal, "role", "unknown")),
            "auth_type": "jwt",
        }

    # Static bearer token (backwards-compat)
    if API_TOKEN and request.headers.get("Authorization") == f"Bearer {API_TOKEN}":
        return {"username": "api", "role": "admin", "auth_type": "static"}

    return {"username": "anonymous", "role": "viewer", "auth_type": "none"}


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

    # Provider health check — routed through active brain (respects fallback),
    # cached for 30 s to avoid flooding a quota-exhausted primary provider.
    active_brain = _get_active_brain()
    if active_brain is not None:
        _now = time.time()
        if _now - state._health_cache_time >= 30:
            try:
                state._health_cache_result = active_brain.health_check()
            except Exception as exc:
                state._health_cache_result = {"ok": False, "error": str(exc)}
            state._health_cache_time = _now
        payload["provider_health"] = state._health_cache_result

    # Offline fallback status — structured dict so clients can query state
    if state.offline_fallback is not None:
        fb = state.offline_fallback
        payload["offline_fallback"] = {
            "enabled": True,
            "using_fallback": fb.is_using_fallback,
            "fallback_ready": fb.fallback_ready,
            "fallback_provider": fb._config.get("provider", "unknown"),
            "fallback_model": fb._config.get("model", "unknown"),
        }
    else:
        payload["offline_fallback"] = {"enabled": False}

    # Provider fallback status (quota/credit error switching)
    if state.provider_fallback is not None:
        pf = state.provider_fallback
        payload["provider_fallback"] = {
            "enabled": True,
            "using_fallback": pf.is_using_fallback,
            "fallback_ready": pf.fallback_ready,
            "fallback_provider": pf._fb_cfg.get("provider", "unknown"),
            "fallback_model": pf._fb_cfg.get("model", "unknown"),
        }
    else:
        payload["provider_fallback"] = {"enabled": False}

    return _maybe_wrap_rcan(payload, request)


@app.post("/api/command", dependencies=[Depends(verify_token)])
async def send_command(cmd: CommandRequest, request: Request):
    """Send an instruction to the robot's brain and receive the action."""
    _check_min_role(request, "operator")  # viewer role blocked
    _check_command_rate(request.client.host if request.client else "unknown")
    if state.brain is None:
        raise HTTPException(status_code=503, detail="Brain not initialized")

    # Use provided image, live camera frame, or blank
    if cmd.image_base64:
        import base64

        image_bytes = base64.b64decode(cmd.image_base64)
    else:
        image_bytes = _capture_live_frame()

    active = _get_active_brain()
    try:
        thought = active.think(image_bytes, cmd.instruction)
    except Exception as _think_exc:
        from castor.providers.base import ProviderQuotaError

        if isinstance(_think_exc, ProviderQuotaError):
            raise HTTPException(
                status_code=402,
                detail=(
                    "AI provider credits exhausted. "
                    "Add 'provider_fallback' to your RCAN config to automatically "
                    "switch to a backup provider (e.g. Ollama or Google Gemini). "
                    f"Provider: {_think_exc.provider_name or 'unknown'}"
                ),
            ) from _think_exc
        raise

    _record_thought(cmd.instruction, thought.raw_text, thought.action)

    # Execute action on hardware if available
    if thought.action and state.driver:
        _execute_action(thought.action)

    return {
        "raw_text": thought.raw_text,
        "action": thought.action,
    }


@app.post("/api/action", dependencies=[Depends(verify_token)])
async def direct_action(action: ActionRequest, request: Request):
    """Send a direct motor command, bypassing the brain.

    Requires bearer auth (enforced via verify_token dependency).
    Bounds are checked against the safety layer before executing.
    """
    _check_min_role(request, "operator")  # viewer role blocked
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
# Runtime lifecycle endpoints  (issue #93)
# ---------------------------------------------------------------------------


@app.post("/api/runtime/pause", dependencies=[Depends(verify_token)])
async def runtime_pause():
    """Pause the perception-action loop without stopping the gateway."""
    state.paused = True
    if state.fs:
        state.fs.ns.write("/proc/paused", {"paused": True, "since": time.time()})
    return {"paused": True}


@app.post("/api/runtime/resume", dependencies=[Depends(verify_token)])
async def runtime_resume():
    """Resume the perception-action loop after a pause."""
    state.paused = False
    if state.fs:
        state.fs.ns.write("/proc/paused", None)
    return {"paused": False}


@app.get("/api/runtime/status", dependencies=[Depends(verify_token)])
async def runtime_status():
    """Return current runtime pause/resume state and uptime."""
    paused = getattr(state, "paused", False)
    if state.fs:
        paused_data = state.fs.ns.read("/proc/paused")
        if isinstance(paused_data, dict) and paused_data.get("paused"):
            paused = True
    return {
        "paused": paused,
        "uptime_s": round(time.time() - state.boot_time, 1),
        "brain_ready": state.brain is not None,
        "driver_ready": state.driver is not None,
    }


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint  (issue #99)
# ---------------------------------------------------------------------------


@app.get("/api/metrics")
async def get_metrics():
    """Prometheus text exposition format metrics (no auth — safe for scrapers)."""
    from fastapi.responses import Response as _Response

    from castor.metrics import get_registry

    # Update live status gauges before rendering
    try:
        reg = get_registry()
        robot = (state.config or {}).get("metadata", {}).get("robot_name", "robot")
        reg.update_status(
            robot=robot,
            brain_up=state.brain is not None,
            driver_up=state.driver is not None,
            active_channels=len(state.channels),
            uptime_s=round(time.time() - state.boot_time, 1),
        )
    except Exception:
        pass

    return _Response(
        content=get_registry().render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# Token usage endpoint  (issue #104)
# ---------------------------------------------------------------------------


@app.get("/api/usage", dependencies=[Depends(verify_token)])
async def get_usage():
    """Return token usage and estimated cost for this session and past 7 days."""
    try:
        from castor.usage import get_tracker

        tracker = get_tracker()
        return {
            "session": tracker.get_session_totals(),
            "daily": tracker.get_daily_totals(days=7),
            "all_time": tracker.get_all_time_totals(),
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Config hot-reload endpoint  (issue #94)
# ---------------------------------------------------------------------------


@app.post("/api/config/reload", dependencies=[Depends(verify_token)])
async def reload_config(request: Request):
    """Reload the RCAN config file in-place without restarting the gateway."""
    _check_min_role(request, "admin")  # operator and viewer roles blocked
    config_path = os.getenv("OPENCASTOR_CONFIG", "robot.rcan.yaml")
    try:
        with open(config_path) as _f:
            new_config = yaml.safe_load(_f)
        state.config = new_config
        # Propagate updated model name to the active brain if changed
        if state.brain and "agent" in new_config:
            new_model = new_config["agent"].get("model")
            if new_model:
                state.brain.model_name = new_model
        logger.info("Config reloaded from %s", config_path)
        return {"status": "reloaded", "config_path": config_path}
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Config file not found: {config_path}"
        ) from None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Config reload failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Provider health detail endpoint  (issue #95)
# ---------------------------------------------------------------------------


@app.get("/api/provider/health", dependencies=[Depends(verify_token)])
async def provider_health():
    """Detailed provider health check including latency and token usage."""
    if state.brain is None:
        raise HTTPException(status_code=503, detail="Brain not initialized")

    try:
        health = await asyncio.to_thread(state.brain.health_check)
    except Exception as exc:
        health = {"ok": False, "error": str(exc)}

    usage: dict = {}
    if hasattr(state.brain, "get_usage_stats"):
        try:
            usage = state.brain.get_usage_stats()
        except Exception:
            pass

    provider_name = (state.config or {}).get("agent", {}).get("provider", "unknown")
    return {
        "provider": provider_name,
        "model": getattr(state.brain, "model_name", None),
        "health": health,
        "usage": usage,
    }


# ---------------------------------------------------------------------------
# Episode memory endpoints  (issue #92)
# ---------------------------------------------------------------------------


@app.get("/api/memory/episodes", dependencies=[Depends(verify_token)])
async def list_episodes(limit: int = 50, source: Optional[str] = None):
    """List recent brain-decision episodes from the SQLite memory store."""
    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    episodes = mem.query_recent(limit=min(limit, 500), source=source)
    return {"episodes": episodes, "total": mem.count()}


@app.get("/api/memory/export", dependencies=[Depends(verify_token)])
async def export_episodes(limit: int = 1000):
    """Export all episodes as JSONL (newline-delimited JSON) for download."""
    import tempfile

    from fastapi.responses import Response as _Response

    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as _f:
        tmp_path = _f.name
    mem.export_jsonl(tmp_path, limit=limit)
    with open(tmp_path) as _f:
        content = _f.read()
    os.unlink(tmp_path)
    return _Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=episodes.jsonl"},
    )


@app.delete("/api/memory/episodes", dependencies=[Depends(verify_token)])
async def clear_episodes():
    """Delete all episodes from the memory store."""
    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    deleted = mem.clear()
    return {"deleted": deleted}


@app.post("/api/memory/replay/{episode_id}", dependencies=[Depends(verify_token)])
async def replay_episode(episode_id: str):
    """Re-execute the action from a stored episode through the active driver."""
    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    ep = mem.get_episode(episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail=f"Episode {episode_id} not found")

    action = ep.get("action")
    if not action:
        raise HTTPException(status_code=422, detail="Episode has no action to replay")

    if state.driver is None:
        raise HTTPException(status_code=503, detail="Driver not initialized")

    try:
        _execute_action(action)
        return {"replayed": True, "episode_id": episode_id, "action": action}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Replay failed: {exc}") from exc


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
                # Run blocking camera capture off the event loop so HTTP chunks flush properly
                frame = await asyncio.to_thread(_capture_live_frame)
                if frame:
                    yield (
                        boundary
                        + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(frame)).encode()
                        + b"\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                else:
                    # No frame yet — short sleep before retrying
                    await asyncio.sleep(0.033)
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
    active = _get_active_brain()
    try:
        thought = active.think(image_bytes, cmd.instruction)
    except Exception as _exc:
        from castor.providers.base import ProviderQuotaError

        if isinstance(_exc, ProviderQuotaError):
            raise HTTPException(status_code=402, detail=str(_exc)) from _exc
        raise
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

    active = _get_active_brain()

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
# Guardian report endpoint (#81)
# ---------------------------------------------------------------------------


@app.get("/api/guardian/report", dependencies=[Depends(verify_token)])
async def guardian_report():
    """Return the current safety veto report from GuardianAgent.

    Returns ``{"available": false}`` when the guardian is not initialized.
    When active, returns the most recent guardian report published to
    ``swarm.guardian_report`` in SharedState, including ``estop_active``,
    ``vetoes``, and ``approved`` action lists.
    """
    # Guardian report lives in the AppState's shared agent state (if any)
    # Try the orchestrator's guardian first, then fall through gracefully.
    try:
        # Look for a guardian attached to the fs or a module-level shared state
        if state.fs is not None and hasattr(state.fs, "_shared_state"):
            report = state.fs._shared_state.get("swarm.guardian_report", None)
            if report is not None:
                return {"available": True, "report": report}

        # No guardian state found — return a graceful unavailable response
        return {"available": False, "reason": "Guardian not initialized"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Guardian report unavailable: {exc}") from exc


# ---------------------------------------------------------------------------
# Audio transcription endpoint (#89)
# ---------------------------------------------------------------------------


@app.post("/api/audio/transcribe", dependencies=[Depends(verify_token)])
async def audio_transcribe(
    file: UploadFile = File(...),
    engine: str = "auto",
):
    """Transcribe an uploaded audio file to text.

    Accepts any common audio format (ogg, mp3, wav, m4a, webm, flac).
    Uses the tiered transcription pipeline from ``castor.voice``:
    Whisper API → local Whisper → Google SpeechRecognition.

    Args:
        file: Multipart audio upload.
        engine: Force a specific engine ("whisper_api", "whisper_local",
                "google") or "auto" (default).

    Returns:
        ``{"text": str, "engine": str, "duration_ms": float}``

    Raises:
        422 if no file is provided.
        503 if no transcription engine is available.
        500 on unexpected error.
    """
    import time as _time

    try:
        from castor import voice as voice_mod
    except ImportError:
        raise HTTPException(
            status_code=503, detail="Voice transcription module not available"
        ) from None

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="Empty audio file")

    available = voice_mod.available_engines()
    if not available and engine == "auto":
        raise HTTPException(
            status_code=503,
            detail="No transcription engines available. "
            "Set OPENAI_API_KEY for Whisper API or install 'whisper'/'SpeechRecognition'.",
        )

    filename = file.filename or "audio.ogg"
    hint = filename.rsplit(".", 1)[-1].lower() if "." in filename else "ogg"

    t0 = _time.time()
    text = voice_mod.transcribe_bytes(audio_bytes, hint_format=hint, engine=engine)
    duration_ms = round((_time.time() - t0) * 1000, 1)

    if text is None:
        raise HTTPException(
            status_code=503,
            detail="Transcription failed — audio may be inaudible or in an unsupported format",
        )

    resolved_engine = engine if engine != "auto" else (available[0] if available else "unknown")
    return {"text": text, "engine": resolved_engine, "duration_ms": duration_ms}


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
# Depth camera endpoints (Issue #117)
# ---------------------------------------------------------------------------


@app.get("/api/depth/frame", dependencies=[Depends(verify_token)])
async def depth_frame():
    """Return a JPEG of the latest RGB frame with JET-colormap depth overlay.

    Requires an OAK-D (or compatible depth camera) to be active.
    Returns 503 when no camera is available or no depth frame has been captured.
    """
    from castor.depth import get_depth_overlay
    from castor.main import get_shared_camera

    camera = get_shared_camera()
    if camera is None or not camera.is_available():
        raise HTTPException(status_code=503, detail="No camera available")

    depth = getattr(camera, "last_depth", None)
    rgb_bytes = await asyncio.to_thread(_capture_live_frame)

    from fastapi.responses import Response as _Resp

    try:
        jpeg = await asyncio.to_thread(get_depth_overlay, rgb_bytes or b"", depth)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Depth overlay failed: {exc}") from exc

    return _Resp(content=jpeg, media_type="image/jpeg")


@app.get("/api/depth/obstacles", dependencies=[Depends(verify_token)])
async def depth_obstacles():
    """Return nearest obstacle distances (cm) per sector: left / center / right.

    Divides the latest depth frame into horizontal thirds and reports the
    minimum depth in each sector.  Returns {"available": false} when no
    depth camera is active.
    """
    from castor.depth import get_obstacle_zones
    from castor.main import get_shared_camera

    camera = get_shared_camera()
    depth = getattr(camera, "last_depth", None) if camera is not None else None
    return await asyncio.to_thread(get_obstacle_zones, depth)


# ---------------------------------------------------------------------------
# WebSocket telemetry stream (Issue #118)
# ---------------------------------------------------------------------------


@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket, token: str = ""):
    """WebSocket endpoint that pushes telemetry JSON every 200 ms.

    Auth: when OPENCASTOR_API_TOKEN is set, the client must pass a matching
    ?token=<value> query parameter (or the connection is closed with
    code 1008 - Policy Violation).

    Pushed frame schema::

        {
            "ts":            float,   # Unix timestamp
            "robot":         str,     # robot_name from config
            "loop_count":    int,     # perception-action loop iterations
            "avg_latency_ms": float,  # last loop latency (ms)
            "camera":        str,     # "online" or "offline"
            "driver":        str,     # "hardware" or "mock" or "none"
            "depth":         dict,    # get_obstacle_zones() result
            "provider":      str,     # active provider name
            "using_fallback": bool,   # True when a fallback provider is active
        }

    The client may send a JSON command:

        {"cmd": "stop"}  — triggers driver.stop() if a driver is active.
    """
    # --- Auth check ---
    if API_TOKEN and token != API_TOKEN:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.debug("WebSocket telemetry client connected")

    async def _push_loop():
        from castor.depth import get_obstacle_zones
        from castor.main import get_shared_camera

        while True:
            try:
                # Collect telemetry
                robot_name = (state.config or {}).get("metadata", {}).get("robot_name", "robot")

                # Loop count + latency from ProcFS if available
                loop_count = 0
                avg_latency_ms = 0.0
                if state.fs is not None:
                    try:
                        snap = state.fs.proc.snapshot()
                        loop_count = snap.get("loop", {}).get("iteration") or 0
                        avg_latency_ms = snap.get("loop", {}).get("latency_ms") or 0.0
                    except Exception:
                        pass

                # Camera status
                camera_status = "offline"
                if state.fs is not None:
                    try:
                        hw = state.fs.proc.snapshot().get("hw", {})
                        camera_status = hw.get("camera", "offline") or "offline"
                    except Exception:
                        pass
                elif hasattr(state, "camera") and state.camera is not None:
                    camera_status = "online" if state.camera.is_available() else "offline"

                # Driver type
                driver_type = "none"
                if state.driver is not None:
                    dt = type(state.driver).__name__.lower()
                    driver_type = "mock" if "mock" in dt or "sim" in dt else "hardware"

                # Depth obstacles
                camera = get_shared_camera()
                depth = getattr(camera, "last_depth", None) if camera is not None else None
                depth_data = await asyncio.to_thread(get_obstacle_zones, depth)

                # Provider name + fallback flag
                provider_name = (state.config or {}).get("agent", {}).get("provider", "none")
                using_fallback = False
                if state.provider_fallback is not None:
                    using_fallback = getattr(state.provider_fallback, "is_using_fallback", False)
                elif state.offline_fallback is not None:
                    using_fallback = getattr(state.offline_fallback, "is_using_fallback", False)

                frame = {
                    "ts": time.time(),
                    "robot": robot_name,
                    "loop_count": loop_count,
                    "avg_latency_ms": avg_latency_ms,
                    "camera": camera_status,
                    "driver": driver_type,
                    "depth": depth_data,
                    "provider": provider_name,
                    "using_fallback": using_fallback,
                }
                await websocket.send_json(frame)
            except WebSocketDisconnect:
                logger.debug("WebSocket telemetry client disconnected (push loop)")
                return
            except Exception as exc:
                logger.debug("WebSocket telemetry push error: %s", exc)
                return
            await asyncio.sleep(0.2)

    async def _recv_loop():
        """Listen for commands from the client (e.g. stop)."""
        try:
            while True:
                data = await websocket.receive_json()
                cmd = data.get("cmd", "") if isinstance(data, dict) else ""
                if cmd == "stop" and state.driver is not None:
                    try:
                        state.driver.stop()
                        logger.info("WebSocket stop command executed")
                    except Exception as exc:
                        logger.warning("WebSocket stop failed: %s", exc)
        except WebSocketDisconnect:
            logger.debug("WebSocket telemetry client disconnected (recv loop)")
        except Exception as exc:
            logger.debug("WebSocket receive error: %s", exc)

    push_task = asyncio.create_task(_push_loop())
    recv_task = asyncio.create_task(_recv_loop())
    try:
        done, pending = await asyncio.wait(
            [push_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
    except Exception:
        pass
    finally:
        for t in (push_task, recv_task):
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
    logger.debug("WebSocket telemetry handler exiting")


# ---------------------------------------------------------------------------
# Voice / STT endpoints (issue #119)
# ---------------------------------------------------------------------------


@app.post("/api/voice/listen", dependencies=[Depends(verify_token)])
async def voice_listen():
    """Capture one microphone phrase and return transcript + brain thought.

    Returns:
        200: {"transcript": "...", "thought": {...}}
        503: {"error": "..."} if listener is not available
    """
    if state.listener is None:
        raise HTTPException(status_code=503, detail="Listener not available")
    if not state.listener.enabled:
        raise HTTPException(
            status_code=503, detail="STT not enabled (set audio.stt_enabled: true in config)"
        )

    transcript = await asyncio.to_thread(state.listener.listen_once)
    if transcript is None:
        raise HTTPException(status_code=503, detail="Could not capture audio or recognise speech")

    thought_dict: Optional[dict] = None
    if state.brain and transcript:
        try:
            image_bytes = _capture_live_frame()
            thought = await asyncio.to_thread(state.brain.think, image_bytes, transcript)
            thought_dict = {"raw_text": thought.raw_text, "action": thought.action}
            _speak_reply(thought.raw_text)
        except Exception as exc:
            logger.warning(f"Brain error during voice listen: {exc}")

    return {"transcript": transcript, "thought": thought_dict}


# ---------------------------------------------------------------------------
# Waypoint navigation endpoints (issue #120)
# ---------------------------------------------------------------------------

import uuid as _uuid  # noqa: E402


@app.post("/api/nav/waypoint", dependencies=[Depends(verify_token)])
async def nav_waypoint(body: WaypointRequest):
    """Start a non-blocking waypoint navigation job.

    Returns a job_id immediately; poll GET /api/nav/status for completion.

    Returns:
        200: {"job_id": "...", "running": true}
        503: if no driver is loaded
    """
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No driver loaded")

    job_id = str(_uuid.uuid4())
    state.nav_job = {"job_id": job_id, "running": True, "result": None}

    async def _run():
        try:
            from castor.nav import WaypointNav

            nav = WaypointNav(state.driver, state.config or {})
            result = await asyncio.to_thread(
                nav.execute,
                body.distance_m,
                body.heading_deg,
                body.speed,
            )
            state.nav_job = {"job_id": job_id, "running": False, "result": result}
        except Exception as exc:
            logger.warning(f"Nav job {job_id} failed: {exc}")
            state.nav_job = {
                "job_id": job_id,
                "running": False,
                "result": {"ok": False, "error": str(exc)},
            }

    asyncio.ensure_future(_run())
    return {"job_id": job_id, "running": True}


@app.get("/api/nav/status", dependencies=[Depends(verify_token)])
async def nav_status():
    """Return the current (or last) navigation job status.

    Returns:
        200: {"running": bool, "job_id": str|None, "result": dict|None}
    """
    if state.nav_job is None:
        return {"running": False, "job_id": None, "result": None}
    return {
        "running": state.nav_job.get("running", False),
        "job_id": state.nav_job.get("job_id"),
        "result": state.nav_job.get("result"),
    }


# ---------------------------------------------------------------------------
# Behavior script endpoints (issue #121)
# ---------------------------------------------------------------------------


class _BehaviorRunRequest(BaseModel):
    path: Optional[str] = None
    behavior: Optional[dict] = None


@app.post("/api/behavior/run", dependencies=[Depends(verify_token)])
async def behavior_run(req: _BehaviorRunRequest):
    """Load and run a behavior script in a background asyncio task.

    Body (JSON):
        ``{"path": "patrol.behavior.yaml"}``  — load from file system
        ``{"behavior": {...}}``                — inline behavior dict

    Returns:
        200: ``{"job_id": str, "name": str}``
    """
    import uuid

    if state.behavior_runner is None:
        from castor.behaviors import BehaviorRunner

        state.behavior_runner = BehaviorRunner(
            driver=state.driver,
            brain=state.brain,
            speaker=getattr(state, "speaker", None),
            config=state.config or {},
        )

    runner = state.behavior_runner

    # Load behavior
    if req.path:
        try:
            behavior = runner.load(req.path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Cannot load behavior: {exc}") from exc
    elif req.behavior:
        behavior = req.behavior
        missing = {"name", "steps"} - set(behavior.keys())
        if missing:
            raise HTTPException(status_code=400, detail=f"Behavior missing keys: {missing}")
    else:
        raise HTTPException(status_code=400, detail="Provide 'path' or 'behavior' in request body")

    job_id = str(uuid.uuid4())
    name = behavior.get("name", "<unnamed>")

    state.behavior_job = {"job_id": job_id, "name": name, "running": True}

    async def _run_bg():
        try:
            await asyncio.to_thread(runner.run, behavior)
        except Exception as exc:
            logger.error("Behavior '%s' failed: %s", name, exc)
        finally:
            if state.behavior_job and state.behavior_job.get("job_id") == job_id:
                state.behavior_job["running"] = False

    asyncio.create_task(_run_bg())
    logger.info("Behavior '%s' started (job_id=%s)", name, job_id)
    return {"job_id": job_id, "name": name}


@app.post("/api/behavior/stop", dependencies=[Depends(verify_token)])
async def behavior_stop():
    """Stop the currently-running behavior.

    Returns:
        200: ``{"stopped": true}``
    """
    if state.behavior_runner is not None:
        await asyncio.to_thread(state.behavior_runner.stop)
    if state.behavior_job:
        state.behavior_job["running"] = False
    return {"stopped": True}


@app.get("/api/behavior/status")
async def behavior_status():
    """Return the current behavior job status (no auth required for status polling).

    Returns:
        200: ``{"running": bool, "name": str|None, "job_id": str|None}``
    """
    if state.behavior_job is None:
        return {"running": False, "name": None, "job_id": None}
    return {
        "running": state.behavior_job.get("running", False),
        "name": state.behavior_job.get("name"),
        "job_id": state.behavior_job.get("job_id"),
    }


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

    expected = hmac.new(auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1).digest()
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
    expected = (
        "v0="
        + hmac.new(signing_secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    )
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
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None

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


def _get_active_brain():
    """Return the active brain provider, respecting fallback priority.

    Priority: provider_fallback (quota-aware) > offline_fallback (connectivity-aware) > brain.
    ``ProviderFallbackManager.think()`` wraps the provider and auto-switches internally,
    so callers should use it directly when available.
    """
    if state.provider_fallback is not None:
        return state.provider_fallback
    if state.offline_fallback is not None:
        return state.offline_fallback.get_active_provider()
    return state.brain


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
    "whatsapp": "whatsapp",  # no markdown, short, phone-friendly
    "telegram": "whatsapp",  # same constraints
    "signal": "whatsapp",
    "sms": "whatsapp",
    "discord": "dashboard",  # supports markdown, richer context
    "slack": "dashboard",
    "irc": "terminal",  # plain text only
    "terminal": "terminal",
    "dashboard": "dashboard",
    "voice": "voice",  # TTS path — no symbols, spoken phrasing
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

    active_provider = _get_active_brain()
    try:
        thought = active_provider.think(image_bytes, instruction, surface=surface)
    except Exception as _exc:
        from castor.providers.base import ProviderQuotaError

        if isinstance(_exc, ProviderQuotaError):
            return (
                f"⚠️ AI provider credits exhausted (HTTP {_exc.http_status}). "
                "Add `provider_fallback` to your RCAN config to auto-switch. "
                "Run `castor wizard` or see CLAUDE.md for instructions."
            )
        raise

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

            # Initialize provider fallback manager (for quota/credit errors)
            if state.config.get("provider_fallback", {}).get("enabled"):
                try:
                    from castor.provider_fallback import ProviderFallbackManager

                    state.provider_fallback = ProviderFallbackManager(
                        config=state.config,
                        primary_provider=state.brain,
                    )
                    state.provider_fallback.probe_fallback()
                    logger.info("Provider fallback manager ready")
                except Exception as _pf_exc:
                    logger.warning("Provider fallback init failed: %s", _pf_exc)

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

            # Initialize STT listener (issue #119)
            from castor.main import Listener

            state.listener = Listener(state.config)
            logger.info(
                "Listener %s",
                "online" if state.listener.enabled else "offline (stt_enabled not set)",
            )

            # Initialize Sisyphus learner loop (provider-wired for LLM augmentation)
            try:
                from castor.learner.sisyphus import SisyphusLoop

                state.learner = SisyphusLoop(config=state.config, provider=state.brain)
                logger.info("Learner loop initialized")
            except Exception as _learner_exc:
                logger.debug("Learner init skipped: %s", _learner_exc)

            # Initialize BehaviorRunner (issue #121)
            try:
                from castor.behaviors import BehaviorRunner

                state.behavior_runner = BehaviorRunner(
                    driver=state.driver,
                    brain=state.brain,
                    speaker=getattr(state, "speaker", None),
                    config=state.config,
                )
                logger.info("BehaviorRunner initialized")
            except Exception as _beh_exc:
                logger.debug("BehaviorRunner init skipped: %s", _beh_exc)

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


# ---------------------------------------------------------------------------
# Fleet management (issue #113)
# ---------------------------------------------------------------------------


@app.get("/api/fleet", dependencies=[Depends(verify_token)])
async def fleet_list():
    """Return all robots discovered via mDNS on the local network."""
    peers = {}
    if state.mdns_browser:
        peers = state.mdns_browser.peers

    robots = []
    for name, peer in peers.items():
        robots.append(
            {
                "ruri": peer.get("ruri", ""),
                "name": peer.get("robot_name", name),
                "ip": peer.get("addresses", [""])[0] if peer.get("addresses") else "",
                "port": peer.get("port", 8000),
                "status": peer.get("status", "unknown"),
                "last_seen": peer.get("discovered_at"),
                "brain": peer.get("model", ""),
                "capabilities": peer.get("capabilities", []),
            }
        )
    return {"robots": robots, "count": len(robots)}


class _FleetCommandRequest(BaseModel):
    instruction: str
    token: Optional[str] = None


@app.post("/api/fleet/{ruri}/command", dependencies=[Depends(verify_token)])
async def fleet_command(ruri: str, body: _FleetCommandRequest, request: Request):
    """Proxy a command to a remote robot identified by RURI."""
    import httpx

    peer = _find_fleet_peer(ruri)
    if not peer:
        from castor.api_errors import not_found_error

        raise HTTPException(status_code=404, detail=not_found_error(f"robot/{ruri}"))

    url = f"http://{peer['ip']}:{peer['port']}/api/command"
    headers = {}
    if body.token:
        headers["Authorization"] = f"Bearer {body.token}"
    elif API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"instruction": body.instruction}, headers=headers)
            return resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail={"error": "Fleet robot timeout"})
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)})


@app.get("/api/fleet/{ruri}/status", dependencies=[Depends(verify_token)])
async def fleet_status(ruri: str):
    """Proxy a status request to a remote robot identified by RURI."""
    import httpx

    peer = _find_fleet_peer(ruri)
    if not peer:
        from castor.api_errors import not_found_error

        raise HTTPException(status_code=404, detail=not_found_error(f"robot/{ruri}"))

    url = f"http://{peer['ip']}:{peer['port']}/api/status"
    headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)})


def _find_fleet_peer(ruri: str) -> Optional[dict]:
    """Find a fleet peer by its RURI (exact or partial match)."""
    if not state.mdns_browser:
        return None
    for _, peer in state.mdns_browser.peers.items():
        if peer.get("ruri", "") == ruri:
            addrs = peer.get("addresses", [])
            if addrs:
                return {"ip": addrs[0], "port": peer.get("port", 8000)}
    return None


# ---------------------------------------------------------------------------
# WebRTC stream (issue #108)
# ---------------------------------------------------------------------------


class _WebRTCOfferRequest(BaseModel):
    sdp: str
    type: str = "offer"


@app.post("/api/stream/webrtc/offer")
async def webrtc_offer(body: _WebRTCOfferRequest):
    """Accept a WebRTC SDP offer and return an answer for P2P camera streaming.

    Requires: ``pip install opencastor[webrtc]``
    Falls back with 501 if aiortc is not installed.
    """
    try:
        from castor.stream import handle_webrtc_offer, webrtc_available

        if not webrtc_available():
            raise HTTPException(
                status_code=501,
                detail={
                    "error": "WebRTC not available",
                    "hint": "pip install opencastor[webrtc]",
                },
            )

        camera_index = int(os.getenv("CAMERA_INDEX", "0"))
        ice_servers = None
        if state.config and "network" in state.config:
            ice_servers = state.config["network"].get("ice_servers")

        answer = await handle_webrtc_offer(
            offer_sdp=body.sdp,
            offer_type=body.type,
            camera_index=camera_index,
            ice_servers=ice_servers,
        )
        return answer
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("WebRTC offer error: %s", exc)
        raise HTTPException(status_code=500, detail={"error": str(exc)})


# ---------------------------------------------------------------------------
# Web Wizard /setup endpoint (issue #111)
# ---------------------------------------------------------------------------


@app.get("/setup")
async def setup_wizard():
    """Serve the web-based setup wizard UI."""
    from fastapi.responses import HTMLResponse

    try:
        from castor.web_wizard import _HTML_TEMPLATE

        return HTMLResponse(content=_HTML_TEMPLATE)
    except Exception as exc:
        return HTMLResponse(
            content=f"<html><body><h1>Setup Wizard Error</h1><pre>{exc}</pre></body></html>",
            status_code=500,
        )


class _SetupTestProviderRequest(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None


@app.post("/setup/api/test-provider")
async def setup_test_provider(body: _SetupTestProviderRequest):
    """Test an API key without saving it (used by the web wizard)."""
    try:
        # Temporarily set the key in env for testing
        env_map = {
            "google": "GOOGLE_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "ollama": "OLLAMA_BASE_URL",
        }
        env_var = env_map.get(body.provider.lower())
        if not env_var:
            raise HTTPException(status_code=400, detail={"error": f"Unknown provider: {body.provider}"})

        # Test by importing the provider and calling health_check
        import os as _os

        _os.environ[env_var] = body.api_key
        try:
            from castor.providers import get_provider

            cfg = {"provider": body.provider, env_var.lower(): body.api_key}
            provider = get_provider(cfg)
            health = provider.health_check()
            return {"ok": health.get("ok", False), "latency_ms": health.get("latency_ms"), "error": health.get("error")}
        finally:
            # Do not persist the key in env — caller must save it
            pass
    except HTTPException:
        raise
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class _SetupSaveConfigRequest(BaseModel):
    rcan_yaml: str
    env_vars: Optional[dict] = None


@app.post("/setup/api/save-config")
async def setup_save_config(body: _SetupSaveConfigRequest):
    """Save a generated RCAN config and optional .env vars (web wizard step 4)."""
    try:
        config_path = os.getenv("OPENCASTOR_CONFIG", "robot.rcan.yaml")
        Path(config_path).write_text(body.rcan_yaml)

        if body.env_vars:
            env_path = Path(".env")
            existing = env_path.read_text() if env_path.exists() else ""
            lines = existing.splitlines()
            for key, val in body.env_vars.items():
                updated = False
                for i, line in enumerate(lines):
                    if line.startswith(f"{key}="):
                        lines[i] = f"{key}={val}"
                        updated = True
                        break
                if not updated:
                    lines.append(f"{key}={val}")
            env_path.write_text("\n".join(lines) + "\n")

        return {"ok": True, "config_path": config_path}
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})


@app.on_event("shutdown")
async def on_shutdown():
    # Close WebRTC peers
    try:
        from castor.stream import close_all_peers

        await close_all_peers()
    except Exception:
        pass

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
