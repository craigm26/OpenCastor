"""
castor/providers/pool_provider.py — ProviderPool (issues #278, #289, #297).

Round-robins think() calls across multiple API keys for the same provider,
spreading request load to avoid per-key rate limits.

Config::

    provider: pool
    pool:
      - provider: google
        api_key: KEY1
        model: gemini-2.0-flash
        weight: 2          # optional; higher = more frequently selected
      - provider: google
        api_key: KEY2
        model: gemini-2.0-flash
      - provider: anthropic
        api_key: KEY3
        model: claude-haiku-4-5

Optional::

    pool_strategy: round_robin          # "random", "weighted" (default: round_robin)
    pool_fallback: true                 # try next provider on failure (default: true)
    pool_health_check_interval_s: 60    # background health probe interval; 0=disabled
    pool_health_cooldown_s: 120         # seconds before re-enabling a degraded provider
"""

from __future__ import annotations

import itertools
import logging
import random
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

from castor.providers.base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.ProviderPool")


class ProviderPool(BaseProvider):
    """Round-robin, random, or weighted load balancer across a pool of provider instances.

    Args:
        config: RCAN agent config. Must contain a ``pool`` list of sub-configs,
                each formatted like a standard provider config dict.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._strategy: str = config.get("pool_strategy", "round_robin").lower()
        self._fallback: bool = bool(config.get("pool_fallback", True))
        pool_configs: List[Dict[str, Any]] = config.get("pool", [])

        # Health-aware routing config (#297)
        self._health_interval_s: float = float(config.get("pool_health_check_interval_s", 0))
        self._health_cooldown_s: float = float(config.get("pool_health_cooldown_s", 120))

        if not pool_configs:
            raise ValueError("ProviderPool: 'pool' list must contain at least one entry")

        # Lazy-init child providers (so missing keys fail at think() time, not import)
        self._providers: List[BaseProvider] = []
        self._weights: List[float] = []  # aligned with _providers; for "weighted" strategy
        self._init_errors: List[str] = []

        from castor.providers import get_provider

        for i, sub_cfg in enumerate(pool_configs):
            try:
                p = get_provider(sub_cfg)
                self._providers.append(p)
                self._weights.append(float(sub_cfg.get("weight", 1)))
                logger.debug(
                    "ProviderPool: loaded pool[%d] provider=%s weight=%.1f",
                    i,
                    sub_cfg.get("provider"),
                    self._weights[-1],
                )
            except Exception as exc:
                self._init_errors.append(f"pool[{i}]: {exc}")
                logger.warning("ProviderPool: failed to load pool[%d]: %s", i, exc)

        if not self._providers:
            raise RuntimeError(
                f"ProviderPool: no providers could be initialised. Errors: {self._init_errors}"
            )

        self._lock = threading.Lock()
        # Round-robin cycle iterator
        self._cycle = itertools.cycle(range(len(self._providers)))
        self._current_index = 0

        # Health-aware routing state (#297)
        # Maps provider index → timestamp when marked degraded
        self._degraded: Dict[int, float] = {}

        logger.info(
            "ProviderPool: initialised %d/%d providers (strategy=%s, fallback=%s)",
            len(self._providers),
            len(pool_configs),
            self._strategy,
            self._fallback,
        )

        # Start background health-check thread if configured
        if self._health_interval_s > 0:
            self._health_thread = threading.Thread(
                target=self._health_probe_loop,
                daemon=True,
                name="ProviderPool-health",
            )
            self._health_stop = threading.Event()
            self._health_thread.start()
            logger.info(
                "ProviderPool: health-check thread started (interval=%.0fs, cooldown=%.0fs)",
                self._health_interval_s,
                self._health_cooldown_s,
            )
        else:
            self._health_thread = None  # type: ignore[assignment]
            self._health_stop = threading.Event()

    # ------------------------------------------------------------------
    # Health-aware routing (#297)
    # ------------------------------------------------------------------

    def _health_probe_loop(self) -> None:
        """Background thread: periodically health-checks every provider."""
        while not self._health_stop.wait(self._health_interval_s):
            for i, provider in enumerate(self._providers):
                try:
                    result = provider.health_check()
                    ok = bool(result.get("ok", True))
                except Exception as exc:
                    ok = False
                    logger.debug("ProviderPool: health probe pool[%d] raised: %s", i, exc)

                with self._lock:
                    if not ok and i not in self._degraded:
                        self._degraded[i] = time.time()
                        logger.warning(
                            "ProviderPool: marking pool[%d] (%s) as degraded",
                            i,
                            getattr(provider, "model_name", "?"),
                        )
                    elif ok and i in self._degraded:
                        del self._degraded[i]
                        logger.info(
                            "ProviderPool: pool[%d] (%s) recovered — re-enabling",
                            i,
                            getattr(provider, "model_name", "?"),
                        )

    def _get_healthy_indices(self) -> List[int]:
        """Return indices of providers that are not currently degraded (or past cooldown)."""
        now = time.time()
        healthy = []
        with self._lock:
            for i in range(len(self._providers)):
                degraded_at = self._degraded.get(i)
                if degraded_at is None:
                    healthy.append(i)
                elif now - degraded_at >= self._health_cooldown_s:
                    # Cooldown expired — tentatively re-enable
                    del self._degraded[i]
                    healthy.append(i)
        if not healthy:
            # All degraded — fall back to all providers so we don't stall
            return list(range(len(self._providers)))
        return healthy

    # ------------------------------------------------------------------
    # Provider selection
    # ------------------------------------------------------------------

    def _next_provider(self) -> BaseProvider:
        """Return the next provider according to the pool strategy."""
        healthy = self._get_healthy_indices()

        if self._strategy == "random":
            return random.choice([self._providers[i] for i in healthy])

        if self._strategy == "weighted":
            candidates = [self._providers[i] for i in healthy]
            weights = [self._weights[i] for i in healthy]
            return random.choices(candidates, weights=weights, k=1)[0]

        # Default: round_robin
        with self._lock:
            idx = next(self._cycle)
            # Advance until we land on a healthy index (or exhaust)
            for _ in range(len(self._providers)):
                if idx in healthy:
                    break
                idx = next(self._cycle)
            self._current_index = idx
        return self._providers[idx]

    def _provider_order_from(self, start: int) -> List[BaseProvider]:
        """Return providers in order starting from ``start``, for fallback."""
        n = len(self._providers)
        return [self._providers[(start + i) % n] for i in range(n)]

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> Optional[str]:
        """Return a combined model name for diagnostics."""
        names = []
        for p in self._providers:
            name = getattr(p, "model_name", None)
            if name and name not in names:
                names.append(name)
        return " | ".join(names) if names else "pool"

    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        """Forward think() to the next provider, with optional fallback."""
        self._check_instruction_safety(instruction)

        primary = self._next_provider()
        start_idx = self._current_index

        if not self._fallback:
            return primary.think(image_bytes, instruction)

        candidates = self._provider_order_from(start_idx)
        last_exc: Optional[Exception] = None

        for provider in candidates:
            try:
                return provider.think(image_bytes, instruction)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "ProviderPool: provider %s failed (%s) — trying next",
                    getattr(provider, "model_name", str(provider)),
                    exc,
                )

        raise RuntimeError(
            f"ProviderPool: all {len(candidates)} providers failed. Last error: {last_exc}"
        ) from last_exc

    def think_stream(self, image_bytes: bytes, instruction: str) -> Iterator[str]:
        """Forward think_stream() to the next provider."""
        self._check_instruction_safety(instruction)

        primary = self._next_provider()
        start_idx = self._current_index

        if not self._fallback:
            yield from primary.think_stream(image_bytes, instruction)
            return

        candidates = self._provider_order_from(start_idx)
        last_exc: Optional[Exception] = None

        for provider in candidates:
            try:
                yield from provider.think_stream(image_bytes, instruction)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "ProviderPool: stream provider %s failed (%s) — trying next",
                    getattr(provider, "model_name", str(provider)),
                    exc,
                )

        raise RuntimeError(
            f"ProviderPool: all {len(candidates)} stream providers failed. Last: {last_exc}"
        ) from last_exc

    def health_check(self) -> Dict[str, Any]:
        """Return aggregated health from all pool members."""
        results = []
        for i, p in enumerate(self._providers):
            try:
                h = p.health_check()
                h["pool_index"] = i
                h["degraded"] = i in self._degraded
                results.append(h)
            except Exception as exc:
                results.append({"ok": False, "pool_index": i, "error": str(exc), "degraded": True})

        all_ok = all(r.get("ok") for r in results)
        return {
            "ok": all_ok,
            "strategy": self._strategy,
            "pool_size": len(self._providers),
            "members": results,
            "init_errors": self._init_errors,
            "degraded_count": len(self._degraded),
        }

    def stop(self) -> None:
        """Stop the background health-check thread (if running)."""
        self._health_stop.set()
        if self._health_thread is not None and self._health_thread.is_alive():
            self._health_thread.join(timeout=2.0)
