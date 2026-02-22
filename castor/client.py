"""Python client SDK for OpenCastor REST API.

A typed, ergonomic wrapper around the OpenCastor REST API.  Suitable for
scripting, testing, and building custom integrations.

Usage::

    from castor.client import CastorClient

    with CastorClient("http://localhost:8000", token="my-api-token") as client:
        print(client.health())
        response = client.command("turn left slowly")
        print(response)

Async variant::

    from castor.client import CastorAsyncClient
    import asyncio

    async def main():
        async with CastorAsyncClient("http://localhost:8000") as client:
            print(await client.health())

    asyncio.run(main())

Environment:
    OPENCASTOR_API_URL    — Default API base URL
    OPENCASTOR_API_TOKEN  — Default bearer token
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

_DEFAULT_URL = os.getenv("OPENCASTOR_API_URL", "http://localhost:8000")
_DEFAULT_TOKEN = os.getenv("OPENCASTOR_API_TOKEN", "")
_DEFAULT_TIMEOUT = 30


class CastorError(Exception):
    """Raised when the OpenCastor API returns an error response."""

    def __init__(self, status: int, body: Any):
        self.status = status
        self.body = body
        msg = body.get("error", body.get("detail", str(body))) if isinstance(body, dict) else str(body)
        super().__init__(f"HTTP {status}: {msg}")


class CastorClient:
    """Synchronous OpenCastor API client.

    Args:
        base_url: API base URL (e.g. ``http://localhost:8000``).
        token: Bearer token.  Falls back to ``OPENCASTOR_API_TOKEN`` env var.
        timeout: Request timeout in seconds (default 30).
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_URL,
        token: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token or _DEFAULT_TOKEN
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "CastorClient":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal HTTP
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        raw: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                content = resp.read()
                if raw:
                    return content
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            try:
                err_body = json.loads(exc.read())
            except Exception:
                err_body = {"error": exc.reason}
            raise CastorError(exc.code, err_body) from exc
        except urllib.error.URLError as exc:
            raise CastorError(0, {"error": str(exc.reason)}) from exc

    def _get(self, path: str, **params: Any) -> Any:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        full = f"{path}?{qs}" if qs else path
        return self._request("GET", full)

    def _post(self, path: str, body: Optional[dict] = None) -> Any:
        return self._request("POST", path, body)

    def _delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # ------------------------------------------------------------------
    # Health & Status
    # ------------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        """GET /health — Quick liveness check."""
        return self._get("/health")

    def status(self) -> Dict[str, Any]:
        """GET /api/status — Runtime status including providers and channels."""
        return self._get("/api/status")

    def provider_health(self) -> Dict[str, Any]:
        """GET /api/provider/health — Brain provider health check."""
        return self._get("/api/provider/health")

    def driver_health(self) -> Dict[str, Any]:
        """GET /api/driver/health — Hardware driver health check."""
        return self._get("/api/driver/health")

    # ------------------------------------------------------------------
    # Command & Control
    # ------------------------------------------------------------------

    def command(self, instruction: str, surface: str = "api") -> Dict[str, Any]:
        """POST /api/command — Send instruction to the LLM brain.

        Args:
            instruction: Natural language instruction.
            surface: Channel surface hint (default ``"api"``).

        Returns:
            Dict with ``raw_text`` and ``action`` keys.
        """
        return self._post("/api/command", {"instruction": instruction, "surface": surface})

    def command_stream(self, instruction: str) -> Iterator[str]:
        """POST /api/command/stream — Stream LLM tokens as NDJSON.

        Yields individual text chunks as they arrive.
        """
        url = f"{self.base_url}/api/command/stream"
        body = json.dumps({"instruction": instruction}).encode()
        req = urllib.request.Request(
            url, data=body, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                for line in resp:
                    line = line.strip()
                    if line:
                        try:
                            chunk = json.loads(line)
                            yield chunk.get("text", "")
                        except json.JSONDecodeError:
                            yield line.decode()
        except urllib.error.HTTPError as exc:
            raise CastorError(exc.code, {"error": exc.reason}) from exc

    def action(self, **kwargs: Any) -> Dict[str, Any]:
        """POST /api/action — Direct motor command (bypass brain)."""
        return self._post("/api/action", kwargs)

    def stop(self) -> Dict[str, Any]:
        """POST /api/stop — Emergency stop."""
        return self._post("/api/stop")

    def estop_clear(self) -> Dict[str, Any]:
        """POST /api/estop/clear — Clear emergency stop."""
        return self._post("/api/estop/clear")

    # ------------------------------------------------------------------
    # Memory & Episodes
    # ------------------------------------------------------------------

    def memory_episodes(self, limit: int = 20) -> List[Dict[str, Any]]:
        """GET /api/memory/episodes — Recent episodes."""
        return self._get("/api/memory/episodes", limit=limit)

    def memory_export(self) -> bytes:
        """GET /api/memory/export — Download all episodes as JSONL."""
        return self._request("GET", "/api/memory/export", raw=True)

    def memory_clear(self) -> Dict[str, Any]:
        """DELETE /api/memory/episodes — Clear all episode memory."""
        return self._delete("/api/memory/episodes")

    # ------------------------------------------------------------------
    # Usage
    # ------------------------------------------------------------------

    def usage(self) -> Dict[str, Any]:
        """GET /api/usage — Token/cost summary."""
        return self._get("/api/usage")

    # ------------------------------------------------------------------
    # Runtime control
    # ------------------------------------------------------------------

    def pause(self) -> Dict[str, Any]:
        """POST /api/runtime/pause — Pause the perception-action loop."""
        return self._post("/api/runtime/pause")

    def resume(self) -> Dict[str, Any]:
        """POST /api/runtime/resume — Resume the perception-action loop."""
        return self._post("/api/runtime/resume")

    def runtime_status(self) -> Dict[str, Any]:
        """GET /api/runtime/status — Loop running/paused state."""
        return self._get("/api/runtime/status")

    def config_reload(self) -> Dict[str, Any]:
        """POST /api/config/reload — Hot-reload robot.rcan.yaml."""
        return self._post("/api/config/reload")

    # ------------------------------------------------------------------
    # Command history
    # ------------------------------------------------------------------

    def command_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """GET /api/command/history — Last N instruction→thought pairs."""
        return self._get("/api/command/history", limit=limit)

    # ------------------------------------------------------------------
    # Behaviors
    # ------------------------------------------------------------------

    def behavior_run(self, behavior_file: str, behavior_name: str) -> Dict[str, Any]:
        """POST /api/behavior/run — Start a named behavior sequence."""
        return self._post(
            "/api/behavior/run",
            {"behavior_file": behavior_file, "behavior_name": behavior_name},
        )

    def behavior_stop(self) -> Dict[str, Any]:
        """POST /api/behavior/stop — Stop the running behavior."""
        return self._post("/api/behavior/stop")

    def behavior_status(self) -> Dict[str, Any]:
        """GET /api/behavior/status."""
        return self._get("/api/behavior/status")

    def behavior_generate(self, description: str, steps_hint: int = 5) -> Dict[str, Any]:
        """POST /api/behavior/generate — Generate YAML behavior from description."""
        return self._post(
            "/api/behavior/generate",
            {"description": description, "steps_hint": steps_hint},
        )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def nav_waypoint(
        self, distance_m: float, heading_deg: float, speed: float = 0.6
    ) -> Dict[str, Any]:
        """POST /api/nav/waypoint — Dead-reckoning move."""
        return self._post(
            "/api/nav/waypoint",
            {"distance_m": distance_m, "heading_deg": heading_deg, "speed": speed},
        )

    def nav_status(self) -> Dict[str, Any]:
        """GET /api/nav/status — Current navigation job status."""
        return self._get("/api/nav/status")

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def list_webhooks(self) -> List[Dict[str, Any]]:
        """GET /api/webhooks — List registered outbound webhooks."""
        return self._get("/api/webhooks")

    def add_webhook(
        self,
        url: str,
        events: Optional[List[str]] = None,
        secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/webhooks — Register a new outbound webhook."""
        return self._post(
            "/api/webhooks",
            {"url": url, "events": events or ["*"], "secret": secret},
        )

    def delete_webhook(self, url: str) -> Dict[str, Any]:
        """DELETE /api/webhooks — Remove a webhook by URL."""
        return self._post("/api/webhooks/delete", {"url": url})

    # ------------------------------------------------------------------
    # Recordings
    # ------------------------------------------------------------------

    def recording_start(self, session_name: Optional[str] = None) -> Dict[str, Any]:
        """POST /api/recording/start — Start video episode recording."""
        return self._post("/api/recording/start", {"session_name": session_name})

    def recording_stop(self) -> Dict[str, Any]:
        """POST /api/recording/stop — Stop recording and flush to disk."""
        return self._post("/api/recording/stop")

    def recording_list(self) -> List[Dict[str, Any]]:
        """GET /api/recording/list — List saved recordings."""
        return self._get("/api/recording/list")

    # ------------------------------------------------------------------
    # Gestures
    # ------------------------------------------------------------------

    def gesture_frame(self, image_base64: str) -> Dict[str, Any]:
        """POST /api/gesture/frame — Recognize gesture from base64 JPEG."""
        return self._post("/api/gesture/frame", {"image_base64": image_base64})


# ---------------------------------------------------------------------------
# Async client (requires httpx)
# ---------------------------------------------------------------------------

try:
    import httpx  # type: ignore

    class CastorAsyncClient:
        """Async OpenCastor API client (requires ``httpx``).

        Usage::

            async with CastorAsyncClient("http://localhost:8000") as client:
                print(await client.health())
        """

        def __init__(
            self,
            base_url: str = _DEFAULT_URL,
            token: Optional[str] = None,
            timeout: int = _DEFAULT_TIMEOUT,
        ):
            self.base_url = base_url.rstrip("/")
            self._token = token or _DEFAULT_TOKEN
            self._timeout = timeout
            self._client: Optional[httpx.AsyncClient] = None

        async def __aenter__(self) -> "CastorAsyncClient":
            headers: Dict[str, str] = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self._timeout,
            )
            return self

        async def __aexit__(self, *args: Any) -> None:
            if self._client:
                await self._client.aclose()

        def _check_client(self) -> httpx.AsyncClient:
            if self._client is None:
                raise RuntimeError("Use 'async with CastorAsyncClient(...) as client:'")
            return self._client

        async def _get(self, path: str, **params: Any) -> Any:
            resp = await self._check_client().get(path, params={k: v for k, v in params.items() if v is not None})
            resp.raise_for_status()
            return resp.json()

        async def _post(self, path: str, body: Optional[dict] = None) -> Any:
            resp = await self._check_client().post(path, json=body)
            resp.raise_for_status()
            return resp.json()

        async def health(self) -> Dict[str, Any]:
            return await self._get("/health")

        async def status(self) -> Dict[str, Any]:
            return await self._get("/api/status")

        async def command(self, instruction: str) -> Dict[str, Any]:
            return await self._post("/api/command", {"instruction": instruction})

        async def stop(self) -> Dict[str, Any]:
            return await self._post("/api/stop")

        async def memory_episodes(self, limit: int = 20) -> List[Dict[str, Any]]:
            return await self._get("/api/memory/episodes", limit=limit)

        async def usage(self) -> Dict[str, Any]:
            return await self._get("/api/usage")

        async def provider_health(self) -> Dict[str, Any]:
            return await self._get("/api/provider/health")

        async def pause(self) -> Dict[str, Any]:
            return await self._post("/api/runtime/pause")

        async def resume(self) -> Dict[str, Any]:
            return await self._post("/api/runtime/resume")

        async def recording_start(self, session_name: Optional[str] = None) -> Dict[str, Any]:
            return await self._post("/api/recording/start", {"session_name": session_name})

        async def recording_stop(self) -> Dict[str, Any]:
            return await self._post("/api/recording/stop")

        async def nav_waypoint(
            self, distance_m: float, heading_deg: float, speed: float = 0.6
        ) -> Dict[str, Any]:
            return await self._post(
                "/api/nav/waypoint",
                {"distance_m": distance_m, "heading_deg": heading_deg, "speed": speed},
            )

except ImportError:
    # httpx not installed — async client not available
    class CastorAsyncClient:  # type: ignore[no-redef]
        """Async client requires httpx: pip install httpx"""

        def __init__(self, *args: Any, **kwargs: Any):
            raise ImportError(
                "CastorAsyncClient requires httpx. Install with: pip install httpx"
            )
