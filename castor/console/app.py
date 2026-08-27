"""The robot console — the chat brains and the live capability surface.

WHY THIS IS IN THE PACKAGE. A fresh `castor up` host got a gateway (signed
actuation) and a castor runtime (telemetry) and NO console — so the phone's
chat had no robot brain to talk to: not the Ollama models sitting on that same
Pi, not the Claude subscription that same Pi is signed in to. Every "brain"
feature in the app quietly degraded to the phone-only tiers. The console
existed, twice, as bench files in two robots' home directories, shared by an
``sys.path.insert`` and a comment saying the right long-term home is this
package. This is that move: one copy, parameterized by ROBOT_HOME, shipped by
`pip install opencastor` so chat arrives with the ten-minute path.

WHAT IT DELIBERATELY IS NOT. The arm robot's bench console also served camera
streams, perception, and calibration, because that robot is an arm with two
cameras and a workspace. None of that is portable — a vehicle whose only eye is
the phone on its back has no frames to serve — so the ported surface is the
routers every robot can honestly answer:

  * models       — the local Ollama catalog, the chat bridge, and the
                   `anthropic-sub` provider that shells the local `claude` CLI
                   with ANTHROPIC_API_KEY stripped, so it spends the operator's
                   SUBSCRIPTION and never a metered key.
  * capabilities — /surface (what this robot can do RIGHT NOW), /gaps, and
                   saved workflows. A macro grants no new authority: running one
                   issues ordinary signed /v1/invoke calls, each judged
                   independently by the gateway.
  * memory       — /memory/recall, the robot's own long-term memory searched by
                   MEANING rather than printed whole. Read-only, and the same
                   ranker `castor memory recall` uses.

``/camera/list`` answers with an empty list rather than 404, because the app asks
every console what it can see, and "nothing — the phone is my eye" is an answer,
not an error.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException

from .capabilities import router as capabilities_router
from .config import console_token, robot_home
from .memory import router as memory_router
from .models import read_active
from .models import router as models_router


def _matches(supplied: str | None, expected: str) -> bool:
    """Constant-time equality for a credential, safe for the absent case.

    Compared as BYTES: `compare_digest` refuses a str with non-ASCII characters
    and raises, and a phone that sends a mangled header must get a 401 — a 500
    would be the console reporting its own failure for someone else's typo.
    """
    if not supplied:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def _auth(authorization: str | None, token: str | None = None) -> None:
    """The read-only console bearer, failing closed.

    Deliberately NOT the actuate bearer: this token grants viewing and model
    management and nothing else, so it can ride in a QR code and (for hosts that
    serve frames) an <img> URL without ever being able to move a wheel. The
    query-string form exists for exactly that case; both are the same token.
    """
    configured = console_token()
    if not configured:
        raise HTTPException(status_code=503, detail="console auth not configured")
    if _matches(authorization, f"Bearer {configured}") or _matches(token, configured):
        return
    raise HTTPException(status_code=401, detail="missing or invalid bearer")


def require_console_auth(
    authorization: str | None = Header(default=None), token: str | None = None
) -> None:
    _auth(authorization, token)


def build_app() -> FastAPI:
    """Assemble the console. Env is read per request, so this is cheap and
    re-buildable — which is what lets a test stand one up over a scratch home."""
    app = FastAPI(title="OpenCastor robot console")

    @app.get("/console/health")
    def health() -> dict:
        """Liveness, unauthenticated on purpose: an operator diagnosing a robot
        that will not pair needs to know the console is up BEFORE they can find
        its token. It reports no secrets and no capability detail."""
        active = read_active()
        return {
            "status": "ok",
            "robot": os.environ.get("ROBOT_NAME", robot_home().name),
            "active_model": active.get("model", ""),
            "provider": active.get("provider", ""),
        }

    @app.get("/camera/list")
    def list_cameras(
        authorization: str | None = Header(default=None), token: str | None = None
    ) -> dict:
        _auth(authorization, token)
        # An answer, not an error. Hosts that serve frames add them here; a
        # vehicle whose only camera is the phone says so plainly.
        return {"cameras": []}

    app.include_router(models_router, dependencies=[Depends(require_console_auth)])
    app.include_router(capabilities_router, dependencies=[Depends(require_console_auth)])
    app.include_router(memory_router, dependencies=[Depends(require_console_auth)])
    return app


#: Importable target for `uvicorn castor.console.app:app`. `python -m
#: castor.console` is the supported way to run it (see __main__.py).
app = build_app()
