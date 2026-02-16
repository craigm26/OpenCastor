"""
OpenCastor API Gateway.
FastAPI server that provides REST endpoints for remote control,
telemetry streaming, and messaging channel webhooks.

Run with:
    python -m castor.api --config robot.rcan.yaml
    # or
    castor gateway --config robot.rcan.yaml
"""

import os
import time
import asyncio
import logging
import argparse
from typing import Dict, Optional

import yaml
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from castor.auth import (
    load_dotenv_if_available,
    list_available_providers,
    list_available_channels,
)

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
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


state = AppState()

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
API_TOKEN = os.getenv("OPENCASTOR_API_TOKEN")


async def verify_token(request: Request):
    """Optional bearer-token auth when OPENCASTOR_API_TOKEN is set."""
    if not API_TOKEN:
        return  # No token configured -- open access
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


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


@app.get("/api/status", dependencies=[Depends(verify_token)])
async def get_status():
    """Return current runtime status and available integrations."""
    return {
        "config_loaded": state.config is not None,
        "robot_name": (
            state.config.get("metadata", {}).get("robot_name") if state.config else None
        ),
        "providers": list_available_providers(),
        "channels_available": list_available_channels(),
        "channels_active": list(state.channels.keys()),
        "last_thought": state.last_thought,
    }


@app.post("/api/command", dependencies=[Depends(verify_token)])
async def send_command(cmd: CommandRequest):
    """Send an instruction to the robot's brain and receive the action."""
    if state.brain is None:
        raise HTTPException(status_code=503, detail="Brain not initialized")

    # Use provided image or a blank frame
    if cmd.image_base64:
        import base64

        image_bytes = base64.b64decode(cmd.image_base64)
    else:
        image_bytes = b"\x00" * 1024

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
    return {"status": "stopped"}


# ---------------------------------------------------------------------------
# Webhook endpoints for messaging channels
# ---------------------------------------------------------------------------
@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request):
    """Twilio WhatsApp webhook endpoint."""
    channel = state.channels.get("whatsapp")
    if not channel:
        raise HTTPException(status_code=503, detail="WhatsApp channel not configured")

    form = await request.form()
    reply = await channel.handle_webhook(dict(form))
    return JSONResponse(content={"reply": reply})


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


def _handle_channel_message(channel_name: str, chat_id: str, text: str) -> str:
    """Callback invoked by channels when a message arrives."""
    if state.brain is None:
        return "Robot brain is not initialized. Please load a config first."

    thought = state.brain.think(b"\x00" * 1024, text)
    state.last_thought = {
        "raw_text": thought.raw_text,
        "action": thought.action,
        "timestamp": time.time(),
        "source": f"{channel_name}:{chat_id}",
    }

    if thought.action and state.driver:
        _execute_action(thought.action)

    return thought.raw_text


async def _start_channels():
    """Initialize and start all configured messaging channels."""
    from castor.channels import get_ready_channels, create_channel

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

            # Initialize brain
            from castor.providers import get_provider

            state.brain = get_provider(state.config["agent"])
            logger.info(f"Brain online: {state.config['agent'].get('model')}")

            # Initialize driver (simulation-safe)
            from castor.main import get_driver

            state.driver = get_driver(state.config)
        except Exception as e:
            logger.warning(f"Config load error (gateway still operational): {e}")
    else:
        logger.info(
            f"No config at {config_path} -- gateway running in unconfigured mode. "
            "Use POST /api/command after loading a config."
        )

    await _start_channels()
    logger.info(
        f"OpenCastor Gateway ready on "
        f"{os.getenv('OPENCASTOR_API_HOST', '127.0.0.1')}:"
        f"{os.getenv('OPENCASTOR_API_PORT', '8000')}"
    )


@app.on_event("shutdown")
async def on_shutdown():
    await _stop_channels()
    if state.driver:
        state.driver.close()
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
