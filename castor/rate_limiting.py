"""
castor/rate_limiting.py — RCAN-driven per-endpoint rate limiting (issue #258).

Reads rate-limit configuration from an RCAN config dict and provides
FastAPI middleware + helper functions to enforce per-IP and per-user limits.

RCAN config::

    rate_limits:
    - endpoint: "/api/command"
      per_ip: 10
      per_user: 20
      window_s: 60
    - endpoint: "/api/webhook"
      per_ip: 5
      per_user: 10
      window_s: 60

Usage::

    from castor.rate_limiting import RateLimitConfig, RateLimiter

    cfg = RateLimitConfig.from_rcan(rcan_config)
    limiter = RateLimiter(cfg)

    # In a FastAPI route:
    limiter.check(endpoint="/api/command", ip="1.2.3.4")   # raises 429 on breach
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger("OpenCastor.RateLimiting")

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class EndpointLimit:
    """Rate-limit rule for a single endpoint.

    Attributes:
        endpoint:  URL path (e.g. ``"/api/command"``).
        per_ip:    Max requests per window per client IP.  0 = unlimited.
        per_user:  Max requests per window per authenticated user.  0 = unlimited.
        window_s:  Sliding window duration in seconds.
    """

    endpoint: str
    per_ip: int = 10
    per_user: int = 20
    window_s: float = 60.0


@dataclass
class RateLimitConfig:
    """Collection of per-endpoint rate-limit rules.

    Attributes:
        limits: List of :class:`EndpointLimit` instances.
    """

    limits: list[EndpointLimit] = field(default_factory=list)

    @classmethod
    def from_rcan(cls, config: dict) -> RateLimitConfig:
        """Build config from an RCAN config dict.

        Reads the top-level ``rate_limits`` list.

        Args:
            config: RCAN config dict (may be empty or missing ``rate_limits``).

        Returns:
            :class:`RateLimitConfig` populated from the config.
        """
        raw = config.get("rate_limits", []) if isinstance(config, dict) else []
        limits = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            endpoint = entry.get("endpoint", "")
            if not endpoint:
                continue
            limits.append(
                EndpointLimit(
                    endpoint=endpoint,
                    per_ip=int(entry.get("per_ip", 10)),
                    per_user=int(entry.get("per_user", 20)),
                    window_s=float(entry.get("window_s", 60.0)),
                )
            )
        return cls(limits=limits)

    def get_limit(self, endpoint: str) -> Optional[EndpointLimit]:
        """Return the :class:`EndpointLimit` for *endpoint*, or ``None`` if unconfigured.

        Args:
            endpoint: URL path to look up.

        Returns:
            Matching :class:`EndpointLimit` or ``None``.
        """
        for lim in self.limits:
            if lim.endpoint == endpoint:
                return lim
        return None

    def to_dict(self) -> list[dict]:
        """Serialise limits to a JSON-serialisable list of dicts.

        Returns:
            List of dicts with ``endpoint``, ``per_ip``, ``per_user``, ``window_s``.
        """
        return [
            {
                "endpoint": lim.endpoint,
                "per_ip": lim.per_ip,
                "per_user": lim.per_user,
                "window_s": lim.window_s,
            }
            for lim in self.limits
        ]


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Thread-safe sliding-window rate limiter.

    Args:
        config: :class:`RateLimitConfig` loaded from RCAN.
    """

    def __init__(self, config: RateLimitConfig):
        self._config = config
        self._lock = threading.Lock()
        # ip_history[endpoint][ip] → deque of timestamps
        self._ip_history: dict[str, dict[str, collections.deque]] = collections.defaultdict(
            lambda: collections.defaultdict(collections.deque)
        )
        # user_history[endpoint][user] → deque of timestamps
        self._user_history: dict[str, dict[str, collections.deque]] = collections.defaultdict(
            lambda: collections.defaultdict(collections.deque)
        )

    # ── Public ───────────────────────────────────────────────────────────────

    def check(
        self,
        endpoint: str,
        ip: Optional[str] = None,
        user: Optional[str] = None,
    ) -> None:
        """Check rate limits for *endpoint*.  Raises HTTP 429 on breach.

        Args:
            endpoint: URL path being accessed.
            ip:       Client IP address (for per-IP limiting).
            user:     Authenticated user identifier (for per-user limiting).

        Raises:
            HTTPException: HTTP 429 with ``Retry-After`` header when a limit is exceeded.
        """
        limit = self._config.get_limit(endpoint)
        if limit is None:
            return  # no rule configured for this endpoint

        now = time.time()
        window = limit.window_s

        with self._lock:
            if ip and limit.per_ip > 0:
                self._enforce(
                    self._ip_history[endpoint][ip],
                    limit.per_ip,
                    window,
                    now,
                    f"IP rate limit exceeded ({limit.per_ip} req/{window:.0f}s)",
                    int(window),
                )

            if user and limit.per_user > 0:
                self._enforce(
                    self._user_history[endpoint][user],
                    limit.per_user,
                    window,
                    now,
                    f"User rate limit exceeded ({limit.per_user} req/{window:.0f}s)",
                    int(window),
                )

    @staticmethod
    def _enforce(
        dq: collections.deque,
        max_count: int,
        window: float,
        now: float,
        detail: str,
        retry_after: int,
    ) -> None:
        """Enforce sliding-window limit in-place.

        Mutates *dq* by evicting expired timestamps and appending *now*.

        Raises:
            HTTPException: 429 when count exceeds *max_count*.
        """
        cutoff = now - window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= max_count:
            raise HTTPException(
                status_code=429,
                detail={"error": detail, "code": "HTTP_429"},
                headers={"Retry-After": str(retry_after)},
            )
        dq.append(now)

    def reset(self) -> None:
        """Clear all rate-limit history (useful in tests)."""
        with self._lock:
            self._ip_history.clear()
            self._user_history.clear()

    def get_config(self) -> RateLimitConfig:
        """Return the current :class:`RateLimitConfig`.

        Returns:
            The active rate-limit configuration.
        """
        return self._config

    def update_config(self, config: RateLimitConfig) -> None:
        """Replace the active configuration and reset history.

        Args:
            config: New :class:`RateLimitConfig` to apply immediately.
        """
        with self._lock:
            self._config = config
            self._ip_history.clear()
            self._user_history.clear()
        logger.info("Rate-limit config updated: %d endpoint rules", len(config.limits))


# ---------------------------------------------------------------------------
# Module-level singleton (for use by api.py)
# ---------------------------------------------------------------------------

_default_limiter: Optional[RateLimiter] = None


def get_limiter() -> Optional[RateLimiter]:
    """Return the module-level :class:`RateLimiter` singleton, or ``None`` if uninitialised.

    Returns:
        Global :class:`RateLimiter` or ``None``.
    """
    return _default_limiter


def init_limiter(config: dict) -> RateLimiter:
    """Initialise the module-level :class:`RateLimiter` from an RCAN config dict.

    Args:
        config: RCAN config dict with optional ``rate_limits`` key.

    Returns:
        Newly created :class:`RateLimiter`.
    """
    global _default_limiter
    rl_config = RateLimitConfig.from_rcan(config)
    _default_limiter = RateLimiter(rl_config)
    logger.info("Rate limiter initialised with %d rules from RCAN config", len(rl_config.limits))
    return _default_limiter
