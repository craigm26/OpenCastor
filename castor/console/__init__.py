"""castor.console — the robot-side chat and capability surface.

Run it with ``python -m castor.console``; `castor up` writes a systemd user unit
that does exactly that. Configuration is entirely environment:

    ROBOT_HOME     the robot's state directory (default ~/robot)
    CONSOLE_TOKEN  the read-only bearer every endpoint but /console/health needs
    CONSOLE_PORT   listen port (default 8082 — `castor up`'s base_port + 2)
    OLLAMA_URL     the model daemon (default http://127.0.0.1:11434)
    CHAT_UPSTREAM  where chat turns go (default: Ollama, per OLLAMA_URL)
"""
from __future__ import annotations

from .app import build_app

__all__ = ["build_app"]
