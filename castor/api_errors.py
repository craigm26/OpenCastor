"""Standardised JSON error envelope for the OpenCastor API gateway.

All errors returned by the gateway now share the same shape::

    {"error": "<human-readable message>", "code": "<ERROR_CODE>", "status": <http_status>}

Usage
-----
Raise ``CastorAPIError`` anywhere inside an endpoint to return a structured
error response without having to construct ``JSONResponse`` manually::

    from castor.api_errors import CastorAPIError

    raise CastorAPIError("BRAIN_NOT_READY", "Brain is not initialized", 503)

Call ``register_error_handlers(app)`` once at application startup to install
the global exception handlers.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("OpenCastor.Gateway")


class CastorAPIError(Exception):
    """Raise this to return a structured JSON error from any endpoint."""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def register_error_handlers(app) -> None:
    """Install global exception handlers on the FastAPI *app* instance.

    Must be called after the ``app`` object is created but before any
    requests are handled (i.e. at module level in ``api.py``).
    """

    @app.exception_handler(CastorAPIError)
    async def _castor_error_handler(request: Request, exc: CastorAPIError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.message, "code": exc.code, "status": exc.status},
        )

    @app.exception_handler(HTTPException)
    async def _http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": detail,
                "code": f"HTTP_{exc.status_code}",
                "status": exc.status_code,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled gateway error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "code": "INTERNAL_ERROR",
                "status": 500,
            },
        )
