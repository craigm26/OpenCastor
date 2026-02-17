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
import logging
import os
import time
from typing import Any, Dict, Optional

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
    version="2026.2.17.3",
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
    """Return current runtime status and available integrations."""
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
    }
    return _maybe_wrap_rcan(payload, request)


@app.post("/api/command", dependencies=[Depends(verify_token)])
async def send_command(cmd: CommandRequest):
    """Send an instruction to the robot's brain and receive the action."""
    if state.brain is None:
        raise HTTPException(status_code=503, detail="Brain not initialized")

    # Use provided image, live camera frame, or blank
    if cmd.image_base64:
        import base64

        image_bytes = base64.b64decode(cmd.image_base64)
    else:
        image_bytes = _capture_live_frame()

    thought = state.brain.think(image_bytes, cmd.instruction)
    state.last_thought = {
        "raw_text": thought.raw_text,
        "action": thought.action,
        "timestamp": time.time(),
    }

    # Execute action on hardware if available
    if thought.action and state.driver:
        _execute_action(thought.action)

    return {
        "raw_text": thought.raw_text,
        "action": thought.action,
    }


@app.post("/api/action", dependencies=[Depends(verify_token)])
async def direct_action(action: ActionRequest):
    """Send a direct motor command, bypassing the brain."""
    if state.driver is None:
        raise HTTPException(status_code=503, detail="No hardware driver active")

    _execute_action(action.model_dump(exclude_none=True))
    return {"status": "executed", "action": action.model_dump(exclude_none=True)}


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
    data = state.fs.read(req.path, principal="api")
    if data is None and not state.fs.exists(req.path):
        raise HTTPException(status_code=404, detail=f"Path not found: {req.path}")
    return {"path": req.path, "data": data}


@app.post("/api/fs/write", dependencies=[Depends(verify_token)])
async def fs_write(req: FSWriteRequest):
    """Write to a virtual filesystem path."""
    if not state.fs:
        raise HTTPException(status_code=503, detail="Filesystem not initialized")
    ok = state.fs.write(req.path, req.data, principal="api")
    if not ok:
        raise HTTPException(status_code=403, detail="Write denied")
    return {"path": req.path, "status": "written"}


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
            status_code=501, detail="JWT not configured. Set OPENCASTOR_JWT_SECRET.",
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
        return {"name": "api", "role": "OPERATOR", "auth_method": "bearer_token"}
    return {"name": "anonymous", "role": "GUEST", "auth_method": "none"}


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
    thought = state.brain.think(image_bytes, cmd.instruction)
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
# Webhook endpoints for messaging channels
# ---------------------------------------------------------------------------
@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request):
    """Twilio WhatsApp webhook endpoint (for whatsapp_twilio channel only)."""
    channel = state.channels.get("whatsapp_twilio")
    if not channel:
        raise HTTPException(
            status_code=503,
            detail="WhatsApp (Twilio) channel not configured. "
            "This webhook is for the legacy Twilio integration only.",
        )

    form = await request.form()
    reply = await channel.handle_webhook(dict(form))
    return JSONResponse(content={"reply": reply})


@app.get("/api/whatsapp/status", dependencies=[Depends(verify_token)])
async def whatsapp_status():
    """Return WhatsApp (neonize) connection status."""
    channel = state.channels.get("whatsapp")
    if not channel:
        return {"status": "not_configured"}
    connected = getattr(channel, "connected", False)
    return {"status": "connected" if connected else "disconnected"}


@app.post("/webhooks/slack")
async def slack_webhook(request: Request):
    """Slack Events API fallback webhook (Socket Mode is preferred)."""
    body = await request.json()
    # Slack URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}
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
    """Grab a frame from the shared camera if available, else blank."""
    try:
        from castor.main import get_shared_camera

        camera = get_shared_camera()
        if camera is not None:
            return camera.capture_jpeg()
    except Exception:
        pass
    return b"\x00" * 1024


def _speak_reply(text: str):
    """Speak via USB speaker if available."""
    try:
        from castor.main import get_shared_speaker

        speaker = get_shared_speaker()
        if speaker is not None:
            speaker.say(text[:120])
    except Exception:
        pass


def _handle_channel_message(channel_name: str, chat_id: str, text: str) -> str:
    """Callback invoked by channels when a message arrives."""
    if state.brain is None:
        return "Robot brain is not initialized. Please load a config first."

    # Push the incoming message into the context window
    if state.fs:
        state.fs.context.push("user", text,
                              metadata={"channel": channel_name, "chat_id": chat_id})

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
    thought = state.brain.think(image_bytes, instruction)
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
        state.fs.context.push("brain", thought.raw_text[:200],
                              metadata=thought.action)
        state.fs.proc.record_thought(thought.raw_text, thought.action)

    # Speak the reply out loud
    _speak_reply(thought.raw_text)

    return thought.raw_text


async def _start_channels():
    """Initialize and start all configured messaging channels."""
    from castor.channels import create_channel, get_ready_channels

    for name in get_ready_channels():
        try:
            channel = create_channel(name, on_message=_handle_channel_message)
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
    load_dotenv_if_available()

    config_path = os.getenv("OPENCASTOR_CONFIG", "robot.rcan.yaml")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                state.config = yaml.safe_load(f)
            logger.info(
                f"Loaded config: {state.config['metadata']['robot_name']}"
            )

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
                ruri_obj = (RURIClass.parse(state.ruri) if state.ruri
                           else RURIClass.from_config(state.config))
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

            # Initialize driver (simulation-safe)
            from castor.main import Camera, Speaker, get_driver

            state.driver = get_driver(state.config)

            # Initialize camera + speaker for live frames and TTS
            from castor.main import set_shared_camera, set_shared_speaker

            state.camera = Camera(state.config)
            set_shared_camera(state.camera)
            if state.fs:
                state.fs.proc.set_camera(
                    "online" if state.camera.is_available() else "offline"
                )

            state.speaker = Speaker(state.config)
            set_shared_speaker(state.speaker)
            if state.fs:
                state.fs.proc.set_speaker(
                    "online" if state.speaker.enabled else "offline"
                )
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
def main():
    import uvicorn

    load_dotenv_if_available()

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
