"""
castor/providers/pool_provider.py — ProviderPool (issues #278, #289, #297, #299, #320, #326).

Round-robins think() calls across multiple API keys for the same provider,
spreading request load to avoid per-key rate limits.

Config::

    provider: pool
    pool:
      - provider: google
        api_key: KEY1
        model: gemini-2.5-flash
        weight: 2          # optional; higher = more frequently selected
        priority: 1        # optional; lower = tried first in cascade strategy
      - provider: google
        api_key: KEY2
        model: gemini-2.5-flash
      - provider: anthropic
        api_key: KEY3
        model: claude-haiku-4-5

Optional::

    pool_strategy: round_robin          # "random", "weighted", "cascade", "adaptive" (default: round_robin)
    pool_fallback: true                 # try next provider on failure (default: true)
    pool_health_check_interval_s: 60    # background health probe interval; 0=disabled
    pool_health_cooldown_s: 120         # seconds before re-enabling a degraded provider
    pool_cascade_reset_s: 300           # seconds of success before resetting cascade to primary
    pool_adaptive_alpha: 0.1            # EMA smoothing factor for adaptive strategy (#320)
    pool_adaptive_window_n: 20          # min observations before adaptive weights kick in (#320)
    pool_record_path: /tmp/pool.jsonl   # record think() calls to JSONL (#326)
    pool_replay_path: /tmp/pool.jsonl   # replay think() calls from JSONL by instruction hash (#326)
    pool_burst_latency_ms: 5000         # latency threshold triggering burst demotion (#331)
    pool_burst_cooldown_s: 30           # seconds a burst-detected provider is demoted (#331)
    pool_ab_split: 0.5                  # fraction routed to provider 0 in A/B mode (#338)
    pool_sticky_session: true           # route same conversation_id to same provider (#359)
    pool_sticky_ttl_s: 3600            # seconds a sticky binding is remembered (#359)
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import random
import threading
import time
from collections.abc import Iterator
from typing import Any, Optional

from castor.providers.base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.ProviderPool")


class ProviderPool(BaseProvider):
    """Round-robin, random, or weighted load balancer across a pool of provider instances.

    Args:
        config: RCAN agent config. Must contain a ``pool`` list of sub-configs,
                each formatted like a standard provider config dict.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._strategy: str = config.get("pool_strategy", "round_robin").lower()
        self._fallback: bool = bool(config.get("pool_fallback", True))
        pool_configs: list[dict[str, Any]] = config.get("pool", [])

        # Health-aware routing config (#297)
        self._health_interval_s: float = float(config.get("pool_health_check_interval_s", 0))
        self._health_cooldown_s: float = float(config.get("pool_health_cooldown_s", 120))

        # Circuit breaker config (#312)
        # threshold=0 disables the breaker entirely.
        self._cb_threshold: int = int(config.get("pool_circuit_breaker_threshold", 0))
        self._cb_cooldown_s: float = float(config.get("pool_circuit_breaker_cooldown_s", 60.0))

        # Cascade strategy config (#299)
        self._cascade_reset_s: float = float(config.get("pool_cascade_reset_s", 300))

        # Adaptive strategy config (#320)
        self._adaptive_alpha: float = float(config.get("pool_adaptive_alpha", 0.1))
        self._adaptive_window_n: int = int(config.get("pool_adaptive_window_n", 20))
        self._ema_latency: dict[int, float] = {}  # per-provider EMA latency ms
        self._obs_count: dict[int, int] = {}  # per-provider observation count

        # Request replay config (#326)
        self._record_path: Optional[str] = config.get("pool_record_path")
        self._replay_path: Optional[str] = config.get("pool_replay_path")
        self._replay_map: dict[str, Any] = {}  # sha256-prefix(instruction) → Thought

        # Burst detection config (#331) — 0 disables burst demotion
        self._burst_latency_ms: float = float(config.get("pool_burst_latency_ms", 5000))
        self._burst_cooldown_s: float = float(config.get("pool_burst_cooldown_s", 30))

        # A/B test config (#338) — split fraction for provider 0 vs provider 1
        self._ab_split: float = float(config.get("pool_ab_split", 0.5))

        # Sticky session config (#359) — route same conversation_id to same provider
        self._sticky_session: bool = bool(config.get("pool_sticky_session", False))
        self._sticky_ttl_s: float = float(config.get("pool_sticky_ttl_s", 3600.0))

        # Issue #399: treat blank/empty raw_text as failure and retry next provider
        self._fallback_on_empty: bool = bool(config.get("pool_fallback_on_empty", False))

        # Issue #345: Cost tracking — per-provider cumulative token/USD accounting
        # Config: pool_cost_per_1k_tokens — dict mapping provider name to USD per 1k tokens
        self._cost_per_1k: dict[str, float] = dict(config.get("pool_cost_per_1k_tokens", {}))
        # provider_index → {tokens_total, cost_usd_total, calls}
        self._cost_tracker: dict[int, dict[str, float]] = {}

        # Issue #340: Shadow mode — forward every request to a secondary provider in parallel,
        # log the response for comparison, but always return the primary's response.
        # Config keys: pool_shadow_provider, pool_shadow_log_path
        self._shadow_provider_name: Optional[str] = config.get("pool_shadow_provider")
        self._shadow_log_path: Optional[str] = config.get("pool_shadow_log_path")
        self._shadow_provider: Optional[BaseProvider] = None

        if not pool_configs:
            raise ValueError("ProviderPool: 'pool' list must contain at least one entry")

        # Lazy-init child providers (so missing keys fail at think() time, not import)
        self._providers: list[BaseProvider] = []
        self._weights: list[float] = []  # aligned with _providers; for "weighted" strategy
        self._priorities: list[int] = []  # aligned with _providers; for "cascade" strategy
        self._init_errors: list[str] = []

        from castor.providers import get_provider

        for i, sub_cfg in enumerate(pool_configs):
            try:
                p = get_provider(sub_cfg)
                self._providers.append(p)
                self._weights.append(float(sub_cfg.get("weight", 1)))
                self._priorities.append(int(sub_cfg.get("priority", i)))
                logger.debug(
                    "ProviderPool: loaded pool[%d] provider=%s weight=%.1f priority=%d",
                    i,
                    sub_cfg.get("provider"),
                    self._weights[-1],
                    self._priorities[-1],
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
        # Circuit breaker state (#312)
        self._cb_failures: dict[int, int] = {}  # consecutive failures per provider
        self._cb_open_until: dict[int, float] = {}  # epoch when circuit re-closes
        self._current_index = 0

        # Sticky session state (#359): conversation_id → (provider_index, expiry_epoch)
        self._sticky_map: dict[str, tuple[int, float]] = {}

        # Burst detection state (#331): provider index → epoch when demotion expires
        self._burst_demoted_until: dict[int, float] = {}
        # A/B test state (#338): per-group counters {0: {success, fail}, 1: {success, fail}}
        self._ab_stats: dict[int, dict[str, int]] = {
            0: {"success": 0, "fail": 0},
            1: {"success": 0, "fail": 0},
        }

        # Health-aware routing state (#297)
        # Maps provider index → timestamp when marked degraded
        self._degraded: dict[int, float] = {}

        # Cascade strategy state (#299)
        # Provider indices sorted by priority (ascending = tried first)
        self._cascade_order: list[int] = sorted(
            range(len(self._providers)), key=lambda i: self._priorities[i]
        )
        self._cascade_current: int = 0  # index into _cascade_order (not provider index)
        self._cascade_last_failure: float = 0.0  # monotonic timestamp of last cascade advance

        logger.info(
            "ProviderPool: initialised %d/%d providers (strategy=%s, fallback=%s)",
            len(self._providers),
            len(pool_configs),
            self._strategy,
            self._fallback,
        )

        # Initialise cost tracker entries for each provider (#345)
        for i in range(len(self._providers)):
            self._cost_tracker[i] = {"tokens_total": 0.0, "cost_usd_total": 0.0, "calls": 0.0}

        # Initialise shadow provider if configured (#340)
        if self._shadow_provider_name:
            try:
                shadow_cfg = {"provider": self._shadow_provider_name}
                # Inherit any explicit shadow-provider-specific keys from config
                for k, v in config.items():
                    if k.startswith("pool_shadow_") and k != "pool_shadow_provider":
                        shadow_cfg[k.replace("pool_shadow_", "", 1)] = v
                shadow_cfg.update(config.get("pool_shadow_config", {}))
                self._shadow_provider = get_provider(shadow_cfg)
                logger.info(
                    "ProviderPool: shadow mode enabled — secondary provider=%s, log=%s",
                    self._shadow_provider_name,
                    self._shadow_log_path,
                )
            except Exception as exc:
                logger.warning(
                    "ProviderPool: shadow provider init failed: %s — shadow disabled", exc
                )
                self._shadow_provider = None

        # Load replay map if configured (#326)
        if self._replay_path:
            self._load_replay_map()

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
                    logger.warning("ProviderPool: health probe pool[%d] raised: %s", i, exc)

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

    def _get_healthy_indices(self) -> list[int]:
        """Return indices of providers that are neither degraded nor circuit-open nor burst-demoted."""
        now = time.time()
        healthy = []
        with self._lock:
            for i in range(len(self._providers)):
                # --- degraded (health-probe) check ---
                degraded_at = self._degraded.get(i)
                if degraded_at is not None:
                    if now - degraded_at >= self._health_cooldown_s:
                        del self._degraded[i]
                    else:
                        continue

                # --- circuit breaker check (#312) ---
                if self._cb_threshold > 0:
                    open_until = self._cb_open_until.get(i, 0.0)
                    if open_until > 0:
                        if now < open_until:
                            continue  # circuit still open
                        # Cooldown expired — half-open: allow one trial request
                        del self._cb_open_until[i]

                # --- burst demotion check (#331) ---
                if self._burst_latency_ms > 0:
                    demoted_until = self._burst_demoted_until.get(i, 0.0)
                    if demoted_until > 0:
                        if now < demoted_until:
                            continue  # burst-demoted
                        del self._burst_demoted_until[i]

                healthy.append(i)
        if not healthy:
            # All unhealthy — fall back to all providers so we don't stall
            return list(range(len(self._providers)))
        return healthy

    # ------------------------------------------------------------------
    # Circuit breaker helpers (#312)
    # ------------------------------------------------------------------

    def _cb_on_success(self, idx: int) -> None:
        """Record a successful call for *idx*: reset failure count and close circuit."""
        if self._cb_threshold <= 0:
            return
        with self._lock:
            if idx in self._cb_failures:
                self._cb_failures.pop(idx)
            if idx in self._cb_open_until:
                self._cb_open_until.pop(idx)
                logger.info(
                    "ProviderPool CB: circuit CLOSED for pool[%d] after successful call", idx
                )

    def _cb_on_failure(self, idx: int) -> None:
        """Record a failed call for *idx*: increment counter; open circuit if threshold reached."""
        if self._cb_threshold <= 0:
            return
        with self._lock:
            self._cb_failures[idx] = self._cb_failures.get(idx, 0) + 1
            count = self._cb_failures[idx]
            if count >= self._cb_threshold and idx not in self._cb_open_until:
                self._cb_open_until[idx] = time.time() + self._cb_cooldown_s
                logger.warning(
                    "ProviderPool CB: circuit OPEN for pool[%d] (%s) after %d consecutive failures"
                    " — cooldown %.0fs",
                    idx,
                    getattr(self._providers[idx], "model_name", "?"),
                    count,
                    self._cb_cooldown_s,
                )

    # ------------------------------------------------------------------
    # Cascade strategy helpers (#299)
    # ------------------------------------------------------------------

    def _cascade_provider(self) -> BaseProvider:
        """Return the current cascade-level provider, resetting to primary if eligible."""
        with self._lock:
            # Attempt to reset to primary if the reset timer has elapsed
            if (
                self._cascade_current > 0
                and self._cascade_reset_s > 0
                and time.monotonic() - self._cascade_last_failure >= self._cascade_reset_s
            ):
                logger.info(
                    "ProviderPool cascade: %.0fs since last failure — resetting to primary",
                    self._cascade_reset_s,
                )
                self._cascade_current = 0
            idx = self._cascade_order[self._cascade_current]
        return self._providers[idx]

    def _cascade_advance(self) -> None:
        """Advance the cascade pointer to the next priority level on failure."""
        with self._lock:
            if self._cascade_current < len(self._cascade_order) - 1:
                self._cascade_current += 1
            self._cascade_last_failure = time.monotonic()
            logger.warning(
                "ProviderPool cascade: advancing to level %d (provider index %d)",
                self._cascade_current,
                self._cascade_order[self._cascade_current],
            )

    def _think_cascade(self, image_bytes: bytes, instruction: str) -> Thought:
        """think() implementation for cascade strategy."""
        for _attempt in range(len(self._cascade_order)):
            provider = self._cascade_provider()
            try:
                result = provider.think(image_bytes, instruction)
                return result
            except Exception as exc:
                logger.warning(
                    "ProviderPool cascade: provider %s failed (%s)",
                    getattr(provider, "model_name", str(provider)),
                    exc,
                )
                self._cascade_advance()

        raise RuntimeError(f"ProviderPool cascade: all {len(self._cascade_order)} providers failed")

    def _think_stream_cascade(self, image_bytes: bytes, instruction: str) -> Iterator[str]:
        """think_stream() implementation for cascade strategy."""
        for _attempt in range(len(self._cascade_order)):
            provider = self._cascade_provider()
            try:
                yield from provider.think_stream(image_bytes, instruction)
                return
            except Exception as exc:
                logger.warning(
                    "ProviderPool cascade stream: provider %s failed (%s)",
                    getattr(provider, "model_name", str(provider)),
                    exc,
                )
                self._cascade_advance()

        raise RuntimeError(
            f"ProviderPool cascade stream: all {len(self._cascade_order)} providers failed"
        )

    # ------------------------------------------------------------------
    # Adaptive strategy helpers (#320)
    # ------------------------------------------------------------------

    def _update_adaptive_weight(self, idx: int, latency_ms: float) -> None:
        """Update the EMA latency for *idx* after a successful call."""
        with self._lock:
            old = self._ema_latency.get(idx, latency_ms)
            self._ema_latency[idx] = (
                old * (1 - self._adaptive_alpha) + latency_ms * self._adaptive_alpha
            )
            self._obs_count[idx] = self._obs_count.get(idx, 0) + 1

    # ------------------------------------------------------------------
    # Burst detection helpers (#331)
    # ------------------------------------------------------------------

    def _burst_check(self, idx: int, latency_ms: float) -> None:
        """Demote *idx* if latency exceeds the burst threshold."""
        if self._burst_latency_ms <= 0:
            return
        if latency_ms > self._burst_latency_ms:
            with self._lock:
                self._burst_demoted_until[idx] = time.time() + self._burst_cooldown_s
                logger.warning(
                    "ProviderPool burst: pool[%d] latency %.0fms exceeds threshold %.0fms"
                    " — demoted for %.0fs",
                    idx,
                    latency_ms,
                    self._burst_latency_ms,
                    self._burst_cooldown_s,
                )

    def _is_burst_demoted(self, idx: int) -> bool:
        """Return True if *idx* is currently burst-demoted."""
        if self._burst_latency_ms <= 0:
            return False
        now = time.time()
        with self._lock:
            until = self._burst_demoted_until.get(idx, 0.0)
            if until > 0 and now < until:
                return True
            if until > 0 and now >= until:
                del self._burst_demoted_until[idx]
        return False

    # ------------------------------------------------------------------
    # A/B test helpers (#338)
    # ------------------------------------------------------------------

    def _ab_group(self) -> int:
        """Return 0 or 1 based on configured split."""
        return 0 if random.random() < self._ab_split else 1

    def _ab_provider_for_group(self, group: int) -> tuple[int, BaseProvider]:
        """Return (index, provider) for the given A/B group."""
        n = len(self._providers)
        if n == 1:
            return 0, self._providers[0]
        # group 0 → provider 0, group 1 → provider 1 (or last if >2 providers)
        idx = 0 if group == 0 else min(1, n - 1)
        return idx, self._providers[idx]

    def _ab_record(self, group: int, success: bool) -> None:
        """Record an A/B outcome for *group*."""
        with self._lock:
            if group not in self._ab_stats:
                self._ab_stats[group] = {"success": 0, "fail": 0}
            if success:
                self._ab_stats[group]["success"] += 1
            else:
                self._ab_stats[group]["fail"] += 1

    # ------------------------------------------------------------------
    # Sticky session helpers (#359)
    # ------------------------------------------------------------------

    def _get_sticky_provider(self, conversation_id: str) -> Optional[tuple[int, BaseProvider]]:
        """Return (index, provider) for a sticky conversation, or None if unbound/expired."""
        if not self._sticky_session or not conversation_id:
            return None
        with self._lock:
            entry = self._sticky_map.get(conversation_id)
            if entry is None:
                return None
            idx, expiry = entry
            if time.time() > expiry:
                del self._sticky_map[conversation_id]
                logger.debug(
                    "ProviderPool sticky: binding expired for conversation %r", conversation_id
                )
                return None
            if idx >= len(self._providers):
                return None
            return idx, self._providers[idx]

    def _set_sticky_provider(self, conversation_id: str, idx: int) -> None:
        """Bind conversation_id to provider idx for the configured TTL."""
        if not self._sticky_session or not conversation_id:
            return
        with self._lock:
            self._sticky_map[conversation_id] = (idx, time.time() + self._sticky_ttl_s)
        logger.debug(
            "ProviderPool sticky: bound conversation %r to pool[%d] (ttl=%.0fs)",
            conversation_id,
            idx,
            self._sticky_ttl_s,
        )

    # ------------------------------------------------------------------
    # Issue #345: Cost tracking helpers
    # ------------------------------------------------------------------

    def _record_cost(self, idx: int, thought: Thought) -> None:
        """Record token usage and estimated cost for a successful think() call.

        Tokens are extracted from ``thought.action`` when available (field
        ``"tokens_used"``), or estimated as 1/4 of the raw_text character
        count as a rough fallback.

        Args:
            idx:    Index of the provider in ``_providers``.
            thought: The ``Thought`` returned by the provider.
        """
        try:
            action = thought.action or {}
            tokens = float(
                action.get("tokens_used", 0)
                or (len(thought.raw_text) / 4 if thought.raw_text else 0)
            )
            provider_name = getattr(self._providers[idx], "model_name", "") or ""
            # Look up cost per 1k tokens — try provider name, then numeric index key
            cost_rate = self._cost_per_1k.get(provider_name, self._cost_per_1k.get(str(idx), 0.0))
            cost = (tokens / 1000.0) * float(cost_rate)
            with self._lock:
                ct = self._cost_tracker.setdefault(
                    idx, {"tokens_total": 0.0, "cost_usd_total": 0.0, "calls": 0.0}
                )
                ct["tokens_total"] += tokens
                ct["cost_usd_total"] += cost
                ct["calls"] += 1
        except Exception as exc:
            logger.debug("ProviderPool._record_cost: %s", exc)

    def cost_summary(self) -> dict[str, Any]:
        """Return cumulative token usage and USD cost per pool provider.

        Returns:
            Dict mapping pool index (str) to ``{"tokens_total", "cost_usd_total", "calls",
            "provider_name"}``, plus a ``"total"`` entry summing all providers.
        """
        summary: dict[str, Any] = {}
        total_tokens = 0.0
        total_cost = 0.0
        total_calls = 0.0
        with self._lock:
            for i in range(len(self._providers)):
                ct = self._cost_tracker.get(i, {})
                provider_name = (
                    getattr(self._providers[i], "model_name", f"pool[{i}]") or f"pool[{i}]"
                )
                tokens = ct.get("tokens_total", 0.0)
                cost = ct.get("cost_usd_total", 0.0)
                calls = ct.get("calls", 0.0)
                summary[str(i)] = {
                    "provider_name": provider_name,
                    "tokens_total": tokens,
                    "cost_usd_total": round(cost, 6),
                    "calls": int(calls),
                }
                total_tokens += tokens
                total_cost += cost
                total_calls += calls
        summary["total"] = {
            "tokens_total": total_tokens,
            "cost_usd_total": round(total_cost, 6),
            "calls": int(total_calls),
        }
        return summary

    def provider_stats(self) -> dict[str, Any]:
        """Return per-provider call count, error count, and latency summary (Issue #405).

        Returns:
            Dict with key ``"providers"`` mapping to a list of per-provider dicts:
            ``{name, index, calls, cost_usd_total, tokens_total, avg_latency_ms, degraded, cb_open}``
        """
        from castor.metrics import get_registry

        registry = get_registry()
        tracker = registry._provider_latency  # ProviderLatencyTracker

        stats = []
        for i, p in enumerate(self._providers):
            name = getattr(p, "model_name", None) or f"pool[{i}]"
            cost_entry = self._cost_tracker.get(i, {})

            # Get latency p50 from tracker
            avg_ms = tracker.percentile(name, 0.5)

            stat = {
                "name": name,
                "index": i,
                "calls": int(cost_entry.get("calls", 0)),
                "cost_usd_total": float(cost_entry.get("cost_usd_total", 0.0)),
                "tokens_total": int(cost_entry.get("tokens_total", 0)),
                "avg_latency_ms": float(avg_ms) if avg_ms is not None else None,
                "degraded": i in self._degraded,
                "cb_open": i in self._cb_open_until and self._cb_open_until[i] > time.monotonic(),
            }
            stats.append(stat)

        return {
            "providers": stats,
            "strategy": self._strategy,
            "pool_size": len(self._providers),
        }

    def latency_percentiles(self) -> dict[str, Any]:
        """Return p50/p95/p99 latency per pool provider (Issue #414).

        Reads from the MetricsRegistry ProviderLatencyTracker.

        Returns:
            Dict with key ``"providers"`` mapping provider name →
            ``{p50_ms, p95_ms, p99_ms, sample_count}`` dicts,
            plus ``"pool_size"`` (int).
        """
        from castor.metrics import get_registry

        tracker = get_registry()._provider_latency

        result = {}
        for i, p in enumerate(self._providers):
            name = getattr(p, "model_name", None) or f"pool[{i}]"
            result[name] = {
                "p50_ms": tracker.percentile(name, 0.50),
                "p95_ms": tracker.percentile(name, 0.95),
                "p99_ms": tracker.percentile(name, 0.99),
                "sample_count": len(tracker._data.get(name, {}).get("samples", [])),
            }

        return {"providers": result, "pool_size": len(self._providers)}

    def cost_report(self) -> dict[str, Any]:
        """Return a per-provider cost breakdown including percentage of total spend (Issue #427).

        Iterates over ``self._providers`` and reads cost data from ``self._cost_tracker``
        (keyed by provider index).

        Returns:
            Dict with keys:

            - ``"providers"``: list of per-provider dicts, each containing:
              ``name`` (str), ``calls`` (int), ``cost_usd_total`` (float),
              ``cost_usd_avg_per_call`` (float), ``pct_of_total`` (float).
            - ``"total_cost_usd"`` (float): sum of all provider costs.
            - ``"pool_size"`` (int): number of providers in the pool.
        """
        with self._lock:
            total_cost = 0.0
            entries = []
            for i, p in enumerate(self._providers):
                name = getattr(p, "model_name", None) or f"pool[{i}]"
                ct = self._cost_tracker.get(i, {})
                calls = int(ct.get("calls", 0))
                cost = float(ct.get("cost_usd_total", 0.0))
                total_cost += cost
                entries.append({"name": name, "calls": calls, "cost_usd_total": cost})

        providers = []
        for entry in entries:
            calls = entry["calls"]
            cost = entry["cost_usd_total"]
            avg = cost / calls if calls > 0 else 0.0
            pct = cost / total_cost * 100.0 if total_cost > 0.0 else 0.0
            providers.append(
                {
                    "name": entry["name"],
                    "calls": calls,
                    "cost_usd_total": cost,
                    "cost_usd_avg_per_call": avg,
                    "pct_of_total": pct,
                }
            )

        return {
            "providers": providers,
            "total_cost_usd": total_cost,
            "pool_size": len(self._providers),
        }

    def reset_stats(self) -> dict[str, Any]:
        """Reset all per-provider counters and state to zero (Issue #416).

        Clears: cost_tracker, cb_failures, cb_open_until, degraded,
        burst_demoted_until, ab_stats.

        Returns:
            Dict with ``"ok": True``, ``"providers_reset"`` (int).
        """
        with self._lock:
            n = len(self._providers)
            # Reset cost tracking
            for i in range(n):
                self._cost_tracker[i] = {"tokens_total": 0.0, "cost_usd_total": 0.0, "calls": 0.0}
            # Reset circuit breaker state
            self._cb_failures.clear()
            self._cb_open_until.clear()
            # Reset degraded/burst state
            self._degraded.clear()
            self._burst_demoted_until.clear()
            # Reset A/B stats
            self._ab_stats = {0: {"success": 0, "fail": 0}, 1: {"success": 0, "fail": 0}}

        logger.info("ProviderPool.reset_stats: cleared stats for %d provider(s)", n)
        return {"ok": True, "providers_reset": n}

    # ------------------------------------------------------------------
    # Issue #340: Shadow mode helpers
    # ------------------------------------------------------------------

    def _shadow_call(self, image_bytes: bytes, instruction: str, primary_thought: Thought) -> None:
        """Fire a shadow think() to the secondary provider and log the comparison.

        This runs in a background thread so the primary response is never delayed.

        Args:
            image_bytes:     Raw image bytes forwarded to the shadow provider.
            instruction:     The instruction forwarded to the shadow provider.
            primary_thought: The primary provider's ``Thought`` for comparison.
        """
        if self._shadow_provider is None:
            return
        try:
            shadow_thought = self._shadow_provider.think(image_bytes, instruction)
            if self._shadow_log_path:
                try:
                    rec = {
                        "ts": time.time(),
                        "instruction": instruction[:200],
                        "primary_action": primary_thought.action,
                        "shadow_action": shadow_thought.action,
                        "primary_raw": primary_thought.raw_text[:300]
                        if primary_thought.raw_text
                        else "",
                        "shadow_raw": shadow_thought.raw_text[:300]
                        if shadow_thought.raw_text
                        else "",
                        "match": primary_thought.action == shadow_thought.action,
                    }
                    with open(self._shadow_log_path, "a") as f:
                        f.write(json.dumps(rec) + "\n")
                except Exception as log_exc:
                    logger.debug("ProviderPool shadow: log write failed: %s", log_exc)
            logger.debug(
                "ProviderPool shadow: primary=%r shadow=%r match=%s",
                primary_thought.action,
                shadow_thought.action,
                primary_thought.action == shadow_thought.action,
            )
        except Exception as exc:
            logger.debug("ProviderPool shadow: secondary provider failed: %s", exc)

    # ------------------------------------------------------------------
    # Request replay helpers (#326)
    # ------------------------------------------------------------------

    @staticmethod
    def _replay_key(instruction: str) -> str:
        """Return a short SHA-256 prefix used as the JSONL record key."""
        return hashlib.sha256(instruction.encode()).hexdigest()[:16]

    def _load_replay_map(self) -> None:
        """Load a previously-recorded JSONL replay file into ``_replay_map``."""
        try:
            with open(self._replay_path) as f:  # type: ignore[arg-type]
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    key = rec.get("key", "")
                    if key:
                        from castor.providers.base import Thought

                        self._replay_map[key] = Thought(
                            raw_text=rec.get("raw_text", ""),
                            action=rec.get("action"),
                        )
            logger.info(
                "ProviderPool: loaded %d replay entries from %s",
                len(self._replay_map),
                self._replay_path,
            )
        except Exception as exc:
            logger.warning(
                "ProviderPool: failed to load replay map from %s: %s",
                self._replay_path,
                exc,
            )

    def _record_thought(self, instruction: str, thought: Thought) -> None:
        """Append a think() result to the JSONL record file."""
        if not self._record_path:
            return
        try:
            rec = {
                "key": self._replay_key(instruction),
                "instruction": instruction,
                "raw_text": thought.raw_text,
                "action": thought.action,
            }
            with open(self._record_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception as exc:
            logger.debug("ProviderPool: record failed: %s", exc)

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

        if self._strategy == "adaptive":
            # Providers with enough observations get EMA-based weights (lower latency → higher
            # probability). Providers below the observation threshold are treated equally so they
            # collect data before being de-prioritised.
            with self._lock:
                qualified = [
                    i for i in healthy if self._obs_count.get(i, 0) >= self._adaptive_window_n
                ]
            if not qualified:
                # Warm-up phase: not enough data yet — use uniform round-robin
                qualified = healthy
            eps = 1.0  # prevent division by zero
            weights = [1.0 / (self._ema_latency.get(i, 1000.0) + eps) for i in qualified]
            idx = random.choices(qualified, weights=weights, k=1)[0]
            with self._lock:
                self._current_index = idx
            return self._providers[idx]

        if self._strategy == "ab_test":
            # A/B test: route to provider 0 or 1 based on split; ignore healthy filtering
            # (A/B needs all traffic through both arms for valid comparison)
            group = self._ab_group()
            idx, provider = self._ab_provider_for_group(group)
            with self._lock:
                self._current_index = idx
            # Attach group info to the call via a thread-local marker
            self._ab_current_group = group  # used by think() to record outcome
            return provider

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

    def _provider_order_from(self, start: int) -> list[BaseProvider]:
        """Return providers in order starting from ``start``, for fallback."""
        n = len(self._providers)
        return [self._providers[(start + i) % n] for i in range(n)]

    def _provider_order_indices_from(self, start: int) -> list[tuple[int, BaseProvider]]:
        """Return (index, provider) pairs starting from ``start``, for CB-aware fallback."""
        n = len(self._providers)
        return [((start + i) % n, self._providers[(start + i) % n]) for i in range(n)]

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

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        conversation_id: Optional[str] = None,
    ) -> Thought:
        """Forward think() to the next provider, with optional fallback."""
        self._check_instruction_safety(instruction)

        # Replay map check (#326): return cached result if instruction matches
        if self._replay_map:
            key = self._replay_key(instruction)
            hit = self._replay_map.get(key)
            if hit is not None:
                logger.debug("ProviderPool: replay hit for key=%s", key)
                return hit

        if self._strategy == "cascade":
            result = self._think_cascade(image_bytes, instruction)
            self._record_thought(instruction, result)
            return result

        # Sticky session (#359): reuse the same provider for an ongoing conversation
        sticky = self._get_sticky_provider(conversation_id) if conversation_id else None
        if sticky is not None:
            start_idx, primary = sticky
            logger.debug(
                "ProviderPool sticky: routing conversation %r to pool[%d]",
                conversation_id,
                start_idx,
            )
        else:
            primary = self._next_provider()
            start_idx = self._current_index

        ab_group = getattr(self, "_ab_current_group", None)

        if not self._fallback:
            try:
                t0 = time.monotonic()
                result = primary.think(image_bytes, instruction)
                latency_ms = (time.monotonic() - t0) * 1000.0
                self._cb_on_success(start_idx)
                self._burst_check(start_idx, latency_ms)
                if self._strategy == "adaptive":
                    self._update_adaptive_weight(start_idx, latency_ms)
                if ab_group is not None:
                    self._ab_record(ab_group, success=True)
                self._record_thought(instruction, result)
                self._record_cost(start_idx, result)
                # Issue #399: raise on empty response when no fallback is available
                if self._fallback_on_empty and (not result.raw_text or not result.raw_text.strip()):
                    raise RuntimeError("ProviderPool: provider returned empty response")
                # Sticky: bind this conversation to the successful provider
                self._set_sticky_provider(conversation_id, start_idx)
                if self._shadow_provider is not None:
                    import threading as _threading

                    _threading.Thread(
                        target=self._shadow_call,
                        args=(image_bytes, instruction, result),
                        daemon=True,
                    ).start()
                return result
            except Exception:
                self._cb_on_failure(start_idx)
                if ab_group is not None:
                    self._ab_record(ab_group, success=False)
                raise

        candidates = self._provider_order_indices_from(start_idx)
        last_exc: Optional[Exception] = None

        for idx, provider in candidates:
            try:
                t0 = time.monotonic()
                result = provider.think(image_bytes, instruction)
                latency_ms = (time.monotonic() - t0) * 1000.0
                self._cb_on_success(idx)
                self._burst_check(idx, latency_ms)
                if self._strategy == "adaptive":
                    self._update_adaptive_weight(idx, latency_ms)
                # Issue #399: treat blank response as failure if configured
                if self._fallback_on_empty and (not result.raw_text or not result.raw_text.strip()):
                    logger.warning(
                        "ProviderPool: provider %s returned empty response — trying next",
                        getattr(provider, "model_name", str(provider)),
                    )
                    last_exc = RuntimeError("empty response")
                    continue
                if ab_group is not None:
                    self._ab_record(ab_group, success=True)
                self._record_thought(instruction, result)
                self._record_cost(idx, result)
                # Sticky: bind this conversation to the successful provider
                self._set_sticky_provider(conversation_id, idx)
                if self._shadow_provider is not None:
                    import threading as _threading

                    _threading.Thread(
                        target=self._shadow_call,
                        args=(image_bytes, instruction, result),
                        daemon=True,
                    ).start()
                return result
            except Exception as exc:
                self._cb_on_failure(idx)
                last_exc = exc
                logger.warning(
                    "ProviderPool: provider %s failed (%s) — trying next",
                    getattr(provider, "model_name", str(provider)),
                    exc,
                )

        if ab_group is not None:
            self._ab_record(ab_group, success=False)
        raise RuntimeError(
            f"ProviderPool: all {len(candidates)} providers failed. Last error: {last_exc}"
        ) from last_exc

    def think_stream(self, image_bytes: bytes, instruction: str) -> Iterator[str]:
        """Forward think_stream() to the next provider."""
        self._check_instruction_safety(instruction)

        if self._strategy == "cascade":
            yield from self._think_stream_cascade(image_bytes, instruction)
            return

        primary = self._next_provider()
        start_idx = self._current_index

        if not self._fallback:
            try:
                yield from primary.think_stream(image_bytes, instruction)
                self._cb_on_success(start_idx)
            except Exception:
                self._cb_on_failure(start_idx)
                raise
            return

        candidates = self._provider_order_indices_from(start_idx)
        last_exc: Optional[Exception] = None

        for idx, provider in candidates:
            try:
                yield from provider.think_stream(image_bytes, instruction)
                self._cb_on_success(idx)
                return
            except Exception as exc:
                self._cb_on_failure(idx)
                last_exc = exc
                logger.warning(
                    "ProviderPool: stream provider %s failed (%s) — trying next",
                    getattr(provider, "model_name", str(provider)),
                    exc,
                )

        raise RuntimeError(
            f"ProviderPool: all {len(candidates)} stream providers failed. Last: {last_exc}"
        ) from last_exc

    def health_check(self) -> dict[str, Any]:
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
        health: dict[str, Any] = {
            "ok": all_ok,
            "strategy": self._strategy,
            "pool_size": len(self._providers),
            "members": results,
            "init_errors": self._init_errors,
            "degraded_count": len(self._degraded),
        }
        if self._strategy == "cascade":
            with self._lock:
                health["cascade_index"] = self._cascade_current
                health["cascade_provider_index"] = self._cascade_order[self._cascade_current]
        # Circuit breaker state (#312)
        if self._cb_threshold > 0:
            now = time.time()
            with self._lock:
                cb_state = {
                    i: {
                        "failures": self._cb_failures.get(i, 0),
                        "open": i in self._cb_open_until and now < self._cb_open_until.get(i, 0),
                        "open_until": self._cb_open_until.get(i, 0),
                    }
                    for i in range(len(self._providers))
                }
            health["circuit_breaker"] = {
                "threshold": self._cb_threshold,
                "cooldown_s": self._cb_cooldown_s,
                "providers": cb_state,
                "open_count": sum(1 for s in cb_state.values() if s["open"]),
            }
        # Adaptive strategy state (#320)
        if self._strategy == "adaptive":
            with self._lock:
                health["adaptive"] = {
                    "alpha": self._adaptive_alpha,
                    "window_n": self._adaptive_window_n,
                    "ema_latency_ms": dict(self._ema_latency),
                    "obs_count": dict(self._obs_count),
                }
        # Request replay state (#326)
        health["replay"] = {
            "record_path": self._record_path,
            "replay_path": self._replay_path,
            "replay_entries": len(self._replay_map),
        }
        # Burst detection state (#331)
        if self._burst_latency_ms > 0:
            now = time.time()
            with self._lock:
                burst_state = {
                    i: {
                        "demoted": i in self._burst_demoted_until
                        and now < self._burst_demoted_until.get(i, 0),
                        "demoted_until": self._burst_demoted_until.get(i, 0),
                    }
                    for i in range(len(self._providers))
                }
            health["burst_detection"] = {
                "threshold_ms": self._burst_latency_ms,
                "cooldown_s": self._burst_cooldown_s,
                "providers": burst_state,
                "demoted_count": sum(1 for s in burst_state.values() if s["demoted"]),
            }
        # A/B test state (#338)
        if self._strategy == "ab_test":
            with self._lock:
                health["ab_test"] = {
                    "split": self._ab_split,
                    "groups": dict(self._ab_stats),
                }
        # Issue #345: cost tracking state
        cost = self.cost_summary()
        for i, member in enumerate(results):
            ct_entry = cost.get(str(i), {})
            member["cost_usd"] = ct_entry.get("cost_usd_total", 0.0)
            member["tokens_total"] = ct_entry.get("tokens_total", 0.0)
            member["calls"] = ct_entry.get("calls", 0)
        health["cost_summary"] = cost
        # Issue #340: shadow mode state
        health["shadow"] = {
            "enabled": self._shadow_provider is not None,
            "provider": self._shadow_provider_name,
            "log_path": self._shadow_log_path,
        }
        # Issue #359: sticky session state
        with self._lock:
            active_sticky = len(self._sticky_map)
        health["sticky_session"] = {
            "enabled": self._sticky_session,
            "ttl_s": self._sticky_ttl_s,
            "active_bindings": active_sticky,
        }
        # Issue #370: warm_providers results
        with self._lock:
            health["warm_results"] = getattr(self, "_warm_results", {})
        return health

    # ------------------------------------------------------------------
    # Issue #370 — pre-flight warm-up health check
    # ------------------------------------------------------------------

    def warm_providers(self, timeout_s: float = 10.0) -> dict[str, bool]:
        """Run health_check() on all pool members in parallel and log results.

        Stores results in ``self._warm_results`` and returns a mapping of
        ``pool_index (str) → ok (bool)``.  Never raises.

        Args:
            timeout_s: Per-provider health-check timeout in seconds (default 10).

        Returns:
            ``{"0": True, "1": False, ...}`` — one entry per provider.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[str, bool] = {}

        def _check(idx: int, provider: Any) -> tuple:
            try:
                h = provider.health_check()
                return idx, bool(h.get("ok", False))
            except Exception as exc:
                logger.warning("ProviderPool.warm_providers[%d] error: %s", idx, exc)
                return idx, False

        with ThreadPoolExecutor(max_workers=max(1, len(self._providers))) as ex:
            futures = {ex.submit(_check, i, p): i for i, p in enumerate(self._providers)}
            for fut in as_completed(futures, timeout=timeout_s):
                try:
                    idx, ok = fut.result()
                    results[str(idx)] = ok
                    status = "ok" if ok else "FAIL"
                    logger.info(
                        "ProviderPool warm[%d] %s — %s",
                        idx,
                        getattr(self._providers[idx], "model_name", "?"),
                        status,
                    )
                except Exception as exc:
                    i = futures[fut]
                    results[str(i)] = False
                    logger.warning("ProviderPool warm[%d] timed out or error: %s", i, exc)

        # Mark any missing (timed out) providers as failed
        for i in range(len(self._providers)):
            results.setdefault(str(i), False)

        with self._lock:
            self._warm_results: dict[str, bool] = results

        return results

    def stop(self) -> None:
        """Stop the background health-check thread (if running)."""
        self._health_stop.set()
        if self._health_thread is not None and self._health_thread.is_alive():
            self._health_thread.join(timeout=2.0)
