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

BRIDGE_VERSION = "1.5.0"

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
                "rcan_version": "1.5",  # GAP-12 version negotiation
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "status": {
                    "online": True,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                },
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

            self._robot_ref().set(
                {
                    "telemetry": {
                        **telemetry,
                        "last_seen": datetime.now(timezone.utc).isoformat(),
                    },
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

            scope: str = doc.get("scope", "chat")
            instruction: str = doc.get("instruction", "")
            is_estop = scope == "safety" and "estop" in instruction.lower()

            # --- GAP-03: Replay check BEFORE R2RAM (prevents DoS) ----------
            if not self._check_replay(cmd_id, doc, is_safety=(scope == "safety")):
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

            # --- Dispatch to local gateway -----------------------------------
            result = self._dispatch_to_gateway(scope, instruction, doc)

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

    def _dispatch_to_gateway(
        self, scope: str, instruction: str, doc: dict[str, Any]
    ) -> dict[str, Any]:
        """Forward a validated command to the local castor gateway."""
        import httpx

        headers = self._auth_headers()

        if scope == "status":
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

        elif scope in ("chat", "control"):
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{self.gateway_url}/api/command",
                    json={
                        "instruction": instruction,
                        "channel": "opencastor_app",
                        "context": "opencastor_fleet_ui",
                    },
                    headers=headers,
                )

        else:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{self.gateway_url}/api/command",
                    json={
                        "instruction": instruction,
                        "channel": "opencastor_app",
                        "context": "opencastor_fleet_ui",
                    },
                    headers=headers,
                )

        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            return resp.json()
        return {"raw": resp.text, "status_code": resp.status_code}

    # ------------------------------------------------------------------
    # Consent request handling
    # ------------------------------------------------------------------

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
                "Bridge LIVE — %s (%s) → Firebase %s [real-time listener, rcan=1.5]",
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
                "Bridge LIVE — %s (%s) → Firebase %s [poll mode, interval=%.0fs, rcan=1.5]",
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
