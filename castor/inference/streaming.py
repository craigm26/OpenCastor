"""
castor.inference.streaming — continuous vision inference loop.

Captures frames at a configurable FPS, runs each through the provider's
think() function, gates on confidence, and executes actions that pass.

Usage::

    from castor.inference.streaming import StreamingInferenceLoop

    loop = StreamingInferenceLoop(
        get_frame_fn=my_camera.capture,
        think_fn=my_provider.think,
        execute_fn=api_state.execute_action,
        fps=2,
        min_confidence=0.8,
    )
    await loop.start()
    # ...
    await loop.stop()

Or via classmethod from a RCAN config dict::

    loop = StreamingInferenceLoop.from_config(
        config,
        get_frame_fn=...,
        think_fn=...,
        execute_fn=...,
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_MAX_FPS = 10.0
_DEFAULT_FPS = 2.0
_DEFAULT_MIN_CONFIDENCE = 0.75

# Optional integrations — graceful no-ops if not installed
try:
    from castor.metrics import (
        record_streaming_action,
        record_streaming_frame,
    )

    HAS_METRICS = True
except Exception:
    HAS_METRICS = False

    def record_streaming_frame(*_: Any, **__: Any) -> None:  # type: ignore[misc]
        pass

    def record_streaming_action(*_: Any, **__: Any) -> None:  # type: ignore[misc]
        pass


try:
    from castor.rcan.commitment_chain import get_commitment_chain

    HAS_CHAIN = True
except Exception:
    HAS_CHAIN = False

    def get_commitment_chain() -> Any:  # type: ignore[misc]
        return None


# ── Types ─────────────────────────────────────────────────────────────────────

FrameFn = Callable[[], Awaitable[Any]]
ThinkFn = Callable[[Any], Awaitable[dict[str, Any]]]
ExecuteFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class StreamingStats:
    frames_captured: int = 0
    frames_gated_pass: int = 0
    frames_gated_block: int = 0
    actions_executed: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def actual_fps(self) -> float:
        if self.elapsed_s <= 0:
            return 0.0
        return self.frames_captured / self.elapsed_s

    def summary(self) -> str:
        return (
            f"frames={self.frames_captured} "
            f"passed={self.frames_gated_pass} "
            f"blocked={self.frames_gated_block} "
            f"actions={self.actions_executed} "
            f"fps={self.actual_fps:.1f} "
            f"elapsed={self.elapsed_s:.1f}s"
        )


# ── Core loop ─────────────────────────────────────────────────────────────────


class StreamingInferenceLoop:
    """
    Continuous vision inference loop with confidence gating.

    Args:
        get_frame_fn:    Async callable returning a raw frame (bytes, ndarray, etc.)
        think_fn:        Async callable accepting a frame, returning a result dict
                         with at least ``{"confidence": float, "cmd": str, ...}``
        execute_fn:      Async callable accepting a result dict; triggers the action
        fps:             Target frames per second (capped at 10)
        min_confidence:  Minimum confidence to pass the gate and execute an action
        dry_run:         If True, gate and log but never call execute_fn
    """

    def __init__(
        self,
        get_frame_fn: FrameFn,
        think_fn: ThinkFn,
        execute_fn: ExecuteFn,
        fps: float = _DEFAULT_FPS,
        min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
        dry_run: bool = False,
    ) -> None:
        self._get_frame = get_frame_fn
        self._think = think_fn
        self._execute = execute_fn
        self.fps = min(fps, _MAX_FPS)
        self.min_confidence = min_confidence
        self.dry_run = dry_run
        self.stats = StreamingStats()
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        get_frame_fn: FrameFn,
        think_fn: ThinkFn,
        execute_fn: ExecuteFn,
    ) -> StreamingInferenceLoop:
        """Build a loop from a RCAN YAML config dict.

        Reads ``agent.streaming.fps`` and ``agent.streaming.min_confidence``.
        Returns ``None`` if ``agent.streaming.enabled`` is falsy.
        """
        streaming_cfg = (config.get("agent") or {}).get("streaming") or {}
        fps = float(streaming_cfg.get("fps", _DEFAULT_FPS))
        min_conf = float(streaming_cfg.get("min_confidence", _DEFAULT_MIN_CONFIDENCE))
        dry_run = bool(streaming_cfg.get("dry_run", False))
        return cls(
            get_frame_fn=get_frame_fn,
            think_fn=think_fn,
            execute_fn=execute_fn,
            fps=fps,
            min_confidence=min_conf,
            dry_run=dry_run,
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def interval(self) -> float:
        """Seconds between frames."""
        return 1.0 / max(self.fps, 0.01)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the loop in the background."""
        if self.is_running:
            logger.debug("StreamingInferenceLoop already running")
            return
        self.stats = StreamingStats()
        self._task = asyncio.ensure_future(self._run())
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        logger.info(
            "StreamingInferenceLoop started [%s] fps=%.1f min_confidence=%.2f",
            mode,
            self.fps,
            self.min_confidence,
        )

    async def stop(self) -> None:
        """Stop the loop and wait for it to finish."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("StreamingInferenceLoop stopped. %s", self.stats.summary())

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        chain = get_commitment_chain() if HAS_CHAIN else None

        while True:
            t0 = time.monotonic()
            try:
                await self._tick(chain)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats.errors += 1
                logger.warning("StreamingInferenceLoop tick error: %s", exc)

            elapsed = time.monotonic() - t0
            sleep_for = max(0.0, self.interval - elapsed)
            await asyncio.sleep(sleep_for)

    async def _tick(self, chain: Any) -> None:
        # 1. Capture
        frame = await self._get_frame()
        self.stats.frames_captured += 1
        record_streaming_frame()

        # 2. Infer
        result: dict[str, Any] = await self._think(frame)
        confidence: float = float(result.get("confidence", 0.0))
        cmd = result.get("cmd", result.get("action", "unknown"))

        logger.debug(
            "frame=%d cmd=%s confidence=%.3f",
            self.stats.frames_captured,
            cmd,
            confidence,
        )

        # 3. Gate
        if confidence < self.min_confidence:
            self.stats.frames_gated_block += 1
            logger.debug("gate blocked: %.3f < %.3f", confidence, self.min_confidence)
            return

        self.stats.frames_gated_pass += 1

        # 4. Execute (or dry-run)
        if not self.dry_run:
            await self._execute(result)
            self.stats.actions_executed += 1
            record_streaming_action()

            if chain is not None:
                try:
                    chain.append(
                        {
                            "action": cmd,
                            "confidence": confidence,
                            "streaming": True,
                            "model_identity": result.get("model_identity", ""),
                        }
                    )
                except Exception:
                    pass

            logger.info(
                "action executed: cmd=%s confidence=%.3f (frame %d)",
                cmd,
                confidence,
                self.stats.frames_captured,
            )
        else:
            logger.info(
                "[DRY-RUN] would execute: cmd=%s confidence=%.3f",
                cmd,
                confidence,
            )
