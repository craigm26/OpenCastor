"""
RCAN HTTP Transport — send and receive RCAN messages over HTTP.

Allows two OpenCastor robots to exchange RCAN messages using:
  POST http://<host>:8000/api/rcan/message  — receive inbound message
  castor.rcan.http_transport.send_message() — send outbound message
  castor.rcan.http_transport.discover_robot() — probe a remote robot
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)


def send_message(
    target_host: str,
    message: dict,
    port: int = 8000,
    timeout_s: float = 5.0,
    api_token: Optional[str] = None,
) -> Optional[dict]:
    """Send a RCAN message to a remote OpenCastor instance over HTTP.

    Args:
        target_host: Hostname or IP (e.g. 'alex.local', '192.168.1.5')
        message:     RCAN message dict (from RCANMessage.to_dict())
        port:        RCAN API port (default 8000)
        timeout_s:   Request timeout in seconds
        api_token:   Optional Bearer token for authenticated remotes

    Returns:
        Response dict from remote, or None on failure.
    """
    url = f"http://{target_host}:{port}/api/rcan/message"
    body = json.dumps(message).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "opencastor-rcan/1.4",
    }
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            logger.info("RCAN message sent to %s: %s", target_host, result)
            return result
    except urllib.error.HTTPError as e:
        logger.error("RCAN HTTP error sending to %s: %s %s", target_host, e.code, e.reason)
        return None
    except urllib.error.URLError as e:
        logger.warning("RCAN transport error sending to %s: %s", target_host, e.reason)
        return None
    except Exception as e:
        logger.error("Unexpected error sending RCAN to %s: %s", target_host, e)
        return None


def discover_robot(
    host: str,
    port: int = 8000,
    timeout_s: float = 3.0,
    api_token: Optional[str] = None,
) -> Optional[dict]:
    """Probe a remote OpenCastor robot for its identity and capabilities.

    Calls GET /api/status on the remote host.

    Args:
        host:       Hostname or IP of the remote robot
        port:       API port (default 8000)
        timeout_s:  Request timeout in seconds
        api_token:  Optional Bearer token for authenticated remotes

    Returns:
        Robot status dict, or None if unreachable.
    """
    url = f"http://{host}:{port}/api/status"
    headers = {"User-Agent": "opencastor-rcan/1.4"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug("discover_robot(%s): %s", host, e)
        return None
