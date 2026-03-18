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
import contextlib
import hashlib
import hmac
import logging
import os
import posixpath
import re as _re
import signal
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from castor.api_errors import register_error_handlers
from castor.auth import (
    list_available_channels,
    list_available_providers,
    load_dotenv_if_available,
)
from castor.fs import CastorFS
from castor.secret_provider import get_jwt_secret_provider
from castor.security_posture import publish_attestation
from castor.setup_service import (
    finalize_setup_session,
    generate_setup_config,
    get_setup_catalog,
    get_setup_metrics,
    get_setup_session,
    resolve_provider_env_var,
    resume_setup_session,
    run_remediation,
    save_config_file,
    save_env_vars,
    select_setup_session,
    start_setup_session,
    verify_setup_config,
)
from castor.setup_service import (
    run_preflight as run_setup_preflight,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("OpenCastor.Gateway")


# ---------------------------------------------------------------------------
# App & state
# ---------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def lifespan(app: "FastAPI"):  # noqa: F821
    """FastAPI lifespan context manager (replaces deprecated @app.on_event)."""
    await on_startup()
    yield
    await on_shutdown()


app = FastAPI(
    title="OpenCastor Gateway",
    description="REST API for controlling your robot and receiving messages from channels.",
    version=__import__("importlib.metadata", fromlist=["version"]).version("opencastor"),
    lifespan=lifespan,
)

# CORS: configurable via OPENCASTOR_CORS_ORIGINS env var (comma-separated).
# Defaults to localhost dashboard only. Set OPENCASTOR_CORS_ORIGINS="*" to allow all origins
# (development only — never use "*" in production).
_cors_origins = os.getenv(
    "OPENCASTOR_CORS_ORIGINS",
    "http://localhost:8501,http://127.0.0.1:8501",
).split(",")
_cors_origins_stripped = [o.strip() for o in _cors_origins]
if _cors_origins_stripped == ["*"]:
    logger.warning(
        "CORS is configured to allow all origins (*). "
        "Set OPENCASTOR_CORS_ORIGINS to restrict origins in production."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins_stripped,
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
_command_history: dict[str, list] = collections.defaultdict(list)  # ip -> [timestamps]
_webhook_history: dict[str, list] = collections.defaultdict(list)  # sender_id -> [timestamps]
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
                detail=(
                    f"Webhook rate limit exceeded ({_WEBHOOK_RATE_LIMIT} req/min). Try again later."
                ),
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
    channels: dict[str, object] = {}
    last_thought: Optional[dict] = None
    boot_time: float = time.time()
    fs: Optional[CastorFS] = None
    ruri: Optional[str] = None  # RCAN URI for this robot instance
    mdns_broadcaster = None
    mdns_browser = None
    rcan_router = None  # RCAN message router
    rcan_node_client = None  # rcan.NodeClient (registry connection)
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
    mission_runner = None  # MissionRunner for sequential waypoints (issue #210)
    behavior_runner = None  # BehaviorRunner instance (issue #121)
    behavior_job = None  # Current behavior job dict or None (issue #121)
    personality_registry = None  # PersonalityRegistry singleton (lazy-init)
    slam_mapper = None  # SLAMMapper instance (lazy-init, issue #136)
    thought_log = None  # ThoughtLog instance (F4 — AI accountability)
    hitl_gate_manager = None  # HiTLGateManager instance (F3 — HiTL gates)


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
    if query_token:
        logger.warning(
            "API token supplied via ?token= query parameter — this exposes the token in "
            "server access logs. Use 'Authorization: Bearer <token>' header instead."
        )
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
    if raw_token:
        try:
            from castor.rcan.jwt_auth import RCANTokenManager

            mgr = RCANTokenManager(issuer=state.ruri or "")
            principal = mgr.verify(raw_token)
            request.state.principal = principal
            request.state.jwt_username = getattr(principal, "name", "unknown")
            request.state.jwt_role = "admin"
            request.state.auth_type = "jwt"
            return
        except Exception:
            pass  # Fall through to static token check

    # --- Layer 3: Static API token (constant-time compare to prevent timing attacks) ---
    if API_TOKEN:
        if not hmac.compare_digest(auth.encode(), f"Bearer {API_TOKEN}".encode()):
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
    # Surface/channel context — tells the brain which UI it's responding to.
    # Values: "whatsapp" | "terminal" | "dashboard" | "opencastor_app" | "rcan" | "voice"
    channel: Optional[str] = None
    context: Optional[str] = None  # alias for channel (bridge sends both)
    # v1.6 RCAN fields
    transport: str = "http"  # GAP-17: transport encoding ("http" | "compact")
    media_chunks: list[dict] = []  # GAP-18: multi-modal payload chunks


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


class IntentCreateRequest(BaseModel):
    goal: str
    priority: int = 0
    deadline_ts: Optional[float] = None
    safety_class: str = "normal"
    owner: str = "api"


class IntentPauseRequest(BaseModel):
    intent_id: str
    paused: bool = True


class IntentReprioritizeRequest(BaseModel):
    intent_id: str
    priority: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Health check -- returns OK if the gateway is running (unauthenticated, minimal info)."""
    import castor as _castor_pkg

    return {
        "status": "ok",
        "uptime_s": round(time.time() - state.boot_time, 1),
        "version": _castor_pkg.__version__,
    }


@app.get("/api/health/detail", dependencies=[Depends(verify_token)])
async def health_detail():
    """Authenticated health check with full runtime state (brain, driver, channels)."""
    import castor as _castor_pkg

    return {
        "status": "ok",
        "uptime_s": round(time.time() - state.boot_time, 1),
        "version": _castor_pkg.__version__,
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


class JWTKeyRotateRequest(BaseModel):
    new_secret: Optional[str] = None
    new_kid: Optional[str] = None


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


@app.post("/auth/rotate-key", dependencies=[Depends(verify_token)])
async def auth_rotate_key(req: JWTKeyRotateRequest, request: Request):
    """Rotate JWT signing key and keep one previous verification key."""
    _check_min_role(request, "admin")
    bundle = get_jwt_secret_provider().rotate(new_secret=req.new_secret, new_kid=req.new_kid)
    return {
        "active_kid": bundle.active.kid,
        "previous_kid": bundle.previous.kid if bundle.previous else None,
        "source": bundle.source,
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

    _agent_cfg = (state.config or {}).get("agent", {})
    _brain_primary = (
        {
            "provider": _agent_cfg.get("provider", "unknown"),
            "model": _agent_cfg.get("model", "unknown"),
        }
        if state.config
        else None
    )
    _brain_secondary = (
        [
            {"provider": s.get("provider"), "model": s.get("model"), "tags": s.get("tags", [])}
            for s in _agent_cfg.get("secondary_models", [])
        ]
        if state.config
        else []
    )
    _active_brain_obj = _get_active_brain()
    _brain_active_model = (
        getattr(_active_brain_obj, "model_name", None) if _active_brain_obj else None
    )

    import castor as _castor_pkg

    payload = {
        "config_loaded": state.config is not None,
        "robot_name": (
            state.config.get("metadata", {}).get("robot_name") if state.config else None
        ),
        "ruri": state.ruri,
        "version": _castor_pkg.__version__,
        "providers": list_available_providers(),
        "channels_available": list_available_channels(),
        "channels_active": list(state.channels.keys()),
        "last_thought": state.last_thought,
        "audit_log_path": str(DEFAULT_AUDIT_LOG_PATH.expanduser()),
        "brain_primary": _brain_primary,
        "brain_secondary": _brain_secondary,
        "brain_active_model": _brain_active_model,
        "speaking": getattr(getattr(state, "speaker", None), "is_speaking", False),
        "caption": getattr(getattr(state, "speaker", None), "current_caption", ""),
        "rcan_bridge": state.rcan_node_client is not None,
    }

    if state.fs:
        payload["security_posture"] = state.fs.ns.read("/proc/safety")

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

    # Camera model + mode
    _cam_obj = getattr(state, "camera", None)
    payload["camera_model"] = getattr(_cam_obj, "model", "unknown") if _cam_obj else "unknown"
    payload["camera_mode"] = (
        getattr(_cam_obj, "composite_mode", "primary_only") if _cam_obj else "primary_only"
    )

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
    # Resolve surface from channel/context fields (bridge sends "opencastor_app")
    _surface = cmd.channel or cmd.context or "whatsapp"
    _think_t0 = time.perf_counter()

    # ── Agent Harness (when enabled in RCAN config) ──────────────────────────
    _agent_cfg = state.fs.config.get("agent", {}) if state.fs else {}
    _harness_cfg = _agent_cfg.get("harness", {})
    _harness_enabled = _harness_cfg.get("enabled", False)  # opt-in

    if _harness_enabled:
        try:
            from castor.harness import AgentHarness, HarnessContext
            from castor.tools import ToolRegistry

            _harness = getattr(state, "_harness", None)
            if _harness is None:
                _tool_reg = getattr(state, "tool_registry", None) or ToolRegistry(_agent_cfg)
                _harness = AgentHarness(
                    provider=active,
                    config=_agent_cfg,
                    tool_registry=_tool_reg,
                )
                state._harness = _harness  # type: ignore[attr-defined]

            _hctx = HarnessContext(
                instruction=cmd.instruction,
                image_bytes=image_bytes,
                surface=_surface,
                scope=getattr(cmd, "scope", "chat") or "chat",
                consent_granted=getattr(cmd, "consent_granted", False) or False,
            )
            _hresult = await _harness.run(_hctx)
            thought = _hresult.thought
            _provider_name = getattr(active, "model_name", None) or "unknown"
            _think_ms = _hresult.total_latency_ms
        except Exception as _harness_exc:
            logger.warning("Harness error, falling back to direct think(): %s", _harness_exc)
            _harness_enabled = False  # fall through to legacy path

    if not _harness_enabled:
        # ── Legacy single-shot path ──────────────────────────────────────────
        try:
            thought = active.think(image_bytes, cmd.instruction, surface=_surface)
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
        finally:
            _think_ms = round((time.perf_counter() - _think_t0) * 1000, 1)
            _provider_name = getattr(active, "model_name", None) or "unknown"
            from castor.metrics import get_registry as _get_reg

            _get_reg().record_provider_latency(_provider_name, _think_ms)

    _think_ms = round(
        getattr(_think_ms, "real", _think_ms) if isinstance(_think_ms, complex) else _think_ms, 1
    )
    _provider_name = getattr(active, "model_name", None) or "unknown"
    from castor.metrics import get_registry as _get_reg

    _get_reg().record_provider_latency(_provider_name, _think_ms)

    logger.info(
        "Brain replied via %s in %.0f ms (harness=%s)", _provider_name, _think_ms, _harness_enabled
    )
    _record_thought(cmd.instruction, thought.raw_text, thought.action)

    # Execute action on hardware if available
    if thought.action and state.driver:
        _execute_action(thought.action)

    return {
        "raw_text": _strip_action_json(thought.raw_text),
        "action": thought.action,
        "model_used": _provider_name,
        "harness": _harness_enabled,
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
            reason = state.fs.last_write_denial or "Unknown safety layer rejection."
            raise HTTPException(
                status_code=422,
                detail=f"Action rejected by safety layer: {reason}",
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


@app.get("/api/fs/estop", dependencies=[Depends(verify_token)])
async def get_estop_status():
    """GET /api/fs/estop — Return current emergency stop state.

    Returns:
        estopped: True if e-stop is active (motor writes blocked).
        proc_status: Current /proc/status value (active, estop, idle, ...).
        last_denial: Reason for the most recent safety layer write rejection.
    """
    if state.fs:
        return {
            "estopped": state.fs.is_estopped,
            "proc_status": state.fs.read("/proc/status", principal="api") or "unknown",
            "last_denial": state.fs.last_write_denial,
        }
    return {"estopped": False, "proc_status": "no_fs", "last_denial": ""}


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


@app.post("/api/system/reboot", dependencies=[Depends(verify_token)])
async def system_reboot(request: Request):
    """Reboot the host machine. Requires admin role."""
    import subprocess

    _check_min_role(request, "admin")
    subprocess.Popen(["sudo", "reboot"])
    return {"status": "rebooting"}


@app.post("/api/system/shutdown", dependencies=[Depends(verify_token)])
async def system_shutdown(request: Request):
    """Shut down the host machine. Requires admin role."""
    import subprocess

    _check_min_role(request, "admin")
    subprocess.Popen(["sudo", "shutdown", "-h", "now"])
    return {"status": "shutting_down"}


@app.post("/api/system/upgrade", dependencies=[Depends(verify_token)])
async def system_upgrade(request: Request):
    """Upgrade the opencastor package via pip. Requires admin role.

    Body (optional JSON): {"version": "2026.3.17.13"}
    If version is omitted, upgrades to the latest PyPI release.

    Returns immediately with status="upgrading" and runs pip in the background.
    Poll GET /api/status for the updated version after ~30s.
    """
    import subprocess
    import sys

    _check_min_role(request, "admin")

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    version: str | None = body.get("version")
    pkg = f"opencastor=={version}" if version else "opencastor"

    # Run pip non-blocking; stdout/stderr captured to a temp log
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--break-system-packages",
        pkg,
    ]
    logger.info("system_upgrade: running %s", " ".join(cmd))
    subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    return {
        "status": "upgrading",
        "package": pkg,
        "note": "Poll /api/status in ~30s to confirm new version",
    }


@app.get("/api/intents", dependencies=[Depends(verify_token)])
async def list_intents(request: Request):
    """List active and queued orchestration intents."""
    _check_min_role(request, "viewer")
    orchestrator = _get_orchestrator()
    if orchestrator is None:
        return {"intents": [], "current_intent": None, "enabled": False}
    intents = orchestrator.list_intents()
    current = orchestrator.get_status().get("current_intent")
    return {"intents": intents, "current_intent": current, "enabled": True}


@app.post("/api/intents", dependencies=[Depends(verify_token)])
async def create_intent(req: IntentCreateRequest, request: Request):
    """Create an orchestration intent in the orchestrator queue."""
    _check_min_role(request, "operator")
    orchestrator = _get_orchestrator()
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    created = orchestrator.submit_intent(
        goal=req.goal,
        priority=req.priority,
        deadline_ts=req.deadline_ts,
        safety_class=req.safety_class,
        owner=req.owner,
    )
    return created


@app.post("/api/intents/pause", dependencies=[Depends(verify_token)])
async def pause_intent(req: IntentPauseRequest, request: Request):
    """Pause or resume an intent."""
    _check_min_role(request, "operator")
    orchestrator = _get_orchestrator()
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    if not orchestrator.pause_intent(req.intent_id, paused=req.paused):
        raise HTTPException(status_code=404, detail="Intent not found")
    return {"ok": True, "intent_id": req.intent_id, "paused": req.paused}


@app.post("/api/intents/reprioritize", dependencies=[Depends(verify_token)])
async def reprioritize_intent(req: IntentReprioritizeRequest, request: Request):
    """Reprioritize an existing intent."""
    _check_min_role(request, "operator")
    orchestrator = _get_orchestrator()
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    if not orchestrator.reprioritize_intent(req.intent_id, priority=req.priority):
        raise HTTPException(status_code=404, detail="Intent not found")
    return {"ok": True, "intent_id": req.intent_id, "priority": req.priority}


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint  (issue #99)
# ---------------------------------------------------------------------------


@app.get("/api/metrics", dependencies=[Depends(verify_token)])
async def get_metrics():
    """Prometheus text exposition format metrics (auth required — exposes provider/model info)."""
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

        # Record config version before applying (issue #146)
        if state.config is not None:
            from castor.config_history import get_history

            get_history().record(state.config, config_path=config_path)

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


@app.get("/api/config/history", dependencies=[Depends(verify_token)])
async def config_history(request: Request):
    """GET /api/config/history — List versioned config snapshots (newest first)."""
    _check_min_role(request, "operator")
    from castor.config_history import get_history

    return {"versions": get_history().list()}


class _ConfigRollbackRequest(BaseModel):
    version_id: str


@app.post("/api/config/rollback", dependencies=[Depends(verify_token)])
async def config_rollback(req: _ConfigRollbackRequest, request: Request):
    """POST /api/config/rollback — Restore a config version and reload.

    Body: ``{"version_id": "<version_id>"}``
    """
    _check_min_role(request, "admin")
    config_path = os.getenv("OPENCASTOR_CONFIG", "robot.rcan.yaml")
    from castor.config_history import get_history

    try:
        restored = get_history().rollback(req.version_id, config_path=config_path)
        state.config = restored
        if state.brain and "agent" in restored:
            new_model = restored["agent"].get("model")
            if new_model:
                state.brain.model_name = new_model
        return {"status": "rolled_back", "version_id": req.version_id, "config_path": config_path}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Write failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Provider health detail endpoint  (issue #95)
# ---------------------------------------------------------------------------


@app.get("/api/provider/health", dependencies=[Depends(verify_token)])
async def provider_health():
    """Detailed provider health check including latency, streaming latency, and token usage."""
    if state.brain is None:
        raise HTTPException(status_code=503, detail="Brain not initialized")

    try:
        health = await asyncio.to_thread(state.brain.health_check)
    except Exception as exc:
        health = {"ok": False, "error": str(exc)}

    # Measure streaming latency: time-to-first-token (#275)
    stream_latency_ms: Optional[float] = None
    try:
        import time as _time

        active_brain = _get_active_brain()
        if active_brain and hasattr(active_brain, "think_stream"):
            t0 = _time.perf_counter()
            gen = active_brain.think_stream(b"", "Reply with one word: ok")
            next(gen, None)  # first token
            stream_latency_ms = round((_time.perf_counter() - t0) * 1000, 1)
    except Exception:
        pass

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
        "stream_latency_ms": stream_latency_ms,
        "usage": usage,
    }


@app.get("/api/interpreter/status", dependencies=[Depends(verify_token)])
async def interpreter_status():
    """GET /api/interpreter/status — Return EmbeddingInterpreter status."""
    _disabled = {
        "enabled": False,
        "backend": "none",
        "dimensions": 0,
        "episode_count": 0,
        "last_goal_similarity": None,
        "escalations_session": 0,
        "avg_latency_ms": None,
        "recent_episodes": [],
    }
    if state.brain is None:
        return _disabled
    brain = state.brain
    interp = getattr(brain, "interpreter", None)
    if interp is None:
        return _disabled
    try:
        return interp.status()
    except Exception as exc:
        return {**_disabled, "error": str(exc)}


@app.get("/api/pool/health", dependencies=[Depends(verify_token)])
async def pool_health():
    """GET /api/pool/health — Health, circuit breaker, adaptive and replay state for ProviderPool.

    Returns ``{"error": "..."}`` with status 200 when brain is not a ProviderPool.
    """
    from castor.providers.pool_provider import ProviderPool

    brain = state.brain
    if brain is None:
        return {"error": "Brain not initialized"}
    if not isinstance(brain, ProviderPool):
        return {"error": "Brain is not a ProviderPool"}
    try:
        return await asyncio.to_thread(brain.health_check)
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Episode memory endpoints  (issue #92)
# ---------------------------------------------------------------------------


@app.get("/api/memory/episodes", dependencies=[Depends(verify_token)])
async def list_episodes(
    limit: int = 50,
    source: Optional[str] = None,
    tags: Optional[str] = None,
):
    """List recent brain-decision episodes from the SQLite memory store.

    Query params:
        limit:  Max episodes to return (default 50, max 500).
        source: Filter by episode source (loop, api, whatsapp, …).
        tags:   Comma-separated list of tags to filter by (ALL must match).
    """
    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    episodes = mem.query_recent(limit=min(limit, 500), source=source, tags=tag_list)
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


@app.get("/api/memory/search", dependencies=[Depends(verify_token)])
async def memory_search(
    q: str,
    limit: int = 10,
    mode: str = "keyword",
    tags: Optional[str] = None,
):
    """GET /api/memory/search — Search episodes by keyword or semantic similarity.

    Query params:
        q:     Search query text.
        limit: Max results (default 10, max 100).
        mode:  "keyword" (SQL LIKE across instruction/thought/action, default) or
               "semantic" (cosine similarity via SentenceTransformers).
        tags:  Comma-separated list of tags to filter results by (ALL must match).
    """
    if not q.strip():
        raise HTTPException(status_code=422, detail="Query 'q' must not be empty")
    cap = min(limit, 100)
    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    results = mem.search(q, limit=cap, mode=mode, tags=tag_list)
    return {"query": q, "mode": mode, "results": results, "count": len(results)}


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
    provider = get_jwt_secret_provider()
    # Re-read env/file-backed keys so runtime config changes are reflected.
    provider.invalidate()
    bundle = provider.get_bundle()
    if not bundle.active.secret or bundle.source == "ephemeral":
        raise HTTPException(
            status_code=501,
            detail="JWT not configured. Set OPENCASTOR_JWT_SECRET.",
        )
    try:
        from castor.rcan.jwt_auth import RCANTokenManager
        from castor.rcan.rbac import RCANRole

        mgr = RCANTokenManager(issuer=state.ruri or "")
        role = RCANRole[req.role.upper()]
        token = mgr.issue(
            subject=req.subject,
            role=role,
            scopes=req.scopes,
            ttl_seconds=req.ttl_seconds,
        )
        return {"token": token, "expires_in": req.ttl_seconds, "kid": bundle.active.kid}
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
    """Expose the WorkAuthority audit log.

    Events: requested, approved, denied, executed, revoked.
    """
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

    # Fail fast when camera is offline so the browser img.onerror fires immediately
    # instead of hanging on a silent 200 stream with no frames.
    if state.camera is None or not state.camera.is_available():
        raise HTTPException(status_code=503, detail="Camera offline")

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


@app.get("/api/v1/transports")
async def get_transports():
    """GET /api/v1/transports — Return supported RCAN transport encodings (v1.6).

    Returns:
        JSON dict with ``supported`` list and ``preferred`` transport name.
    """
    return {"supported": ["http", "compact"], "preferred": "http"}


# v1.6: In-memory media chunk cache (5-minute TTL)
_media_cache: dict[str, tuple[dict, float]] = {}
_MEDIA_TTL_S: float = 300.0


@app.get("/api/v1/media/{chunk_id}")
async def get_media_chunk(chunk_id: str):
    """GET /api/v1/media/{chunk_id} — Serve a stored media chunk (v1.6 multi-modal).

    Currently returns 404 — media storage not yet implemented (stub for v1.6).
    """
    # Check in-memory cache with TTL
    entry = _media_cache.get(chunk_id)
    if entry is not None:
        chunk_data, stored_at = entry
        if time.time() - stored_at < _MEDIA_TTL_S:
            return JSONResponse(chunk_data)
        else:
            del _media_cache[chunk_id]

    raise HTTPException(status_code=404, detail="media storage not yet implemented")


@app.get("/api/rcan/peers", dependencies=[Depends(verify_token)])
async def get_peers():
    """List discovered RCAN peers on the local network."""
    if state.mdns_browser:
        return {"peers": list(state.mdns_browser.peers.values())}
    return {"peers": [], "note": "mDNS not enabled"}


@app.post("/api/rcan/message")
async def rcan_receive_message(request: Request):
    """Receive an inbound RCAN message from a remote robot (federation endpoint).

    This endpoint is intentionally unauthenticated so peer robots can reach it
    without sharing API tokens.  It accepts both the OpenCastor internal dict
    format and the RCAN v1.2 spec format.

    For outbound sends, use ``castor.rcan.http_transport.send_message()``.
    """
    import castor as _castor_pkg

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    if not data:
        return JSONResponse({"error": "empty body"}, status_code=400)

    # Determine message type and source from either format
    msg_type = data.get("type") or data.get("msg_type")
    source = data.get("source") or data.get("source_ruri", "unknown")

    logger.info("RCAN inbound from %s: type=%s", source, msg_type)

    # If the RCAN router is available, dispatch through it
    if state.rcan_router:
        try:
            from castor.rcan.sdk_bridge import parse_inbound

            msg = parse_inbound(data)
            principal = None
            response = state.rcan_router.route(msg, principal)
            return response.to_dict()
        except Exception as e:
            logger.warning("RCAN router dispatch failed, returning lightweight ack: %s", e)

    # Lightweight ack when router not available (e.g. API started standalone)
    response_payload: dict = {"status": "received", "type": msg_type, "source": source}

    # MessageType.DISCOVER = 1 — respond with capabilities
    if msg_type == 1:
        response_payload["capabilities"] = ["status", "teleop", "safety", "registry"]
        response_payload["ruri"] = state.ruri or "rcan://opencastor.unknown.00000000"
        # v1.6 DISCOVER fields
        response_payload["supported_transports"] = ["http", "compact"]
        response_payload["rcan_version"] = "1.6"
        response_payload["loa_enforcement"] = False
        response_payload["min_loa_for_control"] = 1
        response_payload["federation_enabled"] = False

    # MessageType.STATUS = 2 — respond with robot info
    elif msg_type == 2:
        response_payload["robot_name"] = (state.config or {}).get("robot_name", "opencastor")
        response_payload["version"] = _castor_pkg.__version__
        response_payload["rcan_version"] = "1.6"
        response_payload["ruri"] = state.ruri

    return JSONResponse(response_payload, status_code=200)


# ---------------------------------------------------------------------------
# RCAN Protocol endpoints
# ---------------------------------------------------------------------------
@app.post("/rcan", dependencies=[Depends(verify_token)])
async def rcan_message_endpoint(request: Request):
    """Unified RCAN message endpoint.

    Accepts two formats:
      - **RCAN v1.2 spec format** (rcan-py SDK): ``{"rcan": "1.2", "cmd": ..., "target": "rcan://...", ...}``
      - **OpenCastor internal format**: ``{"msg_type": 3, "source": ..., ...}``

    Spec-format messages are bridged to OpenCastor's router transparently.
    """
    if not state.rcan_router:
        raise HTTPException(status_code=501, detail="RCAN router not initialized")

    body = await request.json()
    try:
        from castor.rcan.sdk_bridge import parse_inbound, spec_message_to_opencastor

        parsed = parse_inbound(body)

        # Spec format → bridge to OpenCastor message
        try:
            from rcan import RCANMessage as SpecMsg

            if isinstance(parsed, SpecMsg):
                msg = spec_message_to_opencastor(parsed)
            else:
                msg = parsed
        except ImportError:
            msg = parsed

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

        response: dict[str, Any] = {"episode_id": episode.id, "saved": True}

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
    result = voice_mod.transcribe_bytes(audio_bytes, hint_format=hint, engine=engine)
    duration_ms = round((_time.time() - t0) * 1000, 1)

    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Transcription failed — audio may be inaudible or in an unsupported format",
        )

    # transcribe_bytes() returns a dict {text, confidence, engine} or (legacy) a bare string
    if isinstance(result, dict):
        text = result.get("text", "")
        confidence = result.get("confidence", 0.5)
        resolved_engine = result.get("engine", engine)
    else:
        text = result
        confidence = 0.5
        resolved_engine = engine if engine != "auto" else (available[0] if available else "unknown")

    return {
        "text": text,
        "confidence": confidence,
        "engine": resolved_engine,
        "duration_ms": duration_ms,
    }


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


async def _build_telemetry_payload() -> dict:
    """Assemble one telemetry frame dict for ws_telemetry.

    Extracted from _push_loop to reduce cyclomatic complexity of ws_telemetry.
    Uses the module-level ``state`` object.
    """
    from castor.depth import get_obstacle_zones
    from castor.main import get_shared_camera

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

    return {
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
        while True:
            try:
                frame = await _build_telemetry_payload()
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


@app.get("/api/voice/devices", dependencies=[Depends(verify_token)])
async def voice_devices():
    """Return available audio input devices on the server.

    Returns:
        200: {"devices": [{"index": N, "name": "...", "default": bool}, ...]}
    """
    from castor.voice import list_audio_input_devices

    devices = await asyncio.to_thread(list_audio_input_devices)
    return {"devices": devices}


@app.post("/api/voice/listen", dependencies=[Depends(verify_token)])
async def voice_listen():
    """Capture one microphone phrase and return transcript + brain thought.

    Returns:
        200: {"transcript": "...", "thought": {...}}
        503: {"error": "..."} if listener is not available
    """
    if state.listener is None:
        raise HTTPException(status_code=503, detail="STT listener not initialized")
    if not state.listener.enabled:
        raise HTTPException(status_code=503, detail="no audio input device")

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
# Mission planner endpoints (issue #210)
# ---------------------------------------------------------------------------


class _MissionWaypoint(BaseModel):
    distance_m: float
    heading_deg: float = 0.0
    speed: float = 0.6
    dwell_s: float = 0.0
    label: Optional[str] = None


class _MissionRequest(BaseModel):
    waypoints: list[_MissionWaypoint]
    loop: bool = False


@app.post("/api/nav/mission", dependencies=[Depends(verify_token)])
async def nav_mission_start(body: _MissionRequest):
    """Start a sequential waypoint mission in the background.

    Cancels any currently running mission first.

    Body::

        {
          "waypoints": [
            {"distance_m": 0.5, "heading_deg": 0, "speed": 0.6},
            {"distance_m": 0.3, "heading_deg": 90, "dwell_s": 1.0},
            {"distance_m": 0.5, "heading_deg": 180}
          ],
          "loop": false
        }

    Returns:
        200: {"job_id": "...", "running": true, "total": N}
        400: if waypoints list is empty
        503: if no driver is loaded
    """
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No driver loaded")
    if not body.waypoints:
        raise HTTPException(status_code=400, detail="waypoints list must not be empty")

    from castor.mission import MissionRunner

    if state.mission_runner is None:
        state.mission_runner = MissionRunner(state.driver, state.config or {})

    waypoints = [wp.model_dump() for wp in body.waypoints]
    job_id = await asyncio.to_thread(state.mission_runner.start, waypoints, loop=body.loop)
    return {"job_id": job_id, "running": True, "total": len(waypoints)}


@app.get("/api/nav/mission", dependencies=[Depends(verify_token)])
async def nav_mission_status():
    """Return current (or last) mission status.

    Returns:
        200: {running, job_id, step, total, loop, loop_count, results, error}
    """
    if state.mission_runner is None:
        return {
            "running": False,
            "job_id": None,
            "step": 0,
            "total": 0,
            "loop": False,
            "loop_count": 0,
            "results": [],
            "error": None,
        }
    return state.mission_runner.status()


@app.post("/api/nav/mission/stop", dependencies=[Depends(verify_token)])
async def nav_mission_stop():
    """Cancel the running mission and stop the robot.

    Returns:
        200: {"ok": true, "was_running": bool}
    """
    was_running = False
    if state.mission_runner is not None:
        was_running = state.mission_runner.status().get("running", False)
        await asyncio.to_thread(state.mission_runner.stop)
    elif state.driver is not None:
        state.driver.stop()
    return {"ok": True, "was_running": was_running}


# ---------------------------------------------------------------------------
# Mission generator endpoint (issue #234)
# ---------------------------------------------------------------------------


class _MissionGenerateRequest(BaseModel):
    description: str
    steps_hint: int = 3
    loop: bool = False
    execute: bool = False  # if True, immediately start the generated mission


@app.post("/api/nav/mission/generate", dependencies=[Depends(verify_token)])
async def nav_mission_generate(req: _MissionGenerateRequest):
    """Generate a waypoint mission from a natural language description.

    Uses the active LLM brain to produce a structured list of waypoints that
    can be immediately passed to ``POST /api/nav/mission``.

    Body (JSON)::

        {"description": "circle the room", "steps_hint": 4, "loop": false}

    Returns:
        200: ``{"waypoints": [...], "loop": bool, "job_id": str|null}``
             where each waypoint has keys: ``distance_m``, ``heading_deg``,
             ``speed``, ``dwell_s``, ``label``.
    """
    import json as _json

    brain = _get_active_brain()
    if brain is None:
        raise HTTPException(status_code=503, detail="No AI brain configured")
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No hardware driver configured")

    prompt = (
        f"Generate a robot waypoint mission with exactly {req.steps_hint} waypoints for: "
        f"{req.description}. "
        "Output ONLY a valid JSON array — no explanation, no markdown fences. "
        "Each element must have these keys: "
        '"distance_m" (float, metres to drive, negative = reverse), '
        '"heading_deg" (float, relative turn in degrees before driving, 0 = straight), '
        '"speed" (float 0.0–1.0), '
        '"dwell_s" (float, pause after waypoint in seconds), '
        '"label" (short string). '
        "Example: "
        '[{"distance_m":0.5,"heading_deg":0,"speed":0.6,"dwell_s":0,"label":"forward"},'
        '{"distance_m":0.3,"heading_deg":90,"speed":0.5,"dwell_s":1.0,"label":"turn right"}]'
    )

    try:
        thought = await asyncio.to_thread(brain.think, b"", prompt)
        raw = thought.raw_text.strip()

        # Strip markdown fences if the model wraps the JSON
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(line for line in lines if not line.startswith("```")).strip()

        waypoints = _json.loads(raw)
        if not isinstance(waypoints, list) or len(waypoints) == 0:
            raise ValueError("Brain did not return a JSON array of waypoints")

        # Normalise each waypoint — fill defaults and coerce types
        normalised = []
        for i, wp in enumerate(waypoints):
            normalised.append(
                {
                    "distance_m": float(wp.get("distance_m", 0.5)),
                    "heading_deg": float(wp.get("heading_deg", 0.0)),
                    "speed": float(wp.get("speed", 0.6)),
                    "dwell_s": float(wp.get("dwell_s", 0.0)),
                    "label": str(wp.get("label", f"step-{i + 1}")),
                }
            )

        job_id = None
        if req.execute:
            if state.mission_runner is None:
                from castor.mission import MissionRunner

                state.mission_runner = MissionRunner(state.driver, state.config or {})
            job_id = await asyncio.to_thread(state.mission_runner.start, normalised, loop=req.loop)

        return {"waypoints": normalised, "loop": req.loop, "job_id": job_id}

    except (_json.JSONDecodeError, ValueError) as exc:
        logger.warning("Mission generation parse error: %s", exc)
        raise HTTPException(
            status_code=422,
            detail=f"Brain output could not be parsed as waypoints: {exc}",
        ) from exc
    except Exception as exc:
        logger.error("Mission generation error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    """Return whether a behavior job is running (unauthenticated, no internal details).

    Returns:
        200: ``{"running": bool}``
    """
    running = False if state.behavior_job is None else state.behavior_job.get("running", False)
    return {"running": running}


@app.get("/api/behavior/status/detail", dependencies=[Depends(verify_token)])
async def behavior_status_detail():
    """Return full behavior job state including name and job_id (authenticated).

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
# Behavior generation (#128 — natural language → YAML behavior)
# ---------------------------------------------------------------------------


class _BehaviorGenerateRequest(BaseModel):
    description: str
    steps_hint: int = 5


@app.post("/api/behavior/generate", dependencies=[Depends(verify_token)])
async def behavior_generate(req: _BehaviorGenerateRequest):
    """Generate a YAML behavior file from a natural language description.

    Uses the active LLM brain to produce a structured YAML behavior that can
    be immediately loaded and executed via ``POST /api/behavior/run``.

    Returns:
        200: ``{"behavior_yaml": str, "behavior_name": str, "saved_path": str}``
    """
    brain = _get_active_brain()
    if brain is None:
        raise HTTPException(status_code=503, detail="No AI brain configured")

    prompt = (
        f"Generate a robot behavior YAML with exactly {req.steps_hint} steps for: "
        f"{req.description}. "
        "Output ONLY valid YAML with this structure:\n"
        "behavior_name: snake_case_name\n"
        "steps:\n"
        "  - action: forward|backward|turn_left|turn_right|stop|wait|speak\n"
        "    duration: 2.0\n"
        "    speed: 0.5\n"
        "    text: 'optional speech text'\n"
        "Do not include any explanation, just the YAML."
    )

    try:
        import tempfile

        thought = brain.think(b"", prompt)
        yaml_text = thought.raw_text.strip()
        # Strip markdown fences if present
        if yaml_text.startswith("```"):
            lines = yaml_text.splitlines()
            yaml_text = "\n".join(line for line in lines if not line.startswith("```")).strip()

        tmp = tempfile.NamedTemporaryFile(
            suffix=".yaml",
            prefix="generated_behavior_",
            dir="/tmp",
            delete=False,
            mode="w",
        )
        tmp.write(yaml_text)
        tmp.close()
        saved_path = tmp.name

        behavior_name = "generated_behavior"
        for line in yaml_text.splitlines():
            if line.startswith("behavior_name:"):
                behavior_name = line.split(":", 1)[1].strip().strip("'\"")
                break

        return {
            "behavior_yaml": yaml_text,
            "behavior_name": behavior_name,
            "saved_path": saved_path,
        }
    except Exception as exc:
        logger.error("Behavior generation error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Mission replay endpoint (issue #243)
# ---------------------------------------------------------------------------


@app.post("/api/nav/mission/replay/{job_id}", dependencies=[Depends(verify_token)])
async def nav_mission_replay(job_id: str):
    """Re-run a previously executed mission identified by *job_id*.

    Looks up the waypoints stored when the original mission was launched and
    starts a new mission with the same waypoints and loop setting.

    Returns:
        200: ``{"ok": true, "new_job_id": str, "waypoints": int}``
        404: job_id not found in history
        503: no driver configured
    """
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No hardware driver configured")

    if state.mission_runner is None:
        from castor.mission import MissionRunner

        state.mission_runner = MissionRunner(state.driver, state.config or {})

    waypoints = state.mission_runner.get_waypoints(job_id)
    if waypoints is None:
        raise HTTPException(status_code=404, detail=f"Mission job_id not found: {job_id}")

    loop = state.mission_runner._history.get(job_id, {}).get("loop", False)
    new_job_id = await asyncio.to_thread(state.mission_runner.start, waypoints, loop=loop)
    logger.info(
        "Mission replay: %s → %s (%d waypoints)", job_id[:8], new_job_id[:8], len(waypoints)
    )
    return {"ok": True, "new_job_id": new_job_id, "waypoints": len(waypoints)}


@app.get("/api/nav/mission/history", dependencies=[Depends(verify_token)])
async def nav_mission_history():
    """Return a list of past mission summaries (job_id, waypoint count, loop flag).

    Returns:
        200: ``{"history": [{job_id, total, loop}, ...]}``
    """
    if state.mission_runner is None:
        return {"history": []}
    return {"history": state.mission_runner.list_history()}


# ---------------------------------------------------------------------------
# Mission position + geo-fence endpoints (issue #249)
# ---------------------------------------------------------------------------


@app.get("/api/nav/mission/position", dependencies=[Depends(verify_token)])
async def nav_mission_position():
    """Return the current dead-reckoning position of the robot.

    Position is accumulated across waypoints using wheel-odometry physics from
    the ``physics`` block in the RCAN config.  Resets to (0, 0) when a new
    mission starts.

    Returns:
        200: ``{"x_m": float, "y_m": float, "heading_deg": float}``
    """
    if state.mission_runner is None:
        return {"x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0}
    return state.mission_runner.position()


class _GeofenceRequest(BaseModel):
    x_min: float
    x_max: float
    y_min: float
    y_max: float


@app.post("/api/nav/mission/geofence", dependencies=[Depends(verify_token)])
async def nav_mission_set_geofence(req: _GeofenceRequest):
    """Set a rectangular geo-fence boundary.

    Any running or future mission will abort if the robot's dead-reckoning
    position leaves this bounding box.

    Body (JSON)::

        {"x_min": -2.0, "x_max": 2.0, "y_min": -2.0, "y_max": 2.0}

    Returns:
        200: ``{"ok": true, "geofence": {x_min, x_max, y_min, y_max}}``
    """
    if req.x_min >= req.x_max or req.y_min >= req.y_max:
        raise HTTPException(
            status_code=422,
            detail="x_min must be < x_max and y_min must be < y_max",
        )
    bounds = {"x_min": req.x_min, "x_max": req.x_max, "y_min": req.y_min, "y_max": req.y_max}
    if state.mission_runner is None:
        from castor.mission import MissionRunner

        state.mission_runner = MissionRunner(state.driver, state.config or {})
    state.mission_runner.set_geofence(bounds)
    return {"ok": True, "geofence": bounds}


@app.delete("/api/nav/mission/geofence", dependencies=[Depends(verify_token)])
async def nav_mission_clear_geofence():
    """Clear the geo-fence (disable boundary enforcement).

    Returns:
        200: ``{"ok": true, "geofence": null}``
    """
    if state.mission_runner is not None:
        state.mission_runner.set_geofence(None)
    return {"ok": True, "geofence": None}


# ---------------------------------------------------------------------------
# Arduino sensor WebSocket stream (issue #248)
# ---------------------------------------------------------------------------


@app.websocket("/ws/arduino/sensors")
async def ws_arduino_sensors(websocket: WebSocket, token: str = ""):
    """WebSocket endpoint that streams Arduino sensor readings at a configurable rate.

    Query parameters:
        ``?token=<api_token>``            — required when API_TOKEN is set
        ``?sensor_ids=hcsr04,dht22``      — comma-separated sensor IDs to poll
        ``?rate_hz=5``                    — push rate in Hz (1–20, default 5)

    Pushed frame schema::

        {
            "ts":      float,             # Unix timestamp
            "sensors": {id: data, ...},   # per-sensor readings (None if unavailable)
        }

    Requires an ``ArduinoSerialDriver`` as the active driver.
    """
    if API_TOKEN and token != API_TOKEN:
        await websocket.close(code=1008)
        return

    if state.driver is None or not hasattr(state.driver, "query_sensor"):
        await websocket.close(code=1011)
        return

    params = websocket.query_params
    raw_ids = params.get("sensor_ids", "hcsr04")
    sensor_ids = [s.strip() for s in raw_ids.split(",") if s.strip()]
    try:
        rate_hz = max(0.1, min(20.0, float(params.get("rate_hz", "5"))))
    except ValueError:
        rate_hz = 5.0
    interval = 1.0 / rate_hz

    await websocket.accept()
    logger.debug("WS arduino/sensors connected: sensors=%s rate=%.1fHz", sensor_ids, rate_hz)

    try:
        while True:
            ts = time.time()
            readings = {}
            for sid in sensor_ids:
                try:
                    readings[sid] = await asyncio.to_thread(state.driver.query_sensor, sid)
                except Exception:
                    readings[sid] = None
            try:
                await websocket.send_json({"ts": ts, "sensors": readings})
            except WebSocketDisconnect:
                break
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WS arduino/sensors error: %s", exc)
    logger.debug("WS arduino/sensors disconnected")


# ---------------------------------------------------------------------------
# Arduino sensor + servo endpoints (issues #242, #244)
# ---------------------------------------------------------------------------


@app.get("/api/arduino/sensor/{sensor_id}", dependencies=[Depends(verify_token)])
async def arduino_sensor_read(sensor_id: str):
    """Query an Arduino-attached sensor by its ID (e.g. ``hcsr04``).

    Requires an ``ArduinoSerialDriver`` (protocol ``arduino_serial_json``) as
    the active driver.  In mock mode the driver returns ``None`` and this
    endpoint returns ``{"available": false}``.

    Returns:
        200: sensor data dict from the Arduino, or ``{"available": false}``
        503: no driver, or driver does not support ``query_sensor()``
    """
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No hardware driver configured")
    if not hasattr(state.driver, "query_sensor"):
        raise HTTPException(
            status_code=503,
            detail="Active driver does not support sensor queries (requires ArduinoSerialDriver)",
        )
    result = await asyncio.to_thread(state.driver.query_sensor, sensor_id)
    if result is None:
        return {"available": False, "sensor_id": sensor_id}
    return {"available": True, "sensor_id": sensor_id, "data": result}


class _ServoRequest(BaseModel):
    pin: int
    angle: int  # 0–180 degrees


@app.post("/api/arduino/servo", dependencies=[Depends(verify_token)])
async def arduino_servo_set(req: _ServoRequest):
    """Set an Arduino servo to a given angle (0–180 degrees).

    Requires an ``ArduinoSerialDriver`` as the active driver.

    Body (JSON)::

        {"pin": 9, "angle": 90}

    Returns:
        200: ``{"ok": true, "pin": int, "angle": int, "response": dict|null}``
        422: angle out of range
        503: no driver, or driver does not support ``set_servo()``
    """
    if not (0 <= req.angle <= 180):
        raise HTTPException(status_code=422, detail=f"Angle must be 0–180, got {req.angle}")
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No hardware driver configured")
    if not hasattr(state.driver, "set_servo"):
        raise HTTPException(
            status_code=503,
            detail="Active driver does not support servo control (requires ArduinoSerialDriver)",
        )
    response = await asyncio.to_thread(state.driver.set_servo, req.pin, req.angle)
    return {"ok": True, "pin": req.pin, "angle": req.angle, "response": response}


# ---------------------------------------------------------------------------
# Outbound webhook management (#125)
# ---------------------------------------------------------------------------


class _WebhookAddRequest(BaseModel):
    url: str
    events: Optional[list[str]] = None
    secret: Optional[str] = None
    timeout_s: int = 5
    retry: int = 1


class _WebhookDeleteRequest(BaseModel):
    url: str


@app.get("/api/webhooks", dependencies=[Depends(verify_token)])
async def list_webhooks():
    """GET /api/webhooks — List registered outbound webhooks."""
    from castor.webhooks import get_dispatcher

    return {"webhooks": get_dispatcher().list_hooks()}


@app.post("/api/webhooks", dependencies=[Depends(verify_token)])
async def add_webhook(req: _WebhookAddRequest):
    """POST /api/webhooks — Register a new outbound webhook."""
    from castor.webhooks import WEBHOOK_EVENTS, get_dispatcher

    bad = [e for e in (req.events or []) if e not in WEBHOOK_EVENTS and e != "*"]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown events: {bad}. Valid: {sorted(WEBHOOK_EVENTS)}",
        )
    get_dispatcher().add_hook(
        req.url,
        events=req.events,
        secret=req.secret,
        timeout_s=req.timeout_s,
        retry=req.retry,
    )
    return {"ok": True, "url": req.url}


@app.post("/api/webhooks/delete", dependencies=[Depends(verify_token)])
async def delete_webhook(req: _WebhookDeleteRequest):
    """POST /api/webhooks/delete — Remove a webhook by URL."""
    from castor.webhooks import get_dispatcher

    removed = get_dispatcher().remove_hook(req.url)
    if not removed:
        raise HTTPException(status_code=404, detail="Webhook URL not found")
    return {"ok": True, "url": req.url}


@app.post("/api/webhooks/test", dependencies=[Depends(verify_token)])
async def test_webhook(req: _WebhookDeleteRequest):
    """POST /api/webhooks/test — Send a test ping to a registered webhook URL."""
    from castor.webhooks import get_dispatcher

    hooks = get_dispatcher().list_hooks()
    urls = [h["url"] for h in hooks]
    if req.url not in urls:
        raise HTTPException(status_code=404, detail="Webhook URL not registered")

    results = get_dispatcher().emit_sync("startup", {"test": True})
    return {"ok": all(results), "results": results}


# ---------------------------------------------------------------------------
# Video recording endpoints (#127)
# ---------------------------------------------------------------------------


class _RecordingStartRequest(BaseModel):
    session_name: Optional[str] = None


@app.post("/api/recording/start", dependencies=[Depends(verify_token)])
async def recording_start(req: _RecordingStartRequest = _RecordingStartRequest()):
    """POST /api/recording/start — Begin MP4 video recording of camera stream."""
    from castor.recorder import get_recorder

    rec = get_recorder()
    if rec.is_recording:
        raise HTTPException(status_code=409, detail="Recording already in progress")
    try:
        rec_id = rec.start(req.session_name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "id": rec_id, "session_name": req.session_name}


@app.post("/api/recording/stop", dependencies=[Depends(verify_token)])
async def recording_stop():
    """POST /api/recording/stop — Stop recording and flush to disk."""
    from castor.recorder import get_recorder

    meta = get_recorder().stop()
    if meta is None:
        raise HTTPException(status_code=409, detail="No recording in progress")
    return meta


@app.get("/api/recording/list", dependencies=[Depends(verify_token)])
async def recording_list():
    """GET /api/recording/list — List saved recordings (newest first)."""
    from castor.recorder import get_recorder

    return {"recordings": get_recorder().list_recordings()}


@app.get("/api/recording/{rec_id}", dependencies=[Depends(verify_token)])
async def recording_get(rec_id: str):
    """GET /api/recording/{id} — Metadata for a specific recording."""
    from castor.recorder import get_recorder

    meta = get_recorder().get_recording(rec_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return meta


@app.get("/api/recording/{rec_id}/download", dependencies=[Depends(verify_token)])
async def recording_download(rec_id: str):
    """GET /api/recording/{id}/download — Stream MP4 file."""
    from fastapi.responses import FileResponse

    from castor.recorder import get_recorder

    meta = get_recorder().get_recording(rec_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    path = Path(meta["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Recording file not found on disk")
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=f"{rec_id}.mp4",
    )


@app.delete("/api/recording/{rec_id}", dependencies=[Depends(verify_token)])
async def recording_delete(rec_id: str):
    """DELETE /api/recording/{id} — Delete a recording from disk."""
    from castor.recorder import get_recorder

    removed = get_recorder().delete_recording(rec_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Recording not found")
    return {"ok": True, "id": rec_id}


# ---------------------------------------------------------------------------
# Recording annotation endpoints (issue #250)
# ---------------------------------------------------------------------------


class _AnnotationRequest(BaseModel):
    timestamp_s: float
    label: str
    action: Optional[str] = None


@app.post("/api/recording/{rec_id}/annotations", dependencies=[Depends(verify_token)])
async def recording_add_annotation(rec_id: str, req: _AnnotationRequest):
    """Add a timed annotation label to a recording.

    Body (JSON)::

        {"timestamp_s": 3.5, "label": "turning left", "action": "turn_left"}

    Returns:
        200: ``{"ok": true, "annotation_id": str}``
        404: recording not found
    """
    from castor.recorder import get_recorder

    ann_id = get_recorder().add_annotation(rec_id, req.timestamp_s, req.label, req.action)
    if ann_id is None:
        raise HTTPException(status_code=404, detail=f"Recording not found: {rec_id}")
    return {"ok": True, "annotation_id": ann_id}


@app.get("/api/recording/{rec_id}/annotations", dependencies=[Depends(verify_token)])
async def recording_get_annotations(rec_id: str):
    """List all annotations for a recording, sorted by timestamp.

    Returns:
        200: ``{"annotations": [{id, timestamp_s, label, action, created_at}, ...]}``
        404: recording not found
    """
    from castor.recorder import get_recorder

    annotations = get_recorder().get_annotations(rec_id)
    if annotations is None:
        raise HTTPException(status_code=404, detail=f"Recording not found: {rec_id}")
    return {"annotations": annotations}


@app.delete(
    "/api/recording/{rec_id}/annotations/{annotation_id}", dependencies=[Depends(verify_token)]
)
async def recording_delete_annotation(rec_id: str, annotation_id: str):
    """Delete a specific annotation from a recording.

    Returns:
        200: ``{"ok": true}``
        404: recording or annotation not found
    """
    from castor.recorder import get_recorder

    deleted = get_recorder().delete_annotation(rec_id, annotation_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Annotation {annotation_id!r} not found in recording {rec_id!r}",
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Gesture recognition endpoints (#131)
# ---------------------------------------------------------------------------


class _GestureFrameRequest(BaseModel):
    image_base64: str


@app.post("/api/gesture/frame", dependencies=[Depends(verify_token)])
async def gesture_frame(req: _GestureFrameRequest):
    """POST /api/gesture/frame — Recognize hand gesture from base64 JPEG.

    Returns:
        200: ``{"gesture": str, "action": dict, "confidence": float, "latency_ms": float}``
    """
    from castor.gestures import get_controller

    result = get_controller().recognize_from_base64(req.image_base64)
    return result


@app.get("/api/gesture/gestures", dependencies=[Depends(verify_token)])
async def gesture_list():
    """GET /api/gesture/gestures — List all gesture → action mappings."""
    from castor.gestures import get_controller

    return {"gestures": get_controller().list_gestures()}


# ---------------------------------------------------------------------------
# Snapshot endpoints (issue #148)
# ---------------------------------------------------------------------------


@app.get("/api/snapshot/latest", dependencies=[Depends(verify_token)])
async def snapshot_latest():
    """GET /api/snapshot/latest — Most recent full-state snapshot."""
    from castor.snapshot import get_manager

    snap = get_manager().latest()
    if snap is None:
        raise HTTPException(status_code=404, detail="No snapshots taken yet")
    return snap


@app.get("/api/snapshot/history", dependencies=[Depends(verify_token)])
async def snapshot_history(limit: int = 20):
    """GET /api/snapshot/history — Recent state snapshots (newest first)."""
    from castor.snapshot import get_manager

    return {"snapshots": get_manager().history(limit=min(limit, 100))}


@app.post("/api/snapshot/take", dependencies=[Depends(verify_token)])
async def snapshot_take():
    """POST /api/snapshot/take — Capture a snapshot immediately."""
    from castor.snapshot import get_manager

    snap = get_manager().take(state=state)
    return snap


# ---------------------------------------------------------------------------
# API key rotation endpoints (issue #145)
# ---------------------------------------------------------------------------


class _KeyGenerateRequest(BaseModel):
    label: str = "key"
    role: str = "operator"
    expires_in_days: Optional[int] = None


@app.post("/api/keys/generate", dependencies=[Depends(verify_token)])
async def keys_generate(req: _KeyGenerateRequest, request: Request):
    """POST /api/keys/generate — Generate a new named API key.

    Body: ``{"label": str, "role": str, "expires_in_days": int|null}``

    Returns:
        200: ``{"key": "<raw_key>", "key_id": str, "role": str}``
             **The raw key is shown only once — store it securely.**
    """
    _check_min_role(request, "admin")
    from castor.apikeys import get_manager as _km

    try:
        raw = _km().generate(
            label=req.label,
            role=req.role,
            expires_in_days=req.expires_in_days,
        )
        mgr = _km()
        # Find the newly created key_id by verifying the raw key
        role = mgr.verify(raw)
        return {"key": raw, "role": role, "label": req.label}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/keys/list", dependencies=[Depends(verify_token)])
async def keys_list(request: Request):
    """GET /api/keys/list — List all API keys (hashes hidden)."""
    _check_min_role(request, "admin")
    from castor.apikeys import get_manager as _km

    return {"keys": _km().list()}


@app.delete("/api/keys/{key_id}", dependencies=[Depends(verify_token)])
async def keys_revoke(key_id: str, request: Request):
    """DELETE /api/keys/{key_id} — Revoke an API key."""
    _check_min_role(request, "admin")
    from castor.apikeys import get_manager as _km

    removed = _km().revoke(key_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Key '{key_id}' not found")
    return {"revoked": True, "key_id": key_id}


# ---------------------------------------------------------------------------
# Safety event telemetry endpoints (issue #143)
# ---------------------------------------------------------------------------


@app.get("/api/safety/events", dependencies=[Depends(verify_token)])
async def safety_events(limit: int = 50, event_type: Optional[str] = None):
    """GET /api/safety/events — Recent safety events.

    Query params:
        limit: Max events (default 50).
        event_type: Filter by type (bounds_violation, estop, guardian_veto, etc.).
    """
    from castor.safety_telemetry import get_telemetry

    return {"events": get_telemetry().recent(limit=min(limit, 500), event_type=event_type)}


@app.get("/api/safety/stats", dependencies=[Depends(verify_token)])
async def safety_stats():
    """GET /api/safety/stats — Safety event statistics (counts by type, last 24h)."""
    from castor.safety_telemetry import get_telemetry

    return get_telemetry().stats()


class _SafetyTestBoundsRequest(BaseModel):
    action: dict[str, Any]


@app.post("/api/safety/test-bounds", dependencies=[Depends(verify_token)])
async def safety_test_bounds(req: _SafetyTestBoundsRequest):
    """POST /api/safety/test-bounds — Test an action dict against the BoundsChecker.

    Body: ``{"action": {"speed": 0.8, "direction": "forward", ...}}``

    Returns:
        200: ``{"within_bounds": bool, "violations": list, "margin": float|null}``
    """
    try:
        from castor.safety.bounds import BoundsChecker

        checker = (
            BoundsChecker.from_config(state.config)
            if state.config
            else BoundsChecker.from_robot_type("generic")
        )
        result = checker.check_action(req.action)
        return {
            "within_bounds": result.ok,
            "violations": result.details if not result.ok else [],
            "status": result.status.value
            if hasattr(result.status, "value")
            else str(result.status),
            "margin": result.margin,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Bounds check failed: {exc}") from exc


class _RCANSafetyRequest(BaseModel):
    """Body for POST /api/safety/rcan — RCAN MessageType.SAFETY message handler."""

    safety_event: str  # "STOP" | "ESTOP" | "RESUME"
    reason: str
    message_id: Optional[str] = None
    ruri: Optional[str] = None
    timestamp_ms: Optional[int] = None


@app.post("/api/safety/rcan", dependencies=[Depends(verify_token)])
async def rcan_safety_message(req: _RCANSafetyRequest):
    """POST /api/safety/rcan — RCAN MessageType.SAFETY message (type 6) handler.

    Handles STOP / ESTOP / RESUME safety events from the RCAN protocol layer.
    These bypass all HiTL queues and confidence gates per RCAN §6.

    - STOP  → controlled deceleration to rest (robot can resume without manual clear)
    - ESTOP → immediate actuator cut (requires manual clear_estop)
    - RESUME → clear a prior STOP or ESTOP and restore operation

    RCAN §6 invariant: local safety checks still run. A RESUME is rejected
    if the local e-stop was triggered by an on-device sensor (not a remote STOP).
    """
    event = req.safety_event.upper()
    if event not in ("STOP", "ESTOP", "RESUME"):
        raise HTTPException(
            status_code=422,
            detail=f"safety_event must be STOP, ESTOP, or RESUME — got '{req.safety_event}'",
        )

    result = {"event": event, "reason": req.reason, "message_id": req.message_id}

    if not state.fs:
        raise HTTPException(status_code=503, detail="Safety layer not initialised")

    try:
        if event == "ESTOP":
            ok = state.fs.estop(
                principal="rcan_remote",
                source="rcan",
                reason=req.reason,
            )
            result["accepted"] = ok
            result["detail"] = "ESTOP activated" if ok else state.fs.last_write_denial
        elif event == "STOP":
            ok = state.fs.controlled_stop(
                principal="rcan_remote",
                source="rcan",
                reason=req.reason,
            )
            result["accepted"] = ok
            result["detail"] = "Controlled STOP initiated" if ok else state.fs.last_write_denial
        elif event == "RESUME":
            ok = state.fs.clear_estop(principal="rcan_remote")
            result["accepted"] = ok
            result["detail"] = "RESUME accepted" if ok else state.fs.last_write_denial
            if not ok and state.fs.is_estopped:
                result["hint"] = (
                    "E-stop may have been triggered by a local sensor — "
                    "verify physical safety before clearing locally via POST /api/estop/clear"
                )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Safety handler error: {exc}") from exc

    return result


@app.get("/api/safety/manifest", dependencies=[Depends(verify_token)])
async def safety_manifest():
    """GET /api/safety/manifest — Machine-readable Protocol 66 conformance declaration.

    Returns the full list of Protocol 66 safety rules, their implementation
    status, severity, and current enabled state. Use this to verify safety
    posture without reading source code.
    """
    try:
        from castor.safety.p66_manifest import build_manifest

        return build_manifest(state.fs if state.fs else None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Manifest build failed: {exc}") from exc


@app.websocket("/ws/safety")
async def ws_safety(websocket: WebSocket):
    """WS /ws/safety — Real-time safety event push at 2Hz."""
    token = websocket.query_params.get("token", "")
    if API_TOKEN and token != API_TOKEN:
        await websocket.close(code=4003)
        return
    await websocket.accept()
    from castor.safety_telemetry import get_telemetry

    seen: set = set()
    try:
        while True:
            events = get_telemetry().recent(limit=20)
            new_events = [e for e in events if e["id"] not in seen]
            if new_events:
                for ev in new_events:
                    seen.add(ev["id"])
                await websocket.send_json({"events": new_events})
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        pass


# ---------------------------------------------------------------------------
# Time-lapse generator endpoints (issue #139)
# ---------------------------------------------------------------------------


class _TimelapseGenerateRequest(BaseModel):
    recording_ids: Optional[list[str]] = None
    speed_factor: float = 4.0
    output_fps: int = 24


@app.post("/api/timelapse/generate", dependencies=[Depends(verify_token)])
async def timelapse_generate(req: _TimelapseGenerateRequest):
    """POST /api/timelapse/generate — Compile recordings into a time-lapse MP4.

    Body: ``{"recording_ids": [...], "speed_factor": 4.0, "output_fps": 24}``
    """
    from castor.timelapse import get_generator

    try:
        result = await asyncio.to_thread(
            get_generator().generate,
            recording_ids=req.recording_ids,
            speed_factor=req.speed_factor,
            output_fps=req.output_fps,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Timelapse generation failed: {exc}") from exc


@app.get("/api/timelapse/list", dependencies=[Depends(verify_token)])
async def timelapse_list():
    """GET /api/timelapse/list — List all generated timelapses."""
    from castor.timelapse import get_generator

    return {"timelapses": get_generator().list()}


# ---------------------------------------------------------------------------
# i18n translation endpoints (issue #141)
# ---------------------------------------------------------------------------


@app.get("/api/i18n/languages", dependencies=[Depends(verify_token)])
async def i18n_languages():
    """GET /api/i18n/languages — List supported languages and phrase counts."""
    from castor.i18n import get_translator

    return {"languages": get_translator().supported_languages()}


class _I18nDetectRequest(BaseModel):
    text: str


@app.post("/api/i18n/detect", dependencies=[Depends(verify_token)])
async def i18n_detect(req: _I18nDetectRequest):
    """POST /api/i18n/detect — Detect language of input text.

    Body: ``{"text": "<text>"}``
    Returns: ``{"lang": "<lang_code>"}``
    """
    from castor.i18n import get_translator

    lang = get_translator().detect(req.text)
    return {"lang": lang, "text": req.text}


class _I18nTranslateRequest(BaseModel):
    text: str
    source_lang: Optional[str] = None
    target_lang: Optional[str] = None


@app.post("/api/i18n/translate", dependencies=[Depends(verify_token)])
async def i18n_translate(req: _I18nTranslateRequest):
    """POST /api/i18n/translate — Translate text to/from English.

    Translates to English if source_lang is set (or auto-detected).
    Translates from English if target_lang is set.

    Body: ``{"text": str, "source_lang": str|null, "target_lang": str|null}``
    """
    from castor.i18n import get_translator

    t = get_translator()
    if req.target_lang and req.target_lang != "en":
        result = t.from_english(req.text, req.target_lang)
        return {"translated": result, "source_lang": "en", "target_lang": req.target_lang}
    translated, detected = t.to_english(req.text, source_lang=req.source_lang)
    return {"translated": translated, "source_lang": detected, "target_lang": "en"}


# ---------------------------------------------------------------------------
# Hot-word wake detection endpoints (issue #137)
# ---------------------------------------------------------------------------


@app.post("/api/hotword/start", dependencies=[Depends(verify_token)])
async def hotword_start():
    """POST /api/hotword/start — Start always-on wake word detection.

    The wake phrase is resolved in order:
    1. ``CASTOR_HOTWORD`` environment variable
    2. ``metadata.robot_name`` from the loaded RCAN config
    3. Default: ``"hey castor"``
    """
    from castor.hotword import get_detector

    async def _on_wake():
        """Trigger STT when wake word detected."""
        if state.listener and hasattr(state.listener, "enabled") and state.listener.enabled:
            logger.info("Wake word detected — triggering STT listen")

    # Prefer env var; fall back to robot name from RCAN config so that a robot
    # named "alex" will respond to "alex" without any extra configuration.
    env_phrase = os.getenv("CASTOR_HOTWORD", "")
    robot_name = (state.config or {}).get("metadata", {}).get("robot_name", "")
    wake_phrase = env_phrase or robot_name or "hey castor"

    det = get_detector(wake_phrase=wake_phrase)
    det.start(
        on_wake=lambda: asyncio.run_coroutine_threadsafe(_on_wake(), asyncio.get_event_loop())
    )
    return {**det.status, "wake_phrase": wake_phrase}


@app.post("/api/hotword/stop", dependencies=[Depends(verify_token)])
async def hotword_stop():
    """POST /api/hotword/stop — Stop wake word detection."""
    from castor.hotword import get_detector

    det = get_detector()
    det.stop()
    return det.status


@app.get("/api/hotword/status", dependencies=[Depends(verify_token)])
async def hotword_status():
    """GET /api/hotword/status — Wake word detector status including active wake phrase."""
    from castor.hotword import get_detector

    det = get_detector()
    return {**det.status, "wake_phrase": det._wake_phrase}


# ---------------------------------------------------------------------------
# SLAM + occupancy mapping endpoints (issue #136)
# ---------------------------------------------------------------------------


def _get_slam_mapper():
    """Return the state-scoped SLAMMapper, lazy-init if needed."""
    if state.slam_mapper is None:
        from castor.slam import SLAMMapper

        state.slam_mapper = SLAMMapper()
    return state.slam_mapper


@app.post("/api/nav/map/start", dependencies=[Depends(verify_token)])
async def slam_start():
    """POST /api/nav/map/start — Begin a SLAM mapping session."""
    _get_slam_mapper().start_mapping()
    return {
        "status": "mapping",
        "engine": "depthai"
        if __import__("castor.slam", fromlist=["HAS_DEPTHAI"]).HAS_DEPTHAI
        else "mock",
    }


@app.post("/api/nav/map/stop", dependencies=[Depends(verify_token)])
async def slam_stop():
    """POST /api/nav/map/stop — Finalize and stop the current mapping session."""
    path = _get_slam_mapper().stop_mapping()
    return {"status": "stopped", "map_path": path}


@app.get("/api/nav/map/current", dependencies=[Depends(verify_token)])
async def slam_map_current():
    """GET /api/nav/map/current — PNG of the current occupancy grid."""
    from fastapi.responses import Response as _Response

    png = _get_slam_mapper().get_map_png()
    return _Response(content=png, media_type="image/png")


@app.post("/api/nav/map/navigate", dependencies=[Depends(verify_token)])
async def slam_navigate(body: dict[str, Any]):
    """POST /api/nav/map/navigate — Plan + execute path to {goal_x, goal_y} (metres)."""
    goal_x = float(body.get("goal_x", 0.0))
    goal_y = float(body.get("goal_y", 0.0))
    plan = _get_slam_mapper().navigate_to(goal_x, goal_y)
    return plan


@app.get("/api/nav/map/pose", dependencies=[Depends(verify_token)])
async def slam_pose():
    """GET /api/nav/map/pose — Current robot pose estimate {x, y, theta, confidence}."""
    return _get_slam_mapper().get_pose()


# ---------------------------------------------------------------------------
# Workspace isolation endpoints (issue #134)
# ---------------------------------------------------------------------------


class _WorkspaceCreateRequest(BaseModel):
    name: str
    admin_email: str = ""
    rcan_path: str = ""


@app.post("/workspaces", dependencies=[Depends(verify_token)])
async def workspace_create(req: _WorkspaceCreateRequest, request: Request):
    """POST /workspaces — Create an isolated workspace.

    Body: ``{"name": str, "admin_email": str, "rcan_path": str}``

    Returns:
        200: Workspace metadata including the one-time raw token.
    """
    _check_min_role(request, "admin")
    from castor.workspace import get_manager as _wm

    try:
        ws = _wm().create(name=req.name, admin_email=req.admin_email, rcan_path=req.rcan_path)
        return ws
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/workspaces", dependencies=[Depends(verify_token)])
async def workspace_list(request: Request):
    """GET /workspaces — List all workspaces (admin only)."""
    _check_min_role(request, "admin")
    from castor.workspace import get_manager as _wm

    return {"workspaces": _wm().list()}


@app.get("/workspaces/{ws_id}/status", dependencies=[Depends(verify_token)])
async def workspace_status(ws_id: str, request: Request):
    """GET /workspaces/{id}/status — Workspace health and config status."""
    _check_min_role(request, "operator")
    from castor.workspace import get_manager as _wm

    try:
        return _wm().status(ws_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class _WorkspaceTokenRequest(BaseModel):
    role: str = "operator"
    expires_in_hours: int = 24


@app.post("/workspaces/{ws_id}/token", dependencies=[Depends(verify_token)])
async def workspace_token(ws_id: str, req: _WorkspaceTokenRequest, request: Request):
    """POST /workspaces/{id}/token — Issue a workspace-scoped JWT."""
    _check_min_role(request, "admin")
    from castor.workspace import get_manager as _wm

    try:
        token = _wm().issue_token(ws_id, role=req.role, expires_in_hours=req.expires_in_hours)
        return {"token": token, "workspace_id": ws_id, "role": req.role}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Personality endpoints
# ---------------------------------------------------------------------------


def _get_personality_registry():
    """Return the process-wide PersonalityRegistry, lazy-init if needed."""
    if state.personality_registry is None:
        from castor.personalities import PersonalityRegistry

        state.personality_registry = PersonalityRegistry()
    return state.personality_registry


@app.get("/api/personality/list", dependencies=[Depends(verify_token)])
async def personality_list():
    """GET /api/personality/list — Return all personality profiles."""
    return {"personalities": _get_personality_registry().list_profiles()}


@app.get("/api/personality/current", dependencies=[Depends(verify_token)])
async def personality_current():
    """GET /api/personality/current — Return the active personality profile."""
    reg = _get_personality_registry()
    return {"personality": reg.current.to_dict(), "name": reg.active_name}


class _PersonalitySetRequest(BaseModel):
    name: str


@app.post("/api/personality/set", dependencies=[Depends(verify_token)])
async def personality_set(req: _PersonalitySetRequest):
    """POST /api/personality/set — Switch active personality profile.

    Body: ``{"name": "<profile_name>"}``

    Returns:
        200: ``{"name": str, "greeting": str}``
        404: profile not found
    """
    try:
        profile = _get_personality_registry().set_active(req.name)
        return {"name": profile.name, "greeting": profile.greeting}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Fine-tune export endpoints
# ---------------------------------------------------------------------------


@app.get("/api/finetune/export", dependencies=[Depends(verify_token)])
async def finetune_export(
    format: str = "chatml",
    limit: int = 1000,
    require_action: bool = False,
):
    """GET /api/finetune/export — Download episode memory as a fine-tuning dataset.

    Query params:
        format: jsonl | alpaca | sharegpt | chatml (default: chatml)
        limit: max episodes to export (default: 1000)
        require_action: skip episodes with no parsed action (default: false)

    Returns:
        JSONL file download (``Content-Disposition: attachment``)
    """
    from castor.finetune import EpisodeFinetuneExporter

    try:
        exporter = EpisodeFinetuneExporter()
        data = exporter.export_to_bytes(
            fmt=format,  # type: ignore[arg-type]
            limit=min(limit, 10000),
            require_action=require_action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    filename = f"castor_episodes_{format}.jsonl"
    return StreamingResponse(
        iter([data]),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/finetune/stats", dependencies=[Depends(verify_token)])
async def finetune_stats():
    """GET /api/finetune/stats — Return dataset statistics for the episode memory."""
    from castor.finetune import EpisodeFinetuneExporter

    return EpisodeFinetuneExporter().stats()


class _FinetuneUploadRequest(BaseModel):
    repo_id: str
    token: Optional[str] = None
    fmt: str = "chatml"
    limit: int = 1000
    private: bool = True


@app.post("/api/finetune/upload", dependencies=[Depends(verify_token)])
async def finetune_upload(req: _FinetuneUploadRequest):
    """POST /api/finetune/upload — Upload episode dataset to HuggingFace Hub.

    Body: {repo_id, token?, fmt?, limit?, private?}
    Returns: {ok, url, records, repo_id}
    """
    from castor.finetune import EpisodeFinetuneExporter

    if req.fmt not in ("chatml", "alpaca", "sharegpt", "jsonl"):
        raise HTTPException(status_code=422, detail=f"Unknown format '{req.fmt}'")

    exporter = EpisodeFinetuneExporter()
    try:
        result = await asyncio.to_thread(
            exporter.upload_to_hub,
            req.repo_id,
            token=req.token,
            fmt=req.fmt,  # type: ignore[arg-type]
            limit=req.limit,
            private=req.private,
        )
    except (ImportError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result


# ---------------------------------------------------------------------------
# Memory image endpoints (multi-modal memory #267/#226)
# ---------------------------------------------------------------------------


@app.get("/api/memory/episodes/{episode_id}/image", dependencies=[Depends(verify_token)])
async def memory_episode_image(episode_id: int):
    """GET /api/memory/episodes/{id}/image — Return stored thumbnail JPEG for an episode."""
    mem = state.memory if hasattr(state, "memory") and state.memory else None
    if mem is None:
        from castor.memory import EpisodeMemory

        mem = EpisodeMemory()
    img = await asyncio.to_thread(mem.get_episode_image, episode_id)
    if img is None:
        raise HTTPException(status_code=404, detail="No image for this episode")
    from fastapi.responses import Response

    return Response(content=img, media_type="image/jpeg")


@app.get("/api/memory/images", dependencies=[Depends(verify_token)])
async def memory_episodes_with_images(limit: int = 20):
    """GET /api/memory/images — List episodes that have stored thumbnail images."""
    mem = state.memory if hasattr(state, "memory") and state.memory else None
    if mem is None:
        from castor.memory import EpisodeMemory

        mem = EpisodeMemory()
    episodes = await asyncio.to_thread(mem.episodes_with_images, limit)
    return {"episodes": episodes, "count": len(episodes)}


# ---------------------------------------------------------------------------
# Thermal camera endpoints (AMG8833 #263/#222)
# ---------------------------------------------------------------------------


@app.get("/api/thermal/frame", dependencies=[Depends(verify_token)])
async def thermal_frame():
    """GET /api/thermal/frame — Return 8x8 thermal pixel array from AMG8833."""
    from castor.drivers.thermal_driver import get_thermal

    thermal = get_thermal()
    pixels = await asyncio.to_thread(thermal.capture)
    grid = [pixels[r * 8 : (r + 1) * 8] for r in range(8)]
    return {"pixels": pixels, "grid": grid, "mode": thermal._mode}


@app.get("/api/thermal/hotspot", dependencies=[Depends(verify_token)])
async def thermal_hotspot():
    """GET /api/thermal/hotspot — Return hottest pixel location and temperature."""
    from castor.drivers.thermal_driver import get_thermal

    thermal = get_thermal()
    hotspot = await asyncio.to_thread(thermal.get_hotspot)
    return hotspot


@app.get("/api/thermal/health", dependencies=[Depends(verify_token)])
async def thermal_health():
    """GET /api/thermal/health — Return AMG8833 driver health status."""
    from castor.drivers.thermal_driver import get_thermal

    thermal = get_thermal()
    return thermal.health_check()


# ---------------------------------------------------------------------------
# Thermal heatmap endpoint (#274)
# ---------------------------------------------------------------------------


@app.get("/api/thermal/heatmap", dependencies=[Depends(verify_token)])
async def thermal_heatmap(width: int = 256, height: int = 256):
    """GET /api/thermal/heatmap — AMG8833 8x8 array rendered as a JPEG heatmap.

    Bicubic-upscales to ``width`` × ``height`` pixels with JET colormap.
    """
    from fastapi.responses import Response

    from castor.drivers.thermal_driver import get_thermal

    thermal = get_thermal()
    jpeg_bytes = await asyncio.to_thread(thermal.get_heatmap, width, height)
    if not jpeg_bytes:
        raise HTTPException(status_code=503, detail="Heatmap render failed (cv2 unavailable)")
    return Response(content=jpeg_bytes, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Episode tagging endpoints (#270)
# ---------------------------------------------------------------------------


@app.post("/api/memory/episodes/{episode_id}/tags", dependencies=[Depends(verify_token)])
async def memory_add_tags(episode_id: str, body: dict):
    """POST /api/memory/episodes/{id}/tags — Add tags to an episode.

    Body: {"tags": ["patrol", "outdoor"]}
    """
    tags = body.get("tags", [])
    if not isinstance(tags, list) or not tags:
        raise HTTPException(status_code=422, detail="'tags' must be a non-empty list")
    mem = state.memory if hasattr(state, "memory") and state.memory else None
    if mem is None:
        from castor.memory import EpisodeMemory

        mem = EpisodeMemory()
    ok = await asyncio.to_thread(mem.add_tags, episode_id, tags)
    if not ok:
        raise HTTPException(status_code=404, detail="Episode not found")
    return {"ok": True, "episode_id": episode_id, "tags": tags}


@app.get("/api/nav/mission/status", dependencies=[Depends(verify_token)])
async def nav_mission_status_full():
    """GET /api/nav/mission/status — Mission status including ETA (#277)."""
    if state.mission_runner is None:
        return {
            "running": False,
            "current_waypoint": 0,
            "total_waypoints": 0,
            "position": {},
            "geofence": None,
            "elapsed_s": 0.0,
            "eta_s": None,
        }
    return state.mission_runner.status()


# ---------------------------------------------------------------------------
# Battery monitor endpoints (#279 — INA219)
# ---------------------------------------------------------------------------


@app.get("/api/battery/status", dependencies=[Depends(verify_token)])
async def battery_status():
    """GET /api/battery/status — INA219 battery voltage, current, power, and percent."""
    from castor.drivers.battery_driver import get_battery

    battery = get_battery()
    return await asyncio.to_thread(battery.read)


@app.get("/api/battery/health", dependencies=[Depends(verify_token)])
async def battery_health():
    """GET /api/battery/health — INA219 driver health status."""
    from castor.drivers.battery_driver import get_battery

    battery = get_battery()
    return battery.health_check()


@app.get("/api/battery/history", dependencies=[Depends(verify_token)])
async def battery_history(window_s: float = 86400.0, limit: int = 1000):
    """GET /api/battery/history — Time-series battery readings from SQLite log.

    Query params:
        window_s: Time window in seconds (default 86400 = 24h).
        limit:    Max readings to return (default 1000).
    """
    from castor.drivers.battery_driver import get_battery

    battery = get_battery()
    readings = battery.get_history(window_s=window_s, limit=limit)
    return {"window_s": window_s, "count": len(readings), "readings": readings}


@app.get("/api/battery/cycles", dependencies=[Depends(verify_token)])
async def battery_cycles(window_s: float = 86400.0):
    """GET /api/battery/cycles — Detected charge/discharge cycles from battery history.

    Query params:
        window_s: Time window in seconds (default 86400 = 24h).
    """
    from castor.drivers.battery_driver import get_battery

    battery = get_battery()
    cycles = battery.get_charge_cycles(window_s=window_s)
    return {"window_s": window_s, "count": len(cycles), "cycles": cycles}


# ---------------------------------------------------------------------------
# Action validation endpoints (#271)
# ---------------------------------------------------------------------------


@app.post("/api/action/validate", dependencies=[Depends(verify_token)])
async def action_validate(body: dict):
    """POST /api/action/validate — Validate a robot action dict against built-in or RCAN schemas.

    Body: the action dict, e.g. {"type": "move", "linear": 0.5}
    Returns: {valid, action_type, errors, warnings, schema_source}
    """
    from castor.action_validator import get_validator

    validator = get_validator()
    result = validator.validate(body)
    schema_source = (
        validator.schema_source_for(result.action_type) if result.action_type else "unknown"
    )
    return {
        "valid": result.valid,
        "action_type": result.action_type,
        "errors": result.errors,
        "warnings": result.warnings,
        "schema_source": schema_source,
    }


@app.get("/api/action/schemas", dependencies=[Depends(verify_token)])
async def action_schemas():
    """GET /api/action/schemas — Return list of known action types."""
    from castor.action_validator import get_validator

    return {"types": get_validator().known_types()}


# ---------------------------------------------------------------------------
# Benchmark persistence endpoints (#257)
# ---------------------------------------------------------------------------


@app.get("/api/benchmark/results", dependencies=[Depends(verify_token)])
async def benchmark_results(limit: int = 50):
    """GET /api/benchmark/results — Return persisted benchmark history.

    Returns the most recent ``limit`` benchmark runs from ``~/.castor/benchmarks.jsonl``.
    """
    import json as _json
    import pathlib

    bench_path = pathlib.Path.home() / ".castor" / "benchmarks.jsonl"
    if not bench_path.exists():
        return {"results": [], "count": 0}

    runs: list = []
    try:
        lines = bench_path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
            if len(runs) >= limit:
                break
    except OSError:
        return {"results": [], "count": 0}

    return {"results": runs, "count": len(runs)}


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

    _action_t0 = time.perf_counter()

    # Sign action payload if Ed25519 signing is configured (RCAN §16, issue #441)
    try:
        from castor.rcan.message_signing import sign_action_payload

        action = sign_action_payload(action, state.config)
    except Exception as _se:
        logger.debug("Action signing skipped (non-fatal): %s", _se)

    # Seal a CommitmentRecord for every action (RCAN §16 audit trail)
    try:
        from castor.rcan.commitment_chain import get_commitment_chain

        _cc = get_commitment_chain()
        if _cc.enabled:
            _robot_uri = str(state.ruri) if state.ruri else ""
            _cc.append_action(
                action_type=action_type,
                params={k: v for k, v in action.items() if k != "type"},
                robot_uri=_robot_uri,
                confidence=action.get("confidence"),
                model_identity=action.get("model_identity"),
            )
            from castor.metrics import get_registry as _get_metrics

            _get_metrics().record_commitment()
    except Exception as _ce:
        logger.debug("CommitmentRecord skipped (non-fatal): %s", _ce)
    if action_type == "move":
        state.driver.move(
            action.get("linear", 0.0),
            action.get("angular", 0.0),
        )
    elif action_type == "nav_waypoint":
        from castor.nav import WaypointNav

        distance_m = float(action.get("distance_m", 0.0))
        heading_deg = float(action.get("heading_deg", 0.0))
        speed = float(action.get("speed", 0.6))
        job_id = str(_uuid.uuid4())
        state.nav_job = {"job_id": job_id, "running": True, "result": None}

        def _run_nav():
            try:
                nav = WaypointNav(state.driver, state.config or {})
                result = nav.execute(distance_m, heading_deg, speed)
                state.nav_job = {"job_id": job_id, "running": False, "result": result}
            except Exception as exc:
                logger.warning(f"Nav job {job_id} failed: {exc}")
                state.nav_job = {
                    "job_id": job_id,
                    "running": False,
                    "result": {"ok": False, "error": str(exc)},
                }

        threading.Thread(target=_run_nav, daemon=True).start()
        logger.info(
            f"Nav waypoint started: job={job_id} distance={distance_m}m heading={heading_deg}°"
        )
    elif action_type == "stop":
        state.driver.stop()
    elif action_type == "grip":
        logger.info(f"Grip: {action.get('state', 'unknown')}")
    elif action_type == "wait":
        logger.info(f"Wait: {action.get('duration_ms', 0)}ms")


def _get_orchestrator():
    """Return layer-3 orchestrator if active."""
    brain = state.brain
    return getattr(brain, "orchestrator", None) if brain is not None else None


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


def _strip_action_json(text: str) -> str:
    """Remove inline JSON action blocks from an AI reply before sending to users.

    The AI appends a JSON object so the runtime can extract the action command.
    This strips that block so users and TTS only hear the natural-language part.

    Handles:
    - Trailing JSON:  "Ok, doing it. {"type": "wait", ...}"
    - Mid-text JSON:  "Ok. {"type": "wait"} Ready."
    - Nested objects: {"type": "move", "params": {"speed": 1}}
    """
    # Strip any {...} blocks that look like action objects (contain "type" key)
    # Use a pattern that handles one level of nesting
    cleaned = _re.sub(r"\s*\{[^{}]*\"type\"\s*:[^{}]*(?:\{[^{}]*\}[^{}]*)?\}\s*", " ", text)
    # Collapse multiple spaces / clean up sentence boundaries
    cleaned = _re.sub(r"  +", " ", cleaned).strip()
    # Remove trailing punctuation artifacts like lone periods after stripping
    cleaned = _re.sub(r"\s+\.\s*$", ".", cleaned).strip()
    return cleaned if cleaned else text


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

    # Strip the JSON action block before speaking/sending — users only need the words
    reply_text = _strip_action_json(thought.raw_text)

    # Speak the reply out loud
    _speak_reply(reply_text)

    return reply_text


async def _start_channels():
    """Initialize and start all configured messaging channels."""
    from castor.channels import create_channel, get_ready_channels

    # OPENCASTOR_CHANNELS_DISABLED=whatsapp,telegram  — comma-separated list of
    # channels to skip even if credentials/session files are present.
    _disabled = {
        c.strip().lower()
        for c in os.getenv("OPENCASTOR_CHANNELS_DISABLED", "").split(",")
        if c.strip()
    }

    for name in get_ready_channels():
        if name.lower() in _disabled:
            logger.info(f"Channel {name} skipped (OPENCASTOR_CHANNELS_DISABLED)")
            continue
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

            # Security posture check (attestation token + degraded mode flag)
            try:
                publish_attestation(state.fs)
            except Exception as _sec_exc:
                logger.debug("Security posture check skipped: %s", _sec_exc)

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

            # Initialize RCAN NodeClient (registry connection)
            try:
                from rcan import NodeClient

                rcan_cfg = state.config.get("rcan_protocol", {})
                _rcan_client = NodeClient(
                    root_url=rcan_cfg.get("registry", "https://rcan.dev"),
                )
                state.rcan_node_client = _rcan_client
                logger.info(
                    "RCAN NodeClient connected to registry: %s",
                    rcan_cfg.get("registry", "https://rcan.dev"),
                )
            except Exception as e:
                logger.debug(f"RCAN NodeClient init skipped: {e}")

            # Initialize brain
            from castor.providers import get_provider

            state.brain = get_provider(state.config["agent"])
            state.brain._caps = state.config.get("rcan_protocol", {}).get("capabilities", [])
            state.brain._robot_name = state.config.get("metadata", {}).get("robot_name", "robot")
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

            # Initialize message signing (RCAN §16, issue #441)
            try:
                from castor.rcan.message_signing import get_signer as _get_signer

                _sig = _get_signer(state.config)
                if _sig and _sig.available:
                    logger.info("RCAN message signing ready (kid=%s)", _sig.key_id)
                    if state.fs:
                        state.fs.proc.set_value("rcan_signing_kid", _sig.key_id)
            except Exception as _se:
                logger.debug("RCAN signing init skipped: %s", _se)

            # Initialize multi-provider failover chain (agent.fallbacks in RCAN YAML)
            _agent_cfg = state.config.get("agent", {})
            if _agent_cfg.get("fallbacks"):
                try:
                    from castor.brain import build_provider  # provider factory
                    from castor.providers.failover import ProviderFailoverChain

                    def _provider_factory(pkey: str, pmodel: str):
                        cfg_copy = dict(state.config)
                        cfg_copy.setdefault("agent", {})["provider"] = pkey
                        cfg_copy["agent"]["model"] = pmodel
                        return build_provider(cfg_copy)

                    failover_chain = ProviderFailoverChain.from_config(
                        state.config, _provider_factory
                    )
                    if failover_chain is not None:
                        state.failover_chain = failover_chain
                        logger.info(
                            "Multi-provider failover chain ready: %d fallback(s)",
                            len(failover_chain._fallbacks),
                        )
                except Exception as _fc_exc:
                    logger.warning("Failover chain init failed (non-fatal): %s", _fc_exc)

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

            # Initialize PersonalityRegistry from config
            try:
                from castor.personalities import PersonalityRegistry

                state.personality_registry = PersonalityRegistry()
                state.personality_registry.init_from_config(state.config)
                logger.info(
                    "Personality registry initialized (active: %s)",
                    state.personality_registry.active_name,
                )
            except Exception as _pers_exc:
                logger.debug("Personality registry init skipped: %s", _pers_exc)

            # Initialize ActionValidator with RCAN custom schemas (#318)
            try:
                from castor.action_validator import init_from_config as _av_init

                _av_init(state.config)
                logger.info("ActionValidator initialised from RCAN config")
            except Exception as _av_exc:
                logger.debug("ActionValidator RCAN init skipped: %s", _av_exc)

            # Start snapshot manager background thread (issue #148)
            try:
                from castor.snapshot import get_manager as _snap_mgr

                _snap_interval = float(state.config.get("snapshot_interval_s", 60))
                _snap_mgr().start(
                    interval_s=_snap_interval,
                    state_getter=lambda: state,
                )
                logger.info("Snapshot manager started (interval=%ss)", _snap_interval)
            except Exception as _snap_exc:
                logger.debug("Snapshot manager init skipped: %s", _snap_exc)

            # Initialize ThoughtLog (F4 — AI accountability)
            try:
                from castor.thought_log import ThoughtLog

                _tl_path = state.config.get("agent", {}).get("thought_log_path", None)
                state.thought_log = ThoughtLog(max_memory=1000, storage_path=_tl_path)
                logger.info("ThoughtLog initialized")
            except Exception as _tl_exc:
                logger.debug("ThoughtLog init skipped: %s", _tl_exc)

            # Initialize HiTLGateManager (F3 — HiTL gates)
            try:
                from castor.configure import parse_hitl_gates
                from castor.hitl_gate import HiTLGateManager

                _hgates = parse_hitl_gates(state.config)
                if _hgates:
                    state.hitl_gate_manager = HiTLGateManager(_hgates)
                    logger.info("HiTLGateManager initialized (%d gates)", len(_hgates))
            except Exception as _hg_exc:
                logger.debug("HiTLGateManager init skipped: %s", _hg_exc)

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

    # Wake-up greeting — non-blocking so startup is not delayed
    _robot_name_wakeup = (state.config or {}).get("metadata", {}).get("robot_name", "robot")
    if hasattr(state, "speaker") and state.speaker and getattr(state.speaker, "enabled", False):
        import threading as _threading

        def _wakeup_speak():
            try:
                state.speaker.speak(f"Hello. I am {_robot_name_wakeup}. I am online and ready.")
            except Exception as _ws_exc:
                logger.debug("Wake-up speech failed: %s", _ws_exc)

        _threading.Thread(target=_wakeup_speak, daemon=True).start()
        logger.info("Wake-up greeting queued for %s", _robot_name_wakeup)

    # Auto-start wake word detection if CASTOR_HOTWORD is set or
    # audio.wake_word_enabled: true in RCAN config.
    # Retries once after 3 s if the mic is not yet ready.
    _ww_enabled = bool(os.getenv("CASTOR_HOTWORD", "")) or (
        (state.config or {}).get("audio", {}).get("wake_word_enabled", False)
    )
    if _ww_enabled:

        async def _start_hotword_with_retry():
            from castor.hotword import get_detector

            async def _on_wake():
                if state.listener and getattr(state.listener, "enabled", False):
                    logger.info("Wake word detected — triggering STT listen")

            _env_phrase = os.getenv("CASTOR_HOTWORD", "")
            _robot_name = (state.config or {}).get("metadata", {}).get("robot_name", "")
            _wake_phrase = _env_phrase or _robot_name or "hey castor"

            for attempt in (1, 2):
                try:
                    det = get_detector(wake_phrase=_wake_phrase)
                    det.start(
                        on_wake=lambda: asyncio.run_coroutine_threadsafe(
                            _on_wake(), asyncio.get_event_loop()
                        )
                    )
                    logger.info(
                        "Wake word active: %r via %s (auto-started on boot)",
                        _wake_phrase,
                        det.status.get("engine", "unknown"),
                    )
                    return
                except Exception as _ww_exc:
                    if attempt == 1:
                        logger.debug(
                            "Wake word start attempt %d failed: %s — retrying in 3 s",
                            attempt,
                            _ww_exc,
                        )
                        await asyncio.sleep(3)
                    else:
                        logger.warning("Wake word auto-start failed after retry: %s", _ww_exc)

        asyncio.create_task(_start_hotword_with_retry())
        logger.debug("Wake word auto-start task queued (phrase will log on success)")

    provider = get_jwt_secret_provider()
    try:
        provider.enforce_weak_source_policy()
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise

    # Warn if running without authentication
    if not os.getenv("OPENCASTOR_API_TOKEN") and not provider.get_bundle().active.secret:
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
        raise HTTPException(status_code=504, detail={"error": "Fleet robot timeout"}) from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc


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
        raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc


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
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


# ---------------------------------------------------------------------------
# Web Wizard /setup endpoint (issue #111)
# ---------------------------------------------------------------------------


@app.get("/setup")
async def setup_wizard():
    """Serve the web-based setup wizard UI.

    Injects OPENCASTOR_API_TOKEN as window.__OC_TOKEN so the wizard JS can
    attach Authorization headers to /setup/api/* calls when auth is enabled.
    The token is only sent to this same-origin page — not exposed externally.
    """
    from fastapi.responses import HTMLResponse

    try:
        from castor.web_wizard import _HTML_TEMPLATE

        token_script = f"<script>window.__OC_TOKEN = {repr(API_TOKEN or '')};</script>"
        html = _HTML_TEMPLATE.replace("</head>", f"{token_script}\n</head>", 1)
        return HTMLResponse(content=html)
    except Exception as exc:
        return HTMLResponse(
            content=f"<html><body><h1>Setup Wizard Error</h1><pre>{exc}</pre></body></html>",
            status_code=500,
        )


@app.get("/gamepad")
async def gamepad_page(token: str = ""):
    """Standalone touch + physical gamepad controller.

    Mobile-first D-pad with press-and-hold for continuous movement.
    Also supports physical gamepads via the Gamepad API.
    Pass the API token as ?token=<value> or leave blank for open-auth gateways.
    """
    from fastapi.responses import HTMLResponse

    _robot = (state.config or {}).get("metadata", {}).get("robot_name", "robot")
    _html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>🎮 {_robot} — Remote</title>
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<style>
:root{{--btn:min(96px,22vw);--gap:10px;--radius:14px;}}
*{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent;}}
html,body{{height:100%;background:#0d1117;color:#e6edf3;font-family:monospace;overflow:hidden;
  display:flex;flex-direction:column;}}
#topbar{{display:flex;align-items:center;justify-content:space-between;
  padding:10px 14px;background:#161b22;border-bottom:1px solid #30363d;flex-shrink:0;}}
#robot-lbl{{color:#58a6ff;font-weight:bold;font-size:1rem;}}
#dir-ind{{font-size:1.6rem;min-width:2rem;text-align:center;transition:opacity 0.1s;}}
#estop-btn{{background:#da3633;color:#fff;border:none;border-radius:8px;
  padding:9px 18px;font-size:0.9rem;font-family:monospace;font-weight:bold;
  cursor:pointer;touch-action:manipulation;}}
#estop-btn:active{{opacity:0.75;}}
#dpad-wrap{{flex:1;display:flex;align-items:center;justify-content:center;padding:16px;}}
#dpad{{display:grid;grid-template-columns:repeat(3,var(--btn));
  grid-template-rows:repeat(3,var(--btn));gap:var(--gap);}}
.db{{background:#21262d;border:2px solid #30363d;border-radius:var(--radius);
  font-size:2rem;color:#e6edf3;cursor:pointer;user-select:none;touch-action:none;
  display:flex;align-items:center;justify-content:center;
  transition:background 0.06s,border-color 0.06s,transform 0.06s;}}
.db:active,.db.on{{background:#1f6feb;border-color:#58a6ff;transform:scale(0.94);}}
#btn-stop{{font-size:1.1rem;}}
#btn-stop.on{{background:#3d1f1f;border-color:#da3633;}}
.db-empty{{background:transparent;border:none;pointer-events:none;}}
#ctrl{{padding:10px 16px;border-top:1px solid #30363d;flex-shrink:0;
  display:flex;gap:20px;align-items:center;flex-wrap:wrap;}}
label{{color:#8b949e;font-size:0.75rem;display:flex;flex-direction:column;gap:3px;}}
.sl-row{{display:flex;align-items:center;gap:6px;}}
input[type=range]{{width:110px;accent-color:#58a6ff;}}
.sl-val{{color:#e6edf3;min-width:2.5rem;}}
#gp-pill{{margin-left:auto;font-size:0.72rem;color:#8b949e;
  background:#161b22;border:1px solid #30363d;border-radius:20px;padding:3px 10px;}}
#gp-pill.on{{color:#3fb950;border-color:#3fb950;}}
#statusbar{{padding:5px 14px;background:#161b22;border-top:1px solid #30363d;
  font-size:0.7rem;color:#8b949e;display:flex;justify-content:space-between;flex-shrink:0;}}
#fb{{color:#8b949e;}}
#hint{{color:#484f58;}}
</style>
</head>
<body>
<div id="topbar">
  <span id="robot-lbl">🤖 {_robot}</span>
  <span id="dir-ind">⬜</span>
  <button id="estop-btn">⏹ E-STOP</button>
</div>
<div id="dpad-wrap">
  <div id="dpad">
    <div class="db-empty"></div>
    <div class="db" id="btn-fwd"  data-lin="1"  data-ang="0"  data-dir="↑">▲</div>
    <div class="db-empty"></div>
    <div class="db" id="btn-left" data-lin="0"  data-ang="1"  data-dir="←">◀</div>
    <div class="db" id="btn-stop" data-lin="0"  data-ang="0"  data-dir="⬜">■</div>
    <div class="db" id="btn-right"data-lin="0"  data-ang="-1" data-dir="→">▶</div>
    <div class="db-empty"></div>
    <div class="db" id="btn-back" data-lin="-1" data-ang="0"  data-dir="↓">▼</div>
    <div class="db-empty"></div>
  </div>
</div>
<div id="ctrl">
  <label>Speed
    <div class="sl-row">
      <input type="range" id="sl-speed" min="0.1" max="1" step="0.05" value="0.7"
        oninput="document.getElementById('sp-v').textContent=parseFloat(this.value).toFixed(2)">
      <span class="sl-val" id="sp-v">0.70</span>
    </div>
  </label>
  <label>Turn
    <div class="sl-row">
      <input type="range" id="sl-turn" min="0.1" max="1" step="0.05" value="0.6"
        oninput="document.getElementById('tu-v').textContent=parseFloat(this.value).toFixed(2)">
      <span class="sl-val" id="tu-v">0.60</span>
    </div>
  </label>
  <span id="gp-pill">🎮 no gamepad</span>
</div>
<div id="statusbar">
  <span id="fb">Ready</span>
  <span id="hint">hold=move · release=stop · Start=ESTOP · Sel=clear</span>
</div>
<script>
(function(){{
  const GW    = window.location.origin;
  const TOKEN = "{token}" || new URLSearchParams(location.search).get("token") || "";
  const authH = TOKEN ? {{"Authorization":"Bearer "+TOKEN}} : {{}};
  const jsonH = Object.assign({{"Content-Type":"application/json"}}, authH);

  function fb(msg, c) {{
    const el = document.getElementById("fb");
    el.textContent = msg; el.style.color = c || "#8b949e";
  }}
  function api(path, body) {{
    return fetch(GW + path, {{
      method: "POST", headers: body !== undefined ? jsonH : authH,
      body: body !== undefined ? JSON.stringify(body) : undefined
    }}).then(r => {{
      if (!r.ok) r.json().then(d => fb(d.detail || "error", "#f85149")).catch(() => {{}});
      return r;
    }}).catch(e => fb("" + e, "#f85149"));
  }}

  let moveIv = null, activeBtn = null;

  function startMove(btn) {{
    if (activeBtn === btn) return;
    stopMove();
    activeBtn = btn;
    btn.classList.add("on");
    const lin = parseFloat(btn.dataset.lin);
    const ang = parseFloat(btn.dataset.ang);
    document.getElementById("dir-ind").textContent = btn.dataset.dir || "⬜";
    function doSend() {{
      const spd = parseFloat(document.getElementById("sl-speed").value);
      const trn = parseFloat(document.getElementById("sl-turn").value);
      api("/api/action", {{type:"move", linear: lin * spd, angular: ang * trn}});
    }}
    doSend();
    moveIv = setInterval(doSend, 80);
  }}

  function stopMove() {{
    if (moveIv) {{ clearInterval(moveIv); moveIv = null; }}
    if (activeBtn) {{ activeBtn.classList.remove("on"); activeBtn = null; }}
    document.getElementById("dir-ind").textContent = "⬜";
    api("/api/action", {{type:"move", linear:0, angular:0}});
  }}

  document.querySelectorAll(".db").forEach(btn => {{
    btn.addEventListener("pointerdown", e => {{
      e.preventDefault();
      btn.setPointerCapture(e.pointerId);
      startMove(btn);
    }});
    btn.addEventListener("pointerup",     e => {{ e.preventDefault(); stopMove(); }});
    btn.addEventListener("pointercancel", e => {{ e.preventDefault(); stopMove(); }});
  }});

  document.getElementById("estop-btn").addEventListener("click", () => {{
    stopMove();
    api("/api/stop").then(() => fb("E-STOP active — use /api/estop/clear to resume", "#da3633"));
  }});

  let gpIdx = null, gpRaf = null, prev = {{}}, lastT = 0, lastMoving = false;

  function pressed(gp, i) {{ return gp.buttons[i] && gp.buttons[i].pressed; }}
  function dz(v) {{ return Math.abs(v) > 0.12 ? v : 0; }}
  function justPressed(gp, i) {{
    const on = pressed(gp, i), was = prev[i] || false;
    prev[i] = on; return on && !was;
  }}

  function gpLoop() {{
    gpRaf = requestAnimationFrame(gpLoop);
    if (gpIdx === null) return;
    const gp = navigator.getGamepads()[gpIdx];
    if (!gp) return;

    const spd = parseFloat(document.getElementById("sl-speed").value);
    const trn = parseFloat(document.getElementById("sl-turn").value);

    const dU=pressed(gp,12), dD=pressed(gp,13), dL=pressed(gp,14), dR=pressed(gp,15);
    let lin=0, ang=0;
    if (dU||dD||dL||dR) {{
      if (dU) lin= spd; if (dD) lin=-spd;
      if (dL) ang= trn; if (dR) ang=-trn;
    }} else {{
      lin = -dz(gp.axes[1]||0)*spd;
      ang = -dz(gp.axes[0]||0)*trn;
    }}

    const t = Date.now(), moving = Math.abs(lin)>0.01||Math.abs(ang)>0.01;
    if ((moving || lastMoving) && t - lastT > 80 && !activeBtn) {{
      lastT = t; lastMoving = moving;
      api("/api/action", {{type:"move", linear:lin, angular:ang}});
      document.getElementById("dir-ind").textContent =
        !moving ? "⬜" : lin>0?"↑":lin<0?"↓":ang>0?"←":"→";
    }}

    if (justPressed(gp,0)||justPressed(gp,1))
      api("/api/action", {{type:"move", linear:0, angular:0}});
    if (justPressed(gp,9)) {{
      api("/api/stop");
      fb("E-STOP (gamepad Start)", "#da3633");
    }}
    if (justPressed(gp,8))
      api("/api/estop/clear").then(() => fb("Stop cleared (gamepad Sel)", "#3fb950"));
  }}

  window.addEventListener("gamepadconnected", e => {{
    gpIdx = e.gamepad.index;
    const pill = document.getElementById("gp-pill");
    pill.textContent = "🎮 " + (e.gamepad.id.slice(0,28) || "gamepad");
    pill.className = "on";
    fb("Gamepad connected", "#3fb950");
    if (!gpRaf) gpRaf = requestAnimationFrame(gpLoop);
  }});
  window.addEventListener("gamepaddisconnected", e => {{
    if (e.gamepad.index === gpIdx) {{
      gpIdx = null;
      if (gpRaf) {{ cancelAnimationFrame(gpRaf); gpRaf = null; }}
      document.getElementById("gp-pill").textContent = "🎮 no gamepad";
      document.getElementById("gp-pill").className = "";
      fb("Gamepad disconnected", "#8b949e");
    }}
  }});
}})();
</script>
</body>
</html>"""
    return HTMLResponse(content=_html)


@app.get("/face")
async def robot_face_page(token: str = "", style: str = "friendly", captions: int = 0):
    """Animated robot face kiosk home screen.

    Polls /api/status every 500ms to drive reactive SVG animations.
    Speaking mouth uses requestAnimationFrame composite-sine oscillator.
    Long-press (2 s) anywhere navigates to the Streamlit dashboard (port 8501).
    No auth required (kiosk use).

    Query params:
        style: "friendly" (default), "kawaii", "retro"
    """
    import re as _re

    from fastapi.responses import HTMLResponse

    _tok = _re.sub(r"[^A-Za-z0-9._\-]", "", token)
    _style = style if style in ("friendly", "kawaii", "retro") else "friendly"

    # ── Per-style definitions ──────────────────────────────────────────────────
    _styles = {
        "friendly": {
            "bg": "#f5f7fa",
            "css_extra": "",
            "face_svg": """
    <!-- left eyebrow: thick high arch — friendly/happy -->
    <path id="brow-l" d="M 122 124 Q 156 100 186 116"
          fill="none" stroke="#0d0d0d" stroke-width="6.5" stroke-linecap="round"/>
    <!-- right eyebrow -->
    <path id="brow-r" d="M 214 116 Q 244 100 278 124"
          fill="none" stroke="#0d0d0d" stroke-width="6.5" stroke-linecap="round"/>
    <!-- cheek blush -->
    <ellipse id="cheek-l" cx="120" cy="212" rx="28" ry="19" fill="#fca5a5" opacity="0.45"/>
    <ellipse id="cheek-r" cx="280" cy="212" rx="28" ry="19" fill="#fca5a5" opacity="0.45"/>
    <!-- left eye -->
    <g id="eye-l">
      <circle cx="158" cy="175" r="30" fill="#ffffff" stroke="#0d0d0d" stroke-width="3"/>
      <circle id="iris-l" cx="158" cy="175" r="19" fill="#0057ff"/>
      <circle cx="157" cy="173" r="10" fill="#0d0d0d"/>
      <circle cx="147" cy="163" r="6"  fill="#ffffff"/>
      <circle cx="162" cy="168" r="3"  fill="#ffffff" opacity="0.75"/>
    </g>
    <!-- right eye -->
    <g id="eye-r">
      <circle cx="242" cy="175" r="30" fill="#ffffff" stroke="#0d0d0d" stroke-width="3"/>
      <circle id="iris-r" cx="242" cy="175" r="19" fill="#0057ff"/>
      <circle cx="241" cy="173" r="10" fill="#0d0d0d"/>
      <circle cx="231" cy="163" r="6"  fill="#ffffff"/>
      <circle cx="246" cy="168" r="3"  fill="#ffffff" opacity="0.75"/>
    </g>
    <!-- mouth -->
    <path id="mouth" d="M 138 260 Q 200 304 262 260"
          fill="none" stroke="#0d0d0d" stroke-width="5.5" stroke-linecap="round"/>""",
            "js_presets": """
const IRIS_COLOR   = "#0057ff";
const OFFLINE_IRIS = "#9aa3af";
const CHEEK_BASE   = 0.45;
const HAS_CHEEKS   = true;
const SPEAK_UY=258, SPEAK_AMP=30, SPEAK_X1=140, SPEAK_X2=260;
const M_SMILE = "M 138 260 Q 200 304 262 260";
const M_FLAT  = "M 152 266 Q 200 272 248 266";
const M_SMISH = "M 144 264 Q 200 288 256 264";
const M_FROWN = "M 140 280 Q 200 258 260 280";
const B_IDLE   = ["M 122 124 Q 156 100 186 116","M 214 116 Q 244 100 278 124"];
const B_MOVE   = ["M 122 132 Q 156 116 186 126","M 214 126 Q 244 116 278 132"];
const B_SPEAK  = ["M 120 114 Q 156  90 186 106","M 214 106 Q 244  90 280 114"];
const B_LISTEN = ["M 120 110 Q 156  86 186 102","M 214 102 Q 244  86 280 110"];
const B_ESTOP  = ["M 122 138 Q 156 130 186 140","M 214 140 Q 244 130 278 138"];
const B_OFFLN  = ["M 122 134 Q 156 128 186 136","M 214 136 Q 244 128 278 134"];""",
        },
        "kawaii": {
            "bg": "#fff0f6",
            "css_extra": """
  #eye-l{transform-origin:155px 178px;}
  #eye-r{transform-origin:245px 178px;}""",
            "face_svg": """
    <!-- kawaii brows: thin gentle curves -->
    <path id="brow-l" d="M 126 118 Q 155  98 182 112"
          fill="none" stroke="#c47ab4" stroke-width="5" stroke-linecap="round"/>
    <path id="brow-r" d="M 218 112 Q 245  98 274 118"
          fill="none" stroke="#c47ab4" stroke-width="5" stroke-linecap="round"/>
    <!-- big cheek blush ovals -->
    <ellipse id="cheek-l" cx="112" cy="218" rx="34" ry="22" fill="#ffb3cc" opacity="0.55"/>
    <ellipse id="cheek-r" cx="288" cy="218" rx="34" ry="22" fill="#ffb3cc" opacity="0.55"/>
    <!-- left eye: large purple iris -->
    <g id="eye-l">
      <circle cx="155" cy="178" r="34" fill="#ffffff" stroke="#c47ab4" stroke-width="2.5"/>
      <circle id="iris-l" cx="155" cy="178" r="22" fill="#9b5de5"/>
      <circle cx="154" cy="176" r="11" fill="#1a0030"/>
      <circle cx="142" cy="164" r="8"  fill="#ffffff"/>
      <circle cx="158" cy="170" r="4"  fill="#ffffff" opacity="0.8"/>
      <circle cx="146" cy="188" r="3"  fill="#ffffff" opacity="0.4"/>
    </g>
    <!-- right eye -->
    <g id="eye-r">
      <circle cx="245" cy="178" r="34" fill="#ffffff" stroke="#c47ab4" stroke-width="2.5"/>
      <circle id="iris-r" cx="245" cy="178" r="22" fill="#9b5de5"/>
      <circle cx="244" cy="176" r="11" fill="#1a0030"/>
      <circle cx="232" cy="164" r="8"  fill="#ffffff"/>
      <circle cx="248" cy="170" r="4"  fill="#ffffff" opacity="0.8"/>
      <circle cx="236" cy="188" r="3"  fill="#ffffff" opacity="0.4"/>
    </g>
    <!-- kawaii small cat mouth -->
    <path id="mouth" d="M 172 268 Q 200 288 228 268"
          fill="none" stroke="#c47ab4" stroke-width="4.5" stroke-linecap="round"/>""",
            "js_presets": """
const IRIS_COLOR   = "#9b5de5";
const OFFLINE_IRIS = "#c8aee0";
const CHEEK_BASE   = 0.55;
const HAS_CHEEKS   = true;
const SPEAK_UY=264, SPEAK_AMP=22, SPEAK_X1=162, SPEAK_X2=238;
const M_SMILE = "M 172 268 Q 200 288 228 268";
const M_FLAT  = "M 172 272 Q 200 276 228 272";
const M_SMISH = "M 168 270 Q 200 282 232 270";
const M_FROWN = "M 172 282 Q 200 268 228 282";
const B_IDLE   = ["M 126 118 Q 155  98 182 112","M 218 112 Q 245  98 274 118"];
const B_MOVE   = ["M 126 124 Q 155 108 182 118","M 218 118 Q 245 108 274 124"];
const B_SPEAK  = ["M 124 110 Q 155  90 182 104","M 218 104 Q 245  90 276 110"];
const B_LISTEN = ["M 124 106 Q 155  86 182 100","M 218 100 Q 245  86 276 106"];
const B_ESTOP  = ["M 126 130 Q 155 124 182 132","M 218 132 Q 245 124 274 130"];
const B_OFFLN  = ["M 126 128 Q 155 122 182 130","M 218 130 Q 245 122 274 128"];""",
        },
        "retro": {
            "bg": "#0a0a0a",
            "css_extra": """
  html,body{background:#0a0a0a;}
  svg{filter:drop-shadow(0 0 8px #00ff41);}
  @keyframes scanline{0%{transform:translateY(-100%);}100%{transform:translateY(100vh);}}
  #scanline{animation:scanline 3s linear infinite;}
  #listen-ring{stroke:#00ff41;}""",
            "face_svg": """
    <!-- scanline overlay -->
    <line id="scanline" x1="0" y1="0" x2="400" y2="0" stroke="#00ff41"
          stroke-width="2" opacity="0.12"/>
    <!-- retro pixel brows (rectangles) -->
    <rect id="brow-l" x="124" y="114" width="60" height="8" rx="2"
          fill="#00ff41"/>
    <rect id="brow-r" x="216" y="114" width="60" height="8" rx="2"
          fill="#00ff41"/>
    <!-- no cheeks on retro — dummy invisible elements -->
    <ellipse id="cheek-l" cx="0" cy="0" rx="1" ry="1" fill="none" opacity="0"/>
    <ellipse id="cheek-r" cx="0" cy="0" rx="1" ry="1" fill="none" opacity="0"/>
    <!-- left eye: square pixel style -->
    <g id="eye-l">
      <rect cx="158" cy="175" x="130" y="150" width="56" height="48" rx="6"
            fill="#0a0a0a" stroke="#00ff41" stroke-width="3"/>
      <rect id="iris-l" x="143" y="163" width="30" height="24" rx="3" fill="#00ff41"/>
      <rect x="149" y="169" width="14" height="12" rx="2" fill="#0a0a0a"/>
      <rect x="139" y="158" width="8" height="8" fill="#00ff41" opacity="0.6"/>
    </g>
    <!-- right eye -->
    <g id="eye-r">
      <rect cx="242" cy="175" x="214" y="150" width="56" height="48" rx="6"
            fill="#0a0a0a" stroke="#00ff41" stroke-width="3"/>
      <rect id="iris-r" x="227" y="163" width="30" height="24" rx="3" fill="#00ff41"/>
      <rect x="233" y="169" width="14" height="12" rx="2" fill="#0a0a0a"/>
      <rect x="223" y="158" width="8" height="8" fill="#00ff41" opacity="0.6"/>
    </g>
    <!-- retro mouth: segmented line -->
    <path id="mouth" d="M 142 264 L 168 280 L 200 284 L 232 280 L 258 264"
          fill="none" stroke="#00ff41" stroke-width="4" stroke-linecap="square"
          stroke-linejoin="miter"/>""",
            "js_presets": """
const IRIS_COLOR   = "#00ff41";
const OFFLINE_IRIS = "#1a4d1a";
const CHEEK_BASE   = 0;
const HAS_CHEEKS   = false;
const SPEAK_UY=272, SPEAK_AMP=18, SPEAK_X1=148, SPEAK_X2=252;
const M_SMILE = "M 142 264 L 168 280 L 200 284 L 232 280 L 258 264";
const M_FLAT  = "M 142 272 L 200 272 L 258 272";
const M_SMISH = "M 142 268 L 168 276 L 200 278 L 232 276 L 258 268";
const M_FROWN = "M 142 284 L 168 270 L 200 268 L 232 270 L 258 284";
const B_IDLE   = [null, null];  // retro uses rects, brows handled specially
const B_MOVE   = [null, null];
const B_SPEAK  = [null, null];
const B_LISTEN = [null, null];
const B_ESTOP  = [null, null];
const B_OFFLN  = [null, null];
// retro brow Y positions (rect y attribute)
const RB_IDLE=114, RB_MOVE=120, RB_SPEAK=106, RB_LISTEN=102, RB_ESTOP=128, RB_OFFLN=126;""",
        },
    }

    _s = _styles[_style]
    _face_svg = _s["face_svg"]
    _js_presets = _s["js_presets"]
    _bg = _s["bg"]
    _css_extra = _s["css_extra"]
    _is_retro = "true" if _style == "retro" else "false"
    _captions_enabled = "true" if captions else "false"

    _html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Castor</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box;}}
  html,body{{width:100%;height:100%;overflow:hidden;background:{_bg};
             display:flex;align-items:center;justify-content:center;
             font-family:system-ui,sans-serif;user-select:none;-webkit-user-select:none;}}
  svg{{max-width:min(90vw,90vh);max-height:min(90vw,90vh);}}
  @keyframes blink{{0%,88%,100%{{transform:scaleY(1);}}93%{{transform:scaleY(0.06);}}}}
  #eye-l{{transform-origin:158px 175px;animation:blink 4.2s ease-in-out infinite;}}
  #eye-r{{transform-origin:242px 175px;animation:blink 4.2s ease-in-out infinite 0.09s;}}
  @keyframes listen-ring{{0%,100%{{r:148;opacity:0.18;}}50%{{r:165;opacity:0.6;}}}}
  #listen-ring{{display:none;animation:listen-ring 1.1s ease-in-out infinite;}}
  #lp-ring{{display:none;}}
  @keyframes estop-glow{{0%,100%{{filter:drop-shadow(0 0 10px #c00000);}}
                          50%{{filter:drop-shadow(0 0 32px #c00000);}}}}
  .estop-face{{animation:estop-glow 0.55s ease-in-out infinite;}}
  {_css_extra}
  /* ── Closed captions ─────────────────────────────────────────── */
  #cc-bar{{
    display:none;position:fixed;bottom:0;left:0;right:0;
    padding:14px 24px 18px;text-align:center;
    background:rgba(0,0,0,0.72);backdrop-filter:blur(4px);
    color:#fff;font-size:clamp(1rem,3.5vw,1.6rem);
    font-weight:500;line-height:1.4;letter-spacing:0.01em;
    border-top:2px solid rgba(255,255,255,0.12);
    transition:opacity 0.3s ease;
  }}
  #cc-bar.cc-visible{{display:block;}}
</style>
</head>
<body>
<svg id="svg-face" viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg">
  <circle id="listen-ring" cx="200" cy="195" r="148" fill="none"
          stroke="#0057ff" stroke-width="4"/>
  <circle id="lp-ring" cx="200" cy="195" r="155" fill="none"
          stroke="#0057ff" stroke-width="5" stroke-dasharray="974" stroke-dashoffset="974"
          stroke-linecap="round" transform="rotate(-90 200 195)"/>
  <g id="face-group">
    {_face_svg}
    <!-- estop X eyes (hidden by default) -->
    <g id="x-eyes" style="display:none">
      <line x1="134" y1="151" x2="182" y2="199" stroke="#c00000" stroke-width="7" stroke-linecap="round"/>
      <line x1="182" y1="151" x2="134" y2="199" stroke="#c00000" stroke-width="7" stroke-linecap="round"/>
      <line x1="218" y1="151" x2="266" y2="199" stroke="#c00000" stroke-width="7" stroke-linecap="round"/>
      <line x1="266" y1="151" x2="218" y2="199" stroke="#c00000" stroke-width="7" stroke-linecap="round"/>
    </g>
  </g>
</svg>

<div id="cc-bar"></div>

<script>
const TOKEN    = "{_tok}";
const API      = window.location.origin;
const DASH     = "http://" + window.location.hostname + ":8501";
const LP_MS    = 2000;
const LP_CIRC  = 2 * Math.PI * 155;
const IS_RETRO = {_is_retro};
const CC_ON    = {_captions_enabled};

// Per-style presets (injected by server)
{_js_presets}

// Elements
const faceGroup = document.getElementById("face-group");
const eyeL      = document.getElementById("eye-l");
const eyeR      = document.getElementById("eye-r");
const browL     = document.getElementById("brow-l");
const browR     = document.getElementById("brow-r");
const cheekL    = document.getElementById("cheek-l");
const cheekR    = document.getElementById("cheek-r");
const xEyes     = document.getElementById("x-eyes");
const mouth     = document.getElementById("mouth");
const lpRing    = document.getElementById("lp-ring");
const lsRing    = document.getElementById("listen-ring");
const irisL     = document.getElementById("iris-l");
const irisR     = document.getElementById("iris-r");

// ── Speaking mouth oscillator (rAF composite-sine) ────────────────────────────
let _speakRaf = null, _speakT = 0;
function _speakTick() {{
  _speakT += 1 / 60;
  const amp = SPEAK_AMP * Math.abs(
    0.65 * Math.sin(_speakT * 3.0 * Math.PI) +
    0.35 * Math.sin(_speakT * 6.5 * Math.PI + 0.8)
  );
  const uy = SPEAK_UY, ly = uy + Math.max(2, amp);
  mouth.setAttribute("d",
    `M ${{SPEAK_X1}} ${{uy}} Q 200 ${{uy - 12}} ${{SPEAK_X2}} ${{uy}} Q 200 ${{ly + 10}} ${{SPEAK_X1}} ${{uy}} Z`
  );
  mouth.setAttribute("fill", IS_RETRO ? "#00ff41" : "#0d0d0d");
  _speakRaf = requestAnimationFrame(_speakTick);
}}
function _startSpeak() {{ if (!_speakRaf) _speakTick(); }}
function _stopSpeak()  {{
  if (_speakRaf) {{ cancelAnimationFrame(_speakRaf); _speakRaf = null; }}
  mouth.setAttribute("fill", "none");
}}

// ── Retro brow helper (moves rect y attr instead of path d) ──────────────────
function _setBrows(pathOrY) {{
  if (IS_RETRO) {{
    browL.setAttribute("y", pathOrY);
    browR.setAttribute("y", pathOrY);
  }} else {{
    if (pathOrY[0]) browL.setAttribute("d", pathOrY[0]);
    if (pathOrY[1]) browR.setAttribute("d", pathOrY[1]);
  }}
}}

// ── State machine ─────────────────────────────────────────────────────────────
let state = "idle";

function _resetFace() {{
  faceGroup.classList.remove("estop-face");
  lsRing.style.display = "none";
  _stopSpeak();
  eyeL.style.display = ""; eyeR.style.display = "";
  xEyes.style.display = "none";
  if (irisL) irisL.setAttribute("fill", IRIS_COLOR);
  if (irisR) irisR.setAttribute("fill", IRIS_COLOR);
  eyeL.style.transform = ""; eyeR.style.transform = "";
  mouth.setAttribute("stroke", IS_RETRO ? "#00ff41" : "#0d0d0d");
  if (HAS_CHEEKS) {{
    cheekL.setAttribute("opacity", String(CHEEK_BASE));
    cheekR.setAttribute("opacity", String(CHEEK_BASE));
  }}
}}

function applyState(s) {{
  if (s === state) return;
  state = s;
  _resetFace();

  if (s === "idle") {{
    _setBrows(IS_RETRO ? RB_IDLE : B_IDLE);
    mouth.setAttribute("d", M_SMILE);
  }} else if (s === "moving") {{
    _setBrows(IS_RETRO ? RB_MOVE : B_MOVE);
    mouth.setAttribute("d", M_FLAT);
  }} else if (s === "speaking") {{
    _setBrows(IS_RETRO ? RB_SPEAK : B_SPEAK);
    _startSpeak();
  }} else if (s === "listening") {{
    lsRing.style.display = "block";
    eyeL.style.transform = "scale(1.10)"; eyeL.style.transformOrigin = "158px 175px";
    eyeR.style.transform = "scale(1.10)"; eyeR.style.transformOrigin = "242px 175px";
    _setBrows(IS_RETRO ? RB_LISTEN : B_LISTEN);
    if (HAS_CHEEKS) {{
      cheekL.setAttribute("opacity", String(Math.min(1, CHEEK_BASE + 0.2)));
      cheekR.setAttribute("opacity", String(Math.min(1, CHEEK_BASE + 0.2)));
    }}
    mouth.setAttribute("d", M_SMISH);
  }} else if (s === "estop") {{
    faceGroup.classList.add("estop-face");
    eyeL.style.display = "none"; eyeR.style.display = "none";
    xEyes.style.display = "block";
    _setBrows(IS_RETRO ? RB_ESTOP : B_ESTOP);
    if (HAS_CHEEKS) {{
      cheekL.setAttribute("opacity", "0");
      cheekR.setAttribute("opacity", "0");
    }}
    mouth.setAttribute("stroke", "#c00000");
    mouth.setAttribute("d", M_FROWN);
  }} else if (s === "offline") {{
    if (irisL) irisL.setAttribute("fill", OFFLINE_IRIS);
    if (irisR) irisR.setAttribute("fill", OFFLINE_IRIS);
    _setBrows(IS_RETRO ? RB_OFFLN : B_OFFLN);
    if (HAS_CHEEKS) {{
      cheekL.setAttribute("opacity", String(CHEEK_BASE * 0.4));
      cheekR.setAttribute("opacity", String(CHEEK_BASE * 0.4));
    }}
    mouth.setAttribute("d", M_FLAT);
  }}
}}

// ── Caption bar ───────────────────────────────────────────────────────────────
const ccBar = document.getElementById("cc-bar");
function _updateCaption(speaking, caption) {{
  if (!CC_ON) return;
  if (speaking && caption) {{
    ccBar.textContent = caption;
    ccBar.classList.add("cc-visible");
  }} else {{
    ccBar.classList.remove("cc-visible");
  }}
}}

// ── Poll /api/status every 500ms ──────────────────────────────────────────────
async function poll() {{
  const headers = TOKEN ? {{"Authorization": "Bearer " + TOKEN}} : {{}};
  try {{
    const r = await fetch(API + "/api/status", {{headers, signal: AbortSignal.timeout(1000)}});
    if (!r.ok) {{ applyState("offline"); return; }}
    const d = await r.json();
    _updateCaption(d.speaking, d.caption || "");
    if      (d.estop)                                                  applyState("estop");
    else if (d.listening)                                              applyState("listening");
    else if (d.speaking)                                               applyState("speaking");
    else if (Math.abs(d.linear||0)>0.02||Math.abs(d.angular||0)>0.02) applyState("moving");
    else                                                               applyState("idle");
  }} catch {{ applyState("offline"); }}
}}
setInterval(poll, 500);
poll();

// ── Long-press 2s → backstage dashboard ──────────────────────────────────────
let lpStart = 0, lpAnim = null;
function lpBegin(e) {{
  e.preventDefault();
  lpStart = Date.now();
  lpRing.style.display = "block";
  lpRing.style.strokeDashoffset = LP_CIRC;
  lpAnim = setInterval(() => {{
    const frac = Math.min((Date.now() - lpStart) / LP_MS, 1);
    lpRing.style.strokeDashoffset = LP_CIRC * (1 - frac);
    if (frac >= 1) {{ clearInterval(lpAnim); window.location.href = DASH; }}
  }}, 16);
}}
function lpEnd() {{ clearInterval(lpAnim); lpRing.style.display = "none"; }}
document.addEventListener("pointerdown",   lpBegin);
document.addEventListener("pointerup",     lpEnd);
document.addEventListener("pointercancel", lpEnd);
</script>
</body>
</html>"""
    return HTMLResponse(content=_html)


class _SetupTestProviderRequest(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None


class _SetupPreflightRequest(BaseModel):
    provider: str
    model_profile: Optional[str] = None
    auto_install: bool = False
    stack_id: Optional[str] = None
    session_id: Optional[str] = None


class _SetupGenerateConfigRequest(BaseModel):
    robot_name: str
    provider: str
    model: str
    preset: str = "rpi_rc_car"
    stack_id: Optional[str] = None
    api_key: Optional[str] = None
    session_id: Optional[str] = None


class _SetupSessionStartRequest(BaseModel):
    robot_name: Optional[str] = None


class _SetupSessionSelectRequest(BaseModel):
    stage: str
    values: dict[str, Any] = Field(default_factory=dict)


class _SetupRemediationRequest(BaseModel):
    remediation_id: str
    consent: bool = False
    session_id: Optional[str] = None
    context: Optional[dict[str, Any]] = None


class _SetupVerifyConfigRequest(BaseModel):
    robot_name: str
    provider: str
    model: str
    preset: str = "rpi_rc_car"
    stack_id: Optional[str] = None
    api_key: Optional[str] = None
    allow_warnings: bool = False
    session_id: Optional[str] = None


@app.post("/setup/api/session/start", dependencies=[Depends(verify_token)])
async def setup_session_start(body: _SetupSessionStartRequest):
    """Start a resumable setup-v3 session."""
    try:
        return start_setup_session(robot_name=body.robot_name, wizard_context=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.get("/setup/api/session/{session_id}", dependencies=[Depends(verify_token)])
async def setup_session_get(session_id: str):
    """Return setup session state."""
    try:
        return get_setup_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.post("/setup/api/session/{session_id}/select", dependencies=[Depends(verify_token)])
async def setup_session_select(session_id: str, body: _SetupSessionSelectRequest):
    """Update setup session selections for a specific stage."""
    try:
        return select_setup_session(session_id, stage=body.stage, values=body.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.post("/setup/api/session/{session_id}/resume", dependencies=[Depends(verify_token)])
async def setup_session_resume(session_id: str):
    """Resume an existing setup session."""
    try:
        return resume_setup_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.post("/setup/api/remediate", dependencies=[Depends(verify_token)])
async def setup_remediate(body: _SetupRemediationRequest):
    """Execute a guided remediation action."""
    try:
        return run_remediation(
            body.remediation_id,
            consent=body.consent,
            session_id=body.session_id,
            context=body.context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.post("/setup/api/verify-config", dependencies=[Depends(verify_token)])
async def setup_verify_config(body: _SetupVerifyConfigRequest):
    """Dry-run verification before writing config."""
    try:
        return verify_setup_config(
            robot_name=body.robot_name,
            provider=body.provider,
            model=body.model,
            preset=body.preset,
            stack_id=body.stack_id,
            api_key=body.api_key,
            allow_warnings=body.allow_warnings,
            session_id=body.session_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.get("/setup/api/metrics", dependencies=[Depends(verify_token)])
async def setup_metrics():
    """Local setup reliability metrics aggregation."""
    try:
        return get_setup_metrics()
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.get("/setup/api/catalog", dependencies=[Depends(verify_token)])
async def setup_catalog():
    """Return setup catalog used by web and CLI setup flows."""
    try:
        return get_setup_catalog(wizard_context=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.post("/setup/api/preflight", dependencies=[Depends(verify_token)])
async def setup_preflight(body: _SetupPreflightRequest):
    """Run setup preflight checks for a provider/model profile."""
    try:
        return run_setup_preflight(
            provider=body.provider,
            model_profile=body.model_profile,
            auto_install=body.auto_install,
            stack_id=body.stack_id,
            session_id=body.session_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.post("/setup/api/generate-config", dependencies=[Depends(verify_token)])
async def setup_generate_config(body: _SetupGenerateConfigRequest):
    """Generate and save RCAN config from setup selections."""
    try:
        payload = generate_setup_config(
            robot_name=body.robot_name,
            provider=body.provider,
            model=body.model,
            preset=body.preset,
        )
        env_var = payload["agent_config"].get("env_var")
        if body.api_key and env_var:
            save_env_vars({env_var: body.api_key})
        save_config_file(payload["config"], payload["filename"])
        if body.session_id:
            try:
                select_setup_session(
                    body.session_id,
                    stage="save",
                    values={
                        "robot_name": body.robot_name,
                        "provider": body.provider,
                        "model": body.model,
                        "preset": body.preset,
                        "stack_id": body.stack_id,
                    },
                )
                finalize_setup_session(
                    body.session_id,
                    success=True,
                    reason_code="READY",
                )
            except Exception:
                # Non-fatal: config has already been generated/saved.
                pass
        return {
            "ok": True,
            "filename": payload["filename"],
            "provider": payload["agent_config"]["provider"],
            "model": payload["agent_config"]["model"],
            "preset": body.preset,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.post("/setup/api/test-provider", dependencies=[Depends(verify_token)])
async def setup_test_provider(body: _SetupTestProviderRequest):
    """Test an API key without saving it (used by the web wizard)."""
    try:
        provider_name = body.provider.lower().strip()
        if provider_name == "apple":
            preflight = run_setup_preflight(
                provider="apple",
                model_profile=body.model or "apple-balanced",
                auto_install=False,
            )
            return {
                "ok": preflight.get("ok", False),
                "latency_ms": None,
                "error": None if preflight.get("ok") else preflight.get("reason", "UNKNOWN"),
                "details": preflight,
            }

        env_var = resolve_provider_env_var(provider_name)
        if not env_var:
            raise HTTPException(
                status_code=400, detail={"error": f"Unknown provider: {body.provider}"}
            )

        # Test by importing the provider and calling health_check
        import os as _os

        _os.environ[env_var] = body.api_key
        try:
            from castor.providers import get_provider

            cfg = {"provider": provider_name, "model": body.model or "default-model"}
            cfg["api_key"] = body.api_key
            provider = get_provider(cfg)
            health = provider.health_check()
            return {
                "ok": health.get("ok", False),
                "latency_ms": health.get("latency_ms"),
                "error": health.get("error"),
            }
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
    session_id: Optional[str] = None


@app.post("/setup/api/save-config", dependencies=[Depends(verify_token)])
async def setup_save_config(body: _SetupSaveConfigRequest):
    """Save a generated RCAN config and optional .env vars (web wizard step 4)."""
    try:
        config_path = os.getenv("OPENCASTOR_CONFIG", "robot.rcan.yaml")
        Path(config_path).write_text(body.rcan_yaml)

        if body.env_vars:
            save_env_vars(body.env_vars)

        if body.session_id:
            with contextlib.suppress(Exception):
                finalize_setup_session(body.session_id, success=True, reason_code="READY")

        return {"ok": True, "config_path": config_path}
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


# ── Privacy Mode ──────────────────────────────────────────────────────────────


@app.get("/api/privacy/mode/status", dependencies=[Depends(verify_token)])
async def privacy_mode_status():
    from castor.privacy_mode import get_privacy_mode

    return get_privacy_mode().status()


@app.post("/api/privacy/mode/enable", dependencies=[Depends(verify_token)])
async def privacy_mode_enable():
    from castor.privacy_mode import get_privacy_mode

    get_privacy_mode().enable()
    return {"ok": True, "enabled": True}


@app.post("/api/privacy/mode/disable", dependencies=[Depends(verify_token)])
async def privacy_mode_disable():
    from castor.privacy_mode import get_privacy_mode

    get_privacy_mode().disable()
    return {"ok": True, "enabled": False}


# ── Voice Loop ────────────────────────────────────────────────────────────────


@app.post("/api/voice/loop/start", dependencies=[Depends(verify_token)])
async def voice_loop_start():
    from castor.voice_loop import get_voice_loop

    # Wake phrase priority: CASTOR_HOTWORD env var → robot name from RCAN →
    # "hey castor" fallback.  Robot name from RCAN ensures the robot always
    # wakes on its own name by default without any env config.
    robot_name = (state.config or {}).get("metadata", {}).get("robot_name", "")
    env_hotword = os.getenv("CASTOR_HOTWORD", "")
    hotword = env_hotword or robot_name or "hey castor"
    logger.info(
        "Voice loop wake phrase: %r (robot_name=%r, env=%r)", hotword, robot_name, env_hotword
    )

    loop = get_voice_loop(brain=state.brain, hotword=hotword)
    loop.start()
    return {"ok": True, "state": loop.state}


@app.post("/api/voice/loop/stop", dependencies=[Depends(verify_token)])
async def voice_loop_stop():
    from castor.voice_loop import get_voice_loop

    loop = get_voice_loop()
    loop.stop()
    return {"ok": True, "state": loop.state}


@app.get("/api/voice/loop/status", dependencies=[Depends(verify_token)])
async def voice_loop_status():
    from castor.voice_loop import get_voice_loop

    loop = get_voice_loop()
    return {"running": loop.running, "state": loop.state, "stats": loop.stats}


# ── INA219 Battery Monitor ────────────────────────────────────────────────────


@app.get("/api/battery", dependencies=[Depends(verify_token)])
async def battery_read():
    from castor.ina219 import get_monitor

    mon = get_monitor()
    reading = mon.read()
    return reading


@app.get("/api/battery/latest", dependencies=[Depends(verify_token)])
async def battery_latest():
    from castor.ina219 import get_monitor

    mon = get_monitor()
    return {"mode": mon.mode, **mon.latest}


@app.post("/api/battery/start_poll", dependencies=[Depends(verify_token)])
async def battery_start_poll(interval_s: float = 1.0):
    from castor.ina219 import get_monitor

    mon = get_monitor()
    mon.start(poll_interval_s=interval_s)
    return {"ok": True, "interval_s": interval_s}


# ── RCAN Config Generator ─────────────────────────────────────────────────────


@app.post("/api/config/generate", dependencies=[Depends(verify_token)])
async def generate_rcan_config_endpoint(request: Request):
    body = await request.json()
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(status_code=422, detail={"error": "description required"})

    from castor.rcan_generator import generate_rcan_config

    brain = _get_active_brain()
    yaml_str = generate_rcan_config(description, brain=brain)
    return {"yaml": yaml_str, "char_count": len(yaml_str)}


@app.get("/api/config/generate/templates", dependencies=[Depends(verify_token)])
async def generate_rcan_templates():
    from castor.rcan_generator import list_templates

    return {"templates": list_templates()}


# ── Teams / Matrix webhook inbound ────────────────────────────────────────────


def _verify_webhook_hmac(secret_env: str, raw_body: bytes, signature_header: str) -> bool:
    """Verify HMAC-SHA256 webhook signature.

    Returns True if valid, or if no secret is configured (backward compatible).
    Set the env var to enforce signature verification.
    """
    secret = os.getenv(secret_env, "")
    if not secret:
        logger.warning(
            "Webhook secret %s not configured — skipping signature verification. "
            "Set this env var to enforce HMAC verification.",
            secret_env,
        )
        return True
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


@app.post("/webhooks/teams")
async def teams_webhook(request: Request):
    raw_body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_webhook_hmac("OPENCASTOR_TEAMS_WEBHOOK_SECRET", raw_body, sig):
        raise HTTPException(status_code=401, detail={"error": "Invalid webhook signature"})
    body = await request.json() if not raw_body else __import__("json").loads(raw_body)
    channel = state.channels.get("teams")
    if not channel:
        raise HTTPException(status_code=503, detail={"error": "Teams channel not active"})
    reply = channel.handle_bot_activity(body)
    # Return Bot Framework activity response
    return {"type": "message", "text": reply} if reply else {}


@app.post("/webhooks/matrix")
async def matrix_webhook(request: Request):
    """Placeholder for Matrix push gateway events (sync is handled by matrix-nio directly)."""
    raw_body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_webhook_hmac("OPENCASTOR_MATRIX_WEBHOOK_SECRET", raw_body, sig):
        raise HTTPException(status_code=401, detail={"error": "Invalid webhook signature"})
    return {"ok": True}


# ── 3D Point Cloud ─────────────────────────────────────────────────────────────


@app.get("/api/depth/pointcloud", dependencies=[Depends(verify_token)])
async def pointcloud_json():
    from castor.pointcloud import get_capture

    return get_capture().to_json_dict()


@app.get("/api/depth/pointcloud.ply")
async def pointcloud_ply():
    from fastapi.responses import Response

    from castor.pointcloud import get_capture

    ply_bytes = get_capture().to_ply_bytes()
    return Response(
        content=ply_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=pointcloud.ply"},
    )


@app.get("/api/depth/pointcloud/stats", dependencies=[Depends(verify_token)])
async def pointcloud_stats():
    from castor.pointcloud import get_capture

    return get_capture().stats()


# ── Object Detection ───────────────────────────────────────────────────────────


@app.get("/api/detection/frame")
async def detection_frame():
    """Return JPEG with bounding box overlays."""
    from fastapi.responses import Response

    from castor.detection import get_detector

    det = get_detector()
    jpeg = b""
    if state.camera:
        jpeg = state.camera.capture()
    annotated = det.detect_and_annotate(jpeg)
    return Response(content=annotated, media_type="image/jpeg")


@app.get("/api/detection/latest", dependencies=[Depends(verify_token)])
async def detection_latest():
    from castor.detection import get_detector

    det = get_detector()
    if state.camera and state.camera.is_available():
        frame = state.camera.get_frame()
        if frame:
            det.detect(frame)
    return {"detections": det.latest, "latency_ms": round(det.latency_ms, 1), "mode": det.mode}


@app.post("/api/detection/configure", dependencies=[Depends(verify_token)])
async def detection_configure(request: Request):
    body = await request.json()
    from castor.detection import get_detector

    det = get_detector()
    det.configure(
        conf_threshold=body.get("conf_threshold"),
        model=body.get("model"),
    )
    return {"ok": True, "mode": det.mode}


# ── Sim-to-Real Transfer ───────────────────────────────────────────────────────


@app.get("/api/sim/formats", dependencies=[Depends(verify_token)])
async def sim_formats():
    from castor.sim_bridge import get_bridge

    return {"formats": get_bridge().supported_formats()}


@app.get("/api/sim/export/{fmt}", dependencies=[Depends(verify_token)])
async def sim_export(fmt: str, limit: int = 50):
    from castor.sim_bridge import get_bridge

    episodes = []
    if state.memory:
        episodes = state.memory.query_recent(limit=limit)
    result = get_bridge().export(episodes, fmt=fmt)
    return result


@app.post("/api/sim/import", dependencies=[Depends(verify_token)])
async def sim_import(request: Request):
    from castor.sim_bridge import get_bridge

    body = await request.body()
    fmt = request.headers.get("X-Sim-Format", "json")
    episodes = get_bridge().import_trajectory(body, fmt=fmt)
    if state.memory:
        for ep in episodes:
            try:
                state.memory.log_episode(ep)
            except Exception:
                pass
    return {"ok": True, "imported": len(episodes)}


@app.get("/api/sim/config/{sim}", dependencies=[Depends(verify_token)])
async def sim_config(sim: str):
    from castor.sim_bridge import get_bridge

    rcan_config = {}
    if state.config:
        rcan_config = state.config
    xml_or_sdf = get_bridge().generate_sim_config(rcan_config, sim=sim)
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(xml_or_sdf)


# ── Reactive Avoidance ────────────────────────────────────────────────────────


@app.get("/api/avoidance/status", dependencies=[Depends(verify_token)])
async def avoidance_status():
    from castor.avoidance import get_avoider

    return get_avoider(driver=state.driver).status()


@app.post("/api/avoidance/enable", dependencies=[Depends(verify_token)])
async def avoidance_enable():
    from castor.avoidance import get_avoider

    get_avoider().enable()
    return {"ok": True, "enabled": True}


@app.post("/api/avoidance/disable", dependencies=[Depends(verify_token)])
async def avoidance_disable():
    from castor.avoidance import get_avoider

    get_avoider().disable()
    return {"ok": True, "enabled": False}


# ── LLM Response Cache ────────────────────────────────────────────────────────


@app.get("/api/cache/stats", dependencies=[Depends(verify_token)])
async def cache_stats():
    from castor.response_cache import get_cache

    return get_cache().stats()


@app.post("/api/cache/clear", dependencies=[Depends(verify_token)])
async def cache_clear():
    from castor.response_cache import get_cache

    deleted = get_cache().clear()
    return {"ok": True, "deleted": deleted}


@app.post("/api/cache/enable", dependencies=[Depends(verify_token)])
async def cache_enable():
    from castor.response_cache import get_cache

    get_cache().enable()
    return {"ok": True, "enabled": True}


@app.post("/api/cache/disable", dependencies=[Depends(verify_token)])
async def cache_disable():
    from castor.response_cache import get_cache

    get_cache().disable()
    return {"ok": True, "enabled": False}


# ── IMU ───────────────────────────────────────────────────────────────────────


@app.get("/api/imu/latest", dependencies=[Depends(verify_token)])
async def imu_latest():
    from castor.drivers.imu_driver import get_imu

    return get_imu().read()


@app.get("/api/imu/calibrate", dependencies=[Depends(verify_token)])
async def imu_calibrate():
    from castor.drivers.imu_driver import get_imu

    return get_imu().calibrate()


# ── Lidar ─────────────────────────────────────────────────────────────────────


@app.get("/api/lidar/scan", dependencies=[Depends(verify_token)])
async def lidar_scan():
    import time as _time

    from castor.drivers.lidar_driver import get_lidar

    t0 = _time.monotonic()
    lidar = get_lidar()
    scan = lidar.scan()
    return {
        "scan": scan,
        "latency_ms": round((_time.monotonic() - t0) * 1000, 1),
        "mode": lidar.health_check().get("mode", "unknown"),
    }


@app.get("/api/lidar/obstacles", dependencies=[Depends(verify_token)])
async def lidar_obstacles():
    from castor.drivers.lidar_driver import get_lidar

    return get_lidar().obstacles()


@app.get("/api/lidar/history", dependencies=[Depends(verify_token)])
async def lidar_history(window_s: float = 60.0, limit: int = 500):
    """GET /api/lidar/history — Time-series LiDAR scan history from SQLite log.

    Query params:
        window_s: Time window in seconds (default 60).
        limit:    Max rows to return (default 500).
    """
    from castor.drivers.lidar_driver import get_lidar

    return {
        "window_s": window_s,
        "readings": get_lidar().get_scan_history(window_s=window_s, limit=limit),
    }


@app.get("/api/imu/orientation", dependencies=[Depends(verify_token)])
async def imu_orientation():
    """GET /api/imu/orientation — Current yaw/pitch/roll from IMU.

    Returns:
        {yaw_deg, pitch_deg, roll_deg, confidence, mode}
    """
    from castor.drivers.imu_driver import IMUDriver

    imu = IMUDriver({})
    return imu.orientation()


@app.post("/api/imu/orientation/reset", dependencies=[Depends(verify_token)])
async def imu_orientation_reset():
    """POST /api/imu/orientation/reset — Zero out the orientation state."""
    from castor.drivers.imu_driver import IMUDriver

    imu = IMUDriver({})
    imu.reset_orientation()
    return {"reset": True}


@app.get("/api/imu/steps", dependencies=[Depends(verify_token)])
async def imu_step_count(reset: bool = False):
    """GET /api/imu/steps — Return step count detected by IMU accelerometer peak detection.

    Query params:
        reset: If true, return the count and then zero it (default false).
    """
    from castor.drivers.imu_driver import IMUDriver

    imu = IMUDriver({})
    count = await asyncio.to_thread(imu.step_count, reset)
    return {"step_count": count, "reset": reset}


@app.get("/api/lidar/zone_map", dependencies=[Depends(verify_token)])
async def lidar_zone_map(resolution_m: float = 0.05, size_m: float = 5.0):
    """GET /api/lidar/zone_map — Occupancy grid from latest LiDAR scan.

    Query params:
        resolution_m: Grid cell size in metres (default 0.05).
        size_m:       Map side length in metres (default 5.0).
    """
    from castor.drivers.lidar_driver import LidarDriver

    lidar = LidarDriver({})
    result = await asyncio.to_thread(lidar.zone_map, resolution_m, size_m)
    return result


@app.get("/api/imu/vibration", dependencies=[Depends(verify_token)])
async def imu_vibration(window_n: int = 64):
    """GET /api/imu/vibration — FFT-based vibration analysis from IMU accelerometer.

    Query params:
        window_n: Number of accelerometer samples to collect (default 64).
    """
    from castor.drivers.imu_driver import IMUDriver

    imu = IMUDriver({})
    result = await asyncio.to_thread(imu.vibration_bands, window_n)
    return result


@app.get("/api/memory/export/parquet", dependencies=[Depends(verify_token)])
async def memory_export_parquet(limit: int = 0):
    """GET /api/memory/export/parquet — Export episode memory as Parquet bytes.

    Query params:
        limit: Max episodes to export (0 = all).
    """
    import tempfile

    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        count = await asyncio.to_thread(mem.export_parquet, tmp_path, limit)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    import os

    data = open(tmp_path, "rb").read()
    os.unlink(tmp_path)

    from starlette.responses import Response

    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename=episodes.parquet",
            "X-Episode-Count": str(count),
        },
    )


@app.get("/api/lidar/velocity", dependencies=[Depends(verify_token)])
async def lidar_obstacle_velocity(window_s: float = 2.0):
    """GET /api/lidar/velocity — Obstacle approach/recede velocity via LiDAR scan history.

    Computes linear regression slope (mm/s) for each sector over the last
    *window_s* seconds of scan history.  Negative = approaching, positive = receding.

    Query params:
        window_s: History window in seconds (default 2.0).
    """
    from castor.drivers.lidar_driver import LidarDriver

    lidar = LidarDriver({})
    result = await asyncio.to_thread(lidar.obstacle_velocity, window_s)
    return result


@app.post("/api/memory/trajectory", dependencies=[Depends(verify_token)])
async def replay_trajectory(
    start_id: str,
    end_id: str,
    speed_factor: float = 1.0,
    dry_run: bool = False,
):
    """POST /api/memory/trajectory — Replay a sequence of stored episodes as live commands.

    Executes episode actions in chronological order with original inter-action
    timing scaled by *speed_factor*.

    Query params:
        start_id:     UUID of the first episode in the trajectory.
        end_id:       UUID of the last episode in the trajectory.
        speed_factor: Playback speed multiplier (1.0 = real-time, 2.0 = 2× faster).
        dry_run:      If true, return the sequence without executing it.
    """
    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    start_ep = mem.get_episode(start_id)
    end_ep = mem.get_episode(end_id)

    if start_ep is None:
        raise HTTPException(status_code=404, detail=f"Start episode {start_id} not found")
    if end_ep is None:
        raise HTTPException(status_code=404, detail=f"End episode {end_id} not found")

    start_ts = start_ep["ts"]
    end_ts = end_ep["ts"]
    if start_ts > end_ts:
        raise HTTPException(status_code=422, detail="start_id must be older than end_id")

    # Fetch all episodes in the time range (inclusive), chronological order
    import sqlite3 as _sqlite3

    with _sqlite3.connect(mem.db_path, check_same_thread=False) as con:
        con.row_factory = _sqlite3.Row
        rows = con.execute(
            "SELECT * FROM episodes WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
            (start_ts, end_ts),
        ).fetchall()

    episodes = [mem._row_to_dict(r) for r in rows]
    if not episodes:
        raise HTTPException(status_code=404, detail="No episodes found in the given range")

    if dry_run:
        return {
            "dry_run": True,
            "episode_count": len(episodes),
            "duration_s": round(end_ts - start_ts, 3),
            "episodes": [
                {"id": e["id"], "ts": e["ts"], "action": e.get("action")} for e in episodes
            ],
        }

    if state.driver is None:
        raise HTTPException(status_code=503, detail="Driver not initialized")

    executed = 0
    import asyncio as _asyncio

    prev_ts = None
    for ep in episodes:
        action = ep.get("action")
        if action and prev_ts is not None:
            gap_s = (ep["ts"] - prev_ts) / max(speed_factor, 0.01)
            if gap_s > 0:
                await _asyncio.sleep(min(gap_s, 10.0))  # cap at 10s
        if action:
            try:
                _execute_action(action)
                executed += 1
            except Exception as exc:
                logger.warning("trajectory replay: episode %s failed: %s", ep["id"], exc)
        prev_ts = ep["ts"]

    return {
        "replayed": True,
        "episode_count": len(episodes),
        "executed": executed,
        "duration_s": round(end_ts - start_ts, 3),
        "speed_factor": speed_factor,
    }


@app.get("/api/memory/delta", dependencies=[Depends(verify_token)])
async def memory_delta(since_id: str):
    """GET /api/memory/delta — Export episodes newer than *since_id* as JSONL.

    Query params:
        since_id: UUID of the last known episode; returns all episodes after it.
    """
    import tempfile

    from fastapi.responses import StreamingResponse

    from castor.memory import EpisodeMemory

    db_path = os.getenv("CASTOR_MEMORY_DB", os.path.expanduser("~/.castor/memory.db"))
    mem = EpisodeMemory(db_path=db_path)

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        tmp_path = f.name

    try:
        count = mem.export_delta(since_id=since_id, path=tmp_path)

        def _stream():
            with open(tmp_path, "rb") as fh:
                yield from fh
            os.unlink(tmp_path)

        return StreamingResponse(
            _stream(),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": "attachment; filename=delta.jsonl",
                "X-Delta-Count": str(count),
            },
        )
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/memory/summary", dependencies=[Depends(verify_token)])
async def memory_summary():
    """GET /api/memory/summary — Return the latest auto-generated episode summary."""
    from castor.memory import EpisodeMemory

    db_path = os.getenv("CASTOR_MEMORY_DB", os.path.expanduser("~/.castor/memory.db"))
    mem = EpisodeMemory(db_path=db_path)
    result = mem.get_latest_summary()
    if result is None:
        return {"summary": None, "message": "No summaries generated yet"}
    return result


@app.get("/api/lidar/slam", dependencies=[Depends(verify_token)])
async def lidar_slam():
    """GET /api/lidar/slam — Wall distance and angle per sector from latest LiDAR scan."""
    from castor.drivers.lidar_driver import get_lidar

    lidar = get_lidar()
    return lidar.slam_hint()


@app.get("/api/imu/pose", dependencies=[Depends(verify_token)])
async def imu_pose():
    """GET /api/imu/pose — Dead-reckoning pose estimate from IMU integration.

    Returns {x_m, y_m, heading_deg, confidence, mode}.
    """
    from castor.drivers.imu_driver import get_imu

    imu = get_imu()
    return imu.pose()


@app.post("/api/imu/pose/reset", dependencies=[Depends(verify_token)])
async def imu_pose_reset():
    """POST /api/imu/pose/reset — Zero the dead-reckoning pose estimate."""
    from castor.drivers.imu_driver import get_imu

    imu = get_imu()
    imu.reset_pose()
    return {"reset": True}


@app.get("/api/lidar/moving", dependencies=[Depends(verify_token)])
async def lidar_moving_objects(min_delta_m: float = 0.05):
    """GET /api/lidar/moving — Objects that moved between the last two LiDAR scans (#358)."""
    from castor.drivers.lidar_driver import get_lidar

    return {"moving_objects": get_lidar().moving_objects(min_delta_m=min_delta_m)}


@app.get("/api/imu/tap", dependencies=[Depends(verify_token)])
async def imu_tap_detection():
    """GET /api/imu/tap — Single/double tap detection from accelerometer (#357)."""
    from castor.drivers.imu_driver import get_imu

    return get_imu().tap_detection()


@app.post("/api/imu/tap/reset", dependencies=[Depends(verify_token)])
async def imu_tap_reset():
    """POST /api/imu/tap/reset — Zero tap detection state (#357)."""
    from castor.drivers.imu_driver import get_imu

    get_imu().reset_taps()
    return {"reset": True}


@app.get("/api/memory/replay/similar", dependencies=[Depends(verify_token)])
async def memory_replay_similar(query: str, top_k: int = 5):
    """GET /api/memory/replay/similar — Top-K episodes similar to query (#356)."""
    from castor.memory import EpisodeMemory

    db = __import__("os").getenv(
        "CASTOR_MEMORY_DB", __import__("os").path.expanduser("~/.castor/memory.db")
    )
    mem = EpisodeMemory(db_path=db)
    return {"episodes": mem.replay_similar(query=query, top_k=top_k)}


@app.post("/api/metrics/push", dependencies=[Depends(verify_token)])
async def metrics_push(gateway_url: str = "", job: str = "opencastor"):
    """POST /api/metrics/push — Push metrics to a Prometheus Pushgateway (#361).

    Query params:
        gateway_url: Pushgateway URL (overrides CASTOR_PROMETHEUS_PUSHGATEWAY env var).
        job:         Prometheus job label (default: opencastor).
    """
    from castor.metrics import push_to_gateway

    ok = push_to_gateway(gateway_url=gateway_url or None, job=job)
    if ok:
        return {"status": "pushed", "job": job}
    return JSONResponse(
        status_code=503,
        content={
            "error": "push failed — check CASTOR_PROMETHEUS_PUSHGATEWAY or gateway_url",
            "code": "HTTP_503",
        },
    )


@app.get("/api/metrics/json", dependencies=[Depends(verify_token)])
async def metrics_json():
    """GET /api/metrics/json — Structured JSON snapshot of all metrics (#372)."""
    from castor.metrics import get_registry

    return get_registry().export_json()


@app.get("/api/imu/shake", dependencies=[Depends(verify_token)])
async def imu_shake_detection():
    """GET /api/imu/shake — Single/shake gesture detection (#369)."""
    if state.driver is None:
        return {
            "shaking": False,
            "reversals": 0,
            "axis": None,
            "timestamp": None,
            "error": "no driver",
        }
    from castor.drivers.imu_driver import get_imu

    imu = get_imu()
    return imu.shake_detection()


@app.post("/api/imu/shake/reset", dependencies=[Depends(verify_token)])
async def imu_shake_reset():
    """POST /api/imu/shake/reset — Clear shake detection history (#369)."""
    from castor.drivers.imu_driver import get_imu

    get_imu().reset_shake()
    return {"status": "ok"}


@app.get("/api/lidar/zone_velocity", dependencies=[Depends(verify_token)])
async def lidar_zone_velocity(zone: str = "front", window_s: float = 2.0):
    """GET /api/lidar/zone_velocity — Per-zone approaching speed estimate (#366)."""
    if state.driver is None:
        return {
            "zone": zone,
            "velocity_m_s": 0.0,
            "samples": 0,
            "window_s": window_s,
            "direction": "stationary",
        }
    from castor.drivers.lidar_driver import get_lidar

    lidar = get_lidar()
    return lidar.zone_velocity(zone=zone, window_s=window_s)


@app.get("/api/memory/tag_frequency", dependencies=[Depends(verify_token)])
async def memory_tag_frequency(window_s: float = 3600.0, top_k: int = 10):
    """GET /api/memory/tag_frequency — Action-type tag histogram (#367)."""
    from castor.memory import EpisodeMemory

    db = __import__("os").getenv(
        "CASTOR_MEMORY_DB", __import__("os").path.expanduser("~/.castor/memory.db")
    )
    mem = EpisodeMemory(db_path=db)
    return {"tags": mem.tag_frequency(window_s=window_s, top_k=top_k)}


@app.get("/api/pool/warm", dependencies=[Depends(verify_token)])
async def pool_warm_providers():
    """GET /api/pool/warm — Run warm_providers() health check (#370)."""
    if state.brain is None:
        return JSONResponse(
            status_code=503, content={"error": "no brain configured", "code": "HTTP_503"}
        )
    from castor.providers.pool_provider import ProviderPool

    if not isinstance(state.brain, ProviderPool):
        return JSONResponse(
            status_code=400, content={"error": "brain is not a ProviderPool", "code": "HTTP_400"}
        )
    results = state.brain.warm_providers()
    return {"warm_results": results, "all_ok": all(results.values())}


@app.get("/api/lidar/slam_update", dependencies=[Depends(verify_token)])
async def lidar_slam_update(reset: bool = False):
    """GET /api/lidar/slam_update — Incremental SLAM map accumulation (#376)."""
    from castor.drivers.lidar_driver import get_lidar

    lidar = get_lidar()
    return lidar.slam_update(reset=reset)


@app.get("/api/memory/export/csv", dependencies=[Depends(verify_token)])
async def memory_export_csv(
    window_s: float = 86400.0,
    limit: int = 1000,
    path: str = "/tmp/opencastor_episodes.csv",
):
    """GET /api/memory/export/csv — Export recent episodes to CSV (#377)."""
    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    result = mem.export_csv(path=path, window_s=window_s, limit=limit)
    return result


@app.get("/api/metrics/channel_rates", dependencies=[Depends(verify_token)])
async def metrics_channel_rates():
    """GET /api/metrics/channel_rates — Per-channel message rate histogram (#380)."""
    from castor.metrics import get_registry

    return get_registry().channel_rate_histogram()


@app.get("/api/imu/step_counter", dependencies=[Depends(verify_token)])
async def imu_step_counter(threshold_g: float = 0.3, min_interval_s: float = 0.3):
    """GET /api/imu/step_counter — Pedometer step count (#381)."""
    if state.driver is None:
        return {
            "steps": 0,
            "threshold_g": threshold_g,
            "min_interval_s": min_interval_s,
            "error": "no driver",
        }
    from castor.drivers.imu_driver import get_imu

    imu = get_imu()
    return imu.step_counter(threshold_g=threshold_g, min_interval_s=min_interval_s)


@app.post("/api/imu/step_counter/reset", dependencies=[Depends(verify_token)])
async def imu_step_counter_reset():
    """POST /api/imu/step_counter/reset — Reset step counter (#381)."""
    from castor.drivers.imu_driver import get_imu

    imu = get_imu()
    imu.reset_step_counter()
    return {"reset": True}


@app.get("/api/memory/clusters", dependencies=[Depends(verify_token)])
async def memory_clusters(n_clusters: int = 5, limit: int = 500):
    """GET /api/memory/clusters — K-means episode clustering (#385)."""
    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    try:
        return mem.cluster_episodes(n_clusters=n_clusters, limit=limit)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc), "code": "HTTP_400"})


@app.get("/api/lidar/obstacles_velocity", dependencies=[Depends(verify_token)])
async def lidar_obstacles_velocity():
    """GET /api/lidar/obstacles_velocity — Per-sector obstacle velocity tracking (#393)."""
    from castor.drivers.lidar_driver import get_lidar

    lidar = get_lidar()
    return lidar.obstacles_with_velocity()


@app.get("/api/metrics/channel_message_histogram", dependencies=[Depends(verify_token)])
async def metrics_channel_message_histogram():
    """GET /api/metrics/channel_message_histogram — Binned channel message counts (#395)."""
    from castor.metrics import get_registry

    return get_registry().channel_message_histogram()


@app.get("/api/imu/step_counter/calibrate", dependencies=[Depends(verify_token)])
async def imu_calibrate_step_threshold(
    n_idle: int = 20,
    calibration_factor: float = 2.0,
):
    """GET /api/imu/step_counter/calibrate — Adaptive step threshold calibration (#391)."""
    from castor.drivers.imu_driver import get_imu

    imu = get_imu()
    return imu.calibrate_step_threshold(n_idle=n_idle, calibration_factor=calibration_factor)


# ── Cycle 19 endpoints (#397–#407) ──────────────────────────────────────────


@app.get("/api/metrics/provider_error_histogram", dependencies=[Depends(verify_token)])
async def metrics_provider_error_histogram():
    """GET /api/metrics/provider_error_histogram — Per-provider error counts (#397)."""
    from castor.metrics import get_registry

    return get_registry().provider_error_histogram()


@app.get("/api/lidar/nearest_obstacle_angle", dependencies=[Depends(verify_token)])
async def lidar_nearest_obstacle_angle():
    """GET /api/lidar/nearest_obstacle_angle — Angle of closest obstacle (#398)."""
    from castor.drivers.lidar_driver import get_lidar

    return get_lidar().nearest_obstacle_angle()


@app.get("/api/lidar/scan_rate", dependencies=[Depends(verify_token)])
async def lidar_scan_rate():
    """GET /api/lidar/scan_rate — Estimated scans per second (#403)."""
    from castor.drivers.lidar_driver import get_lidar

    return get_lidar().scan_rate()


@app.get("/api/imu/fall_detection", dependencies=[Depends(verify_token)])
async def imu_fall_detection(
    threshold_g: float = 0.2,
    window_n: int = 3,
):
    """GET /api/imu/fall_detection — Detect sudden free-fall event (#404)."""
    from castor.drivers.imu_driver import get_imu

    return get_imu().fall_detection(threshold_g=threshold_g, window_n=window_n)


@app.post("/api/imu/fall_detection/reset", dependencies=[Depends(verify_token)])
async def imu_reset_fall_detection():
    """POST /api/imu/fall_detection/reset — Clear fall-detection latch (#404)."""
    from castor.drivers.imu_driver import get_imu

    get_imu().reset_fall()
    return {"ok": True}


@app.get("/api/memory/tag_timeline", dependencies=[Depends(verify_token)])
async def memory_tag_timeline(
    tag: str,
    bucket_s: float = 3600.0,
    window_s: float = 86400.0,
):
    """GET /api/memory/tag_timeline — Per-tag count over time buckets (#401)."""
    from castor.memory import EpisodeMemory

    db = __import__("os").getenv(
        "CASTOR_MEMORY_DB", __import__("os").path.expanduser("~/.castor/memory.db")
    )
    mem = EpisodeMemory(db_path=db)
    return {"timeline": mem.tag_timeline(tag=tag, bucket_s=bucket_s, window_s=window_s)}


@app.get("/api/memory/find_by_outcome", dependencies=[Depends(verify_token)])
async def memory_find_by_outcome(
    outcome: str,
    limit: int = 50,
    exact: bool = False,
):
    """GET /api/memory/find_by_outcome — Filter episodes by outcome string (#407)."""
    from castor.memory import EpisodeMemory

    db = __import__("os").getenv(
        "CASTOR_MEMORY_DB", __import__("os").path.expanduser("~/.castor/memory.db")
    )
    mem = EpisodeMemory(db_path=db)
    return {"episodes": mem.find_by_outcome(outcome=outcome, limit=limit, exact=exact)}


@app.get("/api/pool/provider_stats", dependencies=[Depends(verify_token)])
async def pool_provider_stats():
    """GET /api/pool/provider_stats — Per-provider call/error/latency summary (#405)."""
    brain = state.brain
    if brain is None:
        return {"error": "no brain loaded", "code": "HTTP_503"}
    if not hasattr(brain, "provider_stats"):
        return {"error": "brain is not a ProviderPool", "code": "HTTP_400"}
    return brain.provider_stats()


@app.get("/api/doctor/gpu_memory", dependencies=[Depends(verify_token)])
async def doctor_gpu_memory():
    """GET /api/doctor/gpu_memory — GPU VRAM usage check (#406)."""
    from castor.doctor import check_gpu_memory

    ok, name, detail = check_gpu_memory()
    return {"ok": ok, "name": name, "detail": detail}


# ── Cycle 20 endpoints (#409–#419) ──────────────────────────────────────────


@app.get("/api/lidar/sector_history", dependencies=[Depends(verify_token)])
async def lidar_sector_history(window_s: float = 30.0):
    """GET /api/lidar/sector_history — Per-sector distance history (#409)."""
    from castor.drivers.lidar_driver import get_lidar

    return get_lidar().sector_history(window_s=window_s)


@app.get("/api/lidar/point_cloud_2d", dependencies=[Depends(verify_token)])
async def lidar_point_cloud_2d():
    """GET /api/lidar/point_cloud_2d — Full 2D Cartesian point array (#418)."""
    from castor.drivers.lidar_driver import get_lidar

    return get_lidar().point_cloud_2d()


@app.get("/api/imu/heading_history", dependencies=[Depends(verify_token)])
async def imu_heading_history(window_s: float = 60.0):
    """GET /api/imu/heading_history — Timestamped yaw readings over window (#413)."""
    from castor.drivers.imu_driver import get_imu

    return get_imu().heading_history(window_s=window_s)


@app.get("/api/memory/export_tags_csv", dependencies=[Depends(verify_token)])
async def memory_export_tags_csv(
    path: str,
    window_s: float = 3600.0,
    top_k: int = 20,
):
    """GET /api/memory/export_tags_csv — Export tag frequency to CSV (#410)."""
    from castor.memory import EpisodeMemory

    db = __import__("os").getenv(
        "CASTOR_MEMORY_DB", __import__("os").path.expanduser("~/.castor/memory.db")
    )
    return EpisodeMemory(db_path=db).export_tags_csv(path=path, window_s=window_s, top_k=top_k)


@app.post("/api/memory/retention_policy", dependencies=[Depends(verify_token)])
async def memory_retention_policy(
    max_age_s: float = None,
    max_count: int = None,
    keep_flagged: bool = True,
):
    """POST /api/memory/retention_policy — Auto-expire old episodes (#415)."""
    from castor.memory import EpisodeMemory

    db = __import__("os").getenv(
        "CASTOR_MEMORY_DB", __import__("os").path.expanduser("~/.castor/memory.db")
    )
    return EpisodeMemory(db_path=db).retention_policy(
        max_age_s=max_age_s, max_count=max_count, keep_flagged=keep_flagged
    )


@app.get("/api/pool/latency_percentiles", dependencies=[Depends(verify_token)])
async def pool_latency_percentiles():
    """GET /api/pool/latency_percentiles — p50/p95/p99 per provider (#414)."""
    brain = state.brain
    if brain is None:
        return {"error": "no brain loaded", "code": "HTTP_503"}
    if not hasattr(brain, "latency_percentiles"):
        return {"error": "brain is not a ProviderPool", "code": "HTTP_400"}
    return brain.latency_percentiles()


@app.post("/api/pool/reset_stats", dependencies=[Depends(verify_token)])
async def pool_reset_stats():
    """POST /api/pool/reset_stats — Zero all per-provider counters (#416)."""
    brain = state.brain
    if brain is None:
        return {"error": "no brain loaded", "code": "HTTP_503"}
    if not hasattr(brain, "reset_stats"):
        return {"error": "brain is not a ProviderPool", "code": "HTTP_400"}
    return brain.reset_stats()


@app.get("/api/metrics/loop_latency_percentiles", dependencies=[Depends(verify_token)])
async def metrics_loop_latency_percentiles():
    """GET /api/metrics/loop_latency_percentiles — p50/p95/p99 loop duration (#417)."""
    from castor.metrics import get_registry

    return get_registry().loop_latency_percentiles()


@app.get("/api/doctor/swap_usage", dependencies=[Depends(verify_token)])
async def doctor_swap_usage():
    """GET /api/doctor/swap_usage — Swap memory usage check (#412)."""
    from castor.doctor import check_swap_usage

    ok, name, detail = check_swap_usage()
    return {"ok": ok, "name": name, "detail": detail}


@app.get("/api/metrics/error_rate_histogram", dependencies=[Depends(verify_token)])
async def metrics_error_rate_histogram(window_s: float = 3600.0):
    """GET /api/metrics/error_rate_histogram — Bucketed per-provider error rates (#421)."""
    from castor.metrics import get_registry

    return get_registry().error_rate_histogram(window_s=window_s)


@app.get("/api/metrics/uptime", dependencies=[Depends(verify_token)])
async def metrics_uptime():
    """GET /api/metrics/uptime — System uptime in seconds/minutes/hours (#431)."""
    from castor.metrics import get_registry

    return get_registry().uptime_histogram()


@app.get("/api/lidar/arc_scan", dependencies=[Depends(verify_token)])
async def lidar_arc_scan(start_deg: float = 0.0, end_deg: float = 180.0):
    """GET /api/lidar/arc_scan — Readings within angular arc (#422)."""
    if state.driver is None:
        raise HTTPException(status_code=503, detail="Driver not initialised")
    return state.driver.arc_scan(start_deg=start_deg, end_deg=end_deg)


@app.get("/api/lidar/radial_profile", dependencies=[Depends(verify_token)])
async def lidar_radial_profile(n_sectors: int = 36):
    """GET /api/lidar/radial_profile — Min distance per angular sector (#428)."""
    if state.driver is None:
        raise HTTPException(status_code=503, detail="Driver not initialised")
    return state.driver.radial_profile(n_sectors=n_sectors)


@app.get("/api/imu/activity_classifier", dependencies=[Depends(verify_token)])
async def imu_activity_classifier(window_n: int = 32):
    """GET /api/imu/activity_classifier — Classify motion activity (#425)."""
    if state.imu is None:
        raise HTTPException(status_code=503, detail="IMU not initialised")
    return state.imu.activity_classifier(window_n=window_n)


@app.get("/api/imu/tilt_alert", dependencies=[Depends(verify_token)])
async def imu_tilt_alert(max_pitch_deg: float = 30.0, max_roll_deg: float = 30.0):
    """GET /api/imu/tilt_alert — Check tilt against pitch/roll thresholds (#430)."""
    if state.imu is None:
        raise HTTPException(status_code=503, detail="IMU not initialised")
    return state.imu.tilt_alert(max_pitch_deg=max_pitch_deg, max_roll_deg=max_roll_deg)


@app.get("/api/memory/outcome_timeline", dependencies=[Depends(verify_token)])
async def memory_outcome_timeline(
    outcome: str = "",
    bucket_s: float = 3600.0,
    window_s: float = 86400.0,
):
    """GET /api/memory/outcome_timeline — Time-bucketed outcome event counts (#426)."""
    import os

    from castor.memory import EpisodeMemory

    db_path = os.getenv("CASTOR_MEMORY_DB", os.path.expanduser("~/.castor/memory.db"))
    mem = EpisodeMemory(db_path=db_path)
    return mem.outcome_timeline(outcome=outcome, bucket_s=bucket_s, window_s=window_s)


@app.get("/api/pool/cost_report", dependencies=[Depends(verify_token)])
async def pool_cost_report():
    """GET /api/pool/cost_report — Per-provider cost breakdown (#427)."""
    if state.pool is None:
        raise HTTPException(status_code=503, detail="ProviderPool not initialised")
    return state.pool.cost_report()


@app.get("/api/doctor/cpu_temperature", dependencies=[Depends(verify_token)])
async def doctor_cpu_temperature():
    """GET /api/doctor/cpu_temperature — CPU thermal zone check (#424)."""
    from castor.doctor import check_cpu_temperature

    ok, name, detail = check_cpu_temperature()
    return {"ok": ok, "name": name, "detail": detail}


# ---------------------------------------------------------------------------
# F4: Thought Log endpoint
# ---------------------------------------------------------------------------


@app.get("/api/thoughts/{thought_id}", dependencies=[Depends(verify_token)])
async def get_thought(thought_id: str, request: Request):
    """Return a recorded Thought by ID.

    Requires at least ``viewer`` role (status scope).
    The ``reasoning`` field is only included for ``admin`` / operator-level JWT.
    """
    _check_min_role(request, "viewer")

    if state.thought_log is None:
        raise HTTPException(status_code=503, detail="ThoughtLog not initialised")

    # Include reasoning only for admin/operator (config scope)
    role = getattr(request.state, "jwt_role", "viewer")
    from castor.auth_jwt import ROLES

    include_reasoning = ROLES.get(role, 0) >= ROLES.get("operator", 2)

    entry = state.thought_log.get(thought_id, include_reasoning=include_reasoning)
    if entry is None:
        raise HTTPException(status_code=404, detail="Thought not found")
    return JSONResponse(content=entry)


# ---------------------------------------------------------------------------
# F3: HiTL authorization endpoint
# ---------------------------------------------------------------------------


class HiTLAuthorizeRequest(BaseModel):
    pending_id: str
    decision: str  # "approve" | "deny"


@app.post("/api/hitl/authorize", dependencies=[Depends(verify_token)])
async def hitl_authorize(body: HiTLAuthorizeRequest, request: Request):
    """Approve or deny a pending HiTL gate authorization request.

    Requires ``admin`` role (OWNER+).
    """
    _check_min_role(request, "admin")

    if body.decision not in ("approve", "deny"):
        raise HTTPException(
            status_code=400,
            detail="decision must be 'approve' or 'deny'",
        )

    if state.hitl_gate_manager is None:
        raise HTTPException(status_code=503, detail="HiTLGateManager not initialised")

    resolved = state.hitl_gate_manager.authorize(body.pending_id, body.decision)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"No pending HiTL request with id '{body.pending_id}'",
        )
    return JSONResponse(
        content={"ok": True, "pending_id": body.pending_id, "decision": body.decision}
    )


# ---------------------------------------------------------------------------
# Test suite runner endpoints  (issue #515)
# ---------------------------------------------------------------------------

_test_run_state: dict = {"running": False, "result": None, "started_at": None}
_test_run_lock = threading.Lock()


class TestRunRequest(BaseModel):
    """Request body for POST /api/test/run."""

    suite: str = "full"  # "full" | "embedding" | "fast"


@app.post("/api/test/run", dependencies=[Depends(verify_token)])
async def test_run(body: TestRunRequest):
    """Spawn a pytest subprocess and record results."""
    import json as _json
    import subprocess as _subprocess

    with _test_run_lock:
        if _test_run_state["running"]:
            raise HTTPException(status_code=409, detail="Test run already in progress")
        _test_run_state["running"] = True
        _test_run_state["started_at"] = time.time()

    def _run():
        suite_map = {
            "full": ["python", "-m", "pytest", "tests/", "-x", "-q", "--tb=short", "--no-header"],
            "embedding": [
                "python",
                "-m",
                "pytest",
                "tests/test_embedding_interpreter.py",
                "-v",
                "--tb=short",
                "--no-header",
            ],
            "fast": [
                "python",
                "-m",
                "pytest",
                "tests/",
                "-x",
                "-q",
                "--tb=short",
                "-m",
                "not slow",
            ],
        }
        cmd = suite_map.get(body.suite, suite_map["full"])
        try:
            proc = _subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            result = {
                "returncode": proc.returncode,
                "stdout": proc.stdout[-8000:],
                "stderr": proc.stderr[-2000:],
                "passed": proc.returncode == 0,
                "suite": body.suite,
                "completed_at": time.time(),
            }
        except Exception as exc:
            result = {
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
                "passed": False,
                "suite": body.suite,
                "completed_at": time.time(),
            }
        # Save to disk
        try:
            _last_run_path = Path.home() / ".opencastor" / "last_test_run.json"
            _last_run_path.parent.mkdir(parents=True, exist_ok=True)
            _last_run_path.write_text(_json.dumps(result, indent=2))
        except Exception:
            pass
        with _test_run_lock:
            _test_run_state["running"] = False
            _test_run_state["result"] = result

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "suite": body.suite}


@app.get("/api/test/status", dependencies=[Depends(verify_token)])
async def test_status():
    """Return last test run result + running flag."""
    with _test_run_lock:
        return {
            "running": _test_run_state["running"],
            "result": _test_run_state["result"],
            "started_at": _test_run_state["started_at"],
        }


# ---------------------------------------------------------------------------
# HLabs hardware scan (#520)
# ---------------------------------------------------------------------------


@app.get("/api/hardware/scan", dependencies=[Depends(verify_token)])
async def hardware_scan(
    refresh: bool = Query(False, description="Force fresh scan, bypass cache"),
):
    """Scan for all connected hardware and suggest a preset configuration.

    Returns full detect_hardware() output plus a suggested RCAN preset.
    """
    from castor.hardware_detect import (
        detect_hardware,
        invalidate_hardware_cache,
        suggest_preset,
    )

    if refresh:
        invalidate_hardware_cache()
    hw = detect_hardware()
    preset, confidence, reason = suggest_preset(hw)
    return {
        "devices": {
            **hw,
            "suggested_preset": {
                "preset": preset,
                "confidence": confidence,
                "reason": reason,
            },
        },
        "timestamp": time.time(),
        "cached": not refresh,
    }


# ---------------------------------------------------------------------------
# ACB driver telemetry (#524)
# ---------------------------------------------------------------------------


def _find_acb_driver(driver_id: str):
    """Look up an AcbDriver instance by ID from the active driver tree.

    Handles single AcbDriver and CompositeDriver sub-driver lookup.

    Returns the AcbDriver or None.
    """
    from castor.drivers.acb_driver import AcbDriver
    from castor.drivers.composite import CompositeDriver

    if state.driver is None:
        return None

    if isinstance(state.driver, AcbDriver) and state.driver._driver_id == driver_id:
        return state.driver

    if isinstance(state.driver, CompositeDriver):
        sub = state.driver._sub_drivers.get(driver_id)
        if isinstance(sub, AcbDriver):
            return sub

    return None


@app.get("/api/drivers/{driver_id}/telemetry", dependencies=[Depends(verify_token)])
async def driver_telemetry(driver_id: str):
    """Return latest encoder telemetry for an ACB driver.

    Path param ``driver_id`` must match the ``id`` field in the RCAN config.
    """
    drv = _find_acb_driver(driver_id)
    if drv is None:
        raise HTTPException(
            status_code=404, detail=f"ACB driver '{driver_id}' not found or not an AcbDriver"
        )
    return drv.get_telemetry()


# ---------------------------------------------------------------------------
# ACB motor calibration (#521)
# ---------------------------------------------------------------------------


@app.post("/api/drivers/{driver_id}/calibrate", dependencies=[Depends(verify_token)])
async def calibrate_driver(driver_id: str, request: Request):
    """Run motor calibration for the specified ACB driver.

    Requires ``operator`` role or higher.
    """
    _check_min_role(request, "operator")
    drv = _find_acb_driver(driver_id)
    if drv is None:
        raise HTTPException(
            status_code=404, detail=f"ACB driver '{driver_id}' not found or not an AcbDriver"
        )
    result = drv.calibrate()
    return result.to_dict()


# ---------------------------------------------------------------------------
# ACB firmware flash (#523)
# ---------------------------------------------------------------------------


class _FlashRequest(BaseModel):
    firmware_url: Optional[str] = None
    version: Optional[str] = None
    confirm: bool = False


@app.post("/api/drivers/{driver_id}/flash", dependencies=[Depends(verify_token)])
async def flash_driver(driver_id: str, body: _FlashRequest, request: Request):
    """Trigger firmware flash for an ACB driver.

    Requires ``admin`` role.  The firmware is fetched from ``firmware_url``
    or the latest GitHub release when ``version="latest"``.

    **Safety warning**: Use a current-limiting PSU during firmware flashing.
    High current MOSFETs are present on the ACB v2.0 board.
    """
    _check_min_role(request, "admin")

    # Validate driver_id refers to a real AcbDriver
    drv = _find_acb_driver(driver_id)
    if drv is None:
        raise HTTPException(
            status_code=404, detail=f"ACB driver '{driver_id}' not found or not an AcbDriver"
        )

    if not body.confirm:
        return {
            "status": "confirm_required",
            "message": (
                "Set confirm=true to proceed.  Warning: use a current-limiting PSU "
                "during firmware flashing — high current MOSFETs are present on the ACB."
            ),
        }

    import pathlib as _pathlib
    import subprocess as _subprocess
    import urllib.parse as _urllib_parse
    import urllib.request as _urllib_request

    _ALLOWED_FIRMWARE_HOSTS = {
        "github.com",
        "objects.githubusercontent.com",
        "releases.githubusercontent.com",
    }
    _MAX_FIRMWARE_BYTES = 10 * 1024 * 1024  # 10 MB

    firmware_url = body.firmware_url
    version_tag = body.version or "latest"

    cache_dir = _pathlib.Path.home() / ".opencastor" / "firmware"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Resolve URL from GitHub releases when not provided directly
    if not firmware_url:
        try:
            api_url = "https://api.github.com/repos/h-laboratories/acb-v2.0/releases/latest"
            with _urllib_request.urlopen(api_url, timeout=10) as resp:  # noqa: S310
                import json as _json

                release_data = _json.loads(resp.read())
            assets = release_data.get("assets", [])
            bin_assets = [a for a in assets if a.get("name", "").endswith(".bin")]
            if not bin_assets:
                return {"status": "error", "message": "No .bin asset found in latest release"}
            firmware_url = bin_assets[0]["browser_download_url"]
            version_tag = release_data.get("tag_name", "latest")
        except Exception as exc:
            return {"status": "error", "message": f"Failed to fetch release info: {exc}"}

    # Validate firmware_url against allowlist (SSRF prevention)
    _parsed = _urllib_parse.urlparse(firmware_url)
    if _parsed.scheme != "https" or _parsed.hostname not in _ALLOWED_FIRMWARE_HOSTS:
        raise HTTPException(
            status_code=400,
            detail="firmware_url must be an HTTPS GitHub releases URL",
        )

    # Download if not cached (with size limit)
    cache_file = cache_dir / f"acb-v2.0-{version_tag}.bin"
    if not cache_file.exists():
        try:
            with _urllib_request.urlopen(firmware_url, timeout=30) as resp:  # noqa: S310
                chunks = []
                total = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_FIRMWARE_BYTES:
                        raise ValueError("Firmware exceeds 10 MB size limit")
                    chunks.append(chunk)
                cache_file.write_bytes(b"".join(chunks))
        except Exception as exc:
            return {"status": "error", "message": f"Download failed: {exc}"}

    # Check dfu-util availability
    try:
        _subprocess.run(["dfu-util", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, _subprocess.CalledProcessError):
        return {
            "status": "error",
            "message": "dfu-util not found.  Install with: sudo apt install dfu-util",
        }

    # Flash
    logger.warning("ACB firmware flash starting — use a current-limiting PSU. Driver=%s", driver_id)
    try:
        proc = _subprocess.run(
            [
                "dfu-util",
                "-d",
                "0483:DF11",
                "-a",
                "0",
                "-s",
                "0x08000000:leave",
                "-D",
                str(cache_file),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:],
            "firmware": str(cache_file),
            "version": version_tag,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


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


def _assert_port_free(host: str, port: int) -> None:
    """Raise RuntimeError if *port* on *host* is already in use (#556)."""
    import socket as _socket

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            raise RuntimeError(
                f"Port {port} already in use. "
                f"Stop the existing gateway with: castor stop\n"
                f"Or kill the process: fuser -k {port}/tcp"
            ) from None


def _write_pid_file() -> "Path":
    """Write current PID to ~/.opencastor/gateway.pid, killing stale process (#556)."""
    from pathlib import Path as _Path

    pid_dir = _Path.home() / ".opencastor"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / "gateway.pid"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            try:
                import psutil  # type: ignore[import]

                if psutil.pid_exists(old_pid):
                    logger.warning(
                        "Killing stale gateway (pid %d) — use 'castor stop' for clean shutdown",
                        old_pid,
                    )
                    os.kill(old_pid, signal.SIGTERM)
                    time.sleep(1)
            except ImportError:
                pass  # psutil optional — skip stale check
        except (ValueError, ProcessLookupError, OSError):
            pass
    pid_file.write_text(str(os.getpid()))
    return pid_file


def _cleanup_pid_file(pid_file: "Path") -> None:
    """Remove gateway PID file on exit (#556)."""
    with contextlib.suppress(OSError):
        pid_file.unlink()


def main():
    import atexit

    import uvicorn

    load_dotenv_if_available()
    _setup_signal_handlers()

    parser = argparse.ArgumentParser(description="OpenCastor API Gateway")
    parser.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    parser.add_argument("--host", default=os.getenv("OPENCASTOR_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OPENCASTOR_API_PORT", "8000")))
    args = parser.parse_args()

    os.environ["OPENCASTOR_CONFIG"] = args.config

    # Pre-flight checks (#556)
    _assert_port_free(args.host, args.port)
    pid_file = _write_pid_file()
    atexit.register(_cleanup_pid_file, pid_file)

    uvicorn.run(
        "castor.api:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
