"""Multi-tenant workspace isolation for OpenCastor (issue #134).

Allows multiple teams/projects to share one OpenCastor gateway with full
isolation: separate robot configs, episode memory, credentials, and rate
limits per workspace.

Usage::

    from castor.workspace import get_manager

    mgr = get_manager()
    ws = mgr.create("team-alpha", admin_email="alice@example.com")
    token = mgr.issue_token(ws["id"], role="operator")

REST API:
    POST /workspaces               — create workspace {name, admin_email}
    GET  /workspaces               — list (admin only)
    GET  /workspaces/{id}/status   — workspace health
    POST /workspaces/{id}/token    — issue workspace-scoped JWT
"""

import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Workspace")

_STORE_DIR = Path(os.getenv("CASTOR_WORKSPACE_DIR", str(Path.home() / ".castor" / "workspaces")))

try:
    import jwt as _jwt

    HAS_JWT = True
except ImportError:
    HAS_JWT = False


class WorkspaceManager:
    """Manages isolated robot workspaces.

    Each workspace has:
    - Its own RCAN config path
    - Its own episode memory DB path
    - Its own API token (hashed)
    - Per-workspace usage quota
    """

    def __init__(self, store_dir: Path = _STORE_DIR):
        self._dir = store_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "index.json"
        self._workspaces: Dict[str, Dict[str, Any]] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if self._index_path.exists():
            try:
                with open(self._index_path) as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning("WorkspaceManager: load error: %s", exc)
        return {}

    def _save(self) -> None:
        try:
            with open(self._index_path, "w") as f:
                json.dump(self._workspaces, f, indent=2)
        except Exception as exc:
            logger.warning("WorkspaceManager: save error: %s", exc)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        admin_email: str = "",
        rcan_path: str = "",
    ) -> Dict[str, Any]:
        """Create a new isolated workspace.

        Args:
            name: Workspace name (must be unique).
            admin_email: Contact email for the workspace admin.
            rcan_path: Override RCAN config path for this workspace.

        Returns:
            Workspace metadata dict (id, name, admin_email, created_at).

        Raises:
            ValueError: If a workspace with this name already exists.
        """
        if any(w["name"] == name for w in self._workspaces.values()):
            raise ValueError(f"Workspace '{name}' already exists")

        ws_id = secrets.token_hex(8)
        ws_dir = self._dir / ws_id
        ws_dir.mkdir(parents=True, exist_ok=True)

        token_raw = secrets.token_hex(32)
        token_hash = hashlib.sha256(token_raw.encode()).hexdigest()

        entry: Dict[str, Any] = {
            "id": ws_id,
            "name": name,
            "admin_email": admin_email,
            "created_at": time.time(),
            "token_hash": token_hash,
            "rcan_path": rcan_path or str(ws_dir / "robot.rcan.yaml"),
            "memory_db": str(ws_dir / "memory.db"),
            "usage_db": str(ws_dir / "usage.db"),
            "enabled": True,
        }
        self._workspaces[ws_id] = entry
        self._save()
        logger.info("Workspace created: id=%s name=%s", ws_id, name)

        # Return with raw token (shown once)
        result = {k: v for k, v in entry.items() if k != "token_hash"}
        result["token"] = token_raw
        return result

    def get(self, ws_id: str) -> Optional[Dict[str, Any]]:
        """Return workspace metadata (token_hash excluded)."""
        entry = self._workspaces.get(ws_id)
        if entry is None:
            return None
        return {k: v for k, v in entry.items() if k != "token_hash"}

    def list(self) -> List[Dict[str, Any]]:
        """Return all workspaces (token_hash excluded), sorted by creation."""
        result = [
            {k: v for k, v in ws.items() if k != "token_hash"}
            for ws in self._workspaces.values()
        ]
        result.sort(key=lambda x: x.get("created_at", 0))
        return result

    def delete(self, ws_id: str) -> bool:
        """Delete a workspace by ID."""
        if ws_id not in self._workspaces:
            return False
        del self._workspaces[ws_id]
        self._save()
        logger.info("Workspace deleted: id=%s", ws_id)
        return True

    def verify_token(self, ws_id: str, raw_token: str) -> bool:
        """Verify a workspace-scoped token."""
        entry = self._workspaces.get(ws_id)
        if not entry:
            return False
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        return token_hash == entry.get("token_hash", "")

    def issue_token(
        self,
        ws_id: str,
        role: str = "operator",
        expires_in_hours: int = 24,
    ) -> str:
        """Issue a workspace-scoped JWT token.

        Args:
            ws_id: Workspace ID.
            role: Token role (admin/operator/viewer).
            expires_in_hours: Token validity in hours.

        Returns:
            JWT string if PyJWT is available, otherwise a raw hex token.

        Raises:
            ValueError: If workspace not found.
        """
        entry = self._workspaces.get(ws_id)
        if not entry:
            raise ValueError(f"Workspace '{ws_id}' not found")

        if HAS_JWT:
            import jwt

            secret = os.getenv("JWT_SECRET", os.getenv("OPENCASTOR_JWT_SECRET", entry["id"]))
            payload = {
                "sub": ws_id,
                "workspace": ws_id,
                "workspace_name": entry["name"],
                "role": role,
                "iat": int(time.time()),
                "exp": int(time.time()) + expires_in_hours * 3600,
            }
            return jwt.encode(payload, secret, algorithm="HS256")
        else:
            return secrets.token_hex(32)

    def status(self, ws_id: str) -> Dict[str, Any]:
        """Return health/status for a workspace."""
        entry = self._workspaces.get(ws_id)
        if not entry:
            raise ValueError(f"Workspace '{ws_id}' not found")

        rcan_exists = Path(entry.get("rcan_path", "")).exists()
        memory_exists = Path(entry.get("memory_db", "")).exists()

        return {
            "id": ws_id,
            "name": entry["name"],
            "enabled": entry.get("enabled", True),
            "rcan_configured": rcan_exists,
            "memory_initialized": memory_exists,
            "uptime_s": round(time.time() - entry["created_at"], 0),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: Optional[WorkspaceManager] = None


def get_manager() -> WorkspaceManager:
    """Return the process-wide WorkspaceManager."""
    global _manager
    if _manager is None:
        _manager = WorkspaceManager()
    return _manager
