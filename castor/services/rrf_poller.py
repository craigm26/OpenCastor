"""
castor/services/rrf_poller — RRF revocation list polling service.

Polls https://api.rrf.rcan.dev/v2/revocations every ≤ 60 s when any
M2M_TRUSTED sessions are active. Stops automatically when no sessions remain.

On revocation event:
  1. Terminates the affected M2M_TRUSTED session
  2. Emits TRANSPARENCY (16) log entry to commitment chain
  3. Notifies owner via configured channel

Spec: §2.9 — M2M_TRUSTED revocation polling
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("OpenCastor.Services.RRFPoller")

RRF_REVOCATION_URL = "https://api.rrf.rcan.dev/v2/revocations"
POLL_INTERVAL_S = 55  # spec: ≤ 60 s


class RRFRevocationPoller:
    """Background asyncio task that polls the RRF revocation list.

    Usage:
        poller = RRFRevocationPoller(notify_fn=owner_notify)
        await poller.start()
        # ...
        await poller.stop()
    """

    def __init__(
        self,
        notify_fn=None,
        poll_interval: int = POLL_INTERVAL_S,
        revocation_url: str = RRF_REVOCATION_URL,
    ):
        self.notify_fn = notify_fn
        self.poll_interval = poll_interval
        self.revocation_url = revocation_url
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="rrf-revocation-poller")
        logger.info("RRF revocation poller started (interval=%ds)", self.poll_interval)

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("RRF revocation poller stopped")

    async def _poll_loop(self) -> None:
        from castor.auth.m2m_trusted import (
            get_active_sessions,
            has_active_m2m_trusted_sessions,
            revocation_cache,
            terminate_session,
        )

        while self._running:
            if not has_active_m2m_trusted_sessions():
                logger.debug("No active M2M_TRUSTED sessions — pausing poller")
                await asyncio.sleep(10)
                continue

            try:
                data = await asyncio.get_event_loop().run_in_executor(None, self._fetch_revocations)
                revoked_orchestrators = data.get("revoked_orchestrators", [])
                revoked_jtis = data.get("revoked_jtis", [])
                revocation_cache.update(revoked_orchestrators, revoked_jtis)

                # Check active sessions against new revocation list
                sessions = dict(get_active_sessions())
                for orch_id, _session in sessions.items():
                    if orch_id in revoked_orchestrators:
                        logger.warning(
                            "M2M_TRUSTED orchestrator '%s' is now revoked — terminating session",
                            orch_id,
                        )
                        terminate_session(orch_id, reason="revoked")
                        self._log_revocation_event(orch_id)
                        self._notify_owner(
                            f"M2M_TRUSTED orchestrator '{orch_id}' has been REVOKED by RRF. "
                            "Session terminated immediately."
                        )

            except Exception as e:
                logger.warning("RRF revocation poll failed: %s (will retry)", e)

            await asyncio.sleep(self.poll_interval)

    def _fetch_revocations(self) -> dict:
        req = Request(
            self.revocation_url,
            headers={"Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except URLError as e:
            raise RuntimeError(f"RRF revocation fetch failed: {e}") from e

    def _log_revocation_event(self, orchestrator_id: str) -> None:
        """Log revocation to commitment chain as TRANSPARENCY (16)."""
        try:
            from castor.rcan.commitment_chain import CommitmentChain

            chain = CommitmentChain.load()
            chain.append(
                {
                    "event_type": "m2m_trusted_revoked",
                    "orchestrator_id": orchestrator_id,
                    "timestamp": int(time.time()),
                    "source": "rrf_revocation_list",
                }
            )
        except Exception as e:
            logger.error("Failed to log M2M_TRUSTED revocation to commitment chain: %s", e)

    def _notify_owner(self, message: str) -> None:
        if self.notify_fn:
            try:
                self.notify_fn(message)
            except Exception as e:
                logger.error("Failed to notify owner of M2M revocation: %s", e)


# ---------------------------------------------------------------------------
# Singleton poller (used by runtime)
# ---------------------------------------------------------------------------

_poller: Optional[RRFRevocationPoller] = None


def get_poller(notify_fn=None) -> RRFRevocationPoller:
    """Return the global RRFRevocationPoller instance (create if needed)."""
    global _poller
    if _poller is None:
        _poller = RRFRevocationPoller(notify_fn=notify_fn)
    return _poller


async def ensure_poller_running(notify_fn=None) -> RRFRevocationPoller:
    """Ensure the poller is running. Call this when a new M2M_TRUSTED session starts."""
    poller = get_poller(notify_fn=notify_fn)
    await poller.start()
    return poller


def get_revocation_status(rrn: str) -> dict:
    """Return revocation status for *rrn* from the local cache.

    Returns a dict with keys:
      status        — "active" | "revoked" | "unknown"
      last_checked_s — epoch seconds of last successful poll (0 if never)
      source        — "cache" | "rrf" | "unknown"
    """
    try:
        from castor.auth.m2m_trusted import revocation_cache

        is_revoked = revocation_cache.is_revoked(rrn)
        last_checked = getattr(revocation_cache, "last_updated_s", 0) or 0
        return {
            "status": "revoked" if is_revoked else "active",
            "last_checked_s": int(last_checked),
            "source": "cache",
        }
    except Exception:
        return {"status": "unknown", "last_checked_s": 0, "source": "unknown"}


def force_poll() -> dict:
    """Synchronously fetch the RRF revocation list and update the cache.

    Returns a summary dict suitable for CLI display.
    """
    poller = get_poller()
    try:
        data = poller._fetch_revocations()
        revoked_orchestrators = data.get("revoked_orchestrators", [])
        revoked_jtis = data.get("revoked_jtis", [])
        try:
            from castor.auth.m2m_trusted import revocation_cache

            revocation_cache.update(revoked_orchestrators, revoked_jtis)
        except Exception:
            pass
        return {
            "ok": True,
            "revoked_orchestrators": len(revoked_orchestrators),
            "revoked_jtis": len(revoked_jtis),
            "polled_at": int(time.time()),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_cache_contents() -> dict:
    """Return the full contents of the local revocation cache."""
    try:
        from castor.auth.m2m_trusted import revocation_cache

        orchestrators = list(getattr(revocation_cache, "revoked_orchestrators", set()))
        jtis = list(getattr(revocation_cache, "revoked_jtis", set()))
        last_updated = getattr(revocation_cache, "last_updated_s", 0) or 0
        return {
            "revoked_orchestrators": orchestrators,
            "revoked_jtis": jtis,
            "last_updated_s": int(last_updated),
            "entry_count": len(orchestrators) + len(jtis),
        }
    except Exception as exc:
        return {
            "revoked_orchestrators": [],
            "revoked_jtis": [],
            "last_updated_s": 0,
            "entry_count": 0,
            "error": str(exc),
        }
