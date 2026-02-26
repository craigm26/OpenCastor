"""Apple Foundation Models provider for OpenCastor."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import Any, Dict, Iterator

from .apple_preflight import run_apple_preflight
from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Apple")


_PROFILE_DEFAULTS: Dict[str, tuple[str, str]] = {
    "apple-balanced": ("GENERAL", "DEFAULT"),
    "apple-creative": ("GENERAL", "PERMISSIVE_CONTENT_TRANSFORMATIONS"),
    "apple-tagging": ("CONTENT_TAGGING", "DEFAULT"),
}


class AppleProvider(BaseProvider):
    """On-device Apple Foundation Models adapter."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._sdk = self._load_sdk()
        self._profile_id = self._resolve_profile_id(config)
        self._model, self._session = self._build_model_and_session(self._profile_id)

    @staticmethod
    def _load_sdk():
        try:
            import apple_fm_sdk as sdk  # type: ignore

            return sdk
        except Exception as exc:
            raise ImportError(
                "Apple provider requires apple-fm-sdk. "
                "Install extra with: pip install -e '.[apple]'"
            ) from exc

    def _resolve_profile_id(self, config: Dict[str, Any]) -> str:
        profile = str(
            config.get("apple_profile") or config.get("model") or "apple-balanced"
        ).strip()
        if profile in _PROFILE_DEFAULTS:
            return profile
        if profile in {"default-model", "default"}:
            return "apple-balanced"
        # Unknown profile: keep setup resilient and fall back to balanced.
        logger.warning("Unknown Apple profile '%s'; using apple-balanced", profile)
        return "apple-balanced"

    def _build_model_and_session(self, profile_id: str):
        use_case_name, guardrails_name = _PROFILE_DEFAULTS[profile_id]
        use_case = getattr(self._sdk.SystemLanguageModelUseCase, use_case_name)
        guardrails = getattr(self._sdk.SystemLanguageModelGuardrails, guardrails_name)

        model = self._sdk.SystemLanguageModel(use_case=use_case, guardrails=guardrails)
        session = self._sdk.LanguageModelSession(model=model)
        return model, session

    def _run_async(self, coro):
        """Run coroutine from sync context, even when an event loop already exists."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        out: queue.Queue = queue.Queue(maxsize=1)

        def _worker() -> None:
            try:
                result = asyncio.run(coro)
                out.put((True, result))
            except Exception as exc:  # pragma: no cover - exercised via caller behavior
                out.put((False, exc))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join()
        ok, payload = out.get()
        if ok:
            return payload
        raise payload

    def _build_prompt(self, image_bytes: bytes, instruction: str, surface: str) -> str:
        is_blank = not image_bytes or image_bytes == b"\x00" * len(image_bytes)
        if is_blank:
            prefix = self.build_messaging_prompt(
                robot_name=self._robot_name,
                capabilities=self._caps,
                surface=surface,
            )
            return f"{prefix}\n\nUser: {instruction}"
        return instruction

    def _log_usage(self, prompt: str, text: str) -> None:
        try:
            from castor.usage import get_tracker

            get_tracker().log_usage(
                provider="apple",
                model=self._profile_id,
                prompt_tokens=max(1, len(prompt) // 4),
                completion_tokens=max(1, len(text) // 4) if text else 0,
            )
        except Exception:
            pass

    def health_check(self) -> dict:
        t0 = time.time()
        try:
            is_available, reason = self._model.is_available()
            if not is_available:
                reason_name = getattr(reason, "name", str(reason) if reason is not None else "UNKNOWN")
                return {
                    "ok": False,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "error": f"Apple model unavailable: {reason_name}",
                    "reason": reason_name,
                }

            # Cheap ping through session path.
            _ = self._run_async(self._session.respond("ping"))
            return {
                "ok": True,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": str(exc),
            }

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            return safety_block

        prompt = self._build_prompt(image_bytes, instruction, surface)

        try:
            preflight = run_apple_preflight(model_profile_id=self._profile_id)
            if not preflight.get("ok", False):
                reason = preflight.get("reason", "UNKNOWN")
                return Thought(f"Apple model unavailable ({reason}).", None)

            text = self._run_async(self._session.respond(prompt))
            if not isinstance(text, str):
                text = str(text)
            action = self._clean_json(text)
            self._log_usage(prompt, text)
            return Thought(text, action)
        except (self._sdk.RateLimitedError, self._sdk.ConcurrentRequestsError) as exc:
            return Thought(f"Retryable Apple provider error: {exc}", None)
        except Exception as exc:
            logger.error("Apple provider error: %s", exc)
            return Thought(f"Error: {exc}", None)

    def think_stream(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Iterator[str]:
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            yield safety_block.raw_text
            return

        prompt = self._build_prompt(image_bytes, instruction, surface)
        q: queue.Queue = queue.Queue()
        sentinel = object()

        def _worker() -> None:
            async def _run_stream() -> None:
                previous = ""
                async for snapshot in self._session.stream_response(prompt):
                    if not isinstance(snapshot, str):
                        snapshot = str(snapshot)
                    chunk = snapshot
                    if snapshot.startswith(previous):
                        chunk = snapshot[len(previous) :]
                    previous = snapshot
                    if chunk:
                        q.put(chunk)

            try:
                asyncio.run(_run_stream())
            except Exception as exc:
                q.put(f"Error: {exc}")
            finally:
                q.put(sentinel)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        while True:
            item = q.get()
            if item is sentinel:
                break
            yield item

    @property
    def profile_id(self) -> str:
        """Return resolved Apple model profile id."""
        return self._profile_id
