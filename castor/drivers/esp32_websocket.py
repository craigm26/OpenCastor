"""ESP32 network driver with WebSocket-first transport and HTTP fallback.

This driver targets ESP32 rover firmware that exposes:
  - ``GET /status`` for liveness
  - ``POST /cmd`` for command fallback
  - ``ws://<host>:<port>/<endpoint>`` for low-latency command transport

If the endpoint is unreachable, the driver stays in mock mode and logs outgoing
commands while periodically retrying based on ``reconnect_interval_ms``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

import requests

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.ESP32")

try:
    from websocket import WebSocket, create_connection

    HAS_WS_CLIENT = True
except ImportError:
    HAS_WS_CLIENT = False
    WebSocket = object  # type: ignore[assignment,misc]


class ESP32WebsocketDriver(DriverBase):
    """Control an ESP32 robot over WebSocket with HTTP fallback."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.host = str(config.get("host", "")).strip()
        self.port = int(config.get("port", 81))
        self.ws_endpoint = str(config.get("endpoint", "/ws")).strip() or "/ws"
        self.status_endpoint = str(config.get("status_endpoint", "/status")).strip() or "/status"
        self.command_endpoint = str(config.get("command_endpoint", "/cmd")).strip() or "/cmd"
        self.timeout_s = float(config.get("timeout_s", 1.2))
        self.reconnect_interval_s = float(config.get("reconnect_interval_ms", 2000)) / 1000.0

        self._session = requests.Session()
        self._ws: Optional[WebSocket] = None
        self._lock = threading.Lock()
        self._mode = "mock"
        self._last_error: Optional[str] = "driver not connected"
        self._next_retry_at = 0.0

        if not self.host:
            logger.warning("ESP32 driver host is missing; running in mock mode")

    @property
    def _base_http_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def _ws_url(self) -> str:
        endpoint = self.ws_endpoint if self.ws_endpoint.startswith("/") else f"/{self.ws_endpoint}"
        return f"ws://{self.host}:{self.port}{endpoint}"

    def _can_retry(self) -> bool:
        return time.monotonic() >= self._next_retry_at

    def _mark_failure(self, err: str) -> None:
        self._mode = "mock"
        self._last_error = err
        self._next_retry_at = time.monotonic() + self.reconnect_interval_s

    def _mark_success(self) -> None:
        self._mode = "hardware"
        self._last_error = None

    def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _ensure_websocket(self) -> Optional[WebSocket]:
        if not HAS_WS_CLIENT or not self.host:
            return None
        if self._ws is not None:
            return self._ws
        self._ws = create_connection(self._ws_url, timeout=self.timeout_s)
        return self._ws

    def _send_via_websocket(self, payload: Dict[str, Any]) -> bool:
        ws = self._ensure_websocket()
        if ws is None:
            return False
        ws.send(json.dumps(payload))
        return True

    def _send_via_http(self, payload: Dict[str, Any]) -> bool:
        if not self.host:
            return False
        endpoint = (
            self.command_endpoint
            if self.command_endpoint.startswith("/")
            else f"/{self.command_endpoint}"
        )
        self._session.post(
            f"{self._base_http_url}{endpoint}",
            json=payload,
            timeout=self.timeout_s,
        ).raise_for_status()
        return True

    def _send_command(self, payload: Dict[str, Any]) -> bool:
        if not self._can_retry():
            logger.debug("ESP32 retry window active; command skipped")
            return False

        with self._lock:
            ws_err = None
            http_err = None

            if HAS_WS_CLIENT:
                try:
                    if self._send_via_websocket(payload):
                        self._mark_success()
                        return True
                except Exception as exc:
                    ws_err = str(exc)
                    self._close_ws()

            try:
                if self._send_via_http(payload):
                    self._mark_success()
                    return True
            except Exception as exc:
                http_err = str(exc)

            err = f"ws={ws_err or 'n/a'} http={http_err or 'n/a'}"
            self._mark_failure(err)
            logger.warning("ESP32 command transport unavailable: %s", err)
            return False

    @staticmethod
    def _coerce_motion(linear_or_action: Any, angular: float) -> tuple[float, float]:
        if isinstance(linear_or_action, dict):
            linear = float(linear_or_action.get("linear", 0.0))
            ang = float(linear_or_action.get("angular", 0.0))
        else:
            linear = float(linear_or_action)
            ang = float(angular)
        return linear, ang

    def move(
        self,
        linear: float = 0.0,
        angular: float = 0.0,
        linear_x: Optional[float] = None,
        angular_z: Optional[float] = None,
    ) -> None:
        if linear_x is not None:
            linear = linear_x
        if angular_z is not None:
            angular = angular_z
        linear, angular = self._coerce_motion(linear, angular)

        payload = {
            "cmd": "drive",
            "linear": max(-1.0, min(1.0, float(linear))),
            "angular": max(-1.0, min(1.0, float(angular))),
        }
        if not self._send_command(payload):
            logger.info(
                "[MOCK ESP32] linear=%.2f angular=%.2f",
                payload["linear"],
                payload["angular"],
            )

    def stop(self) -> None:
        payload = {"cmd": "stop", "linear": 0.0, "angular": 0.0}
        if not self._send_command(payload):
            logger.info("[MOCK ESP32] stop")

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._close_ws()
        self._session.close()

    def health_check(self) -> Dict[str, Any]:
        if not self.host:
            return {"ok": False, "mode": "mock", "error": "missing host"}

        status_ep = (
            self.status_endpoint
            if self.status_endpoint.startswith("/")
            else f"/{self.status_endpoint}"
        )
        try:
            resp = self._session.get(
                f"{self._base_http_url}{status_ep}",
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            data = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            self._mark_success()
            return {
                "ok": bool(data.get("ok", True)),
                "mode": "hardware",
                "transport": "http",
                "error": None,
            }
        except Exception as http_exc:
            if HAS_WS_CLIENT:
                try:
                    with self._lock:
                        ws = self._ensure_websocket()
                        if ws is not None:
                            self._mark_success()
                            return {
                                "ok": True,
                                "mode": "hardware",
                                "transport": "websocket",
                                "error": None,
                            }
                except Exception as ws_exc:
                    self._mark_failure(f"ws={ws_exc} http={http_exc}")
                    return {"ok": False, "mode": "mock", "error": self._last_error}
            self._mark_failure(str(http_exc))
            return {"ok": False, "mode": "mock", "error": self._last_error}
