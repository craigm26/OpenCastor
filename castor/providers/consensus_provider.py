"""Consensus provider — multi-LLM voting for robot actions.

Queries N child providers in parallel and selects the action that reaches a
configurable quorum.  Designed for safety-critical scenarios where you want two
or more AI systems to agree before the robot moves.

RCAN config example::

    agent:
      provider: consensus
      # Primary is queried first and breaks ties
      primary_provider: google
      primary_model: gemini-2.5-flash
      consensus_providers:
        - provider: google
          model: gemini-2.5-flash
        - provider: anthropic
          model: claude-haiku-4-5-20251001
        - provider: ollama
          model: llama3.2:3b
      quorum: 2           # agreements needed (default: simple majority)
      timeout_ms: 5000    # per-provider timeout (default: 5 000 ms)

Quorum semantics
----------------
* ``quorum: 2`` — at least 2 providers must agree on the **same action type**.
  For ``move`` actions, linear/angular values are averaged across agreeing voters.
* If quorum is not reached within *timeout_ms*, the primary provider's action
  is used as a tiebreak.
* ``think_stream()`` always delegates to the primary provider for low latency.

Action comparison
-----------------
Two thoughts ``agree`` when:

* Both have action type ``stop`` or ``wait``.
* Both have action type ``move`` with ``|Δlinear| ≤ 0.25`` and
  ``|Δangular| ≤ 0.25``.
* Both have action type ``grip`` with the same ``state`` value.
* Both have action type ``nav_waypoint`` within a 20 % distance tolerance and
  30 ° heading tolerance.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from collections.abc import Iterator
from typing import Any

from castor.providers.base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Consensus")


def _get_child_provider(config: dict) -> BaseProvider:
    """Thin wrapper around get_provider — patchable in tests."""
    from castor.providers import get_provider

    return get_provider(config)


# ── Action agreement tolerance constants ──────────────────────────────────────
_MOVE_LINEAR_TOL = 0.25
_MOVE_ANGULAR_TOL = 0.25
_NAV_DIST_TOL_FRAC = 0.20  # 20 % distance tolerance
_NAV_HEADING_TOL_DEG = 30.0


def _action_type(thought: Thought) -> str:
    if thought.action is None:
        return "none"
    return str(thought.action.get("type", "none"))


def _actions_agree(a: Thought, b: Thought) -> bool:
    """Return True when two Thoughts represent the same intended action."""
    ta, tb = _action_type(a), _action_type(b)
    if ta != tb:
        return False

    if ta in ("stop", "wait", "none"):
        return True

    aa, ab = a.action or {}, b.action or {}

    if ta == "move":
        dl = abs(float(aa.get("linear", 0)) - float(ab.get("linear", 0)))
        dang = abs(float(aa.get("angular", 0)) - float(ab.get("angular", 0)))
        return dl <= _MOVE_LINEAR_TOL and dang <= _MOVE_ANGULAR_TOL

    if ta == "grip":
        return aa.get("state") == ab.get("state")

    if ta == "nav_waypoint":
        da = float(aa.get("distance_m", 0))
        db = float(ab.get("distance_m", 0))
        avg_d = (da + db) / 2.0 if (da + db) > 0 else 1.0
        dist_ok = abs(da - db) / avg_d <= _NAV_DIST_TOL_FRAC
        heading_ok = (
            abs(float(aa.get("heading_deg", 0)) - float(ab.get("heading_deg", 0)))
            <= _NAV_HEADING_TOL_DEG
        )
        return dist_ok and heading_ok

    # Unknown action type — require exact type equality only (already checked above)
    return True


def _merge_move(thoughts: list[Thought]) -> Thought:
    """Average linear/angular across agreeing ``move`` thoughts."""
    total_linear = sum(float(t.action.get("linear", 0)) for t in thoughts if t.action)
    total_angular = sum(float(t.action.get("angular", 0)) for t in thoughts if t.action)
    n = len(thoughts)
    merged_action = {
        "type": "move",
        "linear": round(total_linear / n, 4),
        "angular": round(total_angular / n, 4),
    }
    raw_texts = [t.raw_text for t in thoughts]
    return Thought(
        raw_text=f"[consensus:{n}] " + " / ".join(raw_texts),
        action=merged_action,
    )


def _pick_winner(
    thoughts: list[tuple[int, Thought]],
    quorum: int,
    primary_idx: int,
) -> tuple[Thought, str]:
    """Find the first action type that reaches *quorum* agreements.

    Returns ``(winning_thought, reason)`` where *reason* is a short string
    describing how the winner was chosen.
    """
    # Group by action type
    groups: dict[str, list[tuple[int, Thought]]] = {}
    for idx, t in thoughts:
        atype = _action_type(t)
        groups.setdefault(atype, []).append((idx, t))

    # Find types with enough raw count first (fast path)
    for atype, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(members) < quorum:
            continue
        # Pairwise agreement check within the group
        agreeing: list[Thought] = []
        for _, t in members:
            if all(_actions_agree(t, prev) for prev in agreeing):
                agreeing.append(t)
            if len(agreeing) >= quorum:
                if atype == "move":
                    return _merge_move(agreeing), f"quorum={quorum},type=move,merged"
                return agreeing[0], f"quorum={quorum},type={atype}"

    # No quorum — fall back to primary provider's thought
    for idx, t in thoughts:
        if idx == primary_idx:
            return t, "tiebreak=primary"

    # Last resort: first available
    if thoughts:
        return thoughts[0][1], "tiebreak=first"
    return Thought("No consensus reached.", {"type": "stop"}), "tiebreak=empty"


class ConsensusProvider(BaseProvider):
    """Multi-LLM voting provider.

    Wraps multiple child providers and returns the action that reaches quorum.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        # Build child providers from consensus_providers list
        raw_list: list[dict[str, Any]] = config.get("consensus_providers", [])
        if not raw_list:
            raise ValueError(
                "ConsensusProvider requires at least one entry in "
                "`consensus_providers` (RCAN: agent.consensus_providers)"
            )

        # Detect primary provider index (matches primary_provider/primary_model)
        primary_provider = str(config.get("primary_provider", "")).lower()
        primary_model = str(config.get("primary_model", "")).lower()
        self._primary_idx = 0

        self._children: list[BaseProvider] = []
        for i, child_cfg in enumerate(raw_list):
            # Merge parent config keys the child needs (api_key env vars etc.)
            merged = {**config, **child_cfg}
            try:
                child = _get_child_provider(merged)
                self._children.append(child)
                if (
                    primary_provider
                    and child_cfg.get("provider", "").lower() == primary_provider
                    and (not primary_model or child_cfg.get("model", "").lower() == primary_model)
                    and self._primary_idx == 0
                    and i > 0
                ):
                    self._primary_idx = i
            except Exception as exc:
                logger.warning(
                    "ConsensusProvider: skipping child provider %s — %s",
                    child_cfg.get("provider", "?"),
                    exc,
                )

        if not self._children:
            raise ValueError("ConsensusProvider: all child providers failed to initialise")

        self._quorum = int(config.get("quorum", max(1, len(self._children) // 2 + 1)))
        self._timeout_s = float(config.get("timeout_ms", 5000)) / 1000.0

        logger.info(
            "ConsensusProvider: %d children, quorum=%d, timeout=%.1fs, primary_idx=%d",
            len(self._children),
            self._quorum,
            self._timeout_s,
            self._primary_idx,
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _propagate_caps(self) -> None:
        """Push _caps and _robot_name down to all children."""
        for child in self._children:
            child._caps = self._caps
            child._robot_name = self._robot_name

    def _query_child(
        self,
        idx: int,
        child: BaseProvider,
        image_bytes: bytes,
        instruction: str,
        surface: str,
    ) -> tuple[int, Thought]:
        try:
            t0 = time.monotonic()
            thought = child.think(image_bytes, instruction, surface)
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.debug(
                "Consensus child[%d] answered in %.0f ms: type=%s",
                idx,
                elapsed_ms,
                _action_type(thought),
            )
            return idx, thought
        except Exception as exc:
            logger.warning("Consensus child[%d] error: %s", idx, exc)
            return idx, Thought(f"error: {exc}", {"type": "stop"})

    # ── BaseProvider interface ────────────────────────────────────────────────

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        """Query all providers in parallel and return the quorum winner."""
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            return safety_block

        self._propagate_caps()

        results: list[tuple[int, Thought]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self._children)) as executor:
            futures = {
                executor.submit(
                    self._query_child, idx, child, image_bytes, instruction, surface
                ): idx
                for idx, child in enumerate(self._children)
            }
            deadline = time.monotonic() + self._timeout_s
            for future in concurrent.futures.as_completed(
                futures, timeout=max(0.1, deadline - time.monotonic())
            ):
                try:
                    results.append(future.result(timeout=0.1))
                except Exception as exc:
                    idx = futures[future]
                    logger.warning("Consensus child[%d] future error: %s", idx, exc)

        if not results:
            logger.error("ConsensusProvider: all children timed out — stopping")
            return Thought("All consensus providers timed out.", {"type": "stop"})

        winner, reason = _pick_winner(results, self._quorum, self._primary_idx)
        logger.info(
            "ConsensusProvider: winner action=%s reason=%s (from %d/%d responses)",
            _action_type(winner),
            reason,
            len(results),
            len(self._children),
        )
        return winner

    def think_stream(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "chat",
    ) -> Iterator[str]:
        """Run quorum consensus across all children, then stream the winning response in chunks.

        All child providers' ``think()`` calls are dispatched in parallel (same as
        ``think()``).  Once quorum is resolved the winner's ``raw_text`` is yielded
        in 20-character chunks with a small inter-chunk delay to produce a realistic
        streaming feel for callers that render tokens progressively.
        """
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            if safety_block.raw_text:
                yield safety_block.raw_text
            return

        self._propagate_caps()

        results: list[tuple[int, Thought]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self._children)) as executor:
            futures = {
                executor.submit(
                    self._query_child, idx, child, image_bytes, instruction, surface
                ): idx
                for idx, child in enumerate(self._children)
            }
            deadline = time.monotonic() + self._timeout_s
            for future in concurrent.futures.as_completed(
                futures, timeout=max(0.1, deadline - time.monotonic())
            ):
                try:
                    results.append(future.result(timeout=0.1))
                except Exception as exc:
                    idx = futures[future]
                    logger.warning("Consensus stream child[%d] future error: %s", idx, exc)

        if not results:
            logger.error("ConsensusProvider.think_stream: all children timed out")
            yield "[consensus: all providers timed out]"
            return

        winner, reason = _pick_winner(results, self._quorum, self._primary_idx)
        logger.debug(
            "ConsensusProvider.think_stream: winner action=%s reason=%s (%d/%d responses)",
            _action_type(winner),
            reason,
            len(results),
            len(self._children),
        )

        text = winner.raw_text
        chunk_size = 20
        for i in range(0, len(text), chunk_size):
            yield text[i : i + chunk_size]
            time.sleep(0.02)

    def health_check(self) -> dict:
        """Return aggregated health status across all children."""
        self._propagate_caps()
        statuses = []
        all_ok = True
        for idx, child in enumerate(self._children):
            try:
                hc = child.health_check()
                statuses.append({"idx": idx, **hc})
                if not hc.get("ok"):
                    all_ok = False
            except Exception as exc:
                statuses.append({"idx": idx, "ok": False, "error": str(exc)})
                all_ok = False

        return {
            "ok": all_ok,
            "mode": "consensus",
            "quorum": self._quorum,
            "children": len(self._children),
            "child_statuses": statuses,
            "error": None if all_ok else "one or more child providers unhealthy",
        }

    def get_usage_stats(self) -> dict[str, Any]:
        """Aggregate usage stats across all children."""
        self._propagate_caps()
        stats: dict[str, Any] = {"provider": "consensus", "children": []}
        for idx, child in enumerate(self._children):
            try:
                child_stats = child.get_usage_stats()
                stats["children"].append({"idx": idx, **child_stats})
            except Exception:
                pass
        return stats
