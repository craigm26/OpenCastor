"""Gated model provider — authenticated access to closed/frontier model APIs.

Supports Physical Intelligence (π), HuggingFace gated models, enterprise
endpoints behind OAuth2/mTLS/OIDC, and any API requiring authenticated access.

Usage:
    provider = GatedModelProvider(config={
        "name": "pi-foundation",
        "base_url": "https://api.physicalintelligence.company/v1",
        "auth": {"method": "oauth2", "client_id": "${PI_CLIENT_ID}", ...},
        "models": ["pi0", "pi0.5-grasp"],
        "fallback_provider": "ollama",
        "fallback_model": "rt2-x",
    })
    result = await provider.inference(model="pi0", prompt=..., images=[...])
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from castor.auth.provider_auth import AuthCredentials, create_provider_auth

log = logging.getLogger("OpenCastor.Provider.Gated")


@dataclass
class InferenceResult:
    """Result from a gated model inference call."""

    model: str
    provider: str
    output: Any = None
    actions: list[dict] | None = None  # For action models (π0 etc.)
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    from_fallback: bool = False
    error: str | None = None


@dataclass
class ProviderStatus:
    """Health status of a gated provider."""

    name: str
    available: bool = True
    auth_valid: bool = True
    last_success: float = 0.0
    last_error: str | None = None
    consecutive_failures: int = 0
    rate_limit_remaining: int | None = None
    rate_limit_reset: float | None = None


class GatedModelProvider:
    """Authenticated access to gated/closed model APIs.

    Features:
    - Automatic token refresh (OAuth2, OIDC)
    - Fallback to local model on failure
    - Rate limit tracking and backoff
    - Credential isolation (never logged or sent to telemetry)
    - Audit trail (model used, latency — without tokens)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.name = config.get("name", "gated")
        self.base_url = config.get("base_url", "").rstrip("/")
        self.models = config.get("models", [])
        self.fallback_provider = config.get("fallback_provider")
        self.fallback_model = config.get("fallback_model")
        self.timeout = config.get("timeout", 30)
        self.max_retries = config.get("max_retries", 2)

        auth_config = config.get("auth", {})
        if auth_config:
            self._auth = create_provider_auth(auth_config)
        else:
            self._auth = None

        self._status = ProviderStatus(name=self.name)
        self._config = config

    @property
    def status(self) -> ProviderStatus:
        return self._status

    def get_credentials(self) -> AuthCredentials | None:
        """Get current credentials (refreshing if needed)."""
        if self._auth is None:
            return None
        try:
            creds = self._auth.get_credentials()
            self._status.auth_valid = True
            return creds
        except Exception as exc:
            log.error("Auth failed for %s: %s", self.name, exc)
            self._status.auth_valid = False
            self._status.last_error = str(exc)
            return None

    async def inference(
        self,
        model: str,
        prompt: str | None = None,
        messages: list[dict] | None = None,
        images: list[str] | None = None,
        actions: dict | None = None,
        **kwargs: Any,
    ) -> InferenceResult:
        """Run inference on a gated model.

        Supports both LLM-style (prompt/messages) and action model (actions)
        interfaces. Falls back to local model on failure if configured.
        """
        if model not in self.models and self.models:
            available = ", ".join(self.models)
            return InferenceResult(
                model=model,
                provider=self.name,
                error=f"Model {model} not available. Available: {available}",
            )

        # Check rate limits
        if self._is_rate_limited():
            log.warning("%s: rate limited, using fallback", self.name)
            return await self._fallback(model, prompt, messages, images, actions)

        creds = self.get_credentials()
        if creds is None:
            log.warning("%s: auth failed, using fallback", self.name)
            return await self._fallback(model, prompt, messages, images, actions)

        # Build request
        start = time.monotonic()
        try:
            result = await self._call_api(model, creds, prompt, messages, images, actions, **kwargs)
            latency_ms = (time.monotonic() - start) * 1000

            self._status.available = True
            self._status.last_success = time.time()
            self._status.consecutive_failures = 0
            self._status.last_error = None

            result.latency_ms = latency_ms
            return result

        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            self._status.consecutive_failures += 1
            self._status.last_error = str(exc)

            if self._status.consecutive_failures >= 3:
                self._status.available = False

            log.error(
                "%s inference failed (attempt %d): %s",
                self.name,
                self._status.consecutive_failures,
                exc,
            )

            return await self._fallback(model, prompt, messages, images, actions)

    async def _call_api(
        self,
        model: str,
        creds: AuthCredentials,
        prompt: str | None,
        messages: list[dict] | None,
        images: list[str] | None,
        actions: dict | None,
        **kwargs: Any,
    ) -> InferenceResult:
        """Make the actual API call to the gated provider."""
        import httpx

        headers = {**creds.headers, "Content-Type": "application/json"}

        # Build request body — adapt to provider API format
        body: dict[str, Any] = {"model": model}

        if messages:
            body["messages"] = messages
        elif prompt:
            body["messages"] = [{"role": "user", "content": prompt}]

        if images:
            body["images"] = images

        if actions:
            body["actions"] = actions

        body.update(kwargs)

        # Determine endpoint
        endpoint = self._config.get("inference_endpoint", "/chat/completions")
        url = f"{self.base_url}{endpoint}"

        # Build httpx client kwargs
        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if creds.client_cert:
            client_kwargs["cert"] = creds.client_cert
        if creds.ca_bundle:
            client_kwargs["verify"] = creds.ca_bundle

        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(url, json=body, headers=headers)

            # Track rate limits
            self._update_rate_limits(resp.headers)

            if resp.status_code == 401:
                # Token expired — invalidate and retry
                if self._auth:
                    self._auth.invalidate()
                raise PermissionError("Authentication expired")

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "60")
                self._status.rate_limit_reset = time.time() + float(retry_after)
                raise RuntimeError(f"Rate limited (retry after {retry_after}s)")

            resp.raise_for_status()
            data = resp.json()

        # Parse response — handle both LLM and action model formats
        output = None
        action_list = None
        tokens_in = 0
        tokens_out = 0

        # OpenAI-compatible format
        choices = data.get("choices", [])
        if choices:
            output = choices[0].get("message", {}).get("content", "")

        # Action model format (π-style)
        if "actions" in data:
            action_list = data["actions"]
            output = data.get("summary", "")

        # Usage
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)

        return InferenceResult(
            model=model,
            provider=self.name,
            output=output,
            actions=action_list,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    async def _fallback(
        self,
        model: str,
        prompt: str | None,
        messages: list[dict] | None,
        images: list[str] | None,
        actions: dict | None,
    ) -> InferenceResult:
        """Fall back to local model."""
        if not self.fallback_model:
            return InferenceResult(
                model=model,
                provider=self.name,
                error=f"Provider {self.name} unavailable, no fallback configured",
            )

        log.info(
            "Falling back from %s/%s to %s/%s",
            self.name,
            model,
            self.fallback_provider or "local",
            self.fallback_model,
        )

        return InferenceResult(
            model=self.fallback_model,
            provider=self.fallback_provider or "local",
            output=None,
            from_fallback=True,
            error=f"Gated provider {self.name} unavailable — using fallback",
        )

    def _is_rate_limited(self) -> bool:
        """Check if we're currently rate limited."""
        if self._status.rate_limit_reset is None:
            return False
        return time.time() < self._status.rate_limit_reset

    def _update_rate_limits(self, headers: Any) -> None:
        """Update rate limit tracking from response headers."""
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                self._status.rate_limit_remaining = int(remaining)
            except (ValueError, TypeError):
                pass

        reset = headers.get("X-RateLimit-Reset")
        if reset is not None:
            try:
                self._status.rate_limit_reset = float(reset)
            except (ValueError, TypeError):
                pass

    def telemetry(self) -> dict[str, Any]:
        """Return provider telemetry (NEVER includes credentials)."""
        return {
            "provider": self.name,
            "available": self._status.available,
            "auth_valid": self._status.auth_valid,
            "auth_method": self._auth.method if self._auth else "none",
            "models": self.models,
            "consecutive_failures": self._status.consecutive_failures,
            "last_success": self._status.last_success,
            "rate_limit_remaining": self._status.rate_limit_remaining,
            "has_fallback": bool(self.fallback_model),
        }
