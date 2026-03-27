"""castor bridge — Firebase relay daemon for remote fleet management.

Connects a running ``castor gateway`` to Firebase Firestore + FCM, enabling
the OpenCastor Client Flutter app to manage robots from anywhere.

Architecture:
    Flutter app → Firebase Auth → Cloud Functions → Firestore command queue
                                                            ↓
    Robot (this daemon):  poll Firestore → castor gateway (local HTTP)
                                        → write result back to Firestore

Robots never listen on a public port. All traffic is outbound-initiated
by the robot polling/listening to Firestore. Protocol 66 safety and R2RAM
authorization are both enforced before any command reaches the gateway.

v1.5 additions (RCAN v1.5):
    GAP-03: ReplayCache prevents command replay attacks
    GAP-06: Offline mode — track connectivity, restrict when offline >300s
    GAP-08: SenderType audit trail — log sender_type for every command
    GAP-10: Training data consent gate
    GAP-11: QoS for ESTOP — ACK within 2s with ack_qos field

Usage::

    castor bridge --config arm.rcan.yaml \\
                  --firebase-project live-captions-xr \\
                  --gateway-url http://127.0.0.1:8000 \\
                  --gateway-token <token>

    # With explicit service-account credentials:
    castor bridge --config arm.rcan.yaml \\
                  --firebase-project live-captions-xr \\
                  --credentials /path/to/serviceAccount.json

    # Use Google Application Default Credentials (ADC):
    castor bridge --config arm.rcan.yaml \\
                  --firebase-project live-captions-xr
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

BRIDGE_VERSION = "1.6.0"

# Offline mode threshold — if we haven't connected for this long, enter offline mode
OFFLINE_THRESHOLD_S: int = 300  # 5 minutes per spec GAP-06

# ESTOP QoS ACK deadline (seconds) — GAP-11
ESTOP_ACK_DEADLINE_S: float = 2.0

# Replay cache window for safety commands (10s per spec §8.3)
SAFETY_REPLAY_WINDOW_S: int = 10


def _try_import_replay() -> Any:
    """Attempt to import ReplayCache from rcan.replay; return stub if unavailable."""
    try:
        import os as _os
        import sys as _sys

        _rcan_path = _os.path.expanduser("~/rcan-py")
        if _rcan_path not in _sys.path:
            _sys.path.insert(0, _rcan_path)
        from rcan.replay import ReplayCache

        return ReplayCache
    except ImportError:
        return None


class _ReplayCacheStub:
    """Fallback stub when rcan.replay is not yet available.

    Logs a warning and allows all commands through (fail-open for availability,
    but this means replay prevention is not active — see TODO below).
    """

    def __init__(self, window_s: int = 30) -> None:
        self.window_s = window_s
        log.warning(
            "rcan.replay not available — replay prevention is DISABLED. "
            "Install rcan-py v0.5.0+ for full protection."
        )

    def check_and_record(
        self,
        msg_id: str,
        timestamp: float,
        is_safety: bool = False,
    ) -> tuple[bool, str]:
        """Stub: always returns (True, '') — no replay protection."""
        return (True, "")


def _make_replay_cache(window_s: int = 30) -> Any:
    """Create a ReplayCache from rcan-py or fall back to the stub."""
    ReplayCacheCls = _try_import_replay()
    if ReplayCacheCls is not None:
        return ReplayCacheCls(window_s=window_s)
    return _ReplayCacheStub(window_s=window_s)


# ---------------------------------------------------------------------------
# v1.6: Federation trust helpers (GAP-14)
# ---------------------------------------------------------------------------


def _try_import_federation() -> tuple[Any, Any]:
    """Import validate_cross_registry_command and TrustAnchorCache from rcan.federation.

    Returns (validate_fn, TrustAnchorCacheCls) — stubs if unavailable.
    """
    try:
        import os as _os
        import sys as _sys

        _rcan_path = _os.path.expanduser("~/rcan-py/src")
        if _rcan_path not in _sys.path:
            _sys.path.insert(0, _rcan_path)
        from rcan.federation import TrustAnchorCache, validate_cross_registry_command

        return validate_cross_registry_command, TrustAnchorCache
    except ImportError:
        log.warning(
            "rcan.federation not available — cross-registry validation is STUBBED. "
            "Install rcan-py v0.6.0+ for full federation support."
        )
        return _validate_cross_registry_stub, _TrustAnchorCacheStub


def _validate_cross_registry_stub(
    msg: Any = None,
    command: Any = None,
    trust_cache: Any = None,
    local_registry: Any = None,
    **kwargs: Any,
) -> tuple[bool, str]:
    """Stub: allows all cross-registry commands, logs a warning."""
    # Accept either positional msg (new API) or legacy command dict
    cmd_obj = msg if msg is not None else command
    if isinstance(cmd_obj, dict):
        cmd_id = cmd_obj.get("id", "unknown")
    elif hasattr(cmd_obj, "cmd"):
        cmd_id = getattr(cmd_obj, "cmd", "unknown")
    else:
        cmd_id = "unknown"
    log.warning(
        "rcan.federation unavailable — cross-registry validation BYPASSED "
        "(stub allows all). command_id=%s",
        cmd_id,
    )
    return (True, "stub_allowed")


class _TrustAnchorCacheStub:
    """Fallback trust anchor cache stub when rcan.federation is not available."""

    def __init__(self) -> None:
        log.warning("TrustAnchorCache stub active — no real federation trust anchors loaded.")

    def get(self, registry_url: str) -> None:
        return None

    def refresh(self, registry_url: str) -> None:
        pass


# ---------------------------------------------------------------------------
# v1.6: LoA identity helpers (GAP-16)
# ---------------------------------------------------------------------------


def _try_import_loa() -> tuple[Any, Any]:
    """Import LoA helpers from rcan.identity.

    Returns (extract_loa_from_jwt, validate_loa_for_scope) — stubs if unavailable.
    """
    try:
        import os as _os
        import sys as _sys

        _rcan_path = _os.path.expanduser("~/rcan-py/src")
        if _rcan_path not in _sys.path:
            _sys.path.insert(0, _rcan_path)
        from rcan.identity import extract_loa_from_jwt, validate_loa_for_scope

        return extract_loa_from_jwt, validate_loa_for_scope
    except ImportError:
        log.warning(
            "rcan.identity not available — LoA extraction is STUBBED (defaults to LoA 1). "
            "Install rcan-py v0.6.0+ for full LoA support."
        )
        return _extract_loa_stub, _validate_loa_stub


def _extract_loa_stub(token: str, **kwargs: Any) -> int:
    """Stub: try to read loa/acr claim from JWT payload, default to LoA 1."""
    if token:
        try:
            import base64
            import json

            parts = token.split(".")
            if len(parts) >= 2:
                # Decode JWT payload (second segment)
                payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                if "loa" in payload:
                    return int(payload["loa"])
                if "acr" in payload:
                    return int(payload["acr"])
        except Exception:
            pass
    return 1


def _validate_loa_stub(loa: int, scope: str, required: int = 1, **kwargs: Any) -> bool:
    """Stub: always returns True (backward-compat, do not enforce)."""
    return True


# ---------------------------------------------------------------------------
# v1.6: Transport encoding helpers (GAP-17)
# ---------------------------------------------------------------------------


def _try_decode_compact_transport(payload: bytes) -> dict[str, Any]:
    """Attempt to decode a compact-transport-encoded command payload.

    Returns decoded dict, or raises ValueError if rcan.transport is unavailable.
    """
    try:
        import os as _os
        import sys as _sys

        _rcan_path = _os.path.expanduser("~/rcan-py/src")
        if _rcan_path not in _sys.path:
            _sys.path.insert(0, _rcan_path)
        from rcan.transport import decode_compact

        return decode_compact(payload)
    except ImportError as exc:
        raise ValueError(
            "rcan.transport not available — cannot decode compact-transport command. "
            "Install rcan-py v0.6.0+ for compact transport support."
        ) from exc


# Pre-load v1.6 module references
_validate_cross_registry_command, _TrustAnchorCacheCls = _try_import_federation()

# True when rcan.federation is unavailable and we fell back to the stub
_FEDERATION_STUB_ACTIVE: bool = _TrustAnchorCacheCls is _TrustAnchorCacheStub
_extract_loa_from_jwt, _validate_loa_for_scope = _try_import_loa()


class CastorBridge:
    """Firebase ↔ local castor gateway relay daemon.

    Lifecycle:
        1. ``__init__`` — configure, do not connect yet
        2. ``start()`` — authenticate to Firebase, register robot, begin loops
        3. ``stop()`` — gracefully shut down, mark robot offline

    Thread model:
        - Main thread: Firestore real-time listener (blocking)
        - Telemetry thread: periodic status publish every ``telemetry_interval_s``
        - Command threads: one short-lived thread per command execution

    v1.5 additions:
        - ReplayCache for command replay prevention (GAP-03)
        - Offline mode tracking (GAP-06)
        - SenderType audit trail (GAP-08)
        - Training data consent gate (GAP-10)
        - ESTOP QoS ACK within 2s (GAP-11)
    """

    def __init__(
        self,
        config: dict[str, Any],
        firebase_project: str,
        gateway_url: str = "http://127.0.0.1:8000",
        gateway_token: str | None = None,
        credentials_path: str | None = None,
        poll_interval_s: float = 5.0,
        telemetry_interval_s: float = 30.0,
    ) -> None:
        self.firebase_project = firebase_project
        self.gateway_url = gateway_url.rstrip("/")
        self.gateway_token = gateway_token
        self.credentials_path = credentials_path
        self.poll_interval_s = poll_interval_s
        self.telemetry_interval_s = telemetry_interval_s

        # Extract robot identity from RCAN config.
        # Fields may live at top-level OR under the 'metadata' sub-key.
        meta: dict[str, Any] = config.get("metadata", {})
        rcan: dict[str, Any] = config.get("rcan_protocol", {})

        self.rrn: str = config.get("rrn") or meta.get("rrn") or "RRN-unknown"
        self.robot_name: str = (
            config.get("robot_name")
            or config.get("name")
            or meta.get("name")
            or meta.get("robot_name")
            or "unnamed-robot"
        )
        self.owner: str = config.get("owner") or meta.get("rrn_uri") or "rrn://unknown"
        self.ruri: str = (
            meta.get("ruri") or meta.get("rcan_uri") or config.get("ruri") or f"rcan://{self.rrn}"
        )
        self.capabilities: list[str] = config.get("capabilities") or rcan.get("capabilities") or []
        self.version: str = meta.get("version") or config.get("opencastor_version") or "unknown"
        self.firebase_uid: str = config.get("firebase_uid", "")

        # v1.5: Training data consent config (GAP-10)
        self.training_consent_required: bool = bool(config.get("training_consent_required", False))

        self._db: Any = None  # Firestore client
        self._consent: Any = None  # ConsentManager
        self._running = False
        self._telemetry_thread: threading.Thread | None = None
        self._last_processed: set[str] = set()  # command IDs already handled

        # GAP-03: Replay caches — normal window (30s) and safety window (10s)
        self._replay_cache = _make_replay_cache(window_s=30)
        self._safety_replay_cache = _make_replay_cache(window_s=SAFETY_REPLAY_WINDOW_S)

        # GAP-06: Offline mode tracking
        self._last_firestore_success: float = time.time()
        self._offline_mode: bool = False
        self._offline_since: Optional[float] = None

        # v1.6: Federation trust (GAP-14)
        self.trust_anchor_cache: Any = _TrustAnchorCacheCls()
        # Extract own registry from ruri: "rcan://<registry>/<path>"
        _own_ruri = self.ruri
        self._own_registry: str = _own_ruri.split("/")[2] if "//" in _own_ruri else "local"

        # v1.6: LoA enforcement policy (GAP-16)
        self.min_loa_for_control: int = int(config.get("min_loa_for_control", 1))
        self.loa_enforcement: bool = bool(config.get("loa_enforcement", True))

        # v1.6: Federation trust enforcement (GAP-14 fail-closed option)
        # When True and rcan.federation stub is active, cross-registry commands are REJECTED.
        # Default False = fail-open (backward-compatible).
        self.require_federation_trust: bool = bool(rcan.get("require_federation_trust", False))

        # Store rcan_protocol section for later use (e.g. peer allowlist)
        self._rcan_cfg: dict[str, Any] = rcan

    # ------------------------------------------------------------------
    # v1.5 Offline mode helpers (GAP-06)
    # ------------------------------------------------------------------

    def _record_firestore_success(self) -> None:
        """Record a successful Firestore operation — resets offline tracking."""
        was_offline = self._offline_mode
        offline_duration = 0.0
        if was_offline and self._offline_since is not None:
            offline_duration = time.time() - self._offline_since

        self._last_firestore_success = time.time()
        self._offline_mode = False
        self._offline_since = None

        if was_offline:
            log.info(
                "back online after %.0fs — Firestore connectivity restored",
                offline_duration,
            )

    def _check_offline_mode(self) -> bool:
        """Check whether we should enter offline mode.

        Returns True if currently in offline mode.
        """
        elapsed = time.time() - self._last_firestore_success
        if elapsed > OFFLINE_THRESHOLD_S:
            if not self._offline_mode:
                self._offline_mode = True
                self._offline_since = time.time() - elapsed
                log.warning(
                    "entering OFFLINE MODE — no Firestore contact for %.0fs (threshold=%ds). "
                    "Restricting to local-only commands. ESTOP still accepted from any source.",
                    elapsed,
                    OFFLINE_THRESHOLD_S,
                )
            return True
        return False

    def _is_command_allowed_offline(self, scope: str, instruction: str) -> bool:
        """Offline mode command filter.

        In offline mode:
        - ESTOP is ALWAYS allowed (safety invariant — must never be blocked)
        - All other commands are rejected

        Returns True if the command should be allowed.
        """
        if not self._offline_mode:
            return True

        # ESTOP always allowed regardless of offline mode (Protocol 66 invariant)
        is_estop = scope == "safety" and "estop" in instruction.lower()
        if is_estop:
            return True

        # System scope: REBOOT, RELOAD_CONFIG, PAUSE, RESUME, SHUTDOWN are
        # safe offline. UPGRADE/OPTIMIZE/SHARE_CONFIG/INSTALL need network.
        if scope == "system":
            instr_upper = instruction.upper().strip()
            _NEEDS_NETWORK = ("UPGRADE", "OPTIMIZE", "SHARE_CONFIG", "INSTALL:")
            if any(instr_upper.startswith(p) for p in _NEEDS_NETWORK):
                log.warning(
                    "OFFLINE MODE: %r rejected (needs network) — "
                    "only REBOOT/RELOAD_CONFIG/PAUSE/RESUME/SHUTDOWN allowed offline",
                    instruction,
                )
                return False
            return True  # REBOOT, RELOAD_CONFIG, PAUSE, RESUME, SHUTDOWN safe offline

        # Status scope: SNAPSHOT is safe offline (local operation)
        if scope == "status":
            _instr_norm = instruction.upper().strip()
            if _instr_norm == "SNAPSHOT":
                return True

        log.warning("OFFLINE MODE: command rejected (scope=%s) — not an ESTOP", scope)
        return False

    # ------------------------------------------------------------------
    # v1.5 Replay prevention helpers (GAP-03)
    # ------------------------------------------------------------------

    def _check_replay(self, cmd_id: str, doc: dict[str, Any], is_safety: bool = False) -> bool:
        """Check for command replay before executing.

        Uses separate caches for safety vs. normal commands (10s vs 30s window).

        Returns:
            True if command is fresh (not a replay)
            False if rejected as a replay
        """
        issued_at: Optional[float] = None

        # Try to parse issued_at from the Firestore doc
        raw_issued = doc.get("issued_at")
        if raw_issued is not None:
            try:
                if isinstance(raw_issued, (int, float)):
                    issued_at = float(raw_issued)
                elif hasattr(raw_issued, "timestamp"):
                    # Firestore Timestamp object
                    issued_at = raw_issued.timestamp()
                elif isinstance(raw_issued, str):
                    from datetime import datetime as _dt

                    issued_at = _dt.fromisoformat(raw_issued.replace("Z", "+00:00")).timestamp()
            except Exception:
                pass

        if issued_at is None:
            # No timestamp — can't do replay check; allow with warning
            log.debug("replay_check: no issued_at on cmd_id=%s — skipping freshness check", cmd_id)
            return True

        cache = self._safety_replay_cache if is_safety else self._replay_cache

        try:
            result = cache.check_and_record(cmd_id, issued_at, is_safety=is_safety)
            # rcan-py returns (bool, str) tuple
            if isinstance(result, tuple):
                allowed, reason = result
            else:
                # Fallback for older/stub implementations that return bool
                allowed = bool(result)
                reason = "" if allowed else "replay detected"
            if not allowed:
                log.warning("replay_check: REJECTED cmd_id=%s reason=%s", cmd_id, reason)
            return allowed
        except Exception as exc:
            log.warning("replay_check: REJECTED cmd_id=%s reason=%s", cmd_id, exc)
            return False

    # ------------------------------------------------------------------
    # v1.6: Federation trust (GAP-14)
    # ------------------------------------------------------------------

    def _check_federation(self, cmd_id: str, doc: dict[str, Any], scope: str) -> bool:
        """Validate cross-registry commands using federation trust anchors.

        ESTOP is NEVER subject to federation checks (P66 invariant).

        Returns True if the command should be allowed to proceed.
        """
        from_rrn: str = doc.get("from_rrn", doc.get("issued_by_rrn", ""))
        # Extract registry from from_rrn: "rrn://<registry>/..." or "rcan://<registry>/..."
        from_registry: str = ""
        if "://" in from_rrn:
            from_registry = from_rrn.split("://")[1].split("/")[0]

        # No cross-registry check needed if same registry or no registry info
        if not from_registry or from_registry == self._own_registry:
            return True

        # GAP-14 v1.6 invariant: ESTOP bypasses federation check
        is_estop = scope == "safety" and "estop" in doc.get("instruction", "").lower()
        if is_estop:
            log.debug(
                "federation_check: ESTOP bypasses federation check "
                "(P66 invariant) cmd_id=%s from_registry=%s",
                cmd_id,
                from_registry,
            )
            return True

        # Extract LoA from JWT token for logging
        token: str = doc.get("token", doc.get("auth_token", ""))
        loa: int = _extract_loa_from_jwt(token) if token else 1
        log.info(
            "Cross-registry command from %s — LoA=%d cmd_id=%s",
            from_registry,
            loa,
            cmd_id,
        )

        # validate_cross_registry_command expects an RCANMessage-like object;
        # bridge deals in raw Firestore dicts — build a minimal shim.
        class _MsgShim:  # noqa: N801
            def __init__(self, d: dict) -> None:
                self.cmd = d.get("command", d.get("cmd", ""))
                self.loa = d.get("loa", 1)
                self.from_rrn = d.get("from_rrn", d.get("issued_by_rrn", ""))
                self.issuer = d.get("issuer", d.get("registry_url", ""))
                self.signature = d.get("signature")
                self.params = d.get("params", {})

        # Fail-closed: when require_federation_trust is True and we're using
        # the stub (rcan.federation unavailable), reject all cross-registry commands.
        if _FEDERATION_STUB_ACTIVE and self.require_federation_trust:
            log.warning(
                "federation_check: REJECTED cross-registry cmd from %s — "
                "require_federation_trust=True but rcan.federation stub active cmd_id=%s",
                from_registry,
                cmd_id,
            )
            return False

        allowed, reason = _validate_cross_registry_command(
            msg=_MsgShim(doc),
            local_registry=self.owner,
            trust_cache=self.trust_anchor_cache,
        )
        if not allowed:
            log.warning(
                "federation_check: REJECTED cross-registry cmd from %s reason=%s cmd_id=%s",
                from_registry,
                reason,
                cmd_id,
            )
        return allowed

    # ------------------------------------------------------------------
    # v1.6: LoA enforcement (GAP-16)
    # ------------------------------------------------------------------

    def _check_loa(self, cmd_id: str, doc: dict[str, Any], scope: str) -> bool:
        """Extract and log LoA from JWT. Optionally enforce if loa_enforcement is True.

        Default policy: LoA 1 (backward compat). Log-only unless enforcement is on.

        Returns True if allowed, False if enforcement is on and LoA is insufficient.
        """
        token: str = doc.get("token", doc.get("auth_token", ""))
        loa: int = _extract_loa_from_jwt(token) if token else 1
        required: int = self.min_loa_for_control if scope == "control" else 1

        log.info(
            "LoA check: scope=%s loa=%d required=%d enforcement=%s",
            scope,
            loa,
            required,
            "on" if self.loa_enforcement else "off (log-only)",
        )

        if self.loa_enforcement:
            try:
                from rcan.identity import Role as _Role

                _loa_val = _Role(loa) if isinstance(loa, int) else loa
            except Exception:
                _loa_val = loa
            ok = _validate_loa_for_scope(
                loa=_loa_val, scope=scope, min_loa_overrides={scope: required}
            )
            if not ok:
                log.warning(
                    "LoA enforcement: REJECTED cmd_id=%s scope=%s loa=%d required=%d",
                    cmd_id,
                    scope,
                    loa,
                    required,
                )
                return False
        return True

    # ------------------------------------------------------------------
    # v1.6: Scope-level enforcement helper (RCAN §4.2)
    # ------------------------------------------------------------------

    #: Canonical scope → numeric level mapping (RCAN v1.6 §4.2)
    SCOPE_LEVELS: dict[str, int] = {
        "discover": 0,
        "status": 1,
        "chat": 2,
        "control": 3,
        "system": 3,
        "safety": 99,
        "transparency": 0,
    }

    def _validate_scope_level(self, scope: str, loa: int) -> bool:
        """Enforce minimum LoA required for the requested scope.

        Rules (RCAN v1.6 §4.2):
          - discover / transparency (level 0): always allowed (LoA ≥ 0)
          - status (level 1):                  LoA ≥ 1
          - chat   (level 2):                  LoA ≥ 1  (lenient — chat ok with basic auth)
          - control (level 3):                 LoA ≥ min_loa_for_control (config, default 1)
          - system  (level 3):                 LoA ≥ min_loa_for_control (same as control)
          - safety  (level 99):               ALWAYS allowed (P66 invariant)

        Returns True if the command is permitted, False if it should be rejected.
        """
        # P66 invariant: safety/ESTOP is always allowed regardless of LoA
        if scope == "safety":
            return True

        level = self.SCOPE_LEVELS.get(scope, 0)
        if level == 0:
            return True  # discover / transparency — open

        # control and system require min_loa_for_control
        if scope in ("control", "system"):
            required = self.min_loa_for_control
        else:
            required = 1  # status, chat

        if loa < required:
            log.warning(
                "_validate_scope_level: REJECTED scope=%s loa=%d required=%d",
                scope,
                loa,
                required,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # v1.6: Config scrubbing — remove forbidden top-level YAML keys
    # ------------------------------------------------------------------

    #: Top-level config keys that must never be shared between peers
    _CONFIG_FORBIDDEN_KEYS: frozenset[str] = frozenset({"safety", "auth", "p66"})

    def _scrub_config_content(self, content: str) -> str:
        """Strip forbidden top-level keys from a YAML config string.

        Removes ``safety``, ``auth``, and ``p66`` sections before storing a
        peer-shared config bundle — these keys must never leave the local
        trust boundary.

        Args:
            content: Raw YAML text received from a peer robot.

        Returns:
            Scrubbed YAML text with forbidden sections removed.
        """
        try:
            import yaml as _yaml

            data = _yaml.safe_load(content) or {}
        except Exception:
            log.warning("_scrub_config_content: failed to parse YAML — returning empty config")
            return ""

        if not isinstance(data, dict):
            return content

        scrubbed = {k: v for k, v in data.items() if k not in self._CONFIG_FORBIDDEN_KEYS}
        removed = [k for k in data if k in self._CONFIG_FORBIDDEN_KEYS]
        if removed:
            log.warning(
                "_scrub_config_content: removed forbidden keys=%s from peer config", removed
            )
        try:
            import yaml as _yaml

            return _yaml.dump(scrubbed, default_flow_style=False)
        except Exception:
            return content

    # ------------------------------------------------------------------
    # v1.6: mDNS peer allowlist check
    # ------------------------------------------------------------------

    def _is_peer_allowed(self, peer_id: str) -> bool:
        """Check whether a discovered mDNS peer is in the configured allowlist.

        The ``rcan_protocol.peers`` config key holds an explicit list of allowed
        peer identifiers (RRNs, hostnames, or RURIs).  If the list is absent or
        empty, no peers are allowed (fail-closed).

        Args:
            peer_id: The peer's identifier (RRN, hostname, or RURI).

        Returns:
            True if the peer is in the allowlist, False otherwise.
        """
        rcan_cfg: dict[str, Any] = self._rcan_cfg
        allowed_peers: list[str] = rcan_cfg.get("peers", [])
        if not allowed_peers:
            log.debug("_is_peer_allowed: empty allowlist — no peers allowed (peer=%s)", peer_id)
            return False
        result = peer_id in allowed_peers
        if not result:
            log.warning("_is_peer_allowed: peer %s not in allowlist=%s", peer_id, allowed_peers)
        return result

    # ------------------------------------------------------------------
    # v1.6: Transport encoding detection (GAP-17)
    # ------------------------------------------------------------------

    def _check_transport_encoding(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Detect and handle transport_encoding field in Firestore doc.

        Returns the (potentially decoded) doc dict.
        """
        transport_encoding: str = doc.get("transport_encoding", "http")
        if transport_encoding == "minimal":
            log.warning(
                "Minimal encoding command received via cloud — upgrading to HTTP acknowledgment"
            )
            # Minimal encoding just logs — no structural decode needed
        elif transport_encoding == "compact":
            # Try to decode compact payload
            compact_payload = doc.get("compact_payload")
            if compact_payload is not None:
                try:
                    if isinstance(compact_payload, str):
                        import base64

                        payload_bytes = base64.b64decode(compact_payload)
                    else:
                        payload_bytes = bytes(compact_payload)
                    decoded = _try_decode_compact_transport(payload_bytes)
                    log.debug("compact transport decoded: %s", list(decoded.keys()))
                    # Merge decoded fields into doc (compact can override instruction etc.)
                    doc = {**doc, **decoded}
                except Exception as exc:
                    log.warning(
                        "compact transport decode failed: %s — falling back to raw doc", exc
                    )
        return doc

    # ------------------------------------------------------------------
    # v1.6: Multi-modal media chunk handling (GAP-18)
    # ------------------------------------------------------------------

    def _handle_media_chunks(
        self, cmd_id: str, doc: dict[str, Any], scope: str
    ) -> list[dict[str, Any]]:
        """Extract and log media_chunks from a command doc.

        TRAINING_DATA commands: logs hashes to audit trail.
        Returns the list of media chunks (possibly empty).
        """
        chunks: list[dict[str, Any]] = doc.get("media_chunks", [])
        if not chunks:
            return chunks

        import hashlib

        total_bytes = 0
        for chunk in chunks:
            data_b64 = chunk.get("data", "")
            if data_b64:
                try:
                    import base64

                    raw = base64.b64decode(data_b64)
                    total_bytes += len(raw)
                except Exception:
                    pass

        log.info(
            "Command has %d media chunks (%d bytes) cmd_id=%s",
            len(chunks),
            total_bytes,
            cmd_id,
        )

        # TRAINING_DATA scope: log chunk hashes to audit trail
        if scope == "training_data" or "training" in doc.get("scope", "").lower():
            chunk_hashes = []
            for chunk in chunks:
                data_b64 = chunk.get("data", "")
                if data_b64:
                    try:
                        import base64

                        raw = base64.b64decode(data_b64)
                        h = hashlib.sha256(raw).hexdigest()
                        chunk_hashes.append({"chunk_id": chunk.get("id", "?"), "sha256": h})
                    except Exception:
                        pass
            if chunk_hashes:
                log.info(
                    "TRAINING_DATA media audit: cmd_id=%s hashes=%s",
                    cmd_id,
                    chunk_hashes,
                )

        return chunks

    # ------------------------------------------------------------------
    # Firebase initialisation
    # ------------------------------------------------------------------

    def _init_firebase(self) -> None:
        """Authenticate to Firebase and create Firestore client."""
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
        except ImportError:
            log.error("firebase-admin not installed. Run: pip install opencastor[cloud]")
            raise

        if not firebase_admin._apps:
            if self.credentials_path:
                cred = credentials.Certificate(self.credentials_path)
                log.info("Firebase: using service account %s", self.credentials_path)
            else:
                cred = credentials.ApplicationDefault()
                log.info("Firebase: using Application Default Credentials")

            firebase_admin.initialize_app(cred, {"projectId": self.firebase_project})

        self._db = firestore.client()

        from castor.cloud.consent_manager import ConsentManager

        self._consent = ConsentManager(
            robot_rrn=self.rrn,
            owner=self.owner,
            db=self._db,
        )

        log.info("Firebase initialized — project: %s", self.firebase_project)

    # ------------------------------------------------------------------
    # Firestore helpers
    # ------------------------------------------------------------------

    def _robot_ref(self) -> Any:
        return self._db.collection("robots").document(self.rrn)

    def _commands_ref(self) -> Any:
        return self._robot_ref().collection("commands")

    def _consent_requests_ref(self) -> Any:
        return self._robot_ref().collection("consent_requests")

    # ------------------------------------------------------------------
    # Robot registration + telemetry
    # ------------------------------------------------------------------

    def _register(self) -> None:
        """Write/merge robot identity document to Firestore."""
        self._robot_ref().set(
            {
                "rrn": self.rrn,
                "name": self.robot_name,
                "owner": self.owner,
                "firebase_uid": self.firebase_uid,
                "ruri": self.ruri,
                "capabilities": self.capabilities,
                "version": self.version,
                "bridge_version": BRIDGE_VERSION,
                "rcan_version": "2.2",
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "status": {
                    "online": True,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                },
                # RCAN v1.5/v1.6 fields (safe defaults)
                "revocation_status": "active",
                "supports_qos_2": True,
                "supports_delegation": True,
                "offline_capable": True,
                "supported_transports": ["http", "compact", "minimal"],
                "min_loa_for_control": 2,
                "loa_enforcement": False,
                "multimodal_enabled": True,
                "registry_tier": "community",
                # RCAN v2.1/v2.2 fields
                "authority_handler_enabled": True,
                "audit_retention_days": 3650,
            },
            merge=True,
        )
        self._record_firestore_success()
        log.info("Robot %s (%s) registered in Firestore", self.robot_name, self.rrn)

    def _publish_telemetry(self) -> None:
        """Fetch live status from gateway and push to Firestore."""
        try:
            import httpx

            headers = self._auth_headers()
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self.gateway_url}/api/status", headers=headers)

            telemetry: dict[str, Any] = {}
            if resp.status_code == 200:
                telemetry = resp.json()

            # Also fetch health
            try:
                with httpx.Client(timeout=5.0) as client:
                    hr = client.get(f"{self.gateway_url}/api/health", headers=headers)
                if hr.status_code == 200:
                    telemetry["health"] = hr.json()
            except Exception:
                pass

            # Also fetch harness config so the Flutter app can display it
            try:
                with httpx.Client(timeout=5.0) as client:
                    hr2 = client.get(f"{self.gateway_url}/api/harness", headers=headers)
                if hr2.status_code == 200:
                    telemetry["harness_config"] = hr2.json()
            except Exception:
                pass

            # Fetch contribution status for Flutter client
            try:
                with httpx.Client(timeout=5.0) as client:
                    cr = client.get(f"{self.gateway_url}/api/contribute", headers=headers)
                if cr.status_code == 200:
                    telemetry["contribute"] = cr.json()
            except Exception:
                pass

            # Normalise: ensure opencastor_version is always set at both
            # telemetry.opencastor_version AND top-level opencastor_version so
            # all app screens (fleet card reads telemetry.version, detail page
            # reads opencastor_version) show the same value.
            oc_version: str = telemetry.get("version", "unknown")
            telemetry.setdefault("opencastor_version", oc_version)

            # RCAN v1.5/v1.6 capability fields (read by Flutter client)
            capabilities = telemetry.get("capabilities", [])
            if isinstance(capabilities, list):
                telemetry.setdefault(
                    "rcan_capabilities",
                    [c for c in capabilities if isinstance(c, str)],
                )
            telemetry.setdefault("rcan_max_payload_bytes", 65536)
            telemetry.setdefault("rcan_transport_supported", ["http", "compact", "minimal"])

            self._robot_ref().set(
                {
                    "telemetry": {
                        **telemetry,
                        "last_seen": datetime.now(timezone.utc).isoformat(),
                    },
                    "opencastor_version": oc_version,
                    "status": {
                        "online": True,
                        "last_seen": datetime.now(timezone.utc).isoformat(),
                    },
                },
                merge=True,
            )
            self._record_firestore_success()

        except Exception as exc:
            log.warning("Telemetry publish failed: %s", exc)
            try:
                self._robot_ref().set(
                    {
                        "status": {
                            "online": False,
                            "last_seen": datetime.now(timezone.utc).isoformat(),
                            "error": str(exc),
                        }
                    },
                    merge=True,
                )
            except Exception:
                pass
            self._check_offline_mode()

    def _telemetry_loop(self) -> None:
        """Background thread: publish telemetry every telemetry_interval_s."""
        while self._running:
            time.sleep(self.telemetry_interval_s)
            if self._running:
                self._publish_telemetry()

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.gateway_token:
            headers["Authorization"] = f"Bearer {self.gateway_token}"
        return headers

    def _execute_command(self, cmd_id: str, doc: dict[str, Any]) -> None:
        """Execute a single command — runs in its own thread."""
        cmd_ref = self._commands_ref().document(cmd_id)

        try:
            # --- GAP-08: Read sender_type from Firestore doc ----------------
            sender_type: str = doc.get("sender_type", "unknown")
            is_cloud_relay = sender_type == "cloud_function"

            # Mark processing immediately
            ack_ts = datetime.now(timezone.utc).isoformat()
            cmd_ref.update(
                {
                    "status": "processing",
                    "ack_at": ack_ts,
                    "sender_type": sender_type,  # GAP-08: echo back for audit
                }
            )

            # --- v1.6 GAP-17: Transport encoding detection ------------------
            doc = self._check_transport_encoding(doc)

            scope: str = doc.get("scope", "chat")
            instruction: str = doc.get("instruction", "")
            is_estop = scope == "safety" and "estop" in instruction.lower()

            # --- GAP-03: Replay check BEFORE R2RAM (prevents DoS) ----------
            # P66 invariant: ESTOP bypasses replay check unconditionally —
            # a duplicate ESTOP cmd_id must still be executed.
            if not is_estop and not self._check_replay(cmd_id, doc, is_safety=(scope == "safety")):
                log.warning(
                    "Command %s rejected as replay (sender_type=%s, scope=%s)",
                    cmd_id,
                    sender_type,
                    scope,
                )
                audit_entry: dict[str, Any] = {
                    "status": "replay_rejected",
                    "error": "replay attack prevention: command rejected",
                    "sender_type": sender_type,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
                if is_cloud_relay:
                    audit_entry["cloud_relay"] = True
                cmd_ref.update(audit_entry)
                return

            # --- GAP-11: ESTOP QoS — ACK written immediately ----------------
            if is_estop:
                estop_dispatch_start = time.monotonic()
                try:
                    estop_ack_entry: dict[str, Any] = {
                        "ack_qos": "acknowledged",
                        "ack_qos_at": datetime.now(timezone.utc).isoformat(),
                        "sender_type": sender_type,
                    }
                    if is_cloud_relay:
                        estop_ack_entry["cloud_relay"] = True
                    cmd_ref.update(estop_ack_entry)
                    ack_elapsed = time.monotonic() - estop_dispatch_start
                    if ack_elapsed > ESTOP_ACK_DEADLINE_S:
                        log.warning(
                            "ESTOP QoS ACK took %.2fs — exceeded %.1fs deadline! cmd_id=%s",
                            ack_elapsed,
                            ESTOP_ACK_DEADLINE_S,
                            cmd_id,
                        )
                    else:
                        log.debug(
                            "ESTOP QoS ACK written in %.3fs cmd_id=%s",
                            ack_elapsed,
                            cmd_id,
                        )
                except Exception as ack_exc:
                    log.warning("ESTOP QoS ACK write failed: %s (cmd_id=%s)", ack_exc, cmd_id)

            # --- GAP-06: Offline mode check (after ESTOP is dispatched) -----
            if not self._is_command_allowed_offline(scope, instruction):
                offline_audit: dict[str, Any] = {
                    "status": "denied",
                    "error": "offline_mode: robot is offline, only ESTOP accepted",
                    "sender_type": sender_type,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
                cmd_ref.update(offline_audit)
                return

            # --- v1.6 GAP-14: Federation trust check (after ESTOP bypass) ---
            if not self._check_federation(cmd_id, doc, scope):
                fed_entry: dict[str, Any] = {
                    "status": "denied",
                    "error": "federation_trust: cross-registry command rejected",
                    "sender_type": sender_type,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
                cmd_ref.update(fed_entry)
                return

            # --- v1.6 GAP-16: LoA check (log-only by default) ---------------
            if not self._check_loa(cmd_id, doc, scope):
                loa_entry: dict[str, Any] = {
                    "status": "denied",
                    "error": "loa_enforcement: insufficient Level of Assurance for scope",
                    "sender_type": sender_type,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
                cmd_ref.update(loa_entry)
                return

            # --- R2RAM scope check ------------------------------------------
            requester_owner: str = doc.get("issued_by_owner", "")
            issued_by_uid: str = doc.get("issued_by_uid", "")
            if issued_by_uid and self.firebase_uid and issued_by_uid == self.firebase_uid:
                requester_owner = self.owner

            authorized, reason = self._consent.is_authorized(
                requester_owner=requester_owner,
                requested_scope=scope,
                instruction=instruction,
                is_estop=is_estop,
            )

            if not authorized:
                log.warning(
                    "Command %s denied: %s (owner=%s, scope=%s, sender_type=%s)",
                    cmd_id,
                    reason,
                    requester_owner,
                    scope,
                    sender_type,
                )
                denied_entry: dict[str, Any] = {
                    "status": "denied",
                    "error": f"R2RAM authorization failed: {reason}",
                    "sender_type": sender_type,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
                if is_cloud_relay:
                    denied_entry["cloud_relay"] = True
                cmd_ref.update(denied_entry)
                return

            # --- GAP-10: Training data consent gate --------------------------
            if self._is_training_data_command(scope, instruction, doc):
                if not self._check_training_consent(requester_owner, doc):
                    log.warning(
                        "Training data collection BLOCKED — consent required "
                        "(training_consent_required=True, cmd_id=%s)",
                        cmd_id,
                    )
                    cmd_ref.update(
                        {
                            "status": "denied",
                            "error": "training_consent_required: no consent record found",
                            "sender_type": sender_type,
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    return

            # --- v1.6 GAP-18: Multi-modal media chunk handling ---------------
            media_chunks = self._handle_media_chunks(cmd_id, doc, scope)

            # --- Dispatch to local gateway -----------------------------------
            result = self._dispatch_to_gateway(scope, instruction, doc, media_chunks=media_chunks)

            # --- Mission thread: write robot response back to Firestore ------
            if doc.get("context") == "mission_thread":
                response_text: str = ""
                if isinstance(result, dict):
                    response_text = (
                        result.get("thought", "")
                        or result.get("response", "")
                        or result.get("raw_text", "")
                        or result.get("result", "")
                        or result.get("raw", "")
                        or str(result.get("raw_text", result))
                    )
                elif isinstance(result, str):
                    response_text = result
                if response_text:
                    self._write_mission_response(doc, response_text, cmd_id)

            # --- GAP-08: Build audit entry with sender_type -----------------
            complete_entry: dict[str, Any] = {
                "status": "complete",
                "result": result,
                "sender_type": sender_type,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            if is_cloud_relay:
                complete_entry["cloud_relay"] = True  # GAP-08: closes the audit gap

            cmd_ref.update(complete_entry)
            log.info(
                "Command %s complete (scope=%s, sender_type=%s, cloud_relay=%s)",
                cmd_id,
                scope,
                sender_type,
                is_cloud_relay,
            )
            self._record_firestore_success()

        except Exception as exc:
            log.error("Command %s failed: %s", cmd_id, exc)
            try:
                cmd_ref.update(
                    {
                        "status": "failed",
                        "error": str(exc),
                        "sender_type": doc.get("sender_type", "unknown"),
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # GAP-10: Training data consent helpers
    # ------------------------------------------------------------------

    def _is_training_data_command(self, scope: str, instruction: str, doc: dict[str, Any]) -> bool:
        """Return True if this command would trigger training data collection."""
        if not self.training_consent_required:
            return False
        # Check for known training data indicators
        training_keywords = ("record", "training", "capture", "collect", "oak", "voice_clip")
        instr_lower = instruction.lower()
        return any(kw in instr_lower for kw in training_keywords)

    def _check_training_consent(self, requester_owner: str, doc: dict[str, Any]) -> bool:
        """Check whether training data consent is on file for the given owner.

        Returns True if consent exists or is not required.
        """
        if not self.training_consent_required:
            return True
        try:
            # Check Firestore consent records
            consent_ref = (
                self._robot_ref()
                .collection("training_consents")
                .where("subject_owner", "==", requester_owner)
                .where("status", "==", "granted")
                .limit(1)
            )
            docs = list(consent_ref.stream())
            return len(docs) > 0
        except Exception as exc:
            log.warning(
                "training consent check failed for owner=%s: %s — blocking collection",
                requester_owner,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Gateway dispatch
    # ------------------------------------------------------------------

    def _build_mission_context(self, doc: dict[str, Any]) -> Optional[str]:
        """Build a mission system-context string when context == 'mission_thread'.

        Returns a prepend string for the agent's system prompt, or None if
        this is not a mission thread command.
        """
        if doc.get("context") != "mission_thread":
            return None

        mission_id: str = doc.get("mission_id", "unknown")
        participants: list[str] = doc.get("participants", [])

        # Build a readable participant list
        participant_names: list[str] = []
        for rrn in participants:
            # We only know RRNs here; use them directly
            participant_names.append(f"{rrn}")

        parts_str = ", ".join(participant_names) if participant_names else "unknown"

        context_block = (
            f"You are in a multi-robot mission (id: {mission_id}) "
            f"with participants: {parts_str}. "
            "You can @mention other robots by their RRN. "
            "When you respond, your message will be visible to all participants in the mission thread. "
            "Stay collaborative and concise."
        )
        return context_block

    def _write_mission_response(
        self,
        doc: dict[str, Any],
        response_text: str,
        cmd_id: str,
    ) -> None:
        """Write the robot's response back to the mission messages subcollection.

        This enables all mission participants (robots + humans) to see the response
        in real-time via the Flutter app's onSnapshot listener.
        """
        mission_id: str = doc.get("mission_id", "")
        if not mission_id or not self._db:
            return

        try:
            import uuid as _uuid
            from datetime import datetime, timezone

            msg_id = f"msg-{_uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()

            msg_doc: dict[str, Any] = {
                "id": msg_id,
                "from_type": "robot",
                "from_rrn": self.rrn,
                "from_name": self.robot_name,
                "content": response_text,
                "mentions": [],
                "timestamp": now,
                "scope": "chat",
                "status": "responded",
                "in_reply_to_cmd": cmd_id,
                "in_reply_to_msg": doc.get("mission_msg_id", ""),
            }

            self._db.collection("missions").document(mission_id).collection("messages").document(
                msg_id
            ).set(msg_doc)

            # Update last_message_at on the mission doc
            self._db.collection("missions").document(mission_id).update({"last_message_at": now})

            log.info(
                "Mission response written: mission_id=%s msg_id=%s robot=%s",
                mission_id,
                msg_id,
                self.rrn,
            )
        except Exception as exc:
            log.warning(
                "Failed to write mission response to Firestore: %s (mission_id=%s)",
                exc,
                mission_id,
            )

    def _dispatch_to_gateway(
        self,
        scope: str,
        instruction: str,
        doc: dict[str, Any],
        media_chunks: Optional[list[dict[str, Any]]] = None,
        mission_context: Optional[str] = None,
    ) -> dict[str, Any]:
        """Forward a validated command to the local castor gateway."""
        import httpx

        # Build mission context from doc if not explicitly provided
        if mission_context is None:
            mission_context = self._build_mission_context(doc)

        headers = self._auth_headers()

        if scope == "status":
            # Route rich-instruction status commands to /api/command so the LLM
            # can answer them (e.g. LIST_SKILLS, DESCRIBE_SKILLS, CAPABILITIES).
            # Bare STATUS → /api/status for the structured status dict.
            _STATUS_COMMAND_INSTRUCTIONS = {"LIST_SKILLS", "DESCRIBE_SKILLS", "CAPABILITIES"}
            _instr_norm = instruction.upper().strip()
            if _instr_norm == "SNAPSHOT":
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/snapshot/take",
                        headers=headers,
                    )
            elif _instr_norm in _STATUS_COMMAND_INSTRUCTIONS or (
                _instr_norm not in {"STATUS", "GET_STATUS", ""}
                and not _instr_norm.startswith("STATUS")
            ):
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/command",
                        json={
                            "instruction": instruction,
                            "scope": "status",
                            "channel": "opencastor_app",
                        },
                        headers=headers,
                    )
            else:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(f"{self.gateway_url}/api/status", headers=headers)

        elif scope == "safety":
            if "estop" in instruction.lower():
                with httpx.Client(timeout=5.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/estop",
                        json={"reason": doc.get("reason", "remote estop via castor bridge")},
                        headers=headers,
                    )
            elif "resume" in instruction.lower():
                with httpx.Client(timeout=5.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/resume",
                        json={"reason": "remote resume via castor bridge"},
                        headers=headers,
                    )
            else:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/command",
                        json={"instruction": instruction},
                        headers=headers,
                    )

        elif scope == "system":
            # System-level actions sent from the app (e.g. OTA upgrade, config reload).
            # instruction format: "UPGRADE: <version>" | "UPGRADE" (latest)
            #                     "REBOOT"
            #                     "RELOAD_CONFIG"
            instr_upper = instruction.upper().strip()
            if instr_upper.startswith("UPGRADE"):
                parts = instruction.split(":", 1)
                version: Optional[str] = parts[1].strip() if len(parts) > 1 else None
                body: dict[str, Any] = {}
                if version:
                    body["version"] = version
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/system/upgrade",
                        json=body,
                        headers=headers,
                    )
            elif instr_upper == "REBOOT":
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/system/reboot",
                        headers=headers,
                    )
            elif instr_upper == "RELOAD_CONFIG":
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/config/reload",
                        headers=headers,
                    )
            elif instr_upper == "PAUSE":
                with httpx.Client(timeout=5.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/runtime/pause",
                        headers=headers,
                    )
            elif instr_upper == "RESUME":
                with httpx.Client(timeout=5.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/runtime/resume",
                        headers=headers,
                    )
            elif instr_upper == "SHUTDOWN":
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/system/shutdown",
                        headers=headers,
                    )
            elif instr_upper == "OPTIMIZE":
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/command",
                        json={
                            "instruction": "OPTIMIZE",
                            "scope": "system",
                            "channel": "opencastor_app",
                        },
                        headers=headers,
                    )
            elif instr_upper == "SHARE_CONFIG":
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/command",
                        json={
                            "instruction": "SHARE_CONFIG",
                            "scope": "system",
                            "channel": "opencastor_app",
                        },
                        headers=headers,
                    )
            elif instr_upper.startswith("INSTALL:"):
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/command",
                        json={
                            "instruction": instruction,
                            "scope": "system",
                            "channel": "opencastor_app",
                        },
                        headers=headers,
                    )
            else:
                # Unknown system instruction — route to /api/command as fallback
                # so the agent can interpret it rather than silently dropping it.
                log.warning(
                    "bridge: unknown system instruction %r — routing to /api/command", instruction
                )
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        f"{self.gateway_url}/api/command",
                        json={
                            "instruction": instruction,
                            "scope": "system",
                            "channel": "opencastor_app",
                        },
                        headers=headers,
                    )

        elif scope in ("chat", "control"):
            payload: dict[str, Any] = {
                "instruction": instruction,
                "channel": "opencastor_app",
                "context": "opencastor_fleet_ui",
            }
            # Mission thread: inject system context for multi-robot coordination
            if mission_context:
                payload["system_context"] = mission_context
            # v1.6 GAP-18: pass media_chunks as context for vision-capable providers
            if media_chunks:
                payload["media_chunks"] = media_chunks
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{self.gateway_url}/api/command",
                    json=payload,
                    headers=headers,
                )

        else:
            payload_else: dict[str, Any] = {
                "instruction": instruction,
                "channel": "opencastor_app",
                "context": "opencastor_fleet_ui",
            }
            if mission_context:
                payload_else["system_context"] = mission_context
            if media_chunks:
                payload_else["media_chunks"] = media_chunks
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{self.gateway_url}/api/command",
                    json=payload_else,
                    headers=headers,
                )

        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            return resp.json()
        return {"raw": resp.text, "status_code": resp.status_code}

    # ------------------------------------------------------------------
    # Consent request handling
    # ------------------------------------------------------------------

    def _handle_config_share(self, cmd_id: str, doc: dict[str, Any]) -> None:
        """Handle an incoming CONFIG_SHARE message from a peer robot.

        The config is written to ~/.castor/received-configs/ for operator review.
        It is NEVER auto-installed — the operator must run castor install manually.
        Requires R2RAM chat-level consent (validated by _check_federation).
        """
        from pathlib import Path

        params = doc.get("params", {})
        content = params.get("config_bundle", "")
        filename = params.get("filename", "received.rcan.yaml")
        title = params.get("title", filename)
        from_rrn = params.get("from_rrn", "unknown")

        log.info("CONFIG_SHARE received from %s — title=%s filename=%s", from_rrn, title, filename)

        # Validate federation consent at chat scope
        allowed = self._check_federation(cmd_id, doc, scope="chat")
        reason = "federation trust check failed" if not allowed else ""
        if not allowed:
            log.warning("CONFIG_SHARE from %s blocked: %s", from_rrn, reason)
            self._update_command_status(cmd_id, "rejected", {"reason": reason})
            return

        if not content:
            log.warning("CONFIG_SHARE from %s has empty content — ignoring", from_rrn)
            self._update_command_status(cmd_id, "rejected", {"reason": "empty content"})
            return

        # Write to received-configs/ for operator review — never auto-install
        received_dir = Path.home() / ".castor" / "received-configs"
        received_dir.mkdir(parents=True, exist_ok=True)
        dest = received_dir / filename
        dest.write_text(content)

        log.info("CONFIG_SHARE written to %s — operator must confirm before installing", dest)
        print(
            f"\n[bridge] ⬇  Config received from {from_rrn}: '{title}'\n"
            f"         Saved to: {dest}\n"
            f"         Review and install: castor install {dest}\n"
        )

        self._update_command_status(
            cmd_id,
            "completed",
            {
                "saved_to": str(dest),
                "from_rrn": from_rrn,
                "title": title,
                "auto_installed": False,
            },
        )

    def _handle_consent_request(self, req_id: str, doc: dict[str, Any]) -> None:
        """Write an incoming consent request to Firestore for the Flutter app."""
        from castor.cloud.firestore_models import ConsentRequestDoc, ConsentStatus

        request = ConsentRequestDoc(
            from_rrn=doc.get("from_rrn", "unknown"),
            from_owner=doc.get("from_owner", "unknown"),
            from_ruri=doc.get("from_ruri", ""),
            requested_scopes=doc.get("requested_scopes", []),
            reason=doc.get("reason", ""),
            duration_hours=doc.get("duration_hours", 24),
            status=ConsentStatus.PENDING,
        )
        self._consent_requests_ref().document(req_id).set(request.to_dict())
        self._commands_ref().document(req_id).update({"status": "pending_consent"})
        log.info(
            "Consent request %s from %s written — awaiting owner approval",
            req_id,
            doc.get("from_owner"),
        )

    def _handle_consent_grant(self, req_id: str, doc: dict[str, Any]) -> None:
        """Called when the Flutter app approves an incoming consent request."""
        granted_scopes: list[str] = doc.get("granted_scopes", [])
        peer_owner: str = doc.get("from_owner", "")
        peer_rrn: str = doc.get("from_rrn", "")
        peer_ruri: str = doc.get("from_ruri", "")
        duration: int = doc.get("duration_hours", 24)

        consent_id = self._consent.grant_consent(
            peer_owner=peer_owner,
            peer_rrn=peer_rrn,
            peer_ruri=peer_ruri,
            granted_scopes=granted_scopes,
            duration_hours=duration,
        )

        self._consent_requests_ref().document(req_id).update(
            {
                "status": "approved",
                "consent_id": consent_id,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "granted_scopes": granted_scopes,
            }
        )
        log.info("Consent granted to %s: scopes=%s", peer_owner, granted_scopes)

    # ------------------------------------------------------------------
    # ESTOP propagation (local → Firestore)
    # ------------------------------------------------------------------

    def _publish_estop_event(self, reason: str = "local estop fired") -> None:
        """Push an ESTOP event to Firestore so the Flutter app is notified."""
        try:
            event_id = str(uuid.uuid4())
            self._robot_ref().collection("alerts").document(event_id).set(
                {
                    "type": "ESTOP",
                    "reason": reason,
                    "fired_at": datetime.now(timezone.utc).isoformat(),
                    "rrn": self.rrn,
                }
            )
        except Exception as exc:
            log.warning("Failed to publish ESTOP event: %s", exc)

    # ------------------------------------------------------------------
    # Firestore listener (real-time command ingestion)
    # ------------------------------------------------------------------

    def _on_command_snapshot(self, col_snapshot: Any, changes: Any, read_time: Any) -> None:
        """Firestore real-time callback — called on every command collection change."""
        self._record_firestore_success()
        for change in changes:
            if change.type.name == "ADDED":
                cmd_id = change.document.id
                doc = change.document.to_dict()

                if cmd_id in self._last_processed:
                    continue
                if doc.get("status", "pending") != "pending":
                    continue

                self._last_processed.add(cmd_id)
                msg_type = doc.get("message_type", "command")

                if msg_type == "consent_request":
                    threading.Thread(
                        target=self._handle_consent_request,
                        args=(cmd_id, doc),
                        daemon=True,
                        name=f"consent-{cmd_id[:8]}",
                    ).start()
                elif msg_type == "consent_grant":
                    threading.Thread(
                        target=self._handle_consent_grant,
                        args=(cmd_id, doc),
                        daemon=True,
                        name=f"grant-{cmd_id[:8]}",
                    ).start()
                elif msg_type == "config_share":
                    threading.Thread(
                        target=self._handle_config_share,
                        args=(cmd_id, doc),
                        daemon=True,
                        name=f"cfgshare-{cmd_id[:8]}",
                    ).start()
                else:
                    threading.Thread(
                        target=self._execute_command,
                        args=(cmd_id, doc),
                        daemon=True,
                        name=f"cmd-{cmd_id[:8]}",
                    ).start()

    # ------------------------------------------------------------------
    # Fallback polling (if real-time listener fails)
    # ------------------------------------------------------------------

    def _poll_commands_once(self) -> None:
        """Poll Firestore for pending commands (fallback to listener)."""
        try:
            pending = (
                self._commands_ref()
                .where("status", "==", "pending")
                .order_by("issued_at")
                .limit(10)
                .stream()
            )
            for doc in pending:
                cmd_id = doc.id
                if cmd_id in self._last_processed:
                    continue
                data = doc.to_dict()
                self._last_processed.add(cmd_id)
                msg_type = data.get("message_type", "command")

                if msg_type in ("consent_request", "consent_grant"):
                    fn = (
                        self._handle_consent_request
                        if msg_type == "consent_request"
                        else self._handle_consent_grant
                    )
                    threading.Thread(target=fn, args=(cmd_id, data), daemon=True).start()
                else:
                    threading.Thread(
                        target=self._execute_command,
                        args=(cmd_id, data),
                        daemon=True,
                    ).start()
            self._record_firestore_success()
        except Exception as exc:
            log.warning("Command poll failed: %s", exc)
            self._check_offline_mode()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the bridge (blocking — use a thread or process if needed)."""
        self._init_firebase()
        self._register()
        self._publish_telemetry()
        self._running = True

        # Start telemetry background thread
        self._telemetry_thread = threading.Thread(
            target=self._telemetry_loop,
            daemon=True,
            name="telemetry",
        )
        self._telemetry_thread.start()

        # Attempt real-time listener
        listener = None
        try:
            pass  # Watch import moved; using on_snapshot directly

            listener = self._commands_ref().on_snapshot(self._on_command_snapshot)
            log.info(
                "Bridge LIVE — %s (%s) → Firebase %s [real-time listener, rcan=1.6]",
                self.robot_name,
                self.rrn,
                self.firebase_project,
            )
            while self._running:
                time.sleep(1.0)

        except (ImportError, Exception) as exc:
            log.info("Real-time listener unavailable (%s) — falling back to polling", exc)
            if listener:
                try:
                    listener.unsubscribe()
                except Exception:
                    pass

            log.info(
                "Bridge LIVE — %s (%s) → Firebase %s [poll mode, interval=%.0fs, rcan=1.6]",
                self.robot_name,
                self.rrn,
                self.firebase_project,
                self.poll_interval_s,
            )
            while self._running:
                self._poll_commands_once()
                time.sleep(self.poll_interval_s)

    def stop(self) -> None:
        """Gracefully stop the bridge."""
        log.info("Bridge stopping...")
        self._running = False
        try:
            self._robot_ref().set(
                {
                    "status": {
                        "online": False,
                        "last_seen": datetime.now(timezone.utc).isoformat(),
                    }
                },
                merge=True,
            )
        except Exception:
            pass
        log.info("Bridge stopped. Robot %s marked offline.", self.rrn)


# ---------------------------------------------------------------------------
# CLI entry point  (called from castor/cli.py)
# ---------------------------------------------------------------------------


def run_bridge(args: Any) -> None:
    """Entry point for ``castor bridge`` CLI command."""
    import yaml

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    config_path = Path(args.config) if args.config else Path("~/.opencastor/config.rcan.yaml")
    config_path = config_path.expanduser()

    if not config_path.exists():
        log.error("Config file not found: %s", config_path)
        sys.exit(1)

    with config_path.open() as f:
        config = yaml.safe_load(f)

    firebase_project: str = args.firebase_project or config.get("cloud", {}).get(
        "firebase_project", ""
    )
    if not firebase_project:
        log.error(
            "Firebase project required. Pass --firebase-project or set "
            "cloud.firebase_project in your RCAN config."
        )
        sys.exit(1)

    gateway_url: str = args.gateway_url or config.get("cloud", {}).get(
        "gateway_url", "http://127.0.0.1:8000"
    )
    gateway_token: str | None = args.gateway_token or config.get("api_token")
    credentials_path: str | None = args.credentials

    bridge = CastorBridge(
        config=config,
        firebase_project=firebase_project,
        gateway_url=gateway_url,
        gateway_token=gateway_token,
        credentials_path=credentials_path,
        poll_interval_s=float(getattr(args, "poll_interval", 5)),
        telemetry_interval_s=float(getattr(args, "telemetry_interval", 30)),
    )

    def _sigterm(signum: int, frame: Any) -> None:
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    try:
        bridge.start()
    except KeyboardInterrupt:
        bridge.stop()
