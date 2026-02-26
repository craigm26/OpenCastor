"""Tests for Apple provider runtime adapter."""

from __future__ import annotations

import asyncio
import types
from unittest.mock import patch

from castor.providers.apple_provider import AppleProvider


def _fake_sdk_module():
    sdk = types.SimpleNamespace()

    class _UseCase:
        GENERAL = object()
        CONTENT_TAGGING = object()

    class _Guardrails:
        DEFAULT = object()
        PERMISSIVE_CONTENT_TRANSFORMATIONS = object()

    class _Reason:
        name = "UNKNOWN"

    class _Model:
        def __init__(self, use_case=None, guardrails=None):
            self.use_case = use_case
            self.guardrails = guardrails

        def is_available(self):
            return True, None

    class _Session:
        def __init__(self, model=None):
            self.model = model

        async def respond(self, prompt):
            if prompt == "ping":
                return "pong"
            return '{"type":"stop"}'

        async def stream_response(self, prompt):
            yield "{"
            await asyncio.sleep(0)
            yield '{"type"'
            await asyncio.sleep(0)
            yield '{"type":"stop"}'

    class _RateLimitedError(Exception):
        pass

    class _ConcurrentRequestsError(Exception):
        pass

    sdk.SystemLanguageModelUseCase = _UseCase
    sdk.SystemLanguageModelGuardrails = _Guardrails
    sdk.SystemLanguageModelUnavailableReason = _Reason
    sdk.SystemLanguageModel = _Model
    sdk.LanguageModelSession = _Session
    sdk.RateLimitedError = _RateLimitedError
    sdk.ConcurrentRequestsError = _ConcurrentRequestsError
    return sdk


def test_apple_provider_think_and_health_check():
    fake_sdk = _fake_sdk_module()
    with patch.dict("sys.modules", {"apple_fm_sdk": fake_sdk}):
        provider = AppleProvider({"provider": "apple", "model": "apple-balanced"})
        with patch("castor.providers.apple_provider.run_apple_preflight", return_value={"ok": True}):
            thought = provider.think(b"", "stop")

        health = provider.health_check()

    assert thought.action == {"type": "stop"}
    assert health["ok"] is True


def test_apple_provider_stream_emits_chunks():
    fake_sdk = _fake_sdk_module()
    with patch.dict("sys.modules", {"apple_fm_sdk": fake_sdk}):
        provider = AppleProvider({"provider": "apple", "model": "apple-balanced"})
        chunks = list(provider.think_stream(b"", "stop"))

    text = "".join(chunks)
    assert "type" in text
    assert "stop" in text
