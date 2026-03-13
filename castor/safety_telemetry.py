"""Safety event telemetry for OpenCastor (issue #143).

Real-time safety metrics: bounds violations, guardian vetoes, e-stops,
and prompt injection blocks.  Provides a ring buffer + statistics API
and a WebSocket push channel.

Usage::

    from castor.safety_telemetry import get_telemetry

    tel = get_telemetry()
    tel.log("bounds_violation", detail="speed clamped to 0.3", action={"speed": 1.5})
    events = tel.recent(limit=50)
    stats = tel.stats()

REST API:
    GET  /api/safety/events           — recent safety events (limit=50)
    GET  /api/safety/stats            — counts by type, last 24h summary
    POST /api/safety/test-bounds      — test action against BoundsChecker

WebSocket:
    WS /ws/safety                     — real-time safety event push (5Hz)
"""

import collections
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.SafetyTelemetry")

_MAX_EVENTS = 1000
_EVENT_TYPES = {
    "estop",
    "bounds_violation",
    "guardian_veto",
    "injection_block",
    "rate_limit",
    "auth_failure",
    "config_error",
    "other",
}


class SafetyEventLogger:
    """Ring-buffer event store for safety-relevant incidents.

    Args:
        max_events: Maximum events to retain in memory.
    """

    def __init__(self, max_events: int = _MAX_EVENTS):
        self._events: collections.deque[dict[str, Any]] = collections.deque(maxlen=max_events)
        self._counters: dict[str, int] = {t: 0 for t in _EVENT_TYPES}
        self._subscribers: list[Any] = []  # WebSocket connections (weakrefs)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(
        self,
        event_type: str,
        detail: str = "",
        action: Optional[dict[str, Any]] = None,
        severity: str = "warning",
    ) -> dict[str, Any]:
        """Record a safety event.

        Args:
            event_type: Category (bounds_violation, estop, guardian_veto,
                        injection_block, rate_limit, auth_failure, other).
            detail: Human-readable description of the event.
            action: The action dict that triggered it (if applicable).
            severity: warning | error | critical

        Returns:
            The event dict that was recorded.
        """
        canonical = event_type if event_type in _EVENT_TYPES else "other"
        event: dict[str, Any] = {
            "id": f"{int(time.time() * 1000)}_{len(self._events)}",
            "event_type": canonical,
            "detail": detail,
            "action": action,
            "severity": severity,
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._events.append(event)
        self._counters[canonical] = self._counters.get(canonical, 0) + 1
        logger.log(
            logging.ERROR if severity == "critical" else logging.WARNING,
            "SafetyEvent [%s] %s",
            canonical,
            detail,
        )
        return event

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def recent(self, limit: int = 50, event_type: Optional[str] = None) -> list[dict[str, Any]]:
        """Return recent events newest-first.

        Args:
            limit: Max events to return.
            event_type: If set, filter by this event type.
        """
        events = list(self._events)
        if event_type:
            events = [e for e in events if e["event_type"] == event_type]
        return list(reversed(events))[:limit]

    def stats(self) -> dict[str, Any]:
        """Return safety statistics.

        Returns:
            Dict with total_events, by_type counts, last_24h counts,
            last_event timestamp.
        """
        now = time.time()
        cutoff_24h = now - 86400

        last_24h: dict[str, int] = {t: 0 for t in _EVENT_TYPES}
        for ev in self._events:
            if ev["timestamp"] >= cutoff_24h:
                last_24h[ev["event_type"]] = last_24h.get(ev["event_type"], 0) + 1

        last_event = None
        if self._events:
            last_event = self._events[-1]["timestamp_iso"]

        return {
            "total_events": len(self._events),
            "by_type": dict(self._counters),
            "last_24h": last_24h,
            "last_event": last_event,
        }

    def clear(self) -> None:
        """Clear all events (useful for tests)."""
        self._events.clear()
        self._counters = {t: 0 for t in _EVENT_TYPES}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_telemetry: Optional[SafetyEventLogger] = None


def get_telemetry() -> SafetyEventLogger:
    """Return the process-wide SafetyEventLogger."""
    global _telemetry
    if _telemetry is None:
        _telemetry = SafetyEventLogger()
    return _telemetry
